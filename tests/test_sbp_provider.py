"""No-live-network provider transport regression tests."""
from unittest.mock import patch
import pytest
from api.app import sbp_provider


def setup_env(monkeypatch):
    monkeypatch.setenv("PUSHOK_SBP_PAYMENTS_ENABLED", "1")
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "test_shop")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "test_secret")
    monkeypatch.setenv("PUSHOK_PUBLIC_PAYMENT_HOST", "ads.example.org")


def make(monkeypatch, **kwargs):
    setup_env(monkeypatch)
    args = dict(campaign_id="campaign-123", amount_kopecks=30000,
                idempotence_key="req-123", return_url="https://ads.example.org/payment/result")
    args.update(kwargs)
    return sbp_provider.create_sbp_payment(**args)


def test_invoice_request_snapshot(monkeypatch):
    def fake(method, url, *, body, idempotence_key):
        assert method == "POST"
        assert url == "https://api.yookassa.ru/v3/payments"
        assert body["amount"] == {"value": "300.00", "currency": "RUB"}
        assert body["capture"] is True
        assert body["payment_method_data"] == {"type": "sbp"}
        assert body["metadata"] == {"campaign_id": "campaign-123"}
        assert idempotence_key == "req-123"
        return {"id": "provider-001", "status": "pending",
                "confirmation": {"confirmation_url": "https://pay.example/checkout"}}
    monkeypatch.setattr(sbp_provider, "_request", fake)
    assert make(monkeypatch)["status"] == "pending"


@pytest.mark.parametrize("overrides", [
    {"amount_kopecks": 0}, {"amount_kopecks": True},
    {"return_url": "http://ads.example.org/return"},
    {"return_url": "https://evil.example/return"},
    {"return_url": "https://ads.example.org:8443/return"},
    {"idempotence_key": "bad key"}, {"campaign_id": "../secret"},
])
def test_invalid_input_never_calls_provider(monkeypatch, overrides):
    monkeypatch.setattr(sbp_provider, "_request",
                        lambda *a, **kw: pytest.fail("called provider"))
    with pytest.raises(sbp_provider.SBPProviderError):
        make(monkeypatch, **overrides)


def test_checkout_off_by_default(monkeypatch):
    setup_env(monkeypatch)
    monkeypatch.delenv("PUSHOK_SBP_PAYMENTS_ENABLED")
    with pytest.raises(sbp_provider.SBPProviderError, match="disabled"):
        sbp_provider.create_sbp_payment(
            campaign_id="campaign-123", amount_kopecks=30000,
            idempotence_key="req-123",
            return_url="https://ads.example.org/payment/result")


def test_lookup_is_host_pinned_and_survives_gate_off(monkeypatch):
    setup_env(monkeypatch)
    monkeypatch.setenv("PUSHOK_SBP_PAYMENTS_ENABLED", "0")
    with patch.object(sbp_provider, "_request", return_value={"status": "pending"}) as req:
        assert sbp_provider.get_sbp_payment("abc-123")["status"] == "pending"
        req.assert_called_once_with("GET", "https://api.yookassa.ru/v3/payments/abc-123")
    with pytest.raises(sbp_provider.SBPProviderError):
        sbp_provider.get_sbp_payment("../../etc/passwd")


def test_incorrect_invoice_response_fails(monkeypatch):
    monkeypatch.setattr(sbp_provider, "_request", lambda *a, **kw: {"status": "succeeded"})
    with pytest.raises(sbp_provider.SBPProviderError):
        make(monkeypatch)


@pytest.mark.parametrize("confirmation_url", [
    "https://", "https://user:pass@pay.example/checkout",
    "https://pay.example:8443/checkout",
    "https://pay.example/#fragment",
    "https://pay.example/checkout\n", "https://pay.example/checkout\t",
    "https://pay.example./checkout", "http://pay.example/checkout",
    "https://pay.example/checkout with spaces",
])
def test_invalid_provider_redirect_is_rejected(monkeypatch, confirmation_url):
    monkeypatch.setattr(
        sbp_provider, "_request",
        lambda *a, **kw: {
            "id": "provider-001", "status": "pending",
            "confirmation": {"confirmation_url": confirmation_url},
        },
    )
    with pytest.raises(sbp_provider.SBPProviderError, match="invalid_confirmation_url"):
        make(monkeypatch)


def test_valid_provider_redirect_passes(monkeypatch):
    monkeypatch.setattr(
        sbp_provider, "_request",
        lambda *a, **kw: {
            "id": "provider-001", "status": "pending",
            "confirmation": {"confirmation_url": "https://pay.example/checkout?session=abc"},
        },
    )
    assert make(monkeypatch)["confirmation_url"] == "https://pay.example/checkout?session=abc"

@pytest.mark.parametrize('observed,expected,allowed', [(True,True,True),(False,False,True),(True,False,False),(False,True,False),(None,False,False)])
def test_invoice_test_mode_checked_before_exposing_redirect(monkeypatch,observed,expected,allowed):
    monkeypatch.setattr(sbp_provider,'_request',lambda *a,**kw:{'id':'provider-1','status':'pending','test':observed,'confirmation':{'confirmation_url':'https://pay.example/checkout'}})
    if allowed:
        assert make(monkeypatch,expected_test=expected)['payment_id']=='provider-1'
    else:
        with pytest.raises(sbp_provider.SBPProviderError,match='test_mode_mismatch'):
            make(monkeypatch,expected_test=expected)
