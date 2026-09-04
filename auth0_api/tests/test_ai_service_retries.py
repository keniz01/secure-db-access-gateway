import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from openai import RateLimitError
from app.services.ai_service import AIService

@pytest.fixture
def ai_service_real_retry():
    # We want to test the retry logic which is in the method, 
    # so we mock the client but not the service method
    with patch("app.services.ai_service.AsyncOpenAI"), \
         patch("app.services.ai_service.genai.Client"):
        service = AIService()
        # Mock settings for faster retries
        with patch("app.config.settings.settings.AI_RETRIES", 2), \
             patch("app.config.settings.settings.AI_BACKOFF_BASE", 0.01):
            yield service

@pytest.mark.asyncio
async def test_get_greeting_retry_success(ai_service_real_retry):
    """Test that get_greeting retries on RateLimitError and eventually succeeds."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Success after retry"))]
    
    # Mock rate limit error then success
    ai_service_real_retry.client.chat.completions.create = AsyncMock(side_effect=[
        RateLimitError("Rate limit", response=MagicMock(), body={}),
        mock_response
    ])
    
    # Use small backoff for testing
    result = await ai_service_real_retry.get_greeting("system", "user", retries=2, backoff_base=0.01)
    
    assert result == "Success after retry"
    assert ai_service_real_retry.client.chat.completions.create.call_count == 2

@pytest.mark.asyncio
async def test_get_greeting_retry_exhausted(ai_service_real_retry):
    """Test that get_greeting returns None when retries are exhausted."""
    ai_service_real_retry.client.chat.completions.create = AsyncMock(side_effect=[
        RateLimitError("Rate limit", response=MagicMock(), body={}),
        RateLimitError("Rate limit", response=MagicMock(), body={})
    ])
    
    result = await ai_service_real_retry.get_greeting("system", "user", retries=2, backoff_base=0.01)
    
    assert result is None
    assert ai_service_real_retry.client.chat.completions.create.call_count == 2
