from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from services.email_service import _setting, application_url, send_email


def test_resend_email_request_uses_configured_sender_and_secret(monkeypatch):
    class _Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    requests = []

    def fake_open_without_redirect(request, timeout):
        requests.append((request, timeout))
        return _Response()

    monkeypatch.setenv("RESEND_API_KEY", "test-secret")
    monkeypatch.setenv("EMAIL_FROM", "Brand Rule Lab <onboarding@resend.dev>")
    monkeypatch.setattr(
        "services.email_service._open_without_redirect", fake_open_without_redirect
    )

    assert send_email("recipient@example.com", "Verify", "One-time link") is True
    request, timeout = requests[0]
    payload = json.loads(request.data)
    assert request.full_url == "https://api.resend.com/emails"
    assert request.get_header("Authorization") == "Bearer test-secret"
    assert payload["to"] == ["recipient@example.com"]
    assert payload["from"] == "Brand Rule Lab <onboarding@resend.dev>"
    assert timeout == 10


def test_email_service_fails_closed_without_resend_configuration(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert send_email("recipient@example.com", "Verify", "Token") is False


def test_email_service_reads_nested_streamlit_resend_secrets(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "streamlit",
        SimpleNamespace(
            secrets={
                "resend": {
                    "api_key": "nested-test-secret",
                    "from_email": "Brand Rule Lab <team@example.test>",
                }
            }
        ),
    )

    assert _setting("RESEND_API_KEY") == "nested-test-secret"
    assert _setting("EMAIL_FROM") == "Brand Rule Lab <team@example.test>"


def test_application_url_uses_public_deployment_configuration(monkeypatch):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://brand.example.com/")
    assert application_url("?verify=abc") == "https://brand.example.com/?verify=abc"


def test_application_url_uses_configured_oidc_callback_when_public_url_is_unset(monkeypatch):
    monkeypatch.delenv("PUBLIC_APP_URL", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "streamlit",
        SimpleNamespace(
            secrets={
                "auth": {
                    "redirect_uri": "http://localhost:8502/oauth2callback",
                }
            }
        ),
    )

    assert application_url("?verify=abc") == "http://localhost:8502/?verify=abc"
