import pytest
from app.utils.helpers import derive_frontend_origin, normalize_origin, is_allowed_origin

def test_derive_frontend_origin():
    """Test derivation of frontend origin."""
    assert derive_frontend_origin("http://localhost:3000/path") == "http://localhost:3000"
    assert derive_frontend_origin(None, "https://example.com") == "https://example.com"
    assert derive_frontend_origin(None, None, "http://default:5173") == "http://default:5173"
    assert derive_frontend_origin("invalid") == "invalid"

def test_normalize_origin():
    """Test origin normalization."""
    assert normalize_origin("http://user:pass@example.com:8080/path?query") == "http://example.com:8080"
    assert normalize_origin("https://sub.domain.com/") == "https://sub.domain.com"
    assert normalize_origin("not-a-url") is None

def test_is_allowed_origin():
    """Test allowed origin check."""
    allowed = ["http://localhost:5173", "https://app.com"]
    assert is_allowed_origin("http://localhost:5173", allowed) is True
    assert is_allowed_origin("https://app.com", allowed) is True
    assert is_allowed_origin("http://malicious.com", allowed) is False
    assert is_allowed_origin("https://app.com/", allowed) is False  # Exact match required
