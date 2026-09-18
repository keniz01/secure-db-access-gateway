import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from config.app_logger import set_current_correlation_id

try:
    from opentelemetry import trace
except ImportError:  # pragma: no cover
    trace = None


async def correlation_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """
    Middleware that injects a Correlation ID into every request and response.
    Useful for distributed tracing and log correlation.
    """
    start_time = time.perf_counter()

    # Get correlation ID from state, headers, or create a new one
    correlation_id = (
        getattr(getattr(request, "state", None), "correlation_id", None)
        or request.headers.get("X-Correlation-ID")
        or request.headers.get("X-Request-ID")
        or str(uuid.uuid4())
    )
    request.state.correlation_id = correlation_id  # store for later use
    set_current_correlation_id(correlation_id)

    if trace is not None:
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            current_span.set_attribute("app.correlation_id", correlation_id)

    # Process request
    try:
        response: Response = await call_next(request)
    except Exception as e:
        response = Response(content=f"Internal server error: {str(e)}", status_code=500)
        response.headers["X-Query-Status"] = "Error"
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Request-ID"] = correlation_id
        logging.exception(f"[{correlation_id}] Unhandled exception: {e}")
        return response

    # Measure execution time
    execution_time = time.perf_counter() - start_time

    # Add headers
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Request-ID"] = correlation_id
    response.headers["X-Execution-Time"] = f"{execution_time:.4f}s"
    response.headers["X-Query-Status"] = "Success"

    # Log the request/response summary
    logging.info(
        f"[{correlation_id}] {request.method} {request.url.path} "
        f"completed in {execution_time:.4f}s with status {response.status_code}"
    )

    return response
