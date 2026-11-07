"""
ai/gemini_provider.py
---------------------
Google Gemini implementation of the AIProvider interface.
Reads GEMINI_API_KEY, GEMINI_MODEL, and GEMINI_TIMEOUT_SECONDS from environment.

Security & Reliability policy
------------------------------
- API key is loaded exclusively from project-root .env and never logged, printed, or exposed.
- Error messages are sanitized to strip any accidental credential fragments.
- Configurable timeout suitable for model inference (default: 60.0 seconds).
- Distinct classification and backoff for:
    * HTTP 429: TransientQuotaError and immediate fallback routing
    * HTTP 500/502/503: ServiceUnavailableError and fallback routing
    * HTTP 504 / Timeouts: ServiceUnavailableError and fallback routing
    * HTTP 400/401/403/404: NonRetryableAIError (no retry, fails immediately)
- At most ONE retry when no fallback is configured; with fallback, each model is tried once.
- Optional fallback model support (e.g. gemini-3.1-flash-lite) if primary model is unavailable.
- Configures thinking_budget=0 to prevent unbudgeted thinking loops on flash models.
- Clean AIProviderError raising on failure so callers can gracefully record AI checks
  as NOT_OBSERVED without suppressing deterministic rules.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from dotenv import load_dotenv

from ai.base import (
    AIProvider,
    AIProviderError,
    TransientQuotaError,
    ServiceUnavailableError,
    NonRetryableAIError,
    safe_error_summary,
)

logger = logging.getLogger("brand_rule_lab.ai")

# Load credentials exclusively from the project-root .env file.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)

_DEFAULT_MODEL = "gemini-3.8-flash"
_DEFAULT_FALLBACK_MODEL = "gemini-3.1-flash-lite"
_DEFAULT_TIMEOUT_SECONDS = 60.0

# In-memory registry of models that returned daily quota exhaustion (429) during the current process runtime
_EXHAUSTED_MODELS: set[str] = set()


def clear_exhausted_models() -> None:
    """Clear in-memory exhausted models registry (used for test isolation)."""
    _EXHAUSTED_MODELS.clear()


def _sanitize_error_message(message: str, api_key: str = "") -> str:
    """Strip raw credentials, API key patterns, and query params from error text."""
    sanitized = message
    if api_key and len(api_key) > 4:
        sanitized = sanitized.replace(api_key, "[REDACTED_API_KEY]")
    # Google API key pattern (AIza...)
    sanitized = re.sub(r"AIza[0-9A-Za-z-_]{35}", "[REDACTED_API_KEY]", sanitized)
    # URL query parameter key=...
    sanitized = re.sub(r"(key=)[^&\s'\"]+", r"\1[REDACTED_API_KEY]", sanitized)
    return sanitized


def _classify_gemini_error(exc: Exception, api_key: str = "") -> AIProviderError:
    """
    Classify an exception from Google GenAI into a specific AIProviderError subclass.
    Separates 429, transient server errors, client timeouts, and non-retryable errors.
    Ensures no credentials leak into error messages.
    """
    raw_msg = str(exc)
    clean_msg = _sanitize_error_message(raw_msg, api_key)
    err_str = raw_msg.lower()

    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)

    # 429: Rate Limit / Quota Exhaustion
    if (
        status_code == 429
        or "429" in err_str
        or "resource_exhausted" in err_str
        or "quota" in err_str
        or "rate limit" in err_str
        or "ratelimit" in err_str
    ):
        return TransientQuotaError(f"Gemini API rate limit or quota exceeded (HTTP 429): {clean_msg}")

    # Transient server errors should route to fallback when configured.
    if (
        status_code in (500, 502, 503)
        or any(f"HTTP {code}" in err_str or f"{code} " in err_str for code in (500, 502, 503))
        or "503" in err_str
        or "unavailable" in err_str
        or "overloaded" in err_str
        or "high demand" in err_str
        or "server error" in err_str
    ):
        return ServiceUnavailableError(f"Gemini transient server error: {clean_msg}")

    # 504 / Timeouts / Deadline Exceeded
    if (
        isinstance(exc, TimeoutError)
        or status_code == 504
        or "504" in err_str
        or "deadline exceeded" in err_str
        or "deadline expired" in err_str
        or status_code in (408, 504)
        or "timeout" in err_str
        or "timed out" in err_str
        or "timeoutexception" in err_str
        or "readtimeout" in err_str
        or "connecttimeout" in err_str
    ):
        return ServiceUnavailableError(f"Gemini API request timed out (HTTP 504 / Timeout): {clean_msg}")

    # Authentication / Client / Bad Request / Non-retryable
    return NonRetryableAIError(f"Gemini API error ({type(exc).__name__}): {clean_msg}")


def _is_transient(exc: Exception) -> bool:
    """Return True if the error is considered transient and eligible for at most ONE retry."""
    classified = _classify_gemini_error(exc)
    return isinstance(classified, (TransientQuotaError, ServiceUnavailableError))


class GeminiProvider(AIProvider):
    """Resilient wrapper around google-genai SDK with configurable timeouts, bounded retry, and fallback."""

    provider_name = "Google Gemini"

    def __init__(self, timeout_seconds: float | None = None) -> None:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key or api_key in ("your_gemini_api_key_here", ""):
            raise NonRetryableAIError(
                "GEMINI_API_KEY is not set. "
                "Copy .env.example to .env and add your key."
            )
        self._raw_key = api_key
        self._model_name = os.getenv("GEMINI_MODEL", _DEFAULT_MODEL)
        self._fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", _DEFAULT_FALLBACK_MODEL)
        self._last_model_used: str | None = None
        self._used_fallback: bool = False
        self._fallback_reason: str | None = None
        
        # Timeout configured from parameter, env, or 60.0s default
        self._timeout_seconds = float(
            timeout_seconds if timeout_seconds is not None else os.getenv("GEMINI_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))
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
        - Sets thinking_budget=0 to prevent unbudgeted thinking loops on flash models.
        - Never exposes credentials in logs or errors.
        """
        effective_timeout = float(timeout if timeout is not None else self._timeout_seconds)

        # Configure request parameters with reasonable token limits
        config_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": min(max_output_tokens, 2048),
            "http_options": types.HttpOptions(timeout=int(effective_timeout * 1000)),
        }
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        try:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass

        config = types.GenerateContentConfig(**config_kwargs)

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
                    classified = _classify_gemini_error(exc, self._raw_key)
                    last_classified = classified

                    # Client non-retryable errors abort immediately
                    if isinstance(classified, NonRetryableAIError):
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
