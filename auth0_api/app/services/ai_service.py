"""
AI service for interacting with LLM models via Azure OpenAI.
"""

import asyncio
from typing import List, Optional
from google import genai
from google.genai import types
from openai import AsyncOpenAI, RateLimitError, APIError, APITimeoutError
from app.config.settings import settings
from app.config.logging import get_logger
from app.exceptions.handlers import AIServiceError

logger = get_logger(__name__)


class AIService:
    """Service for AI/LLM operations."""

    def __init__(self):
        """Initialize the AI service with Azure OpenAI client."""
        self.client = AsyncOpenAI(
            base_url=settings.AI_BASE_URL,
            api_key=settings.OPENROUTER_API_KEY,
            timeout=settings.AI_REQUEST_TIMEOUT,
        )
        self.embedding_client = genai.Client(api_key=settings.GEMINI_API_KEY)

    async def get_greeting(
        self,
        system: str,
        user: str,
        model: str = settings.AI_MODEL,
        max_tokens: int = settings.AI_MAX_TOKENS,
        retries: int = settings.AI_RETRIES,
        backoff_base: float = settings.AI_BACKOFF_BASE,
    ) -> Optional[str]:
        """
        Generate a greeting or message using the AI model with retry logic.

        Args:
            system: System prompt to guide AI behavior
            user: User prompt/message
            model: Model identifier (default from settings)
            max_tokens: Maximum tokens in response
            retries: Number of retry attempts for transient failures
            backoff_base: Exponential backoff base multiplier

        Returns:
            Generated message or None if AI is unavailable

        Raises:
            AIServiceError: For non-retryable errors
        """
        for attempt in range(1, retries + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system.strip()},
                        {"role": "user", "content": user.strip()},
                    ],
                    max_tokens=max_tokens,
                )

                choices = getattr(response, "choices", None)
                if not choices:
                    raise AIServiceError("Empty response from AI model")

                message = getattr(choices[0], "message", None)
                choice = getattr(message, "content", None)
                if not isinstance(choice, str) or not choice.strip():
                    raise AIServiceError("Empty response from AI model")

                logger.debug("AI response generated successfully")
                return choice.strip()

            except RateLimitError:
                wait = backoff_base ** attempt
                logger.warning(
                    "AI rate limit hit (attempt %d/%d). Retrying in %fs",
                    attempt,
                    retries,
                    wait,
                )
                if attempt < retries:
                    await asyncio.sleep(wait)

            except (APITimeoutError, APIError) as e:
                logger.error("AI API error: %s", e)
                # Do not retry server-side failures
                raise AIServiceError(f"AI service error: {str(e)}") from e

            except Exception as e:
                logger.exception("Unexpected AI failure: %s", e)
                raise AIServiceError(f"Unexpected AI error: {str(e)}") from e

        logger.warning("AI service unavailable after %d retries", retries)
        return None

    async def generate_embeddings(
        self,
        text: str,
        model: str = settings.EMBEDDING_MODEL,
        dimensions: int = settings.EMBEDDING_DIMENSIONS,
        retries: int = settings.AI_RETRIES,
        backoff_base: float = settings.AI_BACKOFF_BASE,
    ) -> List[float]:
        """
        Generate embeddings for text using Gemini.

        Args:
            text: Text to generate embeddings for
            model: Embedding model identifier (default from settings)
            dimensions: Number of dimensions for embeddings (default 768)
            retries: Number of retry attempts for transient failures
            backoff_base: Exponential backoff base multiplier

        Returns:
            List of float values representing the embedding vector

        Raises:
            AIServiceError: For non-retryable errors
        """
        for attempt in range(1, retries + 1):
            try:
                response = await asyncio.to_thread(
                    self.embedding_client.models.embed_content,
                    model=model,
                    contents=text.strip(),
                    config=types.EmbedContentConfig(
                        output_dimensionality=dimensions,
                        task_type="RETRIEVAL_QUERY",
                    ),
                )

                embedding = response.embeddings[0].values
                if not embedding or len(embedding) != dimensions:
                    raise AIServiceError(
                        f"Invalid embedding response: expected {dimensions} dimensions, got {len(embedding) if embedding else 0}"
                    )

                logger.debug("Embeddings generated successfully (dimensions=%d)", len(embedding))
                return embedding

            except RateLimitError:
                wait = backoff_base ** attempt
                logger.warning(
                    "Embedding API rate limit hit (attempt %d/%d). Retrying in %fs",
                    attempt,
                    retries,
                    wait,
                )
                if attempt < retries:
                    await asyncio.sleep(wait)

            except (APITimeoutError, APIError) as e:
                logger.error("Embedding API error: %s", e)
                # Do not retry server-side failures
                raise AIServiceError(f"Embedding service error: {str(e)}") from e

            except AIServiceError:
                raise

            except Exception as e:
                logger.exception("Unexpected embedding failure: %s", e)
                raise AIServiceError(f"Unexpected embedding error: {str(e)}") from e

        logger.warning("Embedding service unavailable after %d retries", retries)
        raise AIServiceError("Failed to generate embeddings after retries")


# Singleton instance
_ai_service: Optional[AIService] = None


def get_ai_service() -> AIService:
    """
    Get or create the singleton AI service instance.

    Returns:
        AIService instance
    """
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service
