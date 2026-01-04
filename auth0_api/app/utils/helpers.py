"""
Utility functions for the Auth0 API application.
"""

from urllib.parse import urlparse
from typing import Optional, List


def derive_frontend_origin(
    react_url: Optional[str] = None,
    frontend_url: Optional[str] = None,
    default: str = "http://localhost:5173"
) -> str:
    """
    Derive the frontend origin from available configuration.

    Args:
        react_url: REACT_APP_URL environment variable value
        frontend_url: FRONTEND_URL environment variable value
        default: Default URL if no other options are available

    Returns:
        Normalized origin URL (scheme://netloc)
    """
    url = react_url or frontend_url or default
    parsed = urlparse(url)

    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"

    return url


def normalize_origin(url: str) -> Optional[str]:
    """
    Normalize and validate a URL to extract the origin.

    Args:
        url: URL to normalize

    Returns:
        Normalized origin (scheme://netloc) or None if invalid
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return None


def is_allowed_origin(origin: str, allowed_origins: List[str]) -> bool:
    """
    Check if an origin is in the allowed origins list.

    Args:
        origin: Origin to check
        allowed_origins: List of allowed origins

    Returns:
        True if origin is allowed, False otherwise
    """
    return origin in allowed_origins
