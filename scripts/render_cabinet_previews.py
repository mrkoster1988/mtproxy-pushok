#!/usr/bin/env python3
"""Private visual previews of the REAL cabinet HTML/CSS/JS with synthetic API data.

Does not contact Telegram, perform payments or start a real MTProxy.
Requires: pip install playwright && python -m playwright install chromium
"""
import argparse
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "api" / "web"
DEMO_USER = "00000000-0000-4000-8000-000000000028"
CHANNEL = "00000000-0000-4000-8000-000000000029"
TARIFF = "00000000-0000-4000-8000-000000000030"
NODE = "00000000-0000-4000-8000-000000000031"


def campaign(identifier: str, channel: str, moderation: str, payment: str, placement: str) -> dict:
    date = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    return {
        "id": identifier, "channel_id": CHANNEL, "channel_title": channel,
        "channel_url": "https://t.me/example_channel",
        "tariff_id": TARIFF, "tariff_name_snapshot": "1 день",
        "duration_seconds_snapshot": 86400, "price_kopecks_snapshot": 30000,
        "currency_snapshot": "RUB", "moderation_status": moderation,
        "payment_status": payment, "placement_status": placement,
        "requested_start_at": date,
        "scheduled_start_at": date if placement in ("scheduled", "active") else None,
        "scheduled_end_at": None,
        "moderator_comment": None, "payment_review_comment": None,
    }


def demo_response(path: str, role: str) -> object:
    path = urlsplit(path).path
    if path.endswith('/admin/control'):
        return {'version':'0.051','stars':False,'sbp':False,'sbp_configured':False,'updater_installed':True,'update':{'state':'idle'},'requested':False}
    if path.endswith('/admin/icons'):
        return {'items':[], 'mapping':{}}
    if path.endswith('/sbp'):
        return {'available': False, 'test_mode': True, 'amount_kopecks': 30000,
                'status': 'not_created', 'can_create': False, 'can_verify': False,
                'confirmation_url': None, 'payment_status': 'awaiting_payment'}
    if path.endswith("/admin/payments/status"):
        return {"stars": {"invoice_enabled_in_server_config": False,
                         "priced_active_tariffs": 0, "recorded_successes": 0,
                         "needs_manual_review": 0}, "sbp": {"checkout_enabled": False}}
    if path.endswith("/admin/tariffs"):
        return [{"id": TARIFF, "name": "1 день", "price_kopecks": 30000,
                 "duration_seconds": 86400, "stars_price_xtr": None, "is_active": True}]
    if path.endswith("/admin/launch-readiness"):
        names = ("nodes_total", "nodes_active", "nodes_with_registered_tag", "campaigns_total",
                 "moderation_pending", "payments_pending", "payments_verified",
                 "placements_scheduled", "placements_active", "placements_completed", "manual_observations")
        return {"statistics": dict.fromkeys(names, 0), "checks": []}
    if path.endswith("/public-config"):
        return {"telegram_bot_username": None}
    if path.endswith("/admin/pushok-core/diagnostic-plan"):
        return {"project_version": "0.050", "execution": "not_started_by_api",
                "steps": [
                    {"title": "Локальный TCP", "verification": "ci_only"},
                    {"title": "Некорректный 64-байтный заголовок", "verification": "ci_only"},
                    {"title": "Подключение Telegram", "verification": "not_verified"},
                    {"title": "Показ спонсора", "verification": "not_verified"},
                ]}
    if path.endswith("/admin/pushok-core/readiness"):
        return {"source_pinned": True, "local_build_verified": None,
                "runtime_status": "not_checked", "changes_applied": False,
                "telegram_connection_verified": False, "sponsor_verified": False}
    if path.endswith("/me"):
        return {"id": DEMO_USER, "telegram_user_id": 123456, "role": role,
                "first_name": "Демо-пользователь", "last_name": "", "username": None}
    if path.endswith("/channels"):
        return [{"id": CHANNEL, "telegram_url": "https://t.me/example_channel",
                 "title": "Новости технологий"}]
    if path.endswith("/tariffs"):
        return [{"id": TARIFF, "name": "1 день", "price_kopecks": 30000,
                 "currency": "RUB", "duration_seconds": 86400}]
    if path.endswith("/operator/campaigns"):
        return [
            campaign("00000000-0000-4000-8000-000000000034",
                     "Новости технологий", "pending", "not_required", "not_scheduled"),
            campaign("00000000-0000-4000-8000-000000000035",
                     "Дизайн и вдохновение", "approved", "proof_submitted", "not_scheduled"),
        ]
    if path.endswith("/campaigns"):
        return [
            campaign("00000000-0000-4000-8000-000000000032",
                     "Новости технологий", "pending", "not_required", "not_scheduled"),
            campaign("00000000-0000-4000-8000-000000000033",
                     "Дизайн и вдохновение", "approved", "verified", "scheduled"),
        ]
    if path.endswith("/operator/proxy-nodes"):
        return [{"id": NODE, "name": "Pushok · тестовый узел", "host": "proxy.example.org",
                 "port": 8443, "status": "active", "target_engine": "official_mtproxy",
                 "engine_selection_mode": "auto", "deployment_profile": "native"}]
    if path.endswith("/availability"):
        base = datetime.now(timezone.utc) + timedelta(days=1)
        return {"days": [
            {"start_at": (base + timedelta(days=d)).isoformat(),
             "available_nodes": 0 if d in (2, 8) else (d % 3 + 1),
             "status": "occupied" if d in (2, 8) else "available"}
            for d in range(14)
        ]}
    raise AssertionError(f"Unknown preview-only API request: {path}")


