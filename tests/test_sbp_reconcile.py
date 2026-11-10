"""СБП: только проверенный банковский объект меняет состояние заказа."""
from uuid import uuid4

import pytest
from api.app.sbp_reconcile import reconcile_sbp_attempt, SBPReconcileError
from api.app.sbp_verification import SBPVerificationError

ATTEMPT = uuid4()
CAMPAIGN = uuid4()


def payment():
    return {
        "id": "provider-1", "status": "succeeded", "paid": True,
        "captured_at": "2026-11-10T21:00:00Z",
        "payment_method": {"type": "sbp"},
        "metadata": {"campaign_id": str(CAMPAIGN)},
        "amount": {"currency": "RUB", "value": "300.00"},
    }


def attempt():
    return {
        "id": ATTEMPT, "campaign_id": CAMPAIGN,
        "provider_payment_id": "provider-1", "status": "pending",
        "amount": 30000, "currency": "RUB", "provider": "sbp",
        "payment_channel": "external_web", "payment_status": "awaiting_payment",
        "moderation_status": "approved", "placement_status": "not_scheduled",
        "advertiser_user_id": uuid4(),
    }


class Cursor:
    def __init__(self, row=None, rowcount=1):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row=None):
        self.row = row if row is not None else attempt()
        self.writes = []

    def execute(self, sql, params):
        if "SELECT a.id" in sql:
            return Cursor(self.row)
        if "UPDATE campaigns" in sql:
            self.writes.append(sql)
            return Cursor({"id": CAMPAIGN})
        if "UPDATE payment_attempts" in sql or "INSERT INTO audit_events" in sql:
            self.writes.append(sql)
            return Cursor()
        raise AssertionError("Unexpected SQL")


def test_verified_payment_updates_order_but_never_sponsor():
    db = FakeConnection()
    result = reconcile_sbp_attempt(db, ATTEMPT, fetch_payment=lambda _: payment())
    assert result == {"status": "applied", "campaign_id": str(CAMPAIGN)}
    assert len(db.writes) == 3
    assert all("placement_status" not in sql and "proxy_nodes" not in sql
               for sql in db.writes)


@pytest.mark.parametrize("change", [
    {"status": "pending"}, {"paid": False},
    {"amount": {"currency": "RUB", "value": "301.00"}},
    {"metadata": {"campaign_id": "other"}},
])
def test_forged_or_unsettled_result_never_mutates(change):
    db = FakeConnection()
    value = payment()
    value.update(change)
    with pytest.raises(SBPVerificationError):
        reconcile_sbp_attempt(db, ATTEMPT, fetch_payment=lambda _: value)
    assert db.writes == []


def test_repeated_success_is_idempotent():
    row = attempt()
    row["status"] = "succeeded"
    row["payment_status"] = "verified"
    db = FakeConnection(row)
    assert reconcile_sbp_attempt(db, ATTEMPT, fetch_payment=lambda _: payment())["status"] == "already_processed"
    assert db.writes == []


@pytest.mark.parametrize("change", [
    {"moderation_status": "pending"}, {"payment_status": "verified"},
    {"placement_status": "active"}, {"provider_payment_id": None},
    {"provider": "telegram_stars"},
])
def test_bad_attempt_never_mutates(change):
    row = attempt()
    row.update(change)
    db = FakeConnection(row)
    with pytest.raises(SBPReconcileError):
        reconcile_sbp_attempt(db, ATTEMPT, fetch_payment=lambda _: payment())
    assert db.writes == []
