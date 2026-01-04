# Auth0 API - Production-Ready Refactored Solution

A refactored, modular authentication API built with FastAPI and Auth0 for the SQL Query Executor platform.

## Project Structure

```
auth0_api/
├── app/
│   ├── __init__.py
│   ├── factory.py              # Application factory
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py         # Configuration management
│   │   └── logging.py          # Logging configuration
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── oauth.py            # OAuth2/Auth0 setup
│   │   └── session.py          # Session and user models
│   ├── services/
│   │   ├── __init__.py
│   │   └── ai_service.py       # LLM/AI service
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth_routes.py      # Authentication endpoints
│   │   └── user_routes.py      # User and dashboard endpoints
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── setup.py            # CORS and session middleware
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── responses.py        # Response models
│   ├── exceptions/
│   │   ├── __init__.py
│   │   └── handlers.py         # Custom exceptions
│   └── utils/
│       ├── __init__.py
│       └── helpers.py          # Utility functions
├── main.py                     # Application entry point
├── pyproject.toml              # Project dependencies
├── .env                        # Environment variables (not in git)
└── README.md                   # This file
```

## Architecture

### Separation of Concerns

- **config/**: Centralized configuration and environment variable management
- **auth/**: OAuth2 and Auth0 integration, user session handling
- **services/**: Business logic (AI service, etc.)
- **routes/**: API endpoints (controllers)
- **middleware/**: Cross-cutting concerns (CORS, sessions)
- **schemas/**: Data models and request/response validation
- **exceptions/**: Custom exception definitions
- **utils/**: Shared helper functions

### Key Features

1. **Configuration Management** (`config/settings.py`)
   - Single source of truth for all application settings
   - Environment variable loading with defaults
   - Easy to test and override

2. **Modular Routes**
   - `auth_routes.py`: Login, callback, logout endpoints
   - `user_routes.py`: User info and dashboard endpoints
   - Easy to add new routes without modifying existing code

3. **Service Layer**
   - `ai_service.py`: Encapsulates LLM interaction with retry logic
   - Singleton pattern for resource efficiency
   - Easy to test and mock

4. **Error Handling**
   - Custom exceptions for different error scenarios
   - Proper HTTP status codes and error responses
   - Comprehensive logging

5. **Middleware Setup**
   - CORS configuration with dynamic origin handling
   - Session management for OAuth state
   - Proper middleware ordering

## Installation

### Prerequisites

- Python 3.12+
- Virtual environment tool (venv, uv, poetry, etc.)

### Setup

1. Create and activate virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

Or with uv:

```bash
uv pip install -r requirements.txt
```

3. Configure environment variables:

```bash
cp .env.example .env
# Edit .env with your Auth0 credentials and other settings
```

## Environment Variables

Required environment variables (see `.env` file):

- `AUTH0_DOMAIN`: Your Auth0 domain
- `AUTH0_CLIENT_ID`: Auth0 application client ID
- `AUTH0_CLIENT_SECRET`: Auth0 application client secret
- `APP_SECRET_KEY`: Secret key for session management
- `GITHUB_TOKEN`: Token for Azure OpenAI access
- `REACT_APP_URL`: Frontend application URL
- `AUTH_LOG_LEVEL`: Logging level (INFO, DEBUG, etc.)

## Running the Application

### Development

```bash
python main.py
```

The server will start at `http://localhost:8001`

### Production

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## API Endpoints

### Authentication

- `GET /api/health` - Health check
- `GET /api/login` - Initiate Auth0 login
- `GET /api/auth` - OAuth callback (Auth0 redirect)
- `GET /api/logout` - Logout and clear session

### User

- `GET /api/user` - Get authenticated user info
- `GET /api/dashboard` - Get dashboard with AI greeting

## Testing

### Basic Health Check

```bash
curl http://localhost:8001/api/health
```

### Test Login Flow (requires Auth0 setup)

1. Visit `http://localhost:8001/api/login` in browser
2. Complete Auth0 authentication
3. Redirected to `/api/auth` callback
4. Session created with user and token

## Development Guide

### Adding a New Route

1. Create handler in appropriate file (`routes/auth_routes.py` or `routes/user_routes.py`)
2. Define response schema in `schemas/responses.py`
3. Add proper logging and error handling
4. Route is automatically included via `app.include_router()`

### Adding a New Service

1. Create service class in `services/` directory
2. Implement singleton pattern if needed
3. Add comprehensive error handling
4. Import in routes where needed

### Extending Configuration

1. Add new setting to `Settings` class in `config/settings.py`
2. Set environment variable with default value
3. Access via `settings.<setting_name>` throughout app

## Production Considerations

1. **Security**
   - Keep `APP_SECRET_KEY` and other secrets secure
   - Use environment variables, never hardcode secrets
   - Validate all inputs

2. **Logging**
   - Configure `AUTH_LOG_LEVEL` appropriately (INFO in production)
   - Monitor logs for authentication failures
   - Log user actions for audit trail

3. **Performance**
   - AI service has retry logic and exponential backoff
   - Consider caching for frequently accessed data
   - Use async/await for I/O operations

4. **Error Handling**
   - All endpoints return proper HTTP status codes
   - Error responses include `status_code`, `detail`, and optional error code
   - Client can parse and handle errors gracefully

5. **CORS**
   - Configured for localhost development
   - Update `ALLOWED_ORIGINS` for production domains
   - Be restrictive with CORS in production

## Maintenance & Scaling

### Code Organization

The modular structure makes it easy to:
- Add new features without affecting existing code
- Test individual components in isolation
- Scale specific parts (e.g., move AI service to separate microservice)

### Future Enhancements

- Implement token refresh logic
- Add database for user profiles
- Cache AI responses
- Rate limiting on endpoints
- Request/response validation middleware
- Database transaction management

## Troubleshooting

### "Not authenticated" error

- Check session middleware is configured
- Verify cookies are enabled in browser
- Ensure callback URL matches Auth0 configuration

### AI service unavailable

- Check `GITHUB_TOKEN` is valid
- Verify network connectivity to Azure OpenAI
- Check logs for rate limiting (automatic retry happens)

### CORS errors

- Add frontend origin to `ALLOWED_ORIGINS` in settings
- Or update `.env` `REACT_APP_URL` to match frontend

## License

This project is part of the SQL Query Executor platform.
