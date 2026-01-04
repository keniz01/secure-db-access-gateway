"""
Auth0 API - Entry point for the SQL Query Executor authentication service.

This module serves as the main entry point for running the FastAPI application.
All configuration, middleware, and route setup is delegated to dedicated modules.
"""

import uvicorn
from app.factory import create_app
from app.config.settings import settings

# Create the FastAPI application
app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        log_level=settings.LOG_LEVEL.lower(),
    )