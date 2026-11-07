from __future__ import annotations

import io
import json
from urllib.error import HTTPError
from unittest.mock import MagicMock

import pytest


def test_environment_configuration_precedes_streamlit_secrets(monkeypatch):
    from ai.config import get_config_value

    monkeypatch.setenv("NVIDIA_API_KEY", "environment-key")
    monkeypatch.setattr("ai.config._read_streamlit_secret", lambda name, default="": "secret-key")
    assert get_config_value("NVIDIA_API_KEY") == "environment-key"


def test_streamlit_root_secret_is_used_when_environment_is_empty(monkeypatch):
    from ai.config import get_config_value
    from ai.nvidia_provider import NvidiaNimProvider

    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setattr(
        "ai.config._read_streamlit_secret",
        lambda name, default="": "streamlit-secret" if name == "NVIDIA_API_KEY" else default,
    )
    assert get_config_value("NVIDIA_API_KEY") == "streamlit-secret"
    assert NvidiaNimProvider()._api_key == "streamlit-secret"


def test_gemini_provider_reads_streamlit_root_secret(monkeypatch):
    from ai.gemini_provider import GeminiProvider

    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr(
        "ai.config._read_streamlit_secret",
        lambda name, default="": "streamlit-gemini-secret" if name == "GEMINI_API_KEY" else default,
    )
    monkeypatch.setattr("ai.gemini_provider.genai.Client", lambda **kwargs: MagicMock())
    assert GeminiProvider()._client is not None


def test_missing_environment_and_streamlit_credentials_are_reported(monkeypatch):
    from ai.base import NonRetryableAIError
    from ai.nvidia_provider import NvidiaNimProvider

    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr("ai.config._read_streamlit_secret", lambda name, default="": default)
    with pytest.raises(NonRetryableAIError, match="NVIDIA_API_KEY is not configured"):
        NvidiaNimProvider()


def test_nvidia_request_uses_bearer_credential_without_exposing_it(monkeypatch):
    from ai.nvidia_provider import NvidiaNimProvider

    api_key = "mock-nvidia-key-never-log"
    monkeypatch.setenv("NVIDIA_API_KEY", api_key)
    captured: dict[str, str] = {}

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        return io.BytesIO(
            json.dumps({"choices": [{"message": {"content": "NVIDIA_NIM_CONNECTION_OK"}}]}).encode()
        )

    monkeypatch.setattr("ai.nvidia_provider.urlopen", fake_urlopen)
    provider = NvidiaNimProvider()
    assert provider.test_connection() == "NVIDIA_NIM_CONNECTION_OK"
    assert captured["authorization"] == f"Bearer {api_key}"
    assert api_key not in str(provider.last_response_metadata)


@pytest.mark.parametrize(
    ("status", "error_type", "category"),
    [
        (400, "NonRetryableAIError", "invalid request"),
        (401, "NonRetryableAIError", "authentication failure"),
        (403, "NonRetryableAIError", "permission failure"),
        (404, "NonRetryableAIError", "model or endpoint not found"),
        (429, "TransientQuotaError", "quota or rate limit"),
        (500, "ServiceUnavailableError", "service failure"),
        (503, "ServiceUnavailableError", "service failure"),
    ],
)
def test_nvidia_http_categories_are_sanitized(monkeypatch, status, error_type, category):
    from ai import base
    from ai.nvidia_provider import NvidiaNimProvider

    key = "do-not-disclose-nvidia-key"
    confidential_text = "private draft copy"
    monkeypatch.setenv("NVIDIA_API_KEY", key)

    def reject_request(request, timeout):
        raise HTTPError(
            request.full_url,
            status,
            "mock failure",
            {},
            io.BytesIO(f'{{"error":{{"message":"{key} {confidential_text}"}}}}'.encode()),
        )

    monkeypatch.setattr("ai.nvidia_provider.urlopen", reject_request)
    with pytest.raises(getattr(base, error_type)) as error:
        NvidiaNimProvider().generate_text(confidential_text)
    message = str(error.value)
    assert f"HTTP {status}" in message
    assert category in message
    assert key not in message
    assert confidential_text not in message


def test_nvidia_network_timeout_has_distinct_safe_category(monkeypatch):
    from ai.base import ServiceUnavailableError
    from ai.nvidia_provider import NvidiaNimProvider

    monkeypatch.setenv("NVIDIA_API_KEY", "mock-nvidia-key")
    monkeypatch.setattr(
        "ai.nvidia_provider.urlopen",
        lambda request, timeout: (_ for _ in ()).throw(TimeoutError()),
    )
    with pytest.raises(ServiceUnavailableError, match="network timeout"):
        NvidiaNimProvider().test_connection()


def test_gemini_3_uses_supported_thinking_config_and_json_mode(monkeypatch):
    from ai.gemini_provider import GeminiProvider
    from google.genai import types

    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
    provider = GeminiProvider()
    response = MagicMock()
    response.text = '{"claims": []}'
    provider._client = MagicMock()
    provider._client.models.generate_content.return_value = response

    assert provider.generate_text("safe test prompt", json_mode=True) == '{"claims": []}'
    config = provider._client.models.generate_content.call_args.kwargs["config"]
    assert config.thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    assert config.thinking_config.thinking_budget is None
    assert config.temperature is None
    assert config.response_mime_type == "application/json"
    assert provider.last_model_used == "gemini-3.8-flash"


