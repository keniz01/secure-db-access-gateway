"""
SQL Query API - Entry point for the SQL Query Executor service.

This module serves as the main entry point for running the FastAPI application.
All configuration, middleware, and route setup is delegated to dedicated modules.
"""

import uvicorn
from app_factory import create_app

# Create the FastAPI application
app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level="info",
    )
