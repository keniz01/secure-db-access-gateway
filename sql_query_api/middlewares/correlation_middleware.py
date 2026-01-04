import logging
import time
import uuid
from fastapi import Request, Response


async def correlation_id_middleware(request: Request, call_next) -> Response:
    """
    Middleware that injects a Correlation ID into every request and response.
    Useful for distributed tracing and log correlation.
    """
    start_time = time.perf_counter()

    # Get correlation ID from request or create a new one
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id  # store for later use

    # Process request
    try:
        response: Response = await call_next(request)
    except Exception as e:
        response = Response(content=f"Internal server error: {str(e)}", status_code=500)
        response.headers["X-Query-Status"] = "Error"
        logging.exception(f"[{correlation_id}] Unhandled exception: {e}")

    # Measure execution time
    execution_time = time.perf_counter() - start_time

    # Add headers
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Execution-Time"] = f"{execution_time:.4f}s"
    response.headers["X-Query-Status"] = "Success"

    # Log the request/response summary
    logging.info(
        f"[{correlation_id}] {request.method} {request.url.path} "
        f"completed in {execution_time:.4f}s with status {response.status_code}"
    )

    return response