class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self.path = "/index.html"
        elif path.startswith("/assets/"):
            self.path = path[len("/assets"):]
        super().do_GET()

    def log_message(self, *_):
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="MTProxy Pushok — private visual preview")
    parser.add_argument("--out", type=Path, default=ROOT / ".build" / "ui-preview")
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), StaticHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            def create_page(role: str, width: int, height: int):
                page = browser.new_page(viewport={"width": width, "height": height},
                                        device_scale_factor=1)
                if role != "guest":
                    page.add_init_script("sessionStorage.setItem('pushok-session','preview-only')")
                def route_api(route):
                    body = json.dumps(demo_response(route.request.url, role), ensure_ascii=False)
                    route.fulfill(status=200, content_type="application/json; charset=utf-8",
                                  body=body)
                page.route("**/api/v1/**", route_api)
                page.goto(url, wait_until="domcontentloaded")
                if role != "guest":
                    page.wait_for_selector("#workspace:not([hidden])", timeout=12000)
                    page.wait_for_selector("body[data-cabinet-loaded=true]", timeout=12000)
                page.evaluate("""() => {
                  const badge=document.createElement('div');
                  badge.textContent='ДЕМО · тестовые данные · не подключено к серверу';
                  badge.style.cssText='display:table;position:relative;max-width:calc(100% - 24px);'+
                   'margin:12px 12px 24px auto;background:#102437;color:#dcfff3;'+
                   'border:1px solid #64cdbb;padding:8px 12px;border-radius:10px;'+
                   'font:bold 11px system-ui;pointer-events:none';
                  document.body.append(badge);
                }""")
                return page
            try:
                landing = create_page("guest", 1440, 900)
                landing.screenshot(path=str(output / "01-landing.png"), full_page=True,
                                   animations="disabled")
                landing.close()

                account = create_page("admin", 1440, 1000)
                screens = [("02-dashboard", "dashboard"), ("03-channels", "channels"),
                           ("04-create", "create"), ("05-campaigns", "campaigns"),
                           ("06-detail", "campaign/00000000-0000-4000-8000-000000000032"),
                           ("07-payments", "payments"), ("08-calendar", "calendar"),
                           ("09-reports", "reports"), ("10-moderation", "moderation"),
                           ("11-sponsors", "sponsors"), ("12-settings", "settings"),
                           ("13-help", "help"), ("14-bot", "bot")]
                for name, route in screens:
                    account.evaluate("route => window.cabinet.go(route)", route)
                    account.wait_for_selector('[data-page="' + route.split('/')[0] + '"]:not([hidden])')
                    if route == "settings":
                        account.wait_for_function("document.querySelector('#settings-stars-counts').textContent.includes('Тарифов')")
                    account.screenshot(path=str(output / (name + ".png")), full_page=True,
                                       animations="disabled")
                account.close()
                mobile = create_page("advertiser", 390, 844)
                assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth + 2"), "Mobile overflow"
                mobile.screenshot(path=str(output / "15-mobile.png"), full_page=True,
                                  animations="disabled")
                mobile.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()

    generated = sorted(output.glob("*.png"))
    assert len(generated) == 15, f"Expected 15 previews, found {len(generated)}"
    for file in generated:
        assert file.stat().st_size > 8000, f"Screenshot too small: {file}"
        print(f"{file.name}: {file.stat().st_size} bytes")
    print("Все изображения — реальные HTML/CSS страницы с синтетическими данными.")


if __name__ == "__main__":
    main()
