import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("telegram_promo_probe", ROOT / "scripts/check_telegram_promo.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class PasswordNeeded(Exception):
    pass


class InvalidCode(Exception):
    pass


class Client:
    """Только допустимые для проверки операции; методов отправки сообщений нет."""
    def __init__(self, response=None, fail=False, need_password=False, logout=True):
        self.response = response or Obj(proxy=False, expires=1794743998)
        self.fail, self.need_password, self.logout = fail, need_password, logout
        self.calls = []

    async def connect(self):
        self.calls.append("connect")

    async def send_code_request(self, phone):
        self.calls.append("send_code")
        return Obj(phone_code_hash="private-code-hash")

    async def sign_in(self, *args, **kwargs):
        self.calls.append("sign_in")
        if self.need_password and "password" not in kwargs:
            raise PasswordNeeded()

    async def __call__(self, request):
        self.calls.append("get_promo")
        if self.fail:
            raise RuntimeError("private exception text")
        return self.response

    async def log_out(self):
        self.calls.append("log_out")
        return self.logout

    async def disconnect(self):
        self.calls.append("disconnect")


class PromoProbeTests(unittest.TestCase):
    def run_probe(self, client, inputs=None):
        answers = iter(inputs or ["+15551234567", "12345", "private-password"])
        out = io.StringIO()
        with redirect_stdout(out):
            result = asyncio.run(probe.probe(client, "push_canal", PasswordNeeded,
                                             InvalidCode, object, lambda _: next(answers)))
        return result, out.getvalue()

    def test_expected_peer_only_and_secrets_not_serialized(self):
        response = Obj(proxy=True, expires=datetime.now(timezone.utc),
                       peer=Obj(channel_id=7), users=[Obj(phone="private-phone")],
                       chats=[Obj(id=8, username="unrelated_peer", access_hash="private-other"),
                              Obj(id=7, username="push_canal", access_hash="private-hash", left=True)])
        result, output = self.run_probe(Client(response))
        self.assertEqual(result["outcome"], "proxy_promo_expected")
        self.assertFalse(result["display_verified"])
        self.assertTrue(result["channel_left"])
        for secret in ("private", "unrelated_peer", "15551234567", "12345"):
            self.assertNotIn(secret, output)

    def test_psa_peer_is_not_proxy_promotion(self):
        response = Obj(proxy=False, peer=Obj(channel_id=7), chats=[Obj(id=7, username="push_canal")])
        result = probe.summarize_promo(response, "push_canal")
        self.assertEqual(result["outcome"], "no_proxy_promo")
        self.assertNotIn("channel_usernames", result)

    def test_empty_and_unresolved_are_distinct(self):
        self.assertEqual(probe.summarize_promo(Obj(), "push_canal")["outcome"], "no_proxy_promo")
        missing = Obj(proxy=True, peer=Obj(channel_id=7), chats=[])
        self.assertEqual(probe.summarize_promo(missing, "push_canal")["outcome"], "proxy_promo_unresolved")

    def test_other_promo_and_active_username_alias(self):
        chat = Obj(id=7, username="other_channel", usernames=[Obj(username="push_canal", active=False)])
        response = Obj(proxy=True, peer=Obj(channel_id=7), chats=[chat])
        self.assertEqual(probe.summarize_promo(response, "push_canal")["outcome"], "proxy_promo_other")
        chat.usernames[0].active = True
        self.assertEqual(probe.summarize_promo(response, "push_canal")["outcome"], "proxy_promo_expected")

    def test_logout_after_two_factor_success(self):
        client = Client(need_password=True)
        _, output = self.run_probe(client)
        self.assertEqual(client.calls, ["connect", "send_code", "sign_in", "sign_in", "get_promo", "log_out", "disconnect"])
        self.assertIn("temporary_session_logout=true", output)

    def test_logout_after_promo_failure(self):
        client = Client(fail=True)
        with self.assertRaises(RuntimeError):
            self.run_probe(client)
        self.assertEqual(client.calls[-2:], ["log_out", "disconnect"])

    def test_logout_failure_is_reported(self):
        _, output = self.run_probe(Client(logout=False))
        self.assertIn("temporary_session_logout=false", output)
        self.assertIn("Устройства", output)

    def test_invalid_phone_does_not_request_login_code(self):
        client = Client()
        with self.assertRaises(ValueError):
            self.run_probe(client, ["bad-input"])
        self.assertEqual(client.calls, ["connect", "disconnect"])

    def test_private_env_parsing_and_unsupported_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "proxy.env"
            path.write_text('MT_PROXY_HOST=45.90.57.136\nMT_PROXY_PORT=8443\nMT_PROXY_SECRET="dd' + "ab" * 16 + '"\n')
            path.chmod(0o600)
            self.assertEqual(probe.load_proxy(path), ("45.90.57.136", 8443, "ab" * 16))
            link = Path(folder) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                probe.load_proxy(link)
            path.write_text('MT_PROXY_HOST=45.90.57.136\nMT_PROXY_PORT=8443\nMT_PROXY_SECRET=ee' + "ab" * 20 + '\n')
            with self.assertRaises(ValueError):
                probe.load_proxy(path)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                probe.load_proxy(path)

    def test_real_sdk_schema_and_memory_proxy_configuration(self):
        try:
            import telethon
            from telethon import connection, types
            from telethon.sessions import MemorySession
        except ImportError:
            self.skipTest("Отдельная проверка с Telethon==1.45.0")
        self.assertEqual(telethon.__version__, probe.TELETHON_VERSION)
        channel = types.Channel(id=7, title="private-title", photo=types.ChatPhotoEmpty(),
                                date=datetime.now(timezone.utc), username="push_canal", left=True,
                                access_hash=123456789)
        response = types.help.PromoData(expires=datetime.now(timezone.utc),
                                        pending_suggestions=[], dismissed_suggestions=[],
                                        chats=[channel], users=[], proxy=True,
                                        peer=types.PeerChannel(channel_id=7))
        self.assertEqual(probe.summarize_promo(response, "push_canal")["outcome"], "proxy_promo_expected")
        captured = []

        async def offline_client(client, *args):
            captured.append(client)
            self.assertIsInstance(client.session, MemorySession)
            self.assertIs(client._connection, connection.ConnectionTcpMTProxyRandomizedIntermediate)
            self.assertEqual(client._proxy, ("45.90.57.136", 8443, "dddd" + "ab" * 15))
            self.assertEqual(client._connection.normalize_secret(client._proxy[2]),
                             bytes.fromhex("dd" + "ab" * 15))
            transport = client._connection("192.0.2.1", 443, 2,
                                           loggers=client._log, proxy=client._proxy)
            self.assertEqual((transport._ip, transport._port), ("45.90.57.136", 8443))
            self.assertIsNone(transport._proxy)  # SOCKS-маршрут не используется.
            self.assertTrue(client._no_updates)
            await client.disconnect()

        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "proxy.env"
            config.write_text('MT_PROXY_HOST=45.90.57.136\nMT_PROXY_PORT=8443\nMT_PROXY_SECRET=dd' + "ab" * 15 + '\n')
            config.chmod(0o600)
            argv = ["probe", "--proxy-env", str(config), "--expected-channel", "push_canal"]
            with patch.object(probe.sys, "argv", argv), patch.object(probe, "hidden", side_effect=["12345", "cd" * 16]), \
                    patch.object(probe, "probe", offline_client), patch.object(probe.logging, "disable"), \
                    patch.object(telethon.TelegramClient, "connect", side_effect=AssertionError("network_forbidden")), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(), 0)
            self.assertEqual(len(captured), 1)
            self.assertEqual(list(Path(folder).iterdir()), [config])


if __name__ == "__main__":
    unittest.main()
