"""
User session and authentication context management.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict


class UserInfo(BaseModel):
    """User information model."""

    model_config = ConfigDict(extra="allow")

    id: str
    email: str
    name: str


class SessionData:
    """Session data container."""

    def __init__(self, user: Optional[UserInfo] = None, access_token: Optional[str] = None):
        self.user = user
        self.access_token = access_token

    def to_dict(self) -> Dict[str, Any]:
        """Convert session data to dictionary."""
        return {
            "user": self.user.model_dump() if self.user else None,
            "access_token": self.access_token,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "SessionData":
        """Create SessionData from dictionary."""
        user_data = data.get("user")
        user = UserInfo(**user_data) if user_data else None
        return SessionData(user=user, access_token=data.get("access_token"))
