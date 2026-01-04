# Development Guide - Auth0 API

This guide provides practical instructions for developing, testing, and maintaining the refactored Auth0 API.

## Quick Start

### 1. Environment Setup

```bash
# Navigate to auth0_api directory
cd auth0_api

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .
# Or if using uv:
uv pip install -e .
```

### 2. Configure Environment

```bash
# Copy and edit the .env file with your credentials
cp .env.example .env
# Edit .env with Auth0 and other API credentials
```

### 3. Run the Application

```bash
# Development mode (with auto-reload)
python main.py

# Or use uvicorn directly
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

## Code Structure Reference

### Understanding the Module Layout

```
app/
├── config/          # Configuration settings and logging setup
├── auth/            # Auth0 and OAuth configuration
├── services/        # Business logic (AI service, etc.)
├── routes/          # API endpoints
├── middleware/      # Cross-cutting concerns (CORS, sessions)
├── schemas/         # Data models and validation
├── exceptions/      # Custom exceptions
└── utils/           # Helper functions
```

### Import Patterns

When working with the codebase, follow these import patterns:

```python
# ✅ DO: Import from modules
from app.config.settings import settings
from app.config.logging import get_logger
from app.services.ai_service import get_ai_service
from app.utils.helpers import derive_frontend_origin

# ❌ DON'T: Circular imports or deeply nested imports
# ❌ DON'T: Import from __pycache__
```

## Adding New Features

### 1. Adding a New Endpoint

**Step 1**: Create the route handler in appropriate file (or create new file in `routes/`)

```python
# In routes/new_routes.py
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from ..config.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["new_feature"])

@router.get("/new-endpoint")
async def new_endpoint(request: Request):
    """
    Description of what this endpoint does.
    """
    try:
        # Your logic here
        return JSONResponse(status_code=status.HTTP_200_OK, content={...})
    except Exception as e:
        logger.exception("Error in new_endpoint: %s", e)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An error occurred"}
        )
```

**Step 2**: Add response schema in `schemas/responses.py`

```python
class NewEndpointResponse(BaseModel):
    """Response schema for new endpoint."""
    field1: str
    field2: int
    
    class Config:
        extra = "allow"
```

**Step 3**: Register the router in `app/factory.py`

```python
from ..routes import new_routes

# In create_app() function:
app.include_router(new_routes.router)
```

### 2. Adding a New Service

**Step 1**: Create service class in `services/`

```python
# In services/my_service.py
from ..config.logging import get_logger
from ..exceptions.handlers import ServiceError

logger = get_logger(__name__)

class MyService:
    """Service for my feature."""
    
    def __init__(self):
        """Initialize the service."""
        self.initialized = True
        logger.debug("MyService initialized")
    
    async def do_something(self, param: str) -> str:
        """
        Do something useful.
        
        Args:
            param: Input parameter
            
        Returns:
            Result
            
        Raises:
            ServiceError: If operation fails
        """
        try:
            # Implementation
            result = f"Processed: {param}"
            return result
        except Exception as e:
            logger.error("Error in do_something: %s", e)
            raise ServiceError(f"Failed to process: {str(e)}") from e

# Singleton pattern
_service: Optional[MyService] = None

def get_my_service() -> MyService:
    """Get or create singleton service instance."""
    global _service
    if _service is None:
        _service = MyService()
    return _service
```

**Step 2**: Use in routes

```python
# In routes/some_route.py
from ..services.my_service import get_my_service

@router.post("/use-service")
async def use_service():
    service = get_my_service()
    result = await service.do_something("input")
    return {"result": result}
```

### 3. Adding Configuration Settings

**Step 1**: Add to `Settings` class in `config/settings.py`

```python
class Settings:
    # ... existing settings ...
    
    # New feature settings
    NEW_FEATURE_ENABLED: bool = os.getenv("NEW_FEATURE_ENABLED", "true").lower() == "true"
    NEW_FEATURE_TIMEOUT: int = int(os.getenv("NEW_FEATURE_TIMEOUT", "30"))
```

**Step 2**: Add to `.env` file

```bash
NEW_FEATURE_ENABLED=true
NEW_FEATURE_TIMEOUT=30
```

**Step 3**: Use in code

```python
from app.config.settings import settings

