import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from config.app_logger import logger, set_current_correlation_id


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs all requests and responses with correlation ID."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Forward the request and emit structured logs with a correlation ID."""
        correlation_id = (
            getattr(getattr(request, "state", None), "correlation_id", None)
            or request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or str(uuid4())
        )
        request.state.correlation_id = correlation_id
        set_current_correlation_id(correlation_id)
        start_time = time.time()

        with logger.contextualize(correlation_id=correlation_id):
            logger.info(f"📥 {request.method} {request.url.path}")

            try:
                response: Response = await call_next(request)
            except Exception as e:
                logger.exception(f"❌ Error during request: {e}")
                raise

            process_time = (time.time() - start_time) * 1000
            logger.info(
                f"📤 {request.method} {request.url.path} | {response.status_code} | {process_time:.2f}ms"
            )

            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Request-ID"] = correlation_id
            return response
