import pytest
from app.services.ai_service import AIService
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
async def test_get_greeting_empty_response(ai_service):
    """Test greeting generation with empty response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=""))]
    ai_service.client.chat.completions.create = AsyncMock(return_value=mock_response)
    
    with pytest.raises(AIServiceError, match="Empty response from AI model"):
        await ai_service.get_greeting("system", "user")

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
