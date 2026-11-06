"""Проверка HTTP и сохранности тестовых данных после перезапуска Compose."""
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import psycopg

base = os.environ["SMOKE_API_URL"]
def fetch(path):
    with urlopen(base + path, timeout=10) as response:
        return response, response.read()

assert json.loads(fetch("/health/ready")[1])["database"] == "ok"
assert b"Pushok" in fetch("/")[1] or b"PUSHOK" in fetch("/")[1]
assert fetch("/assets/app.js")[0].status == 200
tariffs = json.loads(fetch("/api/v1/tariffs")[1])
assert any(t["price_kopecks"] == 30000 and t["duration_seconds"] == 86400 for t in tariffs)
try:
    fetch("/api/v1/campaigns")
except HTTPError as exc:
    assert exc.code == 401
else:
    raise AssertionError("Anonymous access to campaigns")
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    assert conn.execute("SELECT first_name FROM users WHERE telegram_user_id=99000001").fetchone()[0] == "smoke-persisted"
assert Path("/proofs/smoke-persisted.txt").read_text() == "pushok-proof-persistence\n"
print("HTTP AND RESTART OK")
