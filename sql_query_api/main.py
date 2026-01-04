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

# CORS Configuration - Allow requests from React frontend
origins = [
    "http://localhost:5173",  # Vite dev server
    "http://localhost:3000",  # Alternative port
    "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom middlewares
app.add_middleware(LoggingMiddleware)
app.middleware("http")(correlation_id_middleware)

# Exception handlers
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# GraphQL
graphql_router = GraphQLRouter(schema)
app.include_router(graphql_router, prefix="/graphql")
