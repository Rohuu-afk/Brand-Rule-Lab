"""
ai/provider_factory.py
----------------------
Returns the configured AI provider instance.

Usage
-----
    from ai.provider_factory import get_provider
    ai = get_provider()
    text = ai.generate_text(prompt)

Adding a new provider
---------------------
1. Create ai/new_provider.py implementing AIProvider.
2. Add an elif branch below.
3. Set AI_PROVIDER=new_provider in .env.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path

from ai.base import AIProvider, AIProviderError, NonRetryableAIError
from dotenv import load_dotenv

# Load credentials exclusively from the project-root .env file.
# override=False means already-set OS environment variables are never clobbered.
# .env.example is never read.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)
logger = logging.getLogger("brand_rule_lab.ai.provider_factory")


class NvidiaPrimaryWithGeminiFallback(AIProvider):
    """Use NVIDIA NIM first, then stick to Gemini after a primary failure."""

    def __init__(self) -> None:
        self._primary: AIProvider | None = None
        self._fallback: AIProvider | None = None
        self._fallback_active = False
        self._last_provider_used: str | None = None
        self._fallback_reason: str | None = None

    @property
    def last_model_used(self) -> str | None:
        provider = self._fallback if self._fallback_active else self._primary
        return provider.last_model_used if provider else None

    @property
    def last_response_metadata(self) -> dict | None:
        provider = self._fallback if self._fallback_active else self._primary
        return provider.last_response_metadata if provider else None

    @property
    def last_provider_used(self) -> str | None:
        return self._last_provider_used

    @property
    def used_fallback(self) -> bool:
        return self._fallback_active

    @property
    def fallback_reason(self) -> str | None:
        return self._fallback_reason

    def _record_success(self, name: str) -> None:
        self._last_provider_used = name

    def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.2,
        max_output_tokens: int = 1024,
        timeout: float | None = None,
        json_mode: bool = False,
    ) -> str:
        if not self._fallback_active:
            try:
                if self._primary is None:
                    from ai.nvidia_provider import NvidiaNimProvider
                    self._primary = NvidiaNimProvider()
                response = self._primary.generate_text(
                    prompt,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    json_mode=json_mode,
                    **({"timeout": timeout} if timeout is not None else {}),
                )
            except AIProviderError as exc:
                self._fallback_active = True
                self._fallback_reason = (
                    "NVIDIA NIM was unavailable; switched to Google Gemini fallback. "
                    f"Failure type: {type(exc).__name__}."
                )
                logger.warning("%s", self._fallback_reason)
            else:
                self._record_success("NVIDIA NIM")
                return response

        if self._fallback is None:
            from ai.gemini_provider import GeminiProvider
            self._fallback = GeminiProvider()
        response = self._fallback.generate_text(
            prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            json_mode=json_mode,
            **({"timeout": timeout} if timeout is not None else {}),
        )
        self._record_success("Google Gemini")
        return response


def get_provider() -> AIProvider:
    """
    Return NVIDIA NIM as the primary provider with Gemini fallback.
    AI_PROVIDER=gemini remains available as an explicit legacy override.
    """
    provider_name = os.getenv("AI_PROVIDER", "nvidia").strip().lower()

    if provider_name in ("nvidia", "nvidia_nim"):
        return NvidiaPrimaryWithGeminiFallback()
    if provider_name == "gemini":
        from ai.gemini_provider import GeminiProvider
        return GeminiProvider()

    raise NonRetryableAIError(
        f"Unknown AI_PROVIDER '{provider_name}'. Use 'nvidia' or 'gemini'."
    )
