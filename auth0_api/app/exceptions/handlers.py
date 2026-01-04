"""
Custom exceptions for the Auth0 API application.
"""


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    def __init__(self, message: str, error_code: str = "authentication_failed"):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)


class AuthorizationError(Exception):
    """Raised when authorization fails."""

    def __init__(self, message: str, error_code: str = "authorization_failed"):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)


class OAuth2Error(Exception):
    """Raised when OAuth2 operations fail."""

    def __init__(self, message: str, error: str = "", description: str = ""):
        self.message = message
        self.error = error
        self.description = description
        super().__init__(self.message)


class AIServiceError(Exception):
    """Raised when AI service operations fail."""

    def __init__(self, message: str, error_code: str = "ai_service_error"):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)