if settings.NEW_FEATURE_ENABLED:
    timeout = settings.NEW_FEATURE_TIMEOUT
```

## Testing

### Unit Tests

Create tests in a `tests/` directory (at root level):

```python
# tests/test_ai_service.py
import pytest
from app.services.ai_service import AIService
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_ai_service_get_greeting():
    """Test AI service greeting generation."""
    service = AIService()
    
    # Mock the OpenAI client
    with patch.object(service.client.chat.completions, 'create', new_callable=AsyncMock) as mock_create:
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock(message=AsyncMock(content="Hello!"))]
        mock_create.return_value = mock_response
        
        result = await service.get_greeting(
            system="You are helpful",
            user="Say hello"
        )
        
        assert result == "Hello!"
```

### Integration Tests

```python
# tests/test_routes.py
import pytest
from fastapi.testclient import TestClient
from app.factory import create_app

@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)

def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

### Manual Testing with cURL

```bash
# Health check
curl http://localhost:8001/api/health

# Get user (requires authenticated session)
curl -b "session_cookie" http://localhost:8001/api/user

# Test dashboard
curl -b "session_cookie" http://localhost:8001/api/dashboard
```

## Debugging

### Enable Debug Logging

Set environment variable:

```bash
export AUTH_LOG_LEVEL=DEBUG
python main.py
```

### View Request/Response Details

The logging system will show:
- All endpoint accesses
- OAuth operations
- AI service calls
- Error conditions

### Common Issues

| Issue | Solution |
|-------|----------|
| "Not authenticated" | Check session middleware is configured |
| CORS errors | Add frontend origin to `ALLOWED_ORIGINS` in settings |
| AI service timeouts | Check `GITHUB_TOKEN` and network connectivity |
| Missing environment variables | Copy and fill in `.env` file |

## Code Quality

### Linting

```bash
# Install linting tools
pip install flake8 black isort

# Format code
black app/
isort app/

# Check for issues
flake8 app/
```

### Type Checking

```bash
pip install mypy

# Check types
mypy app/
```

## Performance Optimization

### 1. AI Service Caching

Consider caching common greeting requests:

```python
from functools import lru_cache

class AIService:
    @lru_cache(maxsize=100)
    async def get_greeting_cached(self, user_name: str):
        return await self.get_greeting(...)
```

### 2. Connection Pooling

The AsyncOpenAI client already manages connection pooling.

### 3. Async/Await

Always use async functions for I/O operations:

```python
# ✅ DO: Use async
async def fetch_data():
    response = await client.get(...)
    return response

# ❌ DON'T: Block the event loop
def fetch_data():
    response = requests.get(...)  # Blocks!
    return response
```

## Security Checklist

- [ ] Secrets stored in `.env`, never hardcoded
- [ ] Input validation on all endpoints
- [ ] Proper CORS configuration
- [ ] Error messages don't leak sensitive info
- [ ] Session secrets are strong
- [ ] Token validation on protected routes
- [ ] Rate limiting considered

## Deployment Considerations

### Development to Production

1. **Environment Configuration**
   - Use strong secret keys
   - Set `AUTH_LOG_LEVEL=INFO`
   - Configure production CORS origins

2. **Dependencies**
   ```bash
   pip freeze > requirements.txt
   ```

3. **Run with Gunicorn**
   ```bash
   pip install gunicorn
   gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker
   ```

4. **Docker** (example)
   ```dockerfile
   FROM python:3.12-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install -r requirements.txt
   COPY . .
   CMD ["python", "main.py"]
   ```

## Useful Commands

```bash
# Activate venv
source venv/bin/activate

# Install in development mode
pip install -e .

# Run tests
pytest

# Format code
black app/

# Check type annotations
mypy app/

# View installed packages
pip list

# Deactivate venv
deactivate
```

## Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Authlib Documentation](https://docs.authlib.org/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [Auth0 Integration](https://auth0.com/docs)

## Getting Help

If you encounter issues:

1. Check the logs: `DEBUG` level logging in development
2. Review the MIGRATION.md for how code was organized
3. Check ARCHITECTURE.md for overall design
4. Look at similar endpoints for patterns
