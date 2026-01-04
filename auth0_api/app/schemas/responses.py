"""
Response schemas for API endpoints.
"""

from pydantic import BaseModel, Field
from typing import Optional, Any


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(..., description="Health status")


class ErrorResponse(BaseModel):
    """Error response."""

    status_code: int = Field(..., description="HTTP status code")
    detail: str = Field(..., description="Error message")
    error: Optional[str] = Field(None, description="Error code")
    error_description: Optional[str] = Field(None, description="Error description")


class UserResponse(BaseModel):
    """User information response."""

    id: str = Field(..., description="User ID")
    email: str = Field(..., description="User email")
    name: str = Field(..., description="User name")

    class Config:
        """Pydantic configuration."""

        extra = "allow"


class AuthTokenResponse(BaseModel):
    """Authentication token response."""

    access_token: str = Field(..., description="Access token")
    token_type: str = Field(..., description="Token type")
    user: UserResponse = Field(..., description="User information")


class DashboardResponse(BaseModel):
    """Dashboard response with greeting."""

    email: str = Field(..., description="User email")
    name: str = Field(..., description="User name")
    message: str = Field(..., description="Daily greeting/message")
