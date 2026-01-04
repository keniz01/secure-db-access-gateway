"""
AI service for interacting with LLM models via Azure OpenAI.
"""

import asyncio
from typing import Optional
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
            api_key=settings.GITHUB_TOKEN,
            timeout=settings.AI_REQUEST_TIMEOUT,
        )

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

                choice = response.choices[0].message.content
                if not choice:
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
