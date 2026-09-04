"""
Configuration settings for the Auth0 API application.
Loads environment variables and provides configuration objects.
"""

import json
import os
from typing import Any, Dict, List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def read_secret_from_file(file_path: str) -> str:
    """Read secret from file, fallback to empty string if file not found."""
    try:
        with open(file_path, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


class Settings:
    """Application configuration settings."""

    # Application
    APP_NAME: str = "SQL Query Executor Auth API"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = os.getenv("AUTH_LOG_LEVEL", "INFO").upper()

    # Secret Keys - Support both direct env vars and file-based secrets
    SECRET_KEY: str = os.getenv("SECRET_KEY") or read_secret_from_file(os.getenv("SECRET_KEY_FILE", ""))
    SESSION_SECRET_KEY: str = os.getenv("SESSION_SECRET_KEY") or read_secret_from_file(os.getenv("SESSION_SECRET_KEY_FILE", ""))
    APP_SECRET_KEY: str = os.getenv("APP_SECRET_KEY") or read_secret_from_file(os.getenv("SECRET_KEY_FILE", ""))
    SESSION_MAX_AGE: int = int(os.getenv("SESSION_MAX_AGE", "3600"))
    SESSION_COOKIE_SECURE: bool = os.getenv(
        "SESSION_COOKIE_SECURE", "true" if os.getenv("ENVIRONMENT", "development").lower() == "production" else "false"
    ).strip().lower() in {"1", "true", "yes"}

    # Auth0 Configuration
    AUTH0_DOMAIN: str = os.getenv("AUTH0_DOMAIN") or read_secret_from_file(os.getenv("AUTH0_DOMAIN_FILE", ""))
    AUTH0_CLIENT_ID: str = os.getenv("AUTH0_CLIENT_ID") or read_secret_from_file(os.getenv("AUTH0_CLIENT_ID_FILE", ""))
    AUTH0_CLIENT_SECRET: str = os.getenv("AUTH0_CLIENT_SECRET") or read_secret_from_file(os.getenv("AUTH0_CLIENT_SECRET_FILE", ""))
    AUTH0_AUDIENCE: str = os.getenv("AUTH0_AUDIENCE") or read_secret_from_file(os.getenv("AUTH0_AUDIENCE_FILE", ""))
    AUTH0_SCOPE: str = "openid profile email organizations"
    AUTH0_ORG_ID_CLAIM: str = os.getenv("AUTH0_ORG_ID_CLAIM", "org_id")

    # Frontend Configuration
    FRONTEND_URL: str = os.getenv("FRONTEND_URL") or read_secret_from_file(os.getenv("FRONTEND_URL_FILE", ""))
    REACT_APP_URL: str = os.getenv("REACT_APP_URL") or read_secret_from_file(os.getenv("REACT_APP_URL_FILE", ""))

    # AI/LLM Configuration
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY") or read_secret_from_file(os.getenv("OPENROUTER_API_KEY_FILE", ""))
    AI_MODEL: str = os.getenv("AI_MODEL") or read_secret_from_file(os.getenv("AI_MODEL_FILE", ""))
    AI_BASE_URL: str = os.getenv("AI_BASE_URL", "https://openrouter.ai/api/v1")
    AI_REQUEST_TIMEOUT: float = 15.0
    AI_MAX_TOKENS: int = 300
    AI_RETRIES: int = 3
    AI_BACKOFF_BASE: float = 2.0

    # Embedding Configuration
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY") or read_secret_from_file(os.getenv("GEMINI_API_KEY_FILE", ""))
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL") or read_secret_from_file(os.getenv("EMBEDDING_MODEL_FILE", ""))
    EMBEDDING_DIMENSIONS: int = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))

    # SQL Query API Configuration
    SQL_QUERY_API_URL: str = os.getenv("SQL_QUERY_API_URL") or read_secret_from_file(os.getenv("SQL_QUERY_API_URL_FILE", "")) or "http://localhost:8002/graphql"

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
