from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request, Response
from uuid import uuid4
import time
from config.app_logger import logger


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs all requests and responses with correlation ID."""

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Request-ID", str(uuid4()))
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

            response.headers["X-Request-ID"] = correlation_id
            return response
