"""Application factory for creating and configuring the FastAPI application."""

import os
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from strawberry.fastapi import GraphQLRouter

from config.app_logger import configure_telemetry, logger
from exceptions.exception_handlers import (
    http_exception_handler,
    validation_exception_handler,
)
from graphql_schema.schema import make_schema
from metrics import get_metrics_payload
from middlewares.body_size_limit_middleware import BodySizeLimitMiddleware
from middlewares.correlation_middleware import correlation_id_middleware
from middlewares.logging_middleware import LoggingMiddleware
from middlewares.rate_limit_middleware import RateLimitMiddleware
from middlewares.rbac_middleware import RBACMiddleware
from routes.health_routes import router as health_router


def setup_cors_middleware(app: FastAPI) -> None:
    """
    Configure CORS middleware with allowed origins.

    Args:
        app: FastAPI application instance.

    """
    cors_origins_env = os.getenv("CORS_ORIGINS", "")
    if cors_origins_env:
        origins: list[str] = [origin.strip() for origin in cors_origins_env.split(",")]
    else:
        origins = [
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
        ]

    # CORS review: sql_query_api authenticates with a bearer access token, never
    # browser cookies (the Auth0 API BFF holds the session). It is only reachable
    # server-to-server, behind nginx, so allow_credentials stays False; the
    # allowlist is a safety net for explicit browser tooling.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Requested-With",
            "X-Correlation-ID",
            "X-Request-ID",
        ],
        expose_headers=["X-Total-Count", "X-Correlation-ID", "X-Request-ID"],
        max_age=3600,
    )


def setup_security_middleware(app: FastAPI) -> None:
    """
    Configure security headers middleware.

    Args:
        app: FastAPI application instance.

    """

    @app.middleware("http")
    async def add_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def setup_custom_middlewares(app: FastAPI) -> None:
    """
    Add logging, correlation, rate-limit, and RBAC middlewares.

    Args:
        app: FastAPI application instance.

    """
    app.add_middleware(LoggingMiddleware)
    app.middleware("http")(correlation_id_middleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RBACMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)


def setup_exception_handlers(app: FastAPI) -> None:
    """
    Register application exception handlers.

    Args:
        app: FastAPI application instance.

    """
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)


def setup_routes(app: FastAPI) -> None:
    """
    Register the metrics and GraphQL routes.

    Args:
        app: FastAPI application instance.

    """

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(
            content=get_metrics_payload(),
            media_type="text/plain; version=0.7.0; charset=utf-8",
        )

    app.include_router(health_router)
    app.include_router(GraphQLRouter(make_schema()), prefix="/graphql")


async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run startup and shutdown lifecycle hooks for the application."""
    logger.info("🚀 Starting FastAPI application...")
    yield
    logger.info("🛑 Shutting down FastAPI application...")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance.

    """
    app = FastAPI(
        title="Secure Database Query API",
        version="1.0.0",
        lifespan=lifespan,
    )

    setup_cors_middleware(app)
    setup_security_middleware(app)
    setup_custom_middlewares(app)
    setup_exception_handlers(app)
    setup_routes(app)
    configure_telemetry()

    logger.info("Application configured and ready")

    return app
