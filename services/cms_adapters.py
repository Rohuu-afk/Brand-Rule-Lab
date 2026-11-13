"""Draft-only CMS adapter contract with a deterministic local mock."""

from __future__ import annotations

import hashlib
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from ai.config import get_config_value

class CMSConfigurationError(ValueError):
    """Raised when an adapter is not configured for the requested operation."""


class CMSAdapter(Protocol):
    provider: str

    def validate_payload(self, payload: dict) -> None: ...

    def create_draft(self, payload: dict) -> dict: ...


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward draft content or CMS credentials to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_without_redirect(request: urllib.request.Request, timeout: float):
    opener = urllib.request.build_opener(_RejectRedirectHandler())
    return opener.open(request, timeout=timeout)


def _post_json(
    provider: str,
    url: str,
    payload: dict,
    headers: dict[str, str],
    timeout: float,
) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with _open_without_redirect(request, timeout=timeout) as response:
            if response.status not in (200, 201):
                raise CMSConfigurationError(
                    f"{provider} returned unexpected HTTP status {response.status}."
                )
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CMSConfigurationError(
            f"{provider} draft request failed with HTTP {exc.code}."
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CMSConfigurationError(
            f"{provider} draft request failed ({type(exc).__name__})."
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError) as exc:
        raise CMSConfigurationError(
            f"{provider} returned an invalid response ({type(exc).__name__})."
        ) from None
    if not isinstance(data, dict):
        raise CMSConfigurationError(f"{provider} returned an invalid response.")
    return data


def _timeout_from_config(name: str, default: str = "15") -> float:
    try:
        timeout = float(get_config_value(name, default))
    except ValueError:
        raise CMSConfigurationError(f"{name} must be a number.") from None
    if not 1 <= timeout <= 60:
        raise CMSConfigurationError(f"{name} must be between 1 and 60.")
    return timeout


def _validate_text_draft_payload(provider: str, payload: dict) -> None:
    MockCMSAdapter(provider).validate_payload(payload)
    if len(str(payload["title"]).strip()) > 200:
        raise ValueError("CMS draft title must be 200 characters or fewer.")
    if len(str(payload["content"]).encode("utf-8")) > 500_000:
        raise ValueError("CMS draft content exceeds the 500 KB limit.")


@dataclass(frozen=True)
class MockCMSAdapter:
    """Safe local adapter used for demos and tests; it never publishes content."""

    provider: str

    def validate_payload(self, payload: dict) -> None:
        if not isinstance(payload, dict) or not str(payload.get("title", "")).strip():
            raise ValueError("CMS draft title is required.")
        if not str(payload.get("content", "")).strip():
            raise ValueError("CMS draft content is required.")

    def create_draft(self, payload: dict) -> dict:
        self.validate_payload(payload)
        fingerprint = hashlib.sha256(
            f"{self.provider}|{payload['title']}|{payload['content']}".encode("utf-8")
        ).hexdigest()[:16]
        return {
            "provider": self.provider,
            "mode": "mock",
            "draft_id": f"{self.provider.lower()}-draft-{fingerprint}",
            "status": "DRAFT",
            "published": False,
        }


@dataclass(frozen=True)
class ShopifyDraftAdapter:
    """Create an unpublished Shopify product through the Admin GraphQL API."""

    store_domain: str
    access_token: str
    api_version: str
    timeout_seconds: float = 15.0
    provider: str = "shopify"

    def validate_payload(self, payload: dict) -> None:
        _validate_text_draft_payload(self.provider, payload)

    def create_draft(self, payload: dict) -> dict:
        self.validate_payload(payload)
        query = """
            mutation CreateDraftProduct($product: ProductCreateInput!) {
              productCreate(product: $product) {
                product { id title status }
                userErrors { field message }
              }
            }
        """
        response = _post_json(
            self.provider,
            f"https://{self.store_domain}/admin/api/{self.api_version}/graphql.json",
            {
                "query": query,
                "variables": {
                    "product": {
                        "title": str(payload["title"]).strip(),
                        "descriptionHtml": str(payload["content"]).strip(),
                        "status": "DRAFT",
                    }
                },
            },
            {"X-Shopify-Access-Token": self.access_token},
            self.timeout_seconds,
        )
        data = response.get("data")
        result = data.get("productCreate", {}) if isinstance(data, dict) else {}
        product = result.get("product") if isinstance(result, dict) else None
        if (
            not isinstance(product, dict)
            or product.get("status") != "DRAFT"
            or not re.fullmatch(r"gid://shopify/Product/\d+", str(product.get("id", "")))
            or result.get("userErrors")
            or response.get("errors")
        ):
            raise CMSConfigurationError(
                "Shopify did not confirm creation of an unpublished draft product."
            )
        product_id = str(product["id"]).rsplit("/", 1)[-1]
        store_name = self.store_domain.removesuffix(".myshopify.com")
        return {
            "provider": self.provider,
            "mode": "live",
            "draft_id": product_id,
            "status": "DRAFT",
            "published": False,
            "edit_url": f"https://admin.shopify.com/store/{store_name}/products/{product_id}",
        }


@dataclass(frozen=True)
class ContentfulDraftAdapter:
    """Create an unpublished Contentful entry using the Content Management API."""

    space_id: str
    access_token: str
    content_type_id: str
    environment_id: str = "master"
    locale: str = "en-US"
    title_field_id: str = "title"
    content_field_id: str = "content"
    timeout_seconds: float = 15.0
    provider: str = "contentful"

    def validate_payload(self, payload: dict) -> None:
        _validate_text_draft_payload(self.provider, payload)

    def create_draft(self, payload: dict) -> dict:
        self.validate_payload(payload)
        space = urllib.parse.quote(self.space_id, safe="")
        environment = urllib.parse.quote(self.environment_id, safe="")
        content_type = urllib.parse.quote(self.content_type_id, safe="")
        response = _post_json(
            self.provider,
            f"https://api.contentful.com/spaces/{space}/environments/{environment}/entries",
            {
                "fields": {
                    self.title_field_id: {self.locale: str(payload["title"]).strip()},
                    self.content_field_id: {self.locale: str(payload["content"]).strip()},
                }
            },
            {
                "Authorization": f"Bearer {self.access_token}",
                "X-Contentful-Content-Type": self.content_type_id,
            },
            self.timeout_seconds,
        )
        entry_id = response.get("sys", {}).get("id") if isinstance(response.get("sys"), dict) else None
        if not isinstance(entry_id, str) or not entry_id:
            raise CMSConfigurationError(
                "Contentful did not confirm creation of an unpublished draft entry."
            )
        return {
            "provider": self.provider,
            "mode": "live",
            "draft_id": entry_id,
            "status": "DRAFT",
            "published": False,
            "edit_url": (
                f"https://app.contentful.com/spaces/{space}/environments/"
                f"{environment}/entries/{urllib.parse.quote(entry_id, safe='')}"
            ),
        }


@dataclass(frozen=True)
class WordPressDraftAdapter:
    """Create unpublished WordPress posts using an Application Password."""

    site_url: str
    username: str
    application_password: str
    timeout_seconds: float = 15.0
    provider: str = "wordpress"

    def validate_payload(self, payload: dict) -> None:
        _validate_text_draft_payload(self.provider, payload)

    def create_draft(self, payload: dict) -> dict:
        self.validate_payload(payload)
        base = self.site_url.rstrip("/")
        endpoint = base + "/wp-json/wp/v2/posts"
        body = json.dumps({
            "title": str(payload["title"]).strip(),
            "content": str(payload["content"]).strip(),
            "status": "draft",
        }).encode("utf-8")
        credential = base64.b64encode(
            f"{self.username}:{self.application_password}".encode("utf-8")
        ).decode("ascii")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Basic {credential}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "BrandRuleLab/1.0",
            },
            method="POST",
        )
        try:
            with _open_without_redirect(request, timeout=self.timeout_seconds) as response:
                if response.status not in (200, 201):
                    raise CMSConfigurationError(
                        f"WordPress returned unexpected HTTP status {response.status}."
                    )
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Do not return the response body, which may contain site details.
            raise CMSConfigurationError(
                f"WordPress draft request failed with HTTP {exc.code}."
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CMSConfigurationError(
                f"WordPress draft request failed ({type(exc).__name__})."
            ) from None
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError) as exc:
            raise CMSConfigurationError(
                f"WordPress returned an invalid response ({type(exc).__name__})."
            ) from None

        if not isinstance(data, dict) or data.get("status") != "draft" or not data.get("id"):
            raise CMSConfigurationError(
                "WordPress did not confirm creation of an unpublished draft."
            )
        edit_url = data.get("link")
        if edit_url:
            edit = urllib.parse.urlsplit(str(edit_url))
            configured = urllib.parse.urlsplit(base)
            if (
                edit.scheme != configured.scheme
                or edit.netloc.casefold() != configured.netloc.casefold()
                or edit.username
                or edit.password
            ):
                edit_url = None
        return {
            "provider": self.provider,
            "mode": "live",
            "draft_id": str(data["id"]),
            "status": "DRAFT",
            "published": False,
            "edit_url": edit_url,
        }


