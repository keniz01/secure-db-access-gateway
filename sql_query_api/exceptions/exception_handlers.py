import logging
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from config.app_logger import logger

from exceptions.sql_statement_execution_exception import SqlStatementExecutionException


def raise_sql_execution_exception(
    message: str, error: Exception, include_traceback: bool = False
) -> None:
    """
    Raises a SqlStatementExecutionException with formatted error message and optional traceback.

    Args:
        message (str): Contextual message about the error.
        error (Exception): The caught exception.
        include_traceback (bool): If True, appends the full traceback to the message.
    """
    root_cause = error.__cause__ or error
    formatted_message = f"""
[SqlStatementExecutionException]
{message}
↳ Caused by {type(root_cause).__name__}: {root_cause}
""".strip()

    logging.error(message, exc_info=True)
    raise SqlStatementExecutionException(formatted_message) from error


async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(f"⚠️ HTTPException: {exc.detail} | Path: {request.url.path}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "HTTPException",
            "message": exc.detail,
            "path": request.url.path,
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"🛑 ValidationError at {request.url.path}: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation Error",
            "message": "Invalid request data",
            "details": exc.errors(),
            "path": request.url.path,
        },
    )
