"""
Configuration settings for the Auth0 API application.
Loads environment variables and provides configuration objects.
"""

import json
import os
from typing import Dict
from dotenv import load_dotenv

from shared_secrets import is_environment_production, read_secret

# Load environment variables from .env file (development convenience).
load_dotenv()

_PRODUCTION = is_environment_production()

# Ephemeral fallback for dev: generate random key if not configured
def _get_app_secret_key() -> str:
    raw = read_secret("APP_SECRET_KEY", required=_PRODUCTION)
    if raw:
        return raw
    if not _PRODUCTION:
        import secrets
        import warnings

        ephemeral = secrets.token_hex(32)
        warnings.warn(
            "APP_SECRET_KEY not configured - using ephemeral random key (sessions will not persist across restarts). Set APP_SECRET_KEY or APP_SECRET_KEY_FILE for stable sessions.",
            UserWarning,
            stacklevel=2,
        )
        return ephemeral
    return ""


class Settings:
    """Application configuration settings."""

    # Application
    APP_NAME: str = "SQL Query Executor Auth API"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = os.getenv("AUTH_LOG_LEVEL", "INFO").upper()

    # Session signing key. Required in production; it signs the gateway_session
    # cookie, so an empty value would silently produce forgeable sessions.
    # In dev, generates ephemeral key if not configured.
    APP_SECRET_KEY: str = _get_app_secret_key()
    SESSION_MAX_AGE: int = int(os.getenv("SESSION_MAX_AGE", "3600"))
    SESSION_COOKIE_SECURE: bool = os.getenv(
        "SESSION_COOKIE_SECURE", "true" if _PRODUCTION else "false"
    ).strip().lower() in {"1", "true", "yes"}

    # Auth0 Configuration. Required in production so OIDC discovery fails fast
    # at startup instead of at the first login attempt.
    AUTH0_DOMAIN: str = read_secret(
        "AUTH0_DOMAIN",
        required=_PRODUCTION,
    )
    AUTH0_CLIENT_ID: str = read_secret(
        "AUTH0_CLIENT_ID",
        required=_PRODUCTION,
    )
    AUTH0_CLIENT_SECRET: str = read_secret(
        "AUTH0_CLIENT_SECRET",
        required=_PRODUCTION,
    )
    # AUTH0_AUDIENCE: keep the legacy AUTH0_API_AUDIENCE short-circuit so old
    # deployments keep working; read_secret still covers AUTH0_AUDIENCE(_FILE).
    AUTH0_AUDIENCE: str = os.getenv("AUTH0_AUDIENCE") or os.getenv("AUTH0_API_AUDIENCE") or read_secret(
        "AUTH0_AUDIENCE",
        required=_PRODUCTION,
    )
    AUTH0_SCOPE: str = "openid profile email organizations"
    AUTH0_ORG_ID_CLAIM: str = "https://app.secure-db-access-gateway.org/tenant_id"

    # Frontend Configuration
    FRONTEND_URL: str = read_secret("FRONTEND_URL")
    REACT_APP_URL: str = read_secret("REACT_APP_URL")
    # OAuth redirect URI - static, pre-registered in Auth0 dashboard (OAuth 2.1).
    # If OAUTH_REDIRECT_URI is set, use it verbatim; otherwise derive from frontend origin + /auth.
    OAUTH_REDIRECT_URI: str = os.getenv("OAUTH_REDIRECT_URI", "").strip() or ""
    # Session store (memory | redis). Memory is single-process only.
    SESSION_STORE: str = os.getenv("SESSION_STORE", "memory").strip().lower()
    REDIS_URL: str = os.getenv("REDIS_URL", "").strip()

    # AI/LLM Configuration
    OPENROUTER_API_KEY: str = read_secret("OPENROUTER_API_KEY")
    AI_MODEL: str = read_secret("AI_MODEL")
    AI_BASE_URL: str = os.getenv("AI_BASE_URL", "https://openrouter.ai/api/v1")
    AI_REQUEST_TIMEOUT: float = 30.0
    AI_MAX_TOKENS: int = 300
    AI_RETRIES: int = 3
    AI_BACKOFF_BASE: float = 2.0
    # Disable extended reasoning on models that support it (e.g. NVIDIA Nemotron
    # on OpenRouter). Reasoning output adds latency and often pollutes `content`
    # with chain-of-thought prose instead of a clean SQL statement.
    AI_DISABLE_REASONING: bool = os.getenv(
        "AI_DISABLE_REASONING", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}

    # Embedding Configuration
    GEMINI_API_KEY: str = read_secret("GEMINI_API_KEY")
    EMBEDDING_MODEL: str = read_secret("EMBEDDING_MODEL")
    EMBEDDING_DIMENSIONS: int = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))

    # SQL Query API Configuration
    SQL_QUERY_API_URL: str = read_secret("SQL_QUERY_API_URL", default="http://localhost:8002/graphql")

    # Multi-tenancy / org metadata
    ORG_DB_CONNECTIONS: Dict[str, str] = {}
    GRAFANA_PROMETHEUS_URL: str = os.getenv("GRAFANA_PROMETHEUS_URL", "http://localhost:9090")

    # Feature Flags
    ENABLE_AI_GREETING: bool = os.getenv("ENABLE_AI_GREETING", "true").strip().lower() not in {"0", "false", "no", "off"}

    # CORS Configuration - Restrict to configured origins
    # Production: Set CORS_ORIGINS environment variable
    def __init__(self):
        cors_env = os.getenv("CORS_ORIGINS", "")
        if cors_env:
            self.ALLOWED_ORIGINS = [origin.strip() for origin in cors_env.split(",")]
        else:
            self.ALLOWED_ORIGINS = [
                "http://localhost:5173",
                "http://localhost:3000",
                "http://127.0.0.1:5173",
            ]

        # Derive OAUTH_REDIRECT_URI if not explicitly configured
        if not self.OAUTH_REDIRECT_URI:
            from app.utils.helpers import derive_frontend_origin as _derive

            _origin = _derive(self.REACT_APP_URL, self.FRONTEND_URL)
            self.OAUTH_REDIRECT_URI = f"{_origin.rstrip('/')}/auth"

        raw_org_mapping = os.getenv("ORG_DB_CONNECTIONS", "")
        if raw_org_mapping.strip():
            try:
                parsed_org_mapping = json.loads(raw_org_mapping)
                if isinstance(parsed_org_mapping, dict):
                    self.ORG_DB_CONNECTIONS = {str(k): str(v) for k, v in parsed_org_mapping.items()}
                else:
                    self.ORG_DB_CONNECTIONS = {}
            except json.JSONDecodeError:
                self.ORG_DB_CONNECTIONS = {}
        else:
            self.ORG_DB_CONNECTIONS = {}


# Create a singleton instance
settings = Settings()