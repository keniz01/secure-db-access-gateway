"""
Configuration settings for the Auth0 API application.
Loads environment variables and provides configuration objects.
"""

import os
from typing import List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings:
    """Application configuration settings."""

    # Application
    APP_NAME: str = "SQL Query Executor Auth API"
    APP_VERSION: str = "0.1.0"
    LOG_LEVEL: str = os.getenv("AUTH_LOG_LEVEL", "INFO").upper()

    # Secret Keys
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key")
    SESSION_SECRET_KEY: str = os.getenv("SESSION_SECRET_KEY", "your-session-secret-key")
    APP_SECRET_KEY: str = os.getenv("APP_SECRET_KEY", "your-app-secret-key")

    # Auth0 Configuration
    AUTH0_DOMAIN: str = os.getenv("AUTH0_DOMAIN", "")
    AUTH0_CLIENT_ID: str = os.getenv("AUTH0_CLIENT_ID", "")
    AUTH0_CLIENT_SECRET: str = os.getenv("AUTH0_CLIENT_SECRET", "")
    AUTH0_SCOPE: str = "openid profile email"

    # Frontend Configuration
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173/dashboard")
    REACT_APP_URL: str = os.getenv("REACT_APP_URL", "http://localhost:5173")

    # AI/LLM Configuration
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    AI_MODEL: str = "gpt-4o-mini"
    AI_BASE_URL: str = "https://models.inference.ai.azure.com"
    AI_REQUEST_TIMEOUT: float = 15.0
    AI_MAX_TOKENS: int = 300
    AI_RETRIES: int = 3
    AI_BACKOFF_BASE: float = 2.0

    # CORS Configuration
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Alternative port
        "http://127.0.0.1:5173",
    ]

    # Feature Flags
    ENABLE_AI_GREETING: bool = True


# Create a singleton instance
settings = Settings()
