"""
ai/nvidia_provider.py
---------------------
NVIDIA NIM provider using its OpenAI-compatible chat completions endpoint.
"""

from __future__ import annotations

import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai.base import (
    AIProvider,
    NonRetryableAIError,
    ServiceUnavailableError,
    TransientQuotaError,
)
from ai.config import get_config_value


_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
_DEFAULT_TIMEOUT_SECONDS = 40.0
_MAX_TIMEOUT_SECONDS = 120.0


def _http_error_message(status: int) -> str:
    categories = {
        400: ("invalid request", "Check the configured model and request compatibility."),
        401: ("authentication failure", "Verify the NVIDIA_API_KEY secret is current."),
        403: ("permission failure", "Verify the account has access to this model and endpoint."),
        404: ("model or endpoint not found", "Verify NVIDIA_MODEL and the configured endpoint."),
        408: ("network timeout", "Retry after checking network connectivity."),
        429: ("quota or rate limit", "Check NVIDIA quota/rate limits or retry later."),
    }
    if status in categories:
        category, action = categories[status]
        return f"NVIDIA NIM HTTP {status} ({category}): {action}"
    if status >= 500:
        return f"NVIDIA NIM HTTP {status} (service failure): Retry later or check NVIDIA service status."
    return f"NVIDIA NIM HTTP {status} (request rejected): Check provider configuration."


class NvidiaNimProvider(AIProvider):
    """Single-attempt NVIDIA NIM adapter with bounded request timeout."""

    provider_name = "NVIDIA NIM"

    def __init__(self) -> None:
        self._api_key = get_config_value("NVIDIA_API_KEY")
        if not self._api_key:
            raise NonRetryableAIError("NVIDIA_API_KEY is not configured.")

        self._base_url = _BASE_URL
        self._model = get_config_value("NVIDIA_MODEL", _DEFAULT_MODEL)
        if not self._model:
            raise NonRetryableAIError("NVIDIA_MODEL must not be empty.")
        try:
            self._timeout_seconds = float(
                get_config_value("NVIDIA_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))
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
            message = _http_error_message(exc.code)
            if exc.code == 429:
                raise TransientQuotaError(message) from None
            if exc.code in (408, 504) or exc.code >= 500:
                raise ServiceUnavailableError(message) from None
            raise NonRetryableAIError(message) from None
        except (TimeoutError, socket.timeout):
            raise ServiceUnavailableError(
                "NVIDIA NIM network timeout: The request timed out before completion."
            ) from None
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)) or "timed out" in str(exc.reason).lower():
                message = "NVIDIA NIM network timeout: The request timed out before completion."
            else:
                message = "NVIDIA NIM network failure: Check network connectivity and outbound access."
            raise ServiceUnavailableError(message) from None
        except OSError:
            raise ServiceUnavailableError(
                "NVIDIA NIM network failure: Check network connectivity and outbound access."
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
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
            raise ServiceUnavailableError(
                "NVIDIA NIM returned an unusable response (invalid JSON or missing response text)."
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
        """Perform one small authenticated request without any draft content."""
        return self.generate_text(
            "Reply with exactly: NVIDIA_NIM_CONNECTION_OK",
            temperature=0,
            max_output_tokens=16,
        )
