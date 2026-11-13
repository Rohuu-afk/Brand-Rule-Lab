"""Minimal Resend email delivery for account and invitation links."""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

logger = logging.getLogger("brand_rule_lab.email")


class _RejectRedirectHandler(HTTPRedirectHandler):
    """Prevent a mail-provider redirect from receiving the Resend API key."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_without_redirect(request: Request, timeout: float):
    opener = build_opener(_RejectRedirectHandler())
    return opener.open(request, timeout=timeout)


def _setting(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        value = str(st.secrets.get(name, "")).strip()
        if not value:
            nested_keys = {
                "RESEND_API_KEY": ("resend", "api_key"),
                "EMAIL_FROM": ("resend", "from_email"),
            }
            nested = nested_keys.get(name)
            if nested:
                section = st.secrets.get(nested[0], {})
                value = str(section.get(nested[1], "")).strip()
    except Exception:
        value = ""
    return value or default


def send_email(to: str, subject: str, text: str) -> bool:
    """Send a plain-text message via Resend; never log addresses or message tokens."""
    api_key = _setting("RESEND_API_KEY")
    sender = _setting("EMAIL_FROM", "onboarding@resend.dev")
    if not api_key or not sender:
        return False
    body = json.dumps({"from": sender, "to": [to], "subject": subject, "text": text}).encode("utf-8")
    request = Request(
        "https://api.resend.com/emails",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Brand-Rule-Lab/1.0",
        },
        method="POST",
    )
    try:
        with _open_without_redirect(request, timeout=10) as response:
            delivered = 200 <= response.status < 300
        if not delivered:
            logger.warning("Email provider returned non-success status")
        return delivered
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.warning("Email delivery failed (%s)", type(exc).__name__)
        return False


def application_url(path: str = "") -> str:
    base = _setting("PUBLIC_APP_URL")
    if not base:
        try:
            import streamlit as st

            redirect_uri = str(
                st.secrets.get("auth", {}).get("redirect_uri", "")
            ).strip()
            parsed = urlsplit(redirect_uri)
            if (
                parsed.scheme in {"http", "https"}
                and parsed.netloc
                and parsed.path.rstrip("/").endswith("/oauth2callback")
            ):
                base = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        except Exception:
            base = ""
    base = (base or "http://localhost:8501").rstrip("/")
    return f"{base}/{path.lstrip('/')}" if path else base
