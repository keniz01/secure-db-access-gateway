# Migration Guide: From Monolithic to Modular Architecture

This guide documents how the `auth0_api` was refactored from a single `main.py` file to a production-ready modular architecture.

## Overview of Changes

### Before (Monolithic)
- Single `main.py` file with ~338 lines
- All logic mixed together: configuration, middleware, routes, services
- Difficult to test individual components
- Hard to maintain and extend

### After (Modular)
- Well-organized folder structure
- Separation of concerns
- Easy to test each component
- Clear dependency flow
- Production-ready

## File Mapping

### Configuration & Setup

| Before | After |
|--------|-------|
| Inline environment loading | `config/settings.py` - centralized settings |
| Inline logging.basicConfig() | `config/logging.py` - logging setup |
| Inline CORS middleware | `middleware/setup.py` - middleware configuration |
| Inline OAuth client setup | `auth/oauth.py` - OAuth setup |

### Routes & Endpoints

| Before | After |
|--------|-------|
| `@app.get("/login")` | `routes/auth_routes.py` - login endpoint |
| `@app.get("/auth")` | `routes/auth_routes.py` - callback endpoint |
| `@app.get("/logout")` | `routes/auth_routes.py` - logout endpoint |
| `@app.get("/user")` | `routes/user_routes.py` - user endpoint |
| `@app.get("/dashboard")` | `routes/user_routes.py` - dashboard endpoint |
| `@app.get("/health")` | `routes/auth_routes.py` - health check |

### Services

| Before | After |
|--------|-------|
| `async def get_greetings()` | `services/ai_service.py` - AIService class |
| Inline AsyncOpenAI client | `services/ai_service.py` - properly abstracted |

### Models & Schemas

| Before | After |
|--------|-------|
| Inline user dict | `auth/session.py` - UserInfo and SessionData models |
| Inline response dicts | `schemas/responses.py` - Pydantic response models |

### Utilities

| Before | After |
|--------|-------|
| Inline `_derive_frontend_origin()` | `utils/helpers.py` - reusable helper |
| Inline URL parsing | `utils/helpers.py` - URL utilities |

## Key Improvements

### 1. Configuration Management

**Before:**
```python
import os
from dotenv import load_dotenv
load_dotenv()
LOG_LEVEL = os.getenv("AUTH_LOG_LEVEL", "INFO").upper()
# ...scattered throughout file
```

**After:**
```python
from app.config.settings import settings
# Access: settings.LOG_LEVEL, settings.AUTH0_DOMAIN, etc.
```

### 2. Logging

**Before:**
```python
logger = logging.getLogger("auth")
# One logger throughout
```

**After:**
```python
from app.config.logging import get_logger
logger = get_logger(__name__)  # Module-specific logger
# Can track where logs come from
```

### 3. Routes Organization

**Before:**
- All routes in main.py (6 endpoints mixed with setup code)
- Hard to find specific endpoint logic

**After:**
- `auth_routes.py` - 4 auth-related endpoints
- `user_routes.py` - 2 user-related endpoints
- Clear logical separation
- Easy to add more routes

### 4. Service Layer

**Before:**
```python
async def get_greetings(...) -> Optional[str]:
    # Retry logic mixed with business logic
    for attempt in range(1, retries + 1):
        # ...
```

**After:**
```python
class AIService:
    async def get_greeting(self, ...):
        # Clean, testable service method
        
# Usage:
ai_service = get_ai_service()
message = await ai_service.get_greeting(...)
```

### 5. Error Handling

**Before:**
```python
return JSONResponse(
    status_code=status.HTTP_401_UNAUTHORIZED,
    content={
        "status_code": status.HTTP_401_UNAUTHORIZED,
        "detail": error.error or "access_denied",
        # ...
    }
)
```

**After:**
```python
from app.schemas.responses import ErrorResponse

return JSONResponse(
    status_code=status.HTTP_401_UNAUTHORIZED,
    content=ErrorResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=error.error or "access_denied",
    ).dict()
)
```

## Testing Benefits

### Before
- Hard to test without full app instance
- AI service calls hard to mock
- Configuration scattered throughout

### After
```python
# Easy to test individual components
def test_ai_service():
    service = AIService()
    result = await service.get_greeting(...)

def test_helpers():
    origin = normalize_origin("http://example.com:3000")
    assert origin == "http://example.com"

def test_auth_routes():
    # Can mock OAuth, AI service, etc.
    ...
```

## Import Changes

### Before
```python
from fastapi import FastAPI, Request, HTTPException, status
from authlib.integrations.starlette_client import OAuth, OAuthError
from openai import AsyncOpenAI, RateLimitError, APIError, APITimeoutError
import logging
import os
from dotenv import load_dotenv
```

### After
```python
# In main.py (entry point):
from app.factory import create_app
from app.config.settings import settings

app = create_app()
```

## Running the Application

### Before
```bash
python main.py  # Actually, would need uvicorn main:app
```

### After
```bash
python main.py  # Includes uvicorn.run() call
# Or:
uvicorn main:app --reload
```

## Dependency Injection Pattern

The refactored code uses function-based dependency injection for services:

```python
# In routes:
ai_service = get_ai_service()  # Gets singleton
message = await ai_service.get_greeting(...)

# In tests, you could:
ai_service = MockAIService()  # Inject mock
```

## Environment Variable Access

### Before
```python
os.getenv("AUTH0_CLIENT_ID")
os.getenv("AUTH0_CLIENT_SECRET")
os.getenv("AUTH0_DOMAIN")
# scattered throughout file
```

### After
```python
settings.AUTH0_CLIENT_ID
settings.AUTH0_CLIENT_SECRET
settings.AUTH0_DOMAIN
# Type-safe, documented access
```

## Summary of Benefits

1. ✅ **Maintainability**: Clear file organization by concern
2. ✅ **Testability**: Components can be tested in isolation
3. ✅ **Scalability**: Easy to add features without touching existing code
4. ✅ **Readability**: Smaller, focused files with clear purpose
5. ✅ **Configuration**: Centralized settings management
6. ✅ **Error Handling**: Consistent, proper error responses
7. ✅ **Logging**: Module-specific loggers for better debugging
8. ✅ **Type Safety**: Pydantic models for request/response validation
9. ✅ **Production Ready**: Proper structure for deployment
10. ✅ **Team Collaboration**: Multiple developers can work on different modules

## Migration Checklist

- [x] Move configuration to dedicated module
- [x] Extract OAuth setup to dedicated module
- [x] Extract AI service to dedicated service class
- [x] Split routes into logical files
- [x] Create schema/model files
- [x] Set up middleware configuration
- [x] Create application factory
- [x] Update main.py to entry point
- [x] Add comprehensive documentation
- [x] Maintain all original functionality