def get_cms_adapter(provider: str, *, mode: str = "mock") -> CMSAdapter:
    """Return a draft-only adapter, with optional live CMS connections."""
    provider_name = provider.strip().lower()
    if provider_name not in {"wordpress", "shopify", "contentful"}:
        raise ValueError("Unsupported CMS provider.")
    if mode == "mock":
        return MockCMSAdapter(provider_name)
    if mode != "live":
        raise CMSConfigurationError("Choose mock or live mode.")
    if provider_name == "wordpress":
        site_url = get_config_value("WORDPRESS_URL").rstrip("/")
        username = get_config_value("WORDPRESS_USERNAME")
        application_password = get_config_value("WORDPRESS_APPLICATION_PASSWORD")
        if not site_url or not username or not application_password:
            raise CMSConfigurationError(
                "Live WordPress is not configured. Set WORDPRESS_URL, WORDPRESS_USERNAME, and WORDPRESS_APPLICATION_PASSWORD to enable drafts."
            )
        parsed = urllib.parse.urlsplit(site_url)
        local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not local_http:
            raise CMSConfigurationError("WordPress must use HTTPS except on localhost.")
        if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CMSConfigurationError("WORDPRESS_URL must be a plain site URL without credentials or query parameters.")
        timeout = _timeout_from_config("WORDPRESS_TIMEOUT_SECONDS")
        return WordPressDraftAdapter(site_url, username, application_password, timeout)
    if provider_name == "shopify":
        store_domain = get_config_value("SHOPIFY_STORE_DOMAIN").lower()
        access_token = get_config_value("SHOPIFY_ACCESS_TOKEN")
        api_version = get_config_value("SHOPIFY_API_VERSION", "2026-10")
        if not store_domain or not access_token:
            raise CMSConfigurationError(
                "Live Shopify is not configured. Set SHOPIFY_STORE_DOMAIN and SHOPIFY_ACCESS_TOKEN to enable drafts."
            )
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", store_domain):
            raise CMSConfigurationError(
                "SHOPIFY_STORE_DOMAIN must be the store's *.myshopify.com domain."
            )
        if not re.fullmatch(r"\d{4}-(01|04|07|10)", api_version):
            raise CMSConfigurationError(
                "SHOPIFY_API_VERSION must use the YYYY-MM quarterly version format."
            )
        timeout = _timeout_from_config("SHOPIFY_TIMEOUT_SECONDS")
        return ShopifyDraftAdapter(store_domain, access_token, api_version, timeout)
    space_id = get_config_value("CONTENTFUL_SPACE_ID")
    access_token = get_config_value("CONTENTFUL_MANAGEMENT_TOKEN")
    content_type_id = get_config_value("CONTENTFUL_CONTENT_TYPE_ID")
    environment_id = get_config_value("CONTENTFUL_ENVIRONMENT_ID", "master")
    locale = get_config_value("CONTENTFUL_LOCALE", "en-US")
    title_field_id = get_config_value("CONTENTFUL_TITLE_FIELD_ID", "title")
    content_field_id = get_config_value("CONTENTFUL_CONTENT_FIELD_ID", "content")
    if not space_id or not access_token or not content_type_id:
        raise CMSConfigurationError(
            "Live Contentful is not configured. Set CONTENTFUL_SPACE_ID, CONTENTFUL_MANAGEMENT_TOKEN, and CONTENTFUL_CONTENT_TYPE_ID to enable drafts."
        )
    if not all(
        re.fullmatch(r"[A-Za-z0-9_-]+", value)
        for value in (space_id, content_type_id, environment_id, title_field_id, content_field_id)
    ):
        raise CMSConfigurationError(
            "Contentful IDs must use letters, numbers, underscores, or hyphens."
        )
    if not locale or len(locale) > 35:
        raise CMSConfigurationError("CONTENTFUL_LOCALE must be a valid locale identifier.")
    timeout = _timeout_from_config("CONTENTFUL_TIMEOUT_SECONDS")
    return ContentfulDraftAdapter(
        space_id,
        access_token,
        content_type_id,
        environment_id,
        locale,
        title_field_id,
        content_field_id,
        timeout,
    )