def test_gemini_primary_404_routes_to_configured_fallback(monkeypatch):
    from ai.gemini_provider import GeminiProvider, clear_exhausted_models
    from google.genai import types

    clear_exhausted_models()
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
    provider = GeminiProvider()
    response = MagicMock()
    response.text = "fallback response"
    provider._client = MagicMock()
    provider._client.models.generate_content.side_effect = [
        Exception("404 NOT_FOUND"),
        response,
    ]

    assert provider.generate_text("safe prompt") == "fallback response"
    assert [
        call.kwargs["model"]
        for call in provider._client.models.generate_content.call_args_list
    ] == ["gemini-3.8-flash", "gemini-3.1-flash-lite"]
    fallback_config = provider._client.models.generate_content.call_args_list[1].kwargs["config"]
    assert fallback_config.thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    assert fallback_config.temperature is None
    assert provider.used_fallback is True
    assert provider.last_model_used == "gemini-3.1-flash-lite"


def test_gemini_connection_check_uses_fixed_prompt_and_reports_model(monkeypatch):
    from ai.gemini_provider import GeminiProvider

    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    provider = GeminiProvider()
    response = MagicMock()
    response.text = "GEMINI_CONNECTION_OK"
    provider._client = MagicMock()
    provider._client.models.generate_content.return_value = response

    assert provider.test_connection() == "GEMINI_CONNECTION_OK"
    call = provider._client.models.generate_content.call_args
    assert call.kwargs["contents"] == "Reply with exactly: GEMINI_CONNECTION_OK"
    assert provider.last_model_used == "gemini-3.8-flash"


def test_provider_diagnostics_expose_only_credential_presence(monkeypatch):
    from ai.provider_factory import get_provider_diagnostics

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-secret")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setattr(
        "ai.config._read_streamlit_secret",
        lambda name, default="": "mock-nvidia-secret" if name == "NVIDIA_API_KEY" else default,
    )
    diagnostics = get_provider_diagnostics()
    serialized = json.dumps(diagnostics)
    assert diagnostics["selected_provider"] == "Google Gemini"
    assert diagnostics["providers"]["Google Gemini"]["credential_configured"] is True
    assert diagnostics["providers"]["NVIDIA NIM"]["credential_configured"] is True
    assert "mock-gemini-secret" not in serialized
    assert "mock-nvidia-secret" not in serialized


def test_nvidia_success_does_not_invoke_gemini_fallback(monkeypatch):
    from ai import gemini_provider
    from ai.provider_factory import NvidiaPrimaryWithGeminiFallback

    monkeypatch.setenv("NVIDIA_API_KEY", "mock-nvidia-key")
    monkeypatch.setenv("NVIDIA_MODEL", "mock/nim")

    def fake_urlopen(request, timeout):
        return io.BytesIO(
            json.dumps({"choices": [{"message": {"content": "NVIDIA_OK"}}]}).encode()
        )

    class UnexpectedGemini:
        def __init__(self):
            raise AssertionError("Gemini must not run after NVIDIA succeeds")

    monkeypatch.setattr("ai.nvidia_provider.urlopen", fake_urlopen)
    monkeypatch.setattr(gemini_provider, "GeminiProvider", UnexpectedGemini)
    provider = NvidiaPrimaryWithGeminiFallback()

    assert provider.generate_text("safe prompt") == "NVIDIA_OK"
    assert provider.last_provider_used == "NVIDIA NIM"
    assert provider.used_fallback is False
    assert provider.last_model_used == "mock/nim"


def test_gemini_can_be_selected_directly(monkeypatch):
    from ai import gemini_provider
    from ai.provider_factory import get_provider

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "")

    class DirectGemini:
        pass

    monkeypatch.setattr(gemini_provider, "GeminiProvider", DirectGemini)
    assert isinstance(get_provider(), DirectGemini)


@pytest.fixture
def isolated_review_database(monkeypatch, tmp_path):
    from db.connection import apply_schema
    from db.seed import seed

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "provider_review.db"))
    monkeypatch.setenv("UPLOADS_PATH", str(tmp_path / "uploads"))
    apply_schema()
    seed()


def test_nvidia_and_gemini_failure_never_marks_ai_checks_as_pass(
    monkeypatch, isolated_review_database
):
    from ai import gemini_provider, nvidia_provider
    from ai.base import NonRetryableAIError
    from ai.provider_factory import NvidiaPrimaryWithGeminiFallback
    from db.connection import get_connection
    from models.enums import FindingResult
    from services.review_service import run_review

    class FailedNvidia:
        def generate_text(self, prompt, **kwargs):
            raise NonRetryableAIError("NVIDIA NIM HTTP 401 authentication failure.")

    class FailedGemini:
        def generate_text(self, prompt, **kwargs):
            raise NonRetryableAIError("Gemini HTTP 401 authentication failure.")

    monkeypatch.setattr(nvidia_provider, "NvidiaNimProvider", FailedNvidia)
    monkeypatch.setattr(gemini_provider, "GeminiProvider", FailedGemini)
    with get_connection() as conn:
        draft_id = conn.execute(
            "INSERT INTO drafts (title, product_id, market) VALUES ('Provider outage', 4, 'IN')"
        ).lastrowid

    result = run_review(
        draft_id,
        "Say goodbye to acne forever!",
        "paste",
        {},
        4,
        "IN",
        "website",
        None,
        NvidiaPrimaryWithGeminiFallback(),
        ai_consent=True,
    )

    assert result["ai_status"] == "unavailable"
    assert result["ai_operation_status"]["claim_extraction"] == FindingResult.NOT_OBSERVED.value
    assert result["ai_operation_status"]["tone_assessment"] == FindingResult.NOT_OBSERVED.value
    assert not any(
        finding["result"] == FindingResult.PASS.value for finding in result["findings"]
    )
