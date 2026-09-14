import pytest
from app.services.ai_service import AIService
from app.config.settings import settings
from unittest.mock import AsyncMock, patch, MagicMock
from app.exceptions.handlers import AIServiceError

@pytest.fixture
def ai_service():
    with patch("app.services.ai_service.AsyncOpenAI"), patch("app.services.ai_service.genai.Client"):
        return AIService()

@pytest.mark.asyncio
async def test_get_greeting_success(ai_service):
    """Test successful greeting generation."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Hello there!"))]
    ai_service.client.chat.completions.create = AsyncMock(return_value=mock_response)
    
    result = await ai_service.get_greeting("system prompt", "user prompt")
    assert result == "Hello there!"
    ai_service.client.chat.completions.create.assert_called_once()

@pytest.mark.asyncio
async def test_get_greeting_passes_reasoning_disabled(ai_service, monkeypatch):
    """Reasoning is disabled via the OpenRouter extra_body plugin by default."""
    monkeypatch.setattr(settings, "AI_DISABLE_REASONING", True)
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="SELECT 1"))]
    ai_service.client.chat.completions.create = AsyncMock(return_value=mock_response)

    await ai_service.get_greeting("system", "user")

    _, kwargs = ai_service.client.chat.completions.create.call_args
    assert kwargs.get("extra_body") == {"reasoning": {"enabled": False}}

@pytest.mark.asyncio
async def test_get_greeting_empty_response(ai_service):
    """A persistently empty response degrades gracefully to None after retries."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=""))]
    ai_service.client.chat.completions.create = AsyncMock(return_value=mock_response)

    result = await ai_service.get_greeting("system", "user", retries=1, backoff_base=0.01)
    assert result is None


@pytest.mark.asyncio
async def test_get_greeting_retries_empty_response_then_succeeds(ai_service):
    """An intermittent empty response is retried instead of failing instantly."""
    empty = MagicMock()
    empty.choices = [MagicMock(message=MagicMock(content=None, reasoning=None))]
    ok = MagicMock()
    ok.choices = [MagicMock(message=MagicMock(content="Hello after retry"))]
    ai_service.client.chat.completions.create = AsyncMock(side_effect=[empty, ok])

    result = await ai_service.get_greeting("system", "user", retries=2, backoff_base=0.01)
    assert result == "Hello after retry"
    assert ai_service.client.chat.completions.create.call_count == 2


@pytest.mark.asyncio
async def test_get_greeting_falls_back_to_reasoning(ai_service):
    """Empty content falls back to the provider's message.reasoning field."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content=None, reasoning="Reasoned answer here"))
    ]
    ai_service.client.chat.completions.create = AsyncMock(return_value=mock_response)

    result = await ai_service.get_greeting("system prompt", "user prompt")
    assert result == "Reasoned answer here"


@pytest.mark.asyncio
@pytest.mark.parametrize("choices", [None, []])
async def test_get_greeting_missing_choices(ai_service, choices):
    """Test malformed provider responses degrade gracefully to None."""
    mock_response = MagicMock()
    mock_response.choices = choices
    ai_service.client.chat.completions.create = AsyncMock(return_value=mock_response)

    result = await ai_service.get_greeting("system", "user", retries=1, backoff_base=0.01)
    assert result is None

@pytest.mark.asyncio
async def test_generate_embeddings_success(ai_service):
    """Test successful embedding generation."""
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=[0.1, 0.2, 0.3])]
    ai_service.embedding_client.models.embed_content = MagicMock(return_value=mock_response)
    
    result = await ai_service.generate_embeddings("text", dimensions=3)
    assert result == [0.1, 0.2, 0.3]

@pytest.mark.asyncio
async def test_generate_embeddings_invalid_dimensions(ai_service):
    """Test embedding generation with dimension mismatch."""
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=[0.1, 0.2])]
    mock_response.embeddings = [MagicMock(values=[0.1, 0.2])]
    ai_service.embedding_client.models.embed_content = MagicMock(return_value=mock_response)
    
    with pytest.raises(AIServiceError, match="Invalid embedding response"):
        await ai_service.generate_embeddings("text", dimensions=3)
