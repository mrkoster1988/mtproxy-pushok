"""Тесты внутреннего API отправки черновика на модерацию."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused")
from app import main
from app.channel_verification import ChannelNotVerified, TelegramUnavailable

SECRET = "f" * 64
CID = "00000000-0000-4000-8000-000000000197"
UID = "00000000-0000-4000-8000-000000000198"
CHID = "00000000-0000-4000-8000-000000000199"
CHANNEL_TELEGRAM_ID = -1001234567890
PATH = "/api/v1/internal/bot/campaign-drafts/submit"


def campaign_row(status="draft", url="https://t.me/ExampleChannel", telegram_id=None):
    return {
        "id": UUID(CID), "moderation_status": status,
        "advertiser_user_id": UUID(UID), "channel_id": UUID(CHID),
        "telegram_url": url, "telegram_channel_id": telegram_id,
    }


def connection_with_rows(*rows):
    conn = MagicMock()
    execute = conn.__enter__.return_value.execute
    execute.side_effect = [
        MagicMock(**{"fetchone.return_value": row})
        for row in rows
    ]
    return conn


class DraftSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.env = patch.dict(os.environ, {"BOT_INTERNAL_SECRET": SECRET})
        self.env.start()
        self.addCleanup(self.env.stop)

    def submit(self, headers=None, uid=321):
        return self.client.post(
            PATH,
            headers=headers if headers is not None else {"X-Pushok-Bot-Secret": SECRET},
            json={"telegram_user_id": uid, "campaign_id": CID},
        )

    def test_wrong_secret_does_not_read_database_or_telegram(self):
        with patch("app.bot_routes.psycopg.connect") as db, \
             patch("app.bot_routes.verify_channel_admin") as tg:
            response = self.submit(headers={"X-Pushok-Bot-Secret": "e" * 64})
        self.assertEqual(response.status_code, 401)
        db.assert_not_called()
        tg.assert_not_called()

    def test_foreign_campaign_does_not_trigger_telegram(self):
        with patch("app.bot_routes.psycopg.connect", return_value=connection_with_rows(None)), \
             patch("app.bot_routes.verify_channel_admin") as tg:
            response = self.submit()
        self.assertEqual(response.status_code, 404)
        tg.assert_not_called()

    def test_pending_submission_is_idempotent(self):
        with patch("app.bot_routes.psycopg.connect", return_value=connection_with_rows(campaign_row("pending"))) as db, \
             patch("app.bot_routes.verify_channel_admin") as tg:
            response = self.submit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["moderation_status"], "pending")
        self.assertIs(response.json()["verified_now"], False)
        self.assertEqual(response.headers["cache-control"], "no-store")
        db.assert_called_once()
        tg.assert_not_called()

    def test_rejected_campaign_cannot_be_resubmitted(self):
        with patch("app.bot_routes.psycopg.connect", return_value=connection_with_rows(campaign_row("rejected"))), \
             patch("app.bot_routes.verify_channel_admin") as tg:
            response = self.submit()
        self.assertEqual(response.status_code, 409)
        tg.assert_not_called()

    def test_unexpected_channel_url_rejected(self):
        with patch("app.bot_routes.psycopg.connect", return_value=connection_with_rows(
            campaign_row(url="https://evil.example/ExampleChannel")
        )), patch("app.bot_routes.verify_channel_admin") as tg:
            response = self.submit()
        self.assertEqual(response.status_code, 409)
        tg.assert_not_called()

    def test_not_admin_does_not_modify_database(self):
        first = connection_with_rows(campaign_row())
        with patch("app.bot_routes.psycopg.connect", return_value=first) as db, \
             patch("app.bot_routes.verify_channel_admin",
                   side_effect=ChannelNotVerified()) as tg:
            response = self.submit()
        self.assertEqual(response.status_code, 409)
        db.assert_called_once()
        tg.assert_called_once_with("ExampleChannel", 321)

    def test_telegram_unavailable_leaves_draft_unchanged(self):
        first = connection_with_rows(campaign_row())
        with patch("app.bot_routes.psycopg.connect", return_value=first) as db, \
             patch("app.bot_routes.verify_channel_admin",
                   side_effect=TelegramUnavailable()):
            response = self.submit()
        self.assertEqual(response.status_code, 503)
        db.assert_called_once()

    def test_previous_numeric_channel_id_must_match(self):
        first = connection_with_rows(campaign_row(telegram_id=-100777777))
        with patch("app.bot_routes.psycopg.connect", return_value=first) as db, \
             patch("app.bot_routes.verify_channel_admin",
                   return_value=CHANNEL_TELEGRAM_ID):
            response = self.submit()
        self.assertEqual(response.status_code, 409)
        db.assert_called_once()

    def test_success_updates_status_and_writes_audit(self):
        first = connection_with_rows(campaign_row())
        second = connection_with_rows({"id": UUID(CHID)}, {"id": UUID(CID)}, None, None)
        with patch("app.bot_routes.psycopg.connect", side_effect=[first, second]), \
             patch("app.bot_routes.verify_channel_admin",
                   return_value=CHANNEL_TELEGRAM_ID) as tg:
            response = self.submit()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["moderation_status"], "pending")
        self.assertEqual(response.json()["campaign_id"], CID)
        self.assertIs(response.json()["verified_now"], True)
        tg.assert_called_once_with("ExampleChannel", 321)
        first_sql = first.__enter__.return_value.execute.call_args_list[0]
        self.assertIn("u.telegram_user_id = %s", first_sql.args[0])
        self.assertEqual(first_sql.args[1], (UUID(CID), 321))
        changes = second.__enter__.return_value.execute.call_args_list
        self.assertIn("owner_user_id = %s", changes[0].args[0])
        self.assertIn("moderation_status = 'draft'", changes[1].args[0])
        self.assertIn("campaign_submitted_from_bot", changes[2].args[0])
        self.assertIn("bot_operator_notice_outbox", changes[3].args[0])

    def test_changed_campaign_rolls_back_and_does_not_audit(self):
        first = connection_with_rows(campaign_row())
        second = connection_with_rows({"id": UUID(CHID)}, None)
        with patch("app.bot_routes.psycopg.connect", side_effect=[first, second]), \
             patch("app.bot_routes.verify_channel_admin", return_value=CHANNEL_TELEGRAM_ID):
            response = self.submit()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(second.__enter__.return_value.execute.call_count, 2)
        self.assertIsNotNone(second.__exit__.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
