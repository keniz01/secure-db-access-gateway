"""
Authentication routes (login, logout, callback).
"""

from fastapi import APIRouter, Request, status
from fastapi.responses import RedirectResponse, JSONResponse
from authlib.integrations.starlette_client import OAuthError
from urllib.parse import urlencode
from app.config.settings import settings
from app.config.logging import get_logger
from app.auth.oauth import get_oauth_instance
from app.utils.helpers import derive_frontend_origin, normalize_origin, is_allowed_origin
from app.schemas.responses import ErrorResponse, UserResponse
from app.auth.session_store import create_session, revoke_session

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])

# Derive frontend origin
FRONTEND_ORIGIN = derive_frontend_origin(settings.REACT_APP_URL, settings.FRONTEND_URL)


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
    Initiates OAuth2 login flow with Auth0.

    The callback is handled by the /auth endpoint. Accepts an optional
    redirect_origin query parameter to customize the callback URL.

    Args:
        request: HTTP request with optional redirect_origin query param

    Returns:
        Redirect to Auth0 login page
    """
    try:
        # Get potential origin values
        origin_header = request.headers.get('origin')
        param_origin = request.query_params.get('redirect_origin')

        chosen_origin = None

        # Validate redirect_origin param if provided
        if param_origin:
            normalized = normalize_origin(param_origin)
            if normalized and is_allowed_origin(normalized, settings.ALLOWED_ORIGINS):
                chosen_origin = normalized
            else:
                logger.warning(
                    "Rejected redirect_origin param not in allowed origins: %s",
                    normalized or param_origin
                )

        # Fallback to origin header or configured FRONTEND_ORIGIN
        if not chosen_origin:
            if origin_header and is_allowed_origin(origin_header, settings.ALLOWED_ORIGINS):
                chosen_origin = origin_header
            else:
                chosen_origin = FRONTEND_ORIGIN

        redirect_uri = f"{chosen_origin.rstrip('/')}/auth"
        logger.info(
            "Initiating login; callback=%s (origin_header=%s, param=%s)",
            redirect_uri,
            origin_header,
            param_origin
        )

        oauth = get_oauth_instance()
        auth0 = oauth.auth0
        auth_response = await auth0.authorize_redirect(request, redirect_uri)

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
        token = await auth0.authorize_access_token(request)
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

    user_info = token.get('userinfo')

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

    org_id = (
        user_info.get(settings.AUTH0_ORG_ID_CLAIM)
        or user_info.get("org_id")
        or user_info.get("organization")
        or user_info.get("https://example.com/org_id")
        or user_info.get("https://app.read-only-database-explorer.org/org_id")
    )

    user = {
        "id": user_info.get('sub'),
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
    request.session.clear()
    request.session["session_id"] = create_session(user, access_token, settings.SESSION_MAX_AGE)

    logger.info("User authenticated: %s (org=%s)", user_info.get('email'), org_id or "none")

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "user": UserResponse(
                id=user_info.get('sub'),
                email=user_info.get('email'),
                name=user_info.get('name'),
            ).model_dump()
        }
    )


@router.get("/logout")
async def logout(request: Request):
    """
    Logs out the user by clearing session and redirecting to Auth0 logout.

    Args:
        request: HTTP request

    Returns:
        Redirect to Auth0 logout URL
    """
    revoke_session(request.session.get("session_id"))
    request.session.clear()
    logger.info("Session cleared for user logout")

    auth0_logout_url = f"https://{settings.AUTH0_DOMAIN}/v2/logout"
    logout_params = {
        "client_id": settings.AUTH0_CLIENT_ID,
        "returnTo": FRONTEND_ORIGIN
    }

    logout_redirect = f"{auth0_logout_url}?{urlencode(logout_params)}"
    logger.info("Redirecting to Auth0 logout")

    return RedirectResponse(url=logout_redirect)
