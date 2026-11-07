"""
ai/gemini_provider.py
---------------------
Google Gemini implementation of the AIProvider interface.
Reads configuration from environment variables with Streamlit secrets as fallback.

Security & Reliability policy
------------------------------
- API keys are never logged, printed, or exposed.
- Configurable timeout suitable for model inference (default: 60.0 seconds).
- Distinct classification and backoff for:
    * HTTP 429: TransientQuotaError and immediate fallback routing
    * HTTP 500/502/503: ServiceUnavailableError and fallback routing
    * HTTP 504 / Timeouts: ServiceUnavailableError and fallback routing
    * HTTP 400/401/403: NonRetryableAIError (fails immediately)
    * HTTP 404: tries the configured fallback model before failing
- At most ONE retry when no fallback is configured; with fallback, each model is tried once.
- Optional fallback model support (e.g. gemini-3.1-flash-lite) if primary model is unavailable.
- Uses the supported Gemini 3 thinking_level configuration.
- Clean AIProviderError raising on failure so callers can gracefully record AI checks
  as NOT_OBSERVED without suppressing deterministic rules.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from google import genai
from google.genai import types
from ai.base import (
    AIProvider,
    AIProviderError,
    TransientQuotaError,
    ServiceUnavailableError,
    NonRetryableAIError,
    safe_error_summary,
)
from ai.config import get_config_value

logger = logging.getLogger("brand_rule_lab.ai")

_DEFAULT_MODEL = "gemini-3.8-flash"
_DEFAULT_FALLBACK_MODEL = "gemini-3.1-flash-lite"
_DEFAULT_TIMEOUT_SECONDS = 60.0

# In-memory registry of models that returned daily quota exhaustion (429) during the current process runtime
_EXHAUSTED_MODELS: set[str] = set()


def clear_exhausted_models() -> None:
    """Clear in-memory exhausted models registry (used for test isolation)."""
    _EXHAUSTED_MODELS.clear()


def _classify_gemini_error(exc: Exception) -> AIProviderError:
    """
    Classify an exception from Google GenAI into a specific AIProviderError subclass.
    Separates 429, transient server errors, client timeouts, and non-retryable errors.
    Ensures no credentials leak into error messages.
    """
    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int):
        match = re.search(r"\b(?:HTTP\s*)?([45]\d\d)\b", str(exc), re.IGNORECASE)
        status_code = int(match.group(1)) if match else None
    err_str = str(exc).lower()

    # 429: Rate Limit / Quota Exhaustion
    if (status_code == 429
        or "resource_exhausted" in err_str
        or "quota" in err_str
        or "rate limit" in err_str
        or "ratelimit" in err_str
    ):
        return TransientQuotaError("Gemini HTTP 429 (quota or rate limit). Check quota or retry later.")

    # Transient server errors should route to fallback when configured.
    if ((status_code is not None and status_code >= 500)
        or "unavailable" in err_str
        or "overloaded" in err_str
        or "high demand" in err_str
        or "server error" in err_str
    ):
        status = f" HTTP {status_code}" if status_code else ""
        return ServiceUnavailableError(f"Gemini{status} service failure. Retry later or check Google service status.")

    # 504 / Timeouts / Deadline Exceeded
    if (
        isinstance(exc, TimeoutError)
        or status_code == 504
        or "deadline exceeded" in err_str
        or "deadline expired" in err_str
        or status_code in (408, 504)
        or "timeout" in err_str
        or "timed out" in err_str
        or "timeoutexception" in err_str
        or "readtimeout" in err_str
        or "connecttimeout" in err_str
    ):
        return ServiceUnavailableError(
            "Gemini network request timed out. Retry after checking network connectivity."
        )

    safe_categories = {
        400: "invalid request",
        401: "authentication failure",
        403: "permission failure",
        404: "model or endpoint not found",
    }
    if status_code in safe_categories:
        return NonRetryableAIError(
            f"Gemini HTTP {status_code} ({safe_categories[status_code]}). Check provider configuration."
        )
    return NonRetryableAIError(f"Gemini API request failed ({type(exc).__name__}).")


class GeminiProvider(AIProvider):
    """Resilient wrapper around google-genai SDK with configurable timeouts, bounded retry, and fallback."""

    provider_name = "Google Gemini"

    def __init__(self, timeout_seconds: float | None = None) -> None:
        api_key = get_config_value("GEMINI_API_KEY")
        if not api_key or api_key in ("your_gemini_api_key_here", ""):
            raise NonRetryableAIError("GEMINI_API_KEY is not configured.")
        self._model_name = get_config_value("GEMINI_MODEL", _DEFAULT_MODEL)
        self._fallback_model = get_config_value("GEMINI_FALLBACK_MODEL", _DEFAULT_FALLBACK_MODEL)
        self._last_model_used: str | None = None
        self._used_fallback: bool = False
        self._fallback_reason: str | None = None
        
        # Timeout configured from parameter, env, or 60.0s default
        self._timeout_seconds = float(
            timeout_seconds if timeout_seconds is not None else get_config_value(
                "GEMINI_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS)
            )
        )
        self._http_options = types.HttpOptions(timeout=int(self._timeout_seconds * 1000))
        self._client = genai.Client(api_key=api_key, http_options=self._http_options)

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def last_model_used(self) -> str | None:
        return self._last_model_used

    @property
    def used_fallback(self) -> bool:
        return self._used_fallback

    @property
    def fallback_reason(self) -> str | None:
        return self._fallback_reason

    def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_output_tokens: int = 1024,
        timeout: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """
        Call the Gemini API and return the response text.

        Resilience policy:
        - Configurable timeout suitable for model inference (default 60s).
        - Routes transient errors to fallback when configured.
        - Retries at most once only when fallback is unavailable.
        - Seamless fallback to an active model if primary model is experiencing high demand.
        - Uses Gemini 3's supported minimal thinking level.
        - Never exposes credentials in logs or errors.
        """
        effective_timeout = float(timeout if timeout is not None else self._timeout_seconds)

        self._last_model_used = None
        self._used_fallback = False
        self._fallback_reason = None

        # Build candidate models: primary first, followed by fallback if configured
        models_to_try: list[str] = []
        
        # If primary model is already known to be exhausted in this session, route directly to fallback
        if self._model_name in _EXHAUSTED_MODELS and self._fallback_model and self._fallback_model != self._model_name:
            logger.info(
                "Primary model %s is marked exhausted for this session. Directly using fallback %s.",
                self._model_name,
                self._fallback_model,
            )
            models_to_try = [self._fallback_model]
            self._used_fallback = True
            self._fallback_reason = (
                f"Primary model '{self._model_name}' daily quota exhausted. "
                f"Auto-routed to verified fallback '{self._fallback_model}'."
            )
        else:
            models_to_try = [self._model_name]
            if self._fallback_model and self._fallback_model != self._model_name:
                models_to_try.append(self._fallback_model)

        last_classified: AIProviderError | None = None

        for model_idx, target_model in enumerate(models_to_try):
            is_fallback = (target_model != self._model_name)
            if is_fallback:
                self._used_fallback = True
                if not self._fallback_reason:
                    self._fallback_reason = (
                        f"Primary model '{self._model_name}' unavailable. "
                        f"Routed to fallback model '{target_model}'."
                    )
                logger.warning(
                    "Primary model %s unavailable. Attempting fallback model %s...",
                    self._model_name,
                    target_model,
                )

            # When a fallback exists, switch to it after one primary failure rather
            # than spending a second full request timeout on the primary.
            max_attempts = 1 if is_fallback or len(models_to_try) > 1 else 2

            for attempt in range(max_attempts):
                try:
                    config_kwargs: dict[str, Any] = {
                        "max_output_tokens": min(max_output_tokens, 2048),
                        "http_options": types.HttpOptions(timeout=int(effective_timeout * 1000)),
                    }
                    if target_model.lower().startswith("gemini-3"):
                        config_kwargs["thinking_config"] = types.ThinkingConfig(
                            thinking_level=types.ThinkingLevel.LOW
                        )
                    else:
                        config_kwargs["temperature"] = temperature
                    if json_mode:
                        config_kwargs["response_mime_type"] = "application/json"
                    config = types.GenerateContentConfig(**config_kwargs)

                    logger.info(
                        "Executing Gemini request (model=%s, timeout=%.1fs, attempt=%d/%d)...",
                        target_model,
                        effective_timeout,
                        attempt + 1,
                        max_attempts,
                    )
                    response = self._client.models.generate_content(
                        model=target_model,
                        contents=prompt,
                        config=config,
                    )

                    # Extract text content safely across SDK candidate representations
                    text: str | None = None
                    if response and hasattr(response, "text") and response.text is not None:
                        text = response.text
                    elif response and getattr(response, "candidates", None) and response.candidates:
                        cand = response.candidates[0]
                        if getattr(cand, "content", None) and getattr(cand.content, "parts", None):
                            part_texts = [getattr(p, "text", "") for p in cand.content.parts if getattr(p, "text", None)]
                            if part_texts:
                                text = "".join(part_texts)

                    if text is not None:
                        self._last_model_used = target_model
                        logger.info("Gemini request succeeded on %s (attempt=%d).", target_model, attempt + 1)
                        return text
                    return ""

                except Exception as exc:
                    classified = _classify_gemini_error(exc)
                    last_classified = classified

                    # Client non-retryable errors abort immediately
                    if isinstance(classified, NonRetryableAIError):
                        status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                        if not isinstance(status_code, int):
                            match = re.search(r"\b(?:HTTP\s*)?(404)\b", str(exc), re.IGNORECASE)
                            status_code = int(match.group(1)) if match else None
                        if status_code == 404 and not is_fallback and len(models_to_try) > 1:
                            self._used_fallback = True
                            self._fallback_reason = (
                                f"Primary model '{target_model}' was not found. "
                                f"Routed to fallback model '{models_to_try[1]}'."
                            )
                            break
                        logger.error(
                            "Non-retryable Gemini error (%s). Aborting.",
                            safe_error_summary(classified),
                        )
                        raise classified from None

                    # On daily quota exhaustion (429), register model in session exhausted set
                    # and switch immediately to fallback model if available
                    if isinstance(classified, TransientQuotaError):
                        _EXHAUSTED_MODELS.add(target_model)
                        if not is_fallback and len(models_to_try) > 1:
                            logger.warning(
                                "Primary model %s quota exhausted. Registered in session exhausted set. Switching to fallback...",
                                target_model,
                            )
                            self._used_fallback = True
                            self._fallback_reason = (
                                f"Primary model '{target_model}' daily quota exhausted (HTTP 429). "
                                f"Switched to fallback model."
                            )
                            break

                    if attempt + 1 < max_attempts:
                        backoff = 1.5 if "timeout" in str(classified).lower() else 2.0
                        logger.warning(
                            "Gemini transient failure on %s attempt %d (%s). Retrying in %.1fs...",
                            target_model,
                            attempt + 1,
                            safe_error_summary(classified),
                            backoff,
                        )
                        time.sleep(backoff)
                        continue

                    # Exhaust this model's bounded attempts, then try the configured fallback.
                    logger.warning(
                        "Gemini request on %s failed definitively (%s).",
                        target_model,
                        safe_error_summary(classified),
                    )
                    break

        if last_classified:
            raise last_classified from None
        raise ServiceUnavailableError("Gemini API call failed after bounded model attempts.")

    def test_connection(self) -> str:
        """Perform a minimal authenticated request without any draft content."""
        return self.generate_text(
            "Reply with exactly: GEMINI_CONNECTION_OK",
            temperature=0,
            max_output_tokens=16,
        )
