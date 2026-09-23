"""
Authentication routes (login, logout, callback).

OAuth 2.1: Authorization Code + PKCE S256, static redirect_uri, token revocation.
"""

import base64
import hashlib
import secrets

import httpx
from fastapi import APIRouter, Request, status
from fastapi.responses import RedirectResponse, JSONResponse
from authlib.integrations.starlette_client import OAuthError
from urllib.parse import urlencode
from app.config.settings import settings
from app.config.logging import get_logger
from app.auth.oauth import get_oauth_instance
from app.utils.helpers import derive_frontend_origin
from app.schemas.responses import ErrorResponse, UserResponse
from app.auth.session_store import create_session, get_session, revoke_session
from app.security.csrf import clear_csrf_cookie, csrf_failure_reason, set_csrf_cookie

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])

# Derive frontend origin for post-logout returnTo (not for OAuth redirect_uri)
FRONTEND_ORIGIN = derive_frontend_origin(settings.REACT_APP_URL, settings.FRONTEND_URL)


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate RFC 7636 PKCE verifier and S256 challenge."""
    # 43-128 chars URL-safe; 64 bytes -> ~86 chars after base64url
    verifier = secrets.token_urlsafe(64)
    # Ensure length within 43-128; token_urlsafe(64) already satisfies
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def _revoke_token_at_auth0(token: str) -> None:
    """Best-effort RFC 7009 token revocation; never blocks logout."""
    if not token or not settings.AUTH0_DOMAIN or not settings.AUTH0_CLIENT_ID:
        return
    url = f"https://{settings.AUTH0_DOMAIN}/oauth/revoke"
    data = {
        "token": token,
        "client_id": settings.AUTH0_CLIENT_ID,
        "client_secret": settings.AUTH0_CLIENT_SECRET,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, data=data, headers={"content-type": "application/x-www-form-urlencoded"})
            if resp.status_code != 200:
                logger.warning("Token revocation returned %s: %s", resp.status_code, resp.text[:200])
            else:
                logger.info("Access token revoked at Auth0")
    except Exception as exc:  # noqa: BLE001 - best-effort, log and continue
        logger.warning("Token revocation failed (non-fatal): %s", exc)


@router.get("/health")
async def health_check():
    """
    Health check endpoint.

    Returns:
        Health status
    """
    return {"status": "ok"}


@router.get("/login")
async def login(request: Request):
    """
    Initiates OAuth2 login flow with Auth0 (Authorization Code + PKCE S256).

    Uses a static pre-registered redirect_uri per OAuth 2.1. The legacy
    ?redirect_origin query param is ignored (logged at warning) to prevent
    open-redirect and to enforce exact redirect_uri matching.

    Args:
        request: HTTP request

    Returns:
        Redirect to Auth0 login page
    """
    try:
        # OAuth 2.1: static pre-registered redirect_uri - no dynamic origin
        redirect_uri = settings.OAUTH_REDIRECT_URI

        # Legacy param is deprecated; warn but ignore to surface misconfigured clients
        param_origin = request.query_params.get('redirect_origin')
        if param_origin:
            logger.warning(
                "Ignoring deprecated redirect_origin param (OAuth 2.1 static redirect_uri): %s",
                param_origin,
            )

        # PKCE S256 + nonce per RFC 7636 / OIDC
        verifier, challenge = _generate_pkce_pair()
        nonce = secrets.token_urlsafe(16)
        request.session["pkce_verifier"] = verifier
        request.session["oidc_nonce"] = nonce

        logger.info("Initiating login; callback=%s (pkce=yes, nonce=yes)", redirect_uri)

        oauth = get_oauth_instance()
        auth0 = oauth.auth0
        auth_response = await auth0.authorize_redirect(
            request,
            redirect_uri,
            code_challenge=challenge,
            code_challenge_method="S256",
            nonce=nonce,
        )

        if hasattr(auth_response, 'headers') and 'location' in auth_response.headers:
            logger.debug("Auth0 redirect URL: %s...", auth_response.headers['location'][:100])

        return auth_response

    except Exception as e:
        logger.exception("Failed to initiate login: %s", e)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to initiate login",
                error=str(e)
            ).model_dump()
        )


@router.get("/auth")
async def auth_callback(request: Request):
    """
    OAuth2 callback endpoint where Auth0 sends the authorization code.

    Exchanges the authorization code for access token and user information.

    Args:
        request: HTTP request with authorization code

    Returns:
        JSON response with access_token and user info, or error response
    """
    try:
        logger.info("Auth callback invoked")
        oauth = get_oauth_instance()
        auth0 = oauth.auth0
        # Retrieve PKCE verifier stored at /login for token exchange
        verifier = request.session.get("pkce_verifier")
        # Authlib will send code_verifier if provided; if absent for a PKCE-started flow, Auth0 will reject
        if verifier:
            token = await auth0.authorize_access_token(request, code_verifier=verifier)
        else:
            logger.warning("Missing PKCE verifier in session during callback (possible session loss or non-PKCE login)")
            token = await auth0.authorize_access_token(request)
        # One-time use; prevent replay
        request.session.pop("pkce_verifier", None)
        logger.debug("Token exchange successful")

    except OAuthError as error:
        logger.warning(
            "OAuth error during token exchange: error=%s, description=%s",
            error.error,
            error.description
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error.error or "access_denied",
                error_description=error.description or "Authentication failed"
            ).model_dump()
        )
    except Exception as e:
        logger.exception("Unexpected error during token exchange: %s", e)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="access_denied",
                error=str(e)
            ).model_dump()
        )

    # OIDC nonce validation (replay protection for id_token)
    expected_nonce = request.session.pop("oidc_nonce", None)
    if expected_nonce is not None:
        id_claims = token.get("id_token_claims") or {}
        token_nonce = id_claims.get("nonce")
        # Only enforce when an id_token with nonce is present; access_token-only flows skip
        if token_nonce is not None and token_nonce != expected_nonce:
            logger.warning("Nonce mismatch in id_token (expected=%s got=%s)", expected_nonce, token_nonce)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content=ErrorResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="access_denied",
                    error="Invalid nonce",
                ).model_dump(),
            )

    user_info = token.get('userinfo') or token.get('id_token_claims')

    if not user_info:
        logger.error("No userinfo in token response")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="access_denied",
                error="Could not retrieve user information"
            ).model_dump()
        )

    user_id = user_info.get('sub')
    org_id = user_info.get(settings.AUTH0_ORG_ID_CLAIM)
    if not org_id and token.get("userinfo") is not None:
        id_token_claims = token.get("id_token_claims") or {}
        org_id = id_token_claims.get(settings.AUTH0_ORG_ID_CLAIM)
    if not isinstance(org_id, str) or not org_id.strip():
        logger.warning("Authenticated token is missing the required tenant claim")
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=ErrorResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="A trusted tenant claim is required",
                error="missing_tenant_claim",
            ).model_dump(),
        )

    user = {
        "id": user_id,
        "email": user_info.get('email'),
        "name": user_info.get('name'),
        "org_id": org_id,
        "roles": user_info.get('roles') or user_info.get('https://example.com/roles') or [],
    }
    access_token = token.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=ErrorResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="access_denied", error="Missing access token"
        ).model_dump())

    # The signed browser cookie carries only this opaque identifier; tokens remain server-side.
    # Rotate any previously issued server-side session so a stale cookie cannot
    # keep an orphaned session alive after a fresh login.
    previous_session_id = request.session.get("session_id")
    if previous_session_id:
        await revoke_session(previous_session_id)
    request.session.clear()
    request.session["session_id"] = await create_session(user, access_token, settings.SESSION_MAX_AGE)

    logger.info("User authenticated: %s (org=%s)", user_info.get('email'), org_id or "none")

    response = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "user": UserResponse(
                id=user_info.get('sub'),
                email=user_info.get('email'),
                name=user_info.get('name'),
            ).model_dump()
        }
    )
    # Stamp the double-submit CSRF token next to the session cookie so the SPA
    # can echo it in X-CSRF-Token on state-changing calls.
    set_csrf_cookie(response, await get_session(request.session.get("session_id")))
    return response


async def _perform_logout(request: Request) -> str:
    """Revoke token at AS, clear server session, return Auth0 logout URL."""
    session_id = request.session.get("session_id")
    if session_id:
        sess = await get_session(session_id)
        if sess and sess.get("access_token"):
            await _revoke_token_at_auth0(sess["access_token"])
    await revoke_session(session_id)
    request.session.clear()
    logger.info("Session cleared for user logout")
    auth0_logout_url = f"https://{settings.AUTH0_DOMAIN}/v2/logout"
    logout_params = {"client_id": settings.AUTH0_CLIENT_ID, "returnTo": FRONTEND_ORIGIN}
    return f"{auth0_logout_url}?{urlencode(logout_params)}"


@router.post("/logout")
async def logout_post(request: Request):
    """
    Logs out the user (state-changing POST, CSRF-protected).

    Requires double-submit CSRF + Origin/Referer + X-Requested-With when a session exists.
    Returns JSON with Auth0 logout URL; SPA should navigate via window.location.
    """
    session_id = request.session.get("session_id")
    sess = await get_session(session_id) if session_id else None
    if sess:
        reason = csrf_failure_reason(request, sess)
        if reason:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content=ErrorResponse(status_code=status.HTTP_403_FORBIDDEN, detail=reason).model_dump(),
            )
    logout_url = await _perform_logout(request)
    response = JSONResponse(status_code=status.HTTP_200_OK, content={"logout_url": logout_url})
    clear_csrf_cookie(response)
    return response


@router.get("/logout")
async def logout_get(request: Request):
    """
    Deprecated GET logout — returns 405.

    Logout is state-changing and must be POST per OWASP ASVS 4.3.1 / RFC 9110.
    Kept as 405 to surface misconfigured clients; use POST /api/logout.
    """
    logger.warning("Deprecated GET /api/logout rejected (use POST)")
    return JSONResponse(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        content=ErrorResponse(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, detail="Use POST /api/logout").model_dump(),
        headers={"Allow": "POST"},
    )
