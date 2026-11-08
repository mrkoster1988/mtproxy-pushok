#!/usr/bin/env python3
"""Offline read-only plan or exclusive local staging config generation. No server actions."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.pushok_core_config import render_staging_config, validate_domain


def main() -> int:
    parser = argparse.ArgumentParser(description="Pushok Core — подготовка тестовой конфигурации")
    parser.add_argument("--tls-domain", default="proxy.example.org")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--username", default="pushok")
    parser.add_argument("--write", action="store_true",
                        help="Создать в .build/ конфигурацию с новым секретом (0600)")
    args = parser.parse_args()
    try:
        domain = validate_domain(args.tls_domain)
        # Do not read or generate a secret when only displaying the plan.
        if not args.write:
            print(f"ПЛАН: staging 127.0.0.1:{args.port}, tls_domain={domain}, API отключён.")
            print("Никаких сетевых запросов, файлов, Docker или systemd действий.")
            return 0
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise ValueError("write_as_root_not_allowed")
        config = render_staging_config(
            tls_domain=domain, port=args.port, username=args.username,
            ad_tag=os.environ.get("PUSHOK_CORE_AD_TAG") or None,
        )
        base = (ROOT / ".build" / "pushok-core").resolve()
        base.mkdir(parents=True, exist_ok=True)
        target = base / "staging.toml"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(config)
        print("Тестовая конфигурация создана с правами 0600 в .build/pushok-core/staging.toml.")
        print("Секреты, ad_tag и полный файл не печатаются. Сервис не запускается.")
        return 0
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"Отказ: {type(exc).__name__}; параметры или файл требуют проверки.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
