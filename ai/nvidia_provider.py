"""
ai/nvidia_provider.py
---------------------
NVIDIA NIM provider using its OpenAI-compatible chat completions endpoint.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from ai.base import (
    AIProvider,
    AIProviderError,
    NonRetryableAIError,
    ServiceUnavailableError,
    TransientQuotaError,
)


_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)

_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
_DEFAULT_TIMEOUT_SECONDS = 40.0
_MAX_TIMEOUT_SECONDS = 120.0


def _sanitize(message: str, api_key: str = "") -> str:
    if api_key:
        message = message.replace(api_key, "[REDACTED_API_KEY]")
    message = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED_API_KEY]", message)
    message = re.sub(r"nvapi-[A-Za-z0-9_-]+", "[REDACTED_API_KEY]", message)
    return message


def _response_detail(body: bytes, api_key: str) -> str:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "The service returned an unreadable error response."
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if isinstance(error, dict):
        detail = (
            error.get("message")
            or error.get("detail")
            or error.get("type")
            or (payload.get("detail") if isinstance(payload, dict) else "")
            or (payload.get("title") if isinstance(payload, dict) else "")
        )
    elif isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("title") or str(error)
    else:
        detail = str(error)
    return _sanitize(str(detail)[:500], api_key) or "No additional error details were provided."


class NvidiaNimProvider(AIProvider):
    """Single-attempt NVIDIA NIM adapter with bounded request timeout."""

    provider_name = "NVIDIA NIM"

    def __init__(self) -> None:
        self._api_key = os.getenv("NVIDIA_API_KEY", "").strip()
        if not self._api_key:
            raise NonRetryableAIError("NVIDIA_API_KEY is not configured.")

        self._base_url = _BASE_URL
        self._model = os.getenv("NVIDIA_MODEL", _DEFAULT_MODEL).strip()
        if not self._model:
            raise NonRetryableAIError("NVIDIA_MODEL must not be empty.")
        try:
            self._timeout_seconds = float(
                os.getenv("NVIDIA_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))
            )
        except ValueError as exc:
            raise NonRetryableAIError("NVIDIA_TIMEOUT_SECONDS must be numeric.") from exc
        if not 0 < self._timeout_seconds <= _MAX_TIMEOUT_SECONDS:
            raise NonRetryableAIError(
                f"NVIDIA_TIMEOUT_SECONDS must be between 0 and {_MAX_TIMEOUT_SECONDS:g}."
            )
        self._last_model_used: str | None = None
        self._last_response_metadata: dict[str, Any] | None = None

    @property
    def last_model_used(self) -> str | None:
        return self._last_model_used

    @property
    def last_response_metadata(self) -> dict[str, Any] | None:
        return self._last_response_metadata

    def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_output_tokens: int = 1024,
        timeout: float | None = None,
        json_mode: bool = False,
    ) -> str:
        self._last_model_used = None
        self._last_response_metadata = None
        effective_timeout = self._timeout_seconds if timeout is None else float(timeout)
        if not 0 < effective_timeout <= _MAX_TIMEOUT_SECONDS:
            raise NonRetryableAIError(
                f"NVIDIA request timeout must be between 0 and {_MAX_TIMEOUT_SECONDS:g}."
            )

        request_payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": min(max_output_tokens, 4096),
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if json_mode:
            request_payload["response_format"] = {"type": "json_object"}
        payload = json.dumps(request_payload).encode("utf-8")
        request = Request(
            f"{self._base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=effective_timeout) as response:
                body = response.read()
        except HTTPError as exc:
            status = exc.code
            detail = _response_detail(exc.read(), self._api_key)
            message = f"NVIDIA NIM request failed with HTTP {status}: {detail}"
            if status == 429:
                raise TransientQuotaError(_sanitize(message, self._api_key)) from None
            if status in (408, 504) or status >= 500:
                raise ServiceUnavailableError(_sanitize(message, self._api_key)) from None
            raise NonRetryableAIError(_sanitize(message, self._api_key)) from None
        except (TimeoutError, URLError, OSError) as exc:
            detail = _sanitize(str(getattr(exc, "reason", exc)), self._api_key)
            raise ServiceUnavailableError(
                f"NVIDIA NIM request timed out or could not connect: {detail}"
            ) from None

        try:
            result: Any = json.loads(body.decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if not isinstance(content, str) or not content.strip():
                raise ValueError("The response contained no text.")
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ServiceUnavailableError(
                f"NVIDIA NIM returned an unusable response: {_sanitize(str(exc), self._api_key)}"
            ) from None

        choice = result["choices"][0]
        usage = result.get("usage")
        self._last_model_used = str(result.get("model") or self._model)
        self._last_response_metadata = {
            "finish_reason": choice.get("finish_reason"),
            "usage": usage if isinstance(usage, dict) else None,
            "response_length_chars": len(content),
        }
        return content

    def test_connection(self) -> str:
        """Perform one small authenticated request and return the model response."""
        return self.generate_text(
            "Reply with exactly: NVIDIA_NIM_CONNECTION_OK",
            temperature=0,
            max_output_tokens=16,
        )
