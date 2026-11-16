import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("telegram_owner", ROOT / "scripts/telegram_owner.py")
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)


class PasswordNeeded(Exception):
    pass


class InvalidCode(Exception):
    pass


class InvalidPassword(Exception):
    pass


ERRORS = Obj(SessionPasswordNeededError=PasswordNeeded, PhoneCodeInvalidError=InvalidCode,
             PasswordHashInvalidError=InvalidPassword)
CREDS = {"api_id": 123, "api_hash": "ab" * 16}
SAVED = dict(CREDS, session="private-session", user_id=42)


class FakeClient:
    """У тестового клиента нет методов чтения чатов или отправки сообщений."""
    def __init__(self, authorized=False, password=False, invalid_codes=0, logout=True):
        self.authorized, self.password = authorized, password
        self.invalid_codes, self.logout = invalid_codes, logout
        self.calls = []
        self.session = Obj(save=lambda: "private-session")
        self.me = Obj(id=42, username="owner_test", bot=False, phone="private-phone")
        self.bot = Obj(username="MTProxyBot", bot=True)
        self.received_password = None

    async def connect(self): self.calls.append("connect")
    async def disconnect(self): self.calls.append("disconnect")
    async def get_me(self): return self.me if self.authorized else None
    async def get_entity(self, name):
        assert name == "MTProxyBot"
        self.calls.append("resolve_bot")
        return self.bot

    async def send_code_request(self, phone):
        self.calls.append("send_code")
        return Obj(phone_code_hash="private-code-hash")

    async def sign_in(self, *args, **kwargs):
        self.calls.append("sign_in")
        if self.invalid_codes:
            self.invalid_codes -= 1
            raise InvalidCode("private RPC error")
        if self.password and "password" not in kwargs:
            raise PasswordNeeded()
        self.received_password = kwargs.get("password")
        self.authorized = True

    async def log_out(self):
        self.calls.append("log_out")
        return self.logout


class OwnerConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "owner"

    def run_client(self, client, action="connect", existing=None, inputs=None, save_error=False):
        out = io.StringIO()
        answers = iter(inputs or ["+15551234567", "12345", " password with spaces "])
        with owner.PrivateState(self.path) as state, redirect_stdout(out):
            if existing:
                state.save(existing)
            if save_error:
                state.save = lambda _: (_ for _ in ()).throw(OSError("private disk error"))
            result = asyncio.run(owner.operate(client, action, state, CREDS, existing,
                                               ERRORS, lambda _: next(answers)))
        return result, out.getvalue()

    def test_persistent_login_has_private_file_and_no_secret_output(self):
        client = FakeClient()
        result, output = self.run_client(client)
        self.assertEqual(result["state"], "connected")
        self.assertFalse(result["automatic_binding_enabled"])
        self.assertFalse(result["tag_ownership_verified"])
        self.assertNotIn("log_out", client.calls)
        self.assertEqual(client.calls[-1], "disconnect")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.path / "owner.json").stat().st_mode), 0o600)
        with owner.PrivateState(self.path) as state:
            self.assertEqual(state.read(), SAVED)
        for secret in ("private", "15551234567", "12345", CREDS["api_hash"]):
            self.assertNotIn(secret, output + json.dumps(result))

    def test_existing_connection_checks_identity_without_new_login(self):
        client = FakeClient(authorized=True)
        result, _ = self.run_client(client, action="status", existing=SAVED, inputs=[])
        self.assertEqual(result["telegram_user_id"], 42)
        self.assertNotIn("send_code", client.calls)
        self.assertNotIn("log_out", client.calls)

    def test_two_factor_password_preserves_spaces(self):
        client = FakeClient(password=True)
        self.run_client(client)
        self.assertEqual(client.received_password, " password with spaces ")

    def test_invalid_code_retries_without_resending(self):
        client = FakeClient(invalid_codes=1)
        self.run_client(client, inputs=["+15551234567", "11111", "12345"])
        self.assertEqual(client.calls.count("send_code"), 1)
        self.assertEqual(client.calls.count("sign_in"), 2)

    def test_failed_new_login_revokes_only_new_session(self):
        client = FakeClient(invalid_codes=3)
        with self.assertRaises(InvalidCode):
            self.run_client(client, inputs=["+15551234567", "11111", "22222", "33333"])
        self.assertEqual(client.calls[-2:], ["log_out", "disconnect"])
        self.assertFalse((self.path / "owner.json").exists())

    def test_failed_persistence_does_not_leave_new_authorized_session(self):
        client = FakeClient()
        with self.assertRaises(OSError):
            self.run_client(client, save_error=True)
        self.assertEqual(client.calls[-2:], ["log_out", "disconnect"])
        self.assertFalse((self.path / "owner.json").exists())

    def test_revoked_session_is_reported_without_new_code(self):
        client = FakeClient()
        result, _ = self.run_client(client, action="status", existing=SAVED)
        self.assertEqual(result["state"], "revoked")
        self.assertNotIn("send_code", client.calls)
        self.assertTrue((self.path / "owner.json").exists())

    def test_temporary_identity_failure_never_deletes_session(self):
        async def fail(): raise RuntimeError("private transient RPC error")
        for action in ("status", "disconnect"):
            client = FakeClient(authorized=True)
            client.get_me = fail
            with self.assertRaises(RuntimeError):
                self.run_client(client, action=action, existing=SAVED)
            self.assertTrue((self.path / "owner.json").exists())
            self.assertNotIn("send_code", client.calls)
            self.assertNotIn("log_out", client.calls)

    def test_disconnect_requires_confirmed_remote_revocation(self):
        client = FakeClient(authorized=True, logout=False)
        with self.assertRaisesRegex(owner.OwnerError, "logout_not_confirmed"):
            self.run_client(client, action="disconnect", existing=SAVED)
        self.assertTrue((self.path / "owner.json").exists())
        result, _ = self.run_client(FakeClient(authorized=True), action="disconnect", existing=SAVED)
        self.assertEqual(result["state"], "disconnected")
        self.assertFalse((self.path / "owner.json").exists())

    def test_already_revoked_session_can_be_removed(self):
        client = FakeClient()
        self.run_client(client, action="disconnect", existing=SAVED)
        self.assertNotIn("log_out", client.calls)
        self.assertFalse((self.path / "owner.json").exists())

    def test_bot_account_or_wrong_target_is_not_saved(self):
        for wrong_account in (True, False):
            client = FakeClient()
            if wrong_account:
                client.me.bot = True
            else:
                client.bot.username = "DifferentBot"
            with self.assertRaises(owner.OwnerError):
                self.run_client(client)
            self.assertIn("log_out", client.calls)
            self.assertFalse((self.path / "owner.json").exists())

    def test_existing_account_mismatch_never_replaces_saved_identity(self):
        client = FakeClient(authorized=True)
        client.me.id = 99
        with self.assertRaisesRegex(owner.OwnerError, "account_mismatch"):
            self.run_client(client, action="status", existing=SAVED)
        self.assertNotIn("log_out", client.calls)
        self.assertEqual(json.loads((self.path / "owner.json").read_text())["user_id"], 42)

    def test_process_lock_and_unsafe_file_are_rejected(self):
        with owner.PrivateState(self.path) as state:
            with self.assertRaisesRegex(owner.OwnerError, "connection_busy"):
                with owner.PrivateState(self.path): pass
            state.save(SAVED)
            (self.path / "owner.json").chmod(0o644)
            with self.assertRaisesRegex(owner.OwnerError, "unsafe_state_permissions"):
                state.read()

    def test_symlinks_hardlinks_and_repository_storage_rejected(self):
        with owner.PrivateState(self.path) as state:
            target = Path(self.tmp.name) / "secret"
            target.write_text(json.dumps(SAVED)); target.chmod(0o600)
            file = self.path / "owner.json"
            file.symlink_to(target)
            with self.assertRaises(OSError): state.read()
            file.unlink(); os.link(target, file)
            with self.assertRaisesRegex(owner.OwnerError, "unsafe_state_permissions"): state.read()
        link = Path(self.tmp.name) / "owner-link"; link.symlink_to(self.path)
        with self.assertRaises(OSError):
            with owner.PrivateState(link): pass
        with self.assertRaisesRegex(owner.OwnerError, "state_inside_repository"):
            with owner.PrivateState(ROOT / ".test-owner-do-not-create"): pass

    def test_unexpected_fields_and_credentials_are_rejected(self):
        with owner.PrivateState(self.path) as state:
            for wrong in (dict(SAVED, phone="private"), dict(SAVED, api_id=True), dict(SAVED, session="")):
                state.save(wrong)
                with self.assertRaises(owner.OwnerError): state.read()

    def test_sdk_uses_memory_only_and_no_background_updates(self):
        try:
            import telethon
            from telethon.sessions import StringSession
        except ImportError:
            self.skipTest("Optional SDK required: scripts/requirements-telegram-owner.txt")
        self.assertEqual(telethon.__version__, owner.TELETHON_VERSION)
        captured = []

        async def fake_operate(client, *args):
            captured.append(client)
            self.assertIsInstance(client.session, StringSession)
            self.assertTrue(client._no_updates)
            self.assertFalse(client._catch_up)
            await client.disconnect()
            return {"state": "connected"}

        out = io.StringIO()
        with patch.object(sys_module := owner.sys, "argv", ["telegram_owner.py", "connect", "--state-dir", str(self.path)]), \
                patch.object(sys_module.stdin, "isatty", return_value=True), \
                patch.object(owner, "hidden", side_effect=["123", "ab" * 16]), \
                patch.object(owner, "operate", side_effect=fake_operate), redirect_stdout(out):
            with patch.object(out, "isatty", return_value=True):
                original_umask = os.umask(0o077)
                try: self.assertEqual(owner.main(), 0)
                finally: os.umask(original_umask)
        self.assertEqual(len(captured), 1)
        self.assertEqual(list(self.path.glob("*.session*")), [])
        self.assertNotIn("ab" * 16, out.getvalue())
