import os
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import InvalidTokenError, PyJWKClient


def read_secret_from_file(file_path: str) -> str:
    """Read secret from file, falling back to an empty string."""
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return ""


AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN") or read_secret_from_file(os.getenv("AUTH0_DOMAIN_FILE", ""))
AUTH0_AUDIENCE = (
    os.getenv("AUTH0_AUDIENCE")
    or os.getenv("AUTH0_API_AUDIENCE")
    or read_secret_from_file(os.getenv("AUTH0_AUDIENCE_FILE", ""))
)
AUTH0_ISSUER = os.getenv("AUTH0_ISSUER") or (f"https://{AUTH0_DOMAIN}/" if AUTH0_DOMAIN else "")


@dataclass(frozen=True, slots=True)
class Principal:
    """Trusted authorization context derived from a validated access token."""

    user_id: str
    email: str
    org_id: str
    roles: frozenset[str]

    @property
    def role(self) -> str:
        """Return the highest role understood by this application."""
        return "admin" if "admin" in self.roles else "viewer"


def extract_bearer_token(request) -> str | None:
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
    org_id = (
        claims.get("org_id")
        or claims.get("organization")
        or claims.get("https://app.read-only-database-explorer.org/org_id")
        or claims.get("https://example.com/org_id")
        # Keep users isolated while Auth0 Organizations/actions are not configured.
        or user_id
    )

    if not all(isinstance(value, str) and value.strip() for value in (user_id, email, org_id)):
        return None

    return Principal(
        user_id=user_id,
        email=email,
        org_id=org_id,
        roles=normalized_roles,
    )


def validate_access_token(token: str | None) -> dict[str, Any] | None:
    """Verify an Auth0 bearer token and return its validated claims."""
    if not token or not AUTH0_DOMAIN or not AUTH0_AUDIENCE:
        return None

    try:
        jwks_client = PyJWKClient(f"https://{AUTH0_DOMAIN}/.well-known/jwks.json")
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            audience=AUTH0_AUDIENCE,
            issuer=AUTH0_ISSUER or f"https://{AUTH0_DOMAIN}/",
        )
        return claims
    except (InvalidTokenError, ValueError, TypeError):
        return None
