"""Isolated YooKassa SBP API transport (no database or campaign mutations).

Only trusted server code may call this client. Credentials must stay in
environment/secret manager, never browser JavaScript or repository files.
The feature gate defaults to OFF until staging and contract acceptance.
"""
import base64
import json
import os
import re
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_API = "https://api.yookassa.ru/v3/payments"
_PAYMENT_ID = re.compile(r"^[a-zA-Z0-9-]{1,80}$")
_IDEMPOTENCE = re.compile(r"^[a-zA-Z0-9-]{1,64}$")
_MAX_RESPONSE_BYTES = 65536


class SBPProviderError(RuntimeError):
    """Network, provider or configuration failure; payment not confirmed."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def sbp_enabled() -> bool:
    return os.getenv("PUSHOK_SBP_PAYMENTS_ENABLED", "0") == "1"


def _credentials() -> tuple[str, str]:
    shop = os.getenv("YOOKASSA_SHOP_ID", "")
    key = os.getenv("YOOKASSA_SECRET_KEY", "")
    if not shop or not key or ":" in shop or "\n" in shop or "\r" in shop:
        raise SBPProviderError("sbp_credentials_not_configured")
    return shop, key


def _request(method: str, url: str, *, body: Mapping | None = None,
             idempotence_key: str | None = None) -> dict:
    shop, secret = _credentials()
    raw_token = base64.b64encode(f"{shop}:{secret}".encode()).decode("ascii")
    headers = {"Authorization": "Basic " + raw_token, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotence_key:
        headers["Idempotence-Key"] = idempotence_key
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with build_opener(_NoRedirect).open(req, timeout=8) as response:
            if response.status < 200 or response.status >= 300:
                raise SBPProviderError("sbp_provider_http_error")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise SBPProviderError("sbp_provider_unavailable") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise SBPProviderError("sbp_provider_response_too_large")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SBPProviderError("sbp_provider_invalid_json") from exc
    if not isinstance(result, dict):
        raise SBPProviderError("sbp_provider_invalid_response")
    return result


def create_sbp_payment(*, campaign_id: str, amount_kopecks: int,
                       idempotence_key: str, return_url: str) -> dict:
    """Request a redirect invoice. NEVER treat its creation as payment proof."""
    if not sbp_enabled():
        raise SBPProviderError("sbp_checkout_disabled_until_acceptance")
    if not isinstance(campaign_id, str) or not _PAYMENT_ID.fullmatch(campaign_id):
        raise SBPProviderError("sbp_invalid_campaign_id")
    if type(amount_kopecks) is not int or not 100 <= amount_kopecks <= 100000000:
        raise SBPProviderError("sbp_invalid_amount")
    if not isinstance(idempotence_key, str) or not _IDEMPOTENCE.fullmatch(idempotence_key):
        raise SBPProviderError("sbp_invalid_idempotence_key")
    if not isinstance(return_url, str):
        raise SBPProviderError("sbp_invalid_return_url")
    try:
        parts = urlsplit(return_url)
        valid = (parts.scheme == "https" and bool(parts.hostname)
                 and not parts.username and not parts.password and not parts.fragment
                 and parts.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise SBPProviderError("sbp_invalid_return_url")
    expected_host = os.getenv("PUSHOK_PUBLIC_PAYMENT_HOST", "").strip().lower()
    if not expected_host or parts.hostname.lower() != expected_host:
        raise SBPProviderError("sbp_return_host_not_allowed")

    result = _request("POST", _API, idempotence_key=idempotence_key, body={
        "amount": {"value": f"{amount_kopecks // 100}.{amount_kopecks % 100:02d}",
                   "currency": "RUB"},
        "payment_method_data": {"type": "sbp"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": "MTProxy Pushok campaign " + campaign_id[:32],
        "metadata": {"campaign_id": campaign_id},
    })
    confirmation = result.get("confirmation")
    url = confirmation.get("confirmation_url") if isinstance(confirmation, dict) else None
    if result.get("status") != "pending" or not isinstance(url, str):
        raise SBPProviderError("sbp_unexpected_invoice_response")
    # URL приходит от эквайринга, но перед редиректом браузера его
    # обязательно разбираем структурно: строковый startswith пропускает
    # управляющие символы, userinfo и некорректные hostname.
    try:
        destination = urlsplit(url)
        url_valid = (
            destination.scheme == "https"
            and bool(destination.hostname)
            and destination.hostname.strip(".") == destination.hostname
            and destination.username is None
            and destination.password is None
            and destination.port in (None, 443)
            and not destination.fragment
            and not any(ord(char) < 32 or ord(char) == 127 for char in url)
            and not any(char.isspace() for char in url)
        )
    except ValueError:
        url_valid = False
    if not url_valid:
        raise SBPProviderError("sbp_invalid_confirmation_url")
    if not isinstance(result.get("id"), str) or not _PAYMENT_ID.fullmatch(result["id"]):
        raise SBPProviderError("sbp_invalid_provider_payment_id")
    return {"payment_id": result["id"], "confirmation_url": url, "status": "pending"}


def get_sbp_payment(payment_id: str) -> dict:
    """GET an authoritative current payment object; caller verifies its fields."""
    if not isinstance(payment_id, str) or not _PAYMENT_ID.fullmatch(payment_id):
        raise SBPProviderError("sbp_invalid_provider_payment_id")
    # Deliberately permit status reconciliation when checkout was disabled later.
    return _request("GET", _API + "/" + payment_id)
