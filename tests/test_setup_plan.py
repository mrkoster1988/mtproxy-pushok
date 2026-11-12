"""Безопасная проверка русскоязычного мастера: только --plan в пустом каталоге."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "setup.sh"


class SetupPlanTests(unittest.TestCase):
    def invoke(self, answers, existing_env=None, expected_code=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(SOURCE, root / "setup.sh")
            if existing_env is not None:
                (root / ".env").write_text(existing_env)
            result = subprocess.run(
                ["bash", str(root / "setup.sh"), "--plan"],
                input=answers, text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
            expected_files = {"setup.sh", ".env"} if existing_env is not None else {"setup.sh"}
            self.assertEqual({p.name for p in root.iterdir()}, expected_files)
            if existing_env is not None:
                self.assertEqual((root / ".env").read_text(), existing_env)
            return result.stdout + result.stderr

    def test_private_staging_defaults_without_changes(self):
        text = self.invoke("2\n2\n\n\n2\n2\n1\n")
        self.assertIn("mtproxy-pushok-staging", text)
        self.assertIn("8010", text)
        self.assertIn("изменения не выполнялись", text)

    def test_invalid_project_and_port_are_retried(self):
        text = self.invoke("9\n2\n2\n../wrong\nsmoke-staging\n0\n8019\n2\n2\n1\n")
        self.assertIn("Введите номер из списка", text)
        self.assertIn("Порт должен быть", text)
        self.assertIn("smoke-staging", text)
        self.assertIn("8019", text)

    def test_existing_environment_is_preserved(self):
        self.invoke("2\n1\n2\n", existing_env="SESSION_SIGNING_KEY=test-only\n")

    def test_miniapp_production_keeps_commercial_payments_disabled(self):
        secret="12345:fixture_secret"
        text=self.invoke("2\n1\n\n1\nads.example.org\n\n1\n1\n"+secret+"\nfixture_bot\n2\n1\n2\n1\n")
        self.assertIn("https://ads.example.org/miniapp",text)
        self.assertIn("PUSHOK_TELEGRAM_TEST_ENVIRONMENT=0",text)
        self.assertIn("PUSHOK_MINIAPP_ENABLED=1; PUSHOK_STARS_PAYMENTS_ENABLED=0",text)
        self.assertNotIn(secret,text)

    def test_test_environment_and_stars_require_explicit_choices(self):
        secret="12345:fixture_secret"
        text=self.invoke("2\n1\npushok-test\n1\nads-test.example.org\n8010\n1\n2\n"+secret+"\nfixture_bot\n2\n1\n1\n2\n1\n")
        self.assertIn("PUSHOK_TELEGRAM_TEST_ENVIRONMENT=1",text)
        self.assertIn("PUSHOK_MINIAPP_ENABLED=1; PUSHOK_STARS_PAYMENTS_ENABLED=1",text)
        self.assertIn("https://ads-test.example.org/miniapp",text)
        self.assertNotIn(secret,text)

    def test_local_staging_does_not_offer_unreachable_miniapp(self):
        text=self.invoke("2\n2\n\n\n1\n1\n12345:fixture_secret\nfixture_bot\n2\n2\n1\n")
        self.assertIn("Mini App выключена: нужен HTTPS-домен",text)
        self.assertIn("PUSHOK_MINIAPP_ENABLED=0; PUSHOK_STARS_PAYMENTS_ENABLED=0",text)

    def test_default_miniapp_choice_keeps_it_disabled(self):
        text=self.invoke("2\n1\n\n1\nads.example.org\n\n1\n\n12345:fixture_secret\nfixture_bot\n2\n\n2\n1\n")
        self.assertIn("PUSHOK_MINIAPP_ENABLED=0; PUSHOK_STARS_PAYMENTS_ENABLED=0",text)

    def test_test_telegram_refuses_installing_proxy_with_cabinet(self):
        text=self.invoke("3\n3\n1\n\n3\n\n1\n2\n",expected_code=1)
        self.assertIn("Тестовый Telegram настраивается только",text)
        self.assertNotIn("Начать установку пакетов",text)

    def test_invalid_domains_are_retried_before_installation(self):
        invalid=['ads.-example.org','ads.example-.org','ads..example.org',
                 'https://ads.example.org','ads.example.org/path','ads.example.org:443',
                 '127.0.0.1','ads.example.org.','ads_example.org',
                 'a'*64+'.example.org',('a'*63+'.')*4+'org']
        for domain in invalid:
            with self.subTest(domain=domain):
                text=self.invoke("2\n1\n\n1\n"+domain+"\nads.example.org\n\n2\n2\n1\n")
                self.assertIn('Введите DNS-домен',text)
                self.assertIn('Домен кабинета: ads.example.org',text)

    def test_valid_domain_case_and_label_boundary(self):
        for domain in ['ADS.EXAMPLE.ORG','a'*63+'.example.org']:
            with self.subTest(domain=domain):
                text=self.invoke("2\n1\n\n1\n"+domain+"\n\n2\n2\n1\n")
                self.assertIn('Домен кабинета: '+domain.lower(),text)
                self.assertNotIn('Введите DNS-домен',text)

    def test_bad_token_and_owner_id_are_retried_without_leaking_secrets(self):
        token='12345:fixture_secret'
        bad_tokens=['0:fixture_secret','12345:short','1'*17+':fixture_secret']
        text=self.invoke("2\n2\n\n\n1\n1\n"+'\n'.join(bad_tokens)+"\n"+token+"\nfixture_bot\n1\n4503599627370496\n@owner\n42\n2\n1\n")
        self.assertEqual(text.count('Ожидается токен вида'),len(bad_tokens))
        self.assertEqual(text.count('Нужен числовой Telegram ID'),2)
        self.assertIn('Telegram ID владельца: 42',text)
        for secret in bad_tokens+[token]: self.assertNotIn(secret,text)

    def test_existing_invalid_token_refuses_bot_launch_without_changes(self):
        self.invoke("2\n4\n",existing_env='TELEGRAM_BOT_TOKEN=1:short\nTELEGRAM_BOT_USERNAME=fixture_bot\nBOT_INTERNAL_SECRET='+'b'*64+'\n',expected_code=1)
