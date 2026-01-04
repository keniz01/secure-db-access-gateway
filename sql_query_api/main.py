from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from middlewares.logging_middleware import LoggingMiddleware
from middlewares.correlation_middleware import correlation_id_middleware
from exceptions.exception_handlers import (
    http_exception_handler,
    validation_exception_handler,
)
from graphql_schema.schema import schema
from strawberry.fastapi import GraphQLRouter
from config.app_logger import logger
import os
from typing import List


async def lifespan(app: FastAPI):
    logger.info("🚀 Starting FastAPI application...")
    yield
    logger.info("🛑 Shutting down FastAPI application...")


# App setup
app = FastAPI(
    title="Postgres SQL API", 
    version="1.0.0", 
    lifespan=lifespan
)

# CORS Configuration - Restrict to configured origins
cors_origins_env = os.getenv("CORS_ORIGINS", "")
if cors_origins_env:
    # Production: Load from environment
    origins: List[str] = [origin.strip() for origin in cors_origins_env.split(",")]
else:
    # Development: Default to localhost
    origins = [
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Alternative port
        "http://127.0.0.1:5173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    expose_headers=["X-Total-Count"],
    max_age=3600,
)

# Security middleware for response headers
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# Custom middlewares
app.add_middleware(LoggingMiddleware)
app.middleware("http")(correlation_id_middleware)

# Exception handlers
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# GraphQL
graphql_router = GraphQLRouter(schema)
app.include_router(graphql_router, prefix="/graphql")
