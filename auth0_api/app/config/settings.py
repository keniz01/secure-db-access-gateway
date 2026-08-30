"""
Configuration settings for the Auth0 API application.
Loads environment variables and provides configuration objects.
"""

import os
from typing import List
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

    # Auth0 Configuration
    AUTH0_DOMAIN: str = os.getenv("AUTH0_DOMAIN") or read_secret_from_file(os.getenv("AUTH0_DOMAIN_FILE", ""))
    AUTH0_CLIENT_ID: str = os.getenv("AUTH0_CLIENT_ID") or read_secret_from_file(os.getenv("AUTH0_CLIENT_ID_FILE", ""))
    AUTH0_CLIENT_SECRET: str = os.getenv("AUTH0_CLIENT_SECRET") or read_secret_from_file(os.getenv("AUTH0_CLIENT_SECRET_FILE", ""))
    AUTH0_SCOPE: str = "openid profile email"

    # Frontend Configuration
    FRONTEND_URL: str = os.getenv("FRONTEND_URL") or read_secret_from_file(os.getenv("FRONTEND_URL_FILE", ""))
    REACT_APP_URL: str = os.getenv("REACT_APP_URL") or read_secret_from_file(os.getenv("REACT_APP_URL_FILE", ""))

    # AI/LLM Configuration
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN") or read_secret_from_file(os.getenv("GITHUB_TOKEN_FILE", ""))
    AI_MODEL: str = "gpt-4o-mini"
    AI_BASE_URL: str = "https://models.inference.ai.azure.com"
    AI_REQUEST_TIMEOUT: float = 15.0
    AI_MAX_TOKENS: int = 300
    AI_RETRIES: int = 3
    AI_BACKOFF_BASE: float = 2.0

    # Embedding Configuration
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_DIMENSIONS: int = int(os.getenv("EMBEDDING_DIMENSIONS", "384"))

    # SQL Query API Configuration
    SQL_QUERY_API_URL: str = os.getenv("SQL_QUERY_API_URL") or read_secret_from_file(os.getenv("SQL_QUERY_API_URL_FILE", "")) or "http://localhost:8002/graphql"

    # CORS Configuration - Restrict to configured origins
    # Production: Set CORS_ORIGINS environment variable
    def __init__(self):
        cors_env = os.getenv("CORS_ORIGINS", "")
        if cors_env:
            # Production: Load from environment
            self.ALLOWED_ORIGINS = [origin.strip() for origin in cors_env.split(",")]
        else:
            # Development: Default to localhost
            self.ALLOWED_ORIGINS = [
                "http://localhost:5173",  # Vite dev server
                "http://localhost:3000",  # Alternative port
                "http://127.0.0.1:5173",
            ]

    # Feature Flags
    ENABLE_AI_GREETING: bool = os.getenv("ENABLE_AI_GREETING", "true").strip().lower() not in {"0", "false", "no", "off"}


# Create a singleton instance
settings = Settings()
