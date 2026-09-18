import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from app.config.settings import settings
from app.config.logging import (
    get_logger,
    reset_current_correlation_id,
    sanitize_correlation_id,
    set_current_correlation_id,
)
from app.security.csrf import get_allowed_origins

logger = get_logger(__name__)


def setup_cors_middleware(app: FastAPI):
    """
    Configure CORS middleware with allowed origins.

    CORS review outcome:
    * allow_origins is an explicit allowlist (never ``*``) shared with CSRF
      Origin/Referer validation via ``app.security.csrf.get_allowed_origins``.
    * allow_credentials=True is required because every browser request carries
      the httpOnly session cookie; credentials are only combined with concrete
      origins (Starlette rejects ``*`` + credentials).
    * Methods and headers are kept to the minimal set the API and SPA use.
    * In production the SPA and the API share one origin behind nginx, so no
      cross-origin preflights occur; the allowlist exists for local dev mode
      (Vite on :5173 calling https://localhost:8443).

    Args:
        app: FastAPI application instance
    """
    allowed_origins = get_allowed_origins()

    logger.info("CORS allowed origins: %s", allowed_origins)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Requested-With",
            "X-CSRF-Token",
            "X-Correlation-ID",
            "X-Request-ID",
        ],
        expose_headers=["X-Total-Count", "X-Correlation-ID", "X-Request-ID"],
        max_age=3600,
    )
    
    # Security headers middleware
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


def setup_correlation_middleware(app: FastAPI):
    """
    Configure correlation ID middleware to trace requests across services.
    """
    @app.middleware("http")
    async def correlation_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Client-supplied values are only trusted when they match a strict
        # charset, preventing log/response header injection and oversized values.
        correlation_id = (
            sanitize_correlation_id(request.headers.get("X-Correlation-ID"))
            or sanitize_correlation_id(request.headers.get("X-Request-ID"))
            or str(uuid4())
        )
        request.state.correlation_id = correlation_id
        token = set_current_correlation_id(correlation_id)

        start_time = time.perf_counter()
        logger.info(f"[{correlation_id}] 📥 {request.method} {request.url.path}")

        try:
            response: Response = await call_next(request)

            process_time = (time.perf_counter() - start_time) * 1000
            logger.info(
                f"[{correlation_id}] 📤 {request.method} {request.url.path} | "
                f"{response.status_code} | {process_time:.2f}ms"
            )

            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Request-ID"] = correlation_id
            return response
        except Exception as exc:
            logger.exception(f"[{correlation_id}] ❌ Error during request: {exc}")
            raise
        finally:
            # Never leak this request's correlation ID into later (background) tasks.
            reset_current_correlation_id(token)


def setup_session_middleware(app: FastAPI):
    """
    Configure session middleware for OAuth state management.

    Args:
        app: FastAPI application instance
    """
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.APP_SECRET_KEY,
        session_cookie="gateway_session",
        max_age=settings.SESSION_MAX_AGE,
        same_site="lax",
        https_only=settings.SESSION_COOKIE_SECURE,
    )
    logger.debug("Session middleware configured")


def setup_middlewares(app: FastAPI):
    """
    Set up all middlewares for the application.

    Note: Middlewares are added in reverse order (last registered, first executed).

    Args:
        app: FastAPI application instance
    """
    # Added in reverse order of execution:
    # 1. correlation middleware (first to execute on request)
    # 2. security headers middleware
    # 3. CORS middleware
    # 4. session middleware
    setup_session_middleware(app)
    setup_cors_middleware(app)
    setup_correlation_middleware(app)
