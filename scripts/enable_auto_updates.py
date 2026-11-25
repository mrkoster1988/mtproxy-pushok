#!/usr/bin/env python3
"""One-time opt-in to unattended, CI-gated MTProxy stable updates on the VPS."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

ROOT = Path("/opt/mtproxy-pushok")
CONTROL = ROOT / ".control"
CONFIG = CONTROL / "auto-update.json"
SERVICE = "mtproxy-pushok-updater.service"
TIMER = "mtproxy-pushok-updater.timer"
PATH_UNIT = "mtproxy-pushok-updater.path"
MAX_BYTES = 16 * 1024


class Rejected(ValueError):
    pass


def normal(path):
    if path.is_symlink() or not path.is_file():
        raise Rejected("Не найден безопасный файл: " + path.name)
    return path


def existing():
    if not ROOT.is_dir() or ROOT.is_symlink() or not CONTROL.is_dir() or CONTROL.is_symlink():
        raise Rejected("Нужна существующая установка /opt/mtproxy-pushok/.control")
    if CONTROL.stat().st_mode & 0o077:
        raise Rejected("Права .control должны запрещать доступ другим пользователям")
    result = {"enabled": False}
    if CONFIG.exists() or CONFIG.is_symlink():
        normal(CONFIG)
        raw = CONFIG.read_bytes()
        if len(raw) > MAX_BYTES:
            raise Rejected("Некорректная настройка автоматических обновлений")
        try:
            doc = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise Rejected("Некорректная настройка автоматических обновлений") from exc
        if not isinstance(doc, dict) or ("enabled" in doc and type(doc["enabled"]) is not bool):
            raise Rejected("Неверный тип флага автоматических обновлений")
        result = doc
    return result


def safe_save(doc):
    owner = CONTROL.stat()
    fd, temp = tempfile.mkstemp(prefix=".mtproxy-auto-", dir=CONTROL)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), owner.st_uid, owner.st_gid)
            json.dump(doc, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, CONFIG)
    finally:
        Path(temp).unlink(missing_ok=True)


def unit_exists():
    return all((Path("/etc/systemd/system") / name).is_file()
               for name in (SERVICE, TIMER, PATH_UNIT))


def plan():
    data = existing()
    return {"state": "ready", "automatic_updates_enabled": data.get("enabled") is True,
            "updater_units_installed": unit_exists(),
            "published_source": "release/stable",
            "no_changes_made": True}


def install():
    if os.geteuid() != 0:
        raise Rejected("Нужны права root")
    doc = existing()
    normal(ROOT / "scripts" / "install_updater.sh")
    if not unit_exists():
        subprocess.run(["bash", "scripts/install_updater.sh", "--install-only"],
                       cwd=ROOT, check=True, timeout=180)
    doc = dict(doc)
    doc["enabled"] = True
    safe_save(doc)
    subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=30)
    subprocess.run(["systemctl", "enable", "--now", TIMER, PATH_UNIT],
                   check=True, timeout=30)
    subprocess.run(["systemctl", "start", SERVICE], check=True, timeout=3600)
    return {"state": "enabled", "source": "release/stable", "timer": TIMER,
            "note": "Реальный VPS обновляется только после зелёного CI и приватного бэкапа"}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("plan", "install", "disable"))
    args = parser.parse_args(argv)
    if args.command == "plan":
        result = plan()
    elif args.command == "install":
        result = install()
    else:
        if os.geteuid() != 0:
            raise Rejected("Нужны права root")
        doc = existing()
        doc["enabled"] = False
        safe_save(doc)
        result = {"state": "disabled", "note": "Текущая версия и данные сохранены"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Rejected, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print("Автообновление MTProxy остановлено: " + str(exc) if isinstance(exc, Rejected)
              else "Автообновление MTProxy остановлено: ошибка файлов или systemd", file=sys.stderr)
        raise SystemExit(1)
