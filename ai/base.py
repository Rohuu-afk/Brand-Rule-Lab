"""
ai/base.py
----------
Abstract base class for AI providers.
Any provider (Gemini, future OpenAI fallback, etc.) must implement these methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    """
    Every AI provider must implement generate_text().
    The app calls only this interface — switching providers means swapping
    the class returned by provider_factory.py, nothing else.
    """

    @abstractmethod
    def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """
        Send *prompt* to the model and return the raw text response.

        Raises
        ------
        AIProviderError   — quota exhaustion, network timeout, or model error.
        """
        ...

    @property
    def last_model_used(self) -> str | None:
        """Name of the exact model that generated the most recent response."""
        return None

    @property
    def last_response_metadata(self) -> dict[str, Any] | None:
        """Non-content metadata returned for the most recent response, when available."""
        return None

    @property
    def used_fallback(self) -> bool:
        """True if the fallback model was utilized due to primary model unavailability or quota exhaustion."""
        return False

    @property
    def fallback_reason(self) -> str | None:
        """Explanation if fallback routing was engaged."""
        return None


class AIProviderError(RuntimeError):
    """Base exception raised when an AI provider returns an error or times out."""


class TransientQuotaError(AIProviderError):
    """Raised on HTTP 429 / RESOURCE_EXHAUSTED / Rate limit / Quota exhaustion."""


class ServiceUnavailableError(AIProviderError):
    """Raised on HTTP 503 / 504 / UNAVAILABLE / model overload / server timeout."""


class NonRetryableAIError(AIProviderError):
    """Raised on non-retryable errors such as bad requests (400), auth failure (401/403), or invalid arguments."""


def safe_error_summary(error: BaseException) -> str:
    """Summarize an AI error without echoing provider text, credentials, or draft content."""
    message = str(error).casefold()
    statuses = {
        "http 400": "HTTP 400 invalid request",
        "http 401": "HTTP 401 authentication failure",
        "http 403": "HTTP 403 permission failure",
        "http 404": "HTTP 404 model or endpoint not found",
        "http 429": "HTTP 429 quota or rate limit",
    }
    http_summary = next((summary for token, summary in statuses.items() if token in message), None)
    if http_summary:
        summary = http_summary
    elif "network timeout" in message or any(token in message for token in ("timeout", "timed out", "deadline")):
        summary = "request timed out"
    elif any(token in message for token in ("http 5", "service failure", "service unavailable")):
        status = next(
            (token.upper() for token in ("http 500", "http 502", "http 503", "http 504") if token in message),
            "HTTP 5xx",
        )
        summary = f"{status} service failure"
    elif "network failure" in message:
        summary = "network connection failed"
    elif "invalid json" in message or "schema validation" in message:
        summary = "invalid JSON or schema response"
    elif any(token in message for token in ("api key", "unauthorized", "authentication", "not configured")):
        summary = "provider authentication or configuration failed"
    elif any(token in message for token in ("quota", "rate limit", "resource_exhausted", "429")):
        summary = "provider quota or rate limit reached"
    else:
        summary = "provider request failed"
    return f"{type(error).__name__}: {summary}"
