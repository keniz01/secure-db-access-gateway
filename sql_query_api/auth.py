import os
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import Request
from jwt import InvalidTokenError, PyJWKClient
from shared_secrets import is_environment_production, read_secret

_REQUIRED_AUTH_SECRETS = is_environment_production() and not os.getenv("CI")
_LEGACY_API_AUDIENCE = os.getenv("AUTH0_API_AUDIENCE", "")

AUTH0_DOMAIN = read_secret("AUTH0_DOMAIN", required=_REQUIRED_AUTH_SECRETS)
AUTH0_AUDIENCE = read_secret(
    "AUTH0_AUDIENCE",
    required=_REQUIRED_AUTH_SECRETS and not _LEGACY_API_AUDIENCE,
)
if not AUTH0_AUDIENCE:
    AUTH0_AUDIENCE = _LEGACY_API_AUDIENCE
AUTH0_ISSUER = os.getenv("AUTH0_ISSUER") or (f"https://{AUTH0_DOMAIN}/" if AUTH0_DOMAIN else "")
TENANT_ID_CLAIM = "https://app.secure-db-access-gateway.org/tenant_id"


@dataclass(frozen=True, slots=True)
class Principal:
    """Trusted authorization context derived from a validated access token."""

    user_id: str
    email: str
    org_id: str
    roles: frozenset[str]
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def role(self) -> str:
        """Return the highest role understood by this application (deprecated)."""
        return "admin" if "admin" in self.roles else "viewer"

    def has_role(self, role: str) -> bool:
        """Return whether the principal holds the given role."""
        return role.lower() in self.roles

    def has_any_role(self, roles: set[str] | frozenset[str]) -> bool:
        """Return whether the principal holds any of the given roles."""
        return bool(self.roles & frozenset(r.lower() for r in roles))

    def __post_init__(self) -> None:
        """Normalize the principal attributes mapping after initialization."""
        object.__setattr__(self, "attributes", dict(self.attributes or {}))

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - dynamic fallback lookup for attributes
        """Return a value from the principal attributes mapping for attribute access."""
        attributes = object.__getattribute__(self, "attributes")
        if name in attributes:
            return attributes[name]
        raise AttributeError(name)


def extract_bearer_token(request: Request) -> str | None:
    """Extract a bearer token from the Authorization header."""
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def build_principal_from_claims(claims: dict[str, Any] | None) -> Principal | None:
    """Map validated Auth0 claims to the internal principal contract."""
    if not claims:
        return None

    roles = claims.get("roles") or claims.get("https://app.read-only-database-explorer.org/roles") or []
    if isinstance(roles, str):
        roles = [roles]

    normalized_roles = frozenset(str(role).lower() for role in roles if str(role).strip())

    user_id = claims.get("sub")
    # Auth0 access tokens for a custom API may omit profile claims. The
    # validated subject remains a stable, non-spoofable audit identity.
    email = claims.get("email") or claims.get("preferred_username") or user_id
    org_id = claims.get(TENANT_ID_CLAIM)

    if not all(isinstance(value, str) and value.strip() for value in (user_id, email, org_id)):
        return None

    return Principal(
        user_id=user_id,
        email=email,
        org_id=org_id,
        roles=normalized_roles,
        attributes={
            str(key): value
            for key, value in claims.items()
            if key not in {"sub", "email", "preferred_username", "roles", TENANT_ID_CLAIM}
        },
    )


_jwks_client: PyJWKClient | None = None
_jwks_client_url: str | None = None


def _get_jwks_client() -> PyJWKClient:
    """Singleton JWKS client with key caching (avoids per-request fetch)."""
    global _jwks_client, _jwks_client_url
    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    if _jwks_client is None or _jwks_client_url != url:
        # cache_keys=True enables in-memory JWK set caching per PyJWT docs
        _jwks_client = PyJWKClient(url, cache_keys=True)
        _jwks_client_url = url
    return _jwks_client


def validate_access_token(token: str | None) -> dict[str, Any] | None:
    """Verify an Auth0 bearer token and return its validated claims."""
    if not token or not AUTH0_DOMAIN or not AUTH0_AUDIENCE:
        return None

    try:
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            audience=AUTH0_AUDIENCE,
            issuer=AUTH0_ISSUER or f"https://{AUTH0_DOMAIN}/",
            leeway=2,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
        # Scope is requested but RS authorizes via policy engine; if scope is
        # present, ensure it is a well-formed string (defense against malformed tokens)
        scope_val = claims.get("scope") or claims.get("scp")
        if scope_val is not None and not isinstance(scope_val, str):
            return None
        expected = {"openid", "profile", "email"}
        if scope_val and not expected.intersection(set(scope_val.split())):
            return None
        return claims
    except (InvalidTokenError, ValueError, TypeError):
        return None
