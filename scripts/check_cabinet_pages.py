#!/usr/bin/env python3
"""Chromium + real FastAPI + disposable PostgreSQL; no Telegram or payment calls.

Requires an explicitly approved TEST_ADMIN_DSN and PUSHOK_TEST_DB_ALLOWED=1.
Sessions are seeded only into a fresh test database; no login bypass is added to API.
"""
import argparse
import base64
import io
import os
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "api"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / ".build/cabinet-pages")
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    from test_postgres import PostgresIntegration, _approved_test_postgres
    if not _approved_test_postgres():
        parser.error("Requires an explicitly approved local test PostgreSQL")
    import psycopg
    import uvicorn
    from PIL import Image
    from playwright.sync_api import sync_playwright, expect
    from app import main as api

    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # These values belong only to the local disposable test server.
    os.environ["SESSION_SIGNING_KEY"] = "browser-test-only-" * 8
    os.environ["PAYMENT_INSTRUCTIONS_B64"] = base64.b64encode(
        "ТЕСТОВАЯ ИНСТРУКЦИЯ. Не переводите деньги.".encode()).decode()
    os.environ.pop("TELEGRAM_BOT_USERNAME", None)
    os.environ["PUSHOK_STARS_PAYMENTS_ENABLED"] = "0"
    PostgresIntegration.setUpClass()
    server = None
    thread = None
    sock = None
    failures = []
    try:
        sessions = {}
        session_ids = {}
        with psycopg.connect(PostgresIntegration.dsn) as connection:
            for i, role in enumerate(("advertiser", "operator", "admin", "outsider")):
                user_id, session_id = uuid4(), uuid4()
                connection.execute(
                    "INSERT INTO users(id,telegram_user_id,role,first_name) VALUES (%s,%s,%s,%s)",
                    (user_id, 99116000 + i, "advertiser" if role == "outsider" else role,
                     "Тест " + role))
                connection.execute(
                    "INSERT INTO user_sessions(id,user_id,expires_at) VALUES (%s,%s,now()+interval '1 hour')",
                    (session_id, user_id))
                sessions[role] = api._create_session_token(user_id, session_id)
                session_ids[role] = session_id
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(api.app, log_level="warning"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started, "Test API failed to start"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"],
                                        executable_path=args.browser_executable)
            try:
                def new_page(role, width=1440):
                    context = browser.new_context(viewport={"width": width, "height": 1000},
                                                  accept_downloads=True)
                    context.add_init_script(
                        "sessionStorage.setItem('pushok-session', " + repr(sessions[role]) + ")")
                    page = context.new_page()
                    page.on("pageerror", lambda error: failures.append(str(error)))
                    # Never allow external requests, including Telegram and acquiring.
                    page.route("**/*", lambda route: route.continue_()
                               if route.request.url.startswith(base + "/") else route.abort())
                    page.goto(base)
                    expect(page.locator("body")).to_have_class("cabinet-mode")
                    expect(page.locator("body")).to_have_attribute("data-cabinet-loaded", "true")
                    page.evaluate("""() => {
                        const badge=document.createElement('div');
                        badge.textContent='ТЕСТ · изолированная база · без реальных оплат и Telegram';
                        badge.style.cssText='position:fixed;top:0;right:0;max-width:90vw;z-index:100;'+
                          'background:#0b243a;color:white;padding:3px 8px;font:10px system-ui;pointer-events:none';
                        document.body.append(badge);
                    }""")
                    return page

                def go(page, route):
                    page.evaluate("route => window.cabinet.go(route)", route)
                    expect(page.locator('[data-page="' + route.split('/')[0] + '"]')).to_be_visible()

                def refresh(page):
                    with page.expect_response(lambda r: r.url.endswith('/campaigns') and r.request.method == 'GET'):
                        page.locator('#refresh').click()
                    expect(page.locator('#refresh')).to_be_enabled()

                admin = new_page("admin")
                go(admin, "sponsors")
                admin.locator('#node-form [name=name]').fill('Тестовый узел')
                admin.locator('#node-form [name=host]').fill('node.example.org')
                admin.locator('#engine-preview').click()
                expect(admin.locator('#engine-advice')).to_contain_text('План:')
                admin.locator('#node-form button[type=submit]').click()
                expect(admin.locator('#nodes-list')).to_contain_text('Тестовый узел')
                admin.get_by_role('button', name='Поставить на паузу', exact=True).click()
                expect(admin.get_by_role('button', name='Возобновить', exact=True)).to_be_visible()
                admin.get_by_role('button', name='Возобновить', exact=True).click()
                expect(admin.get_by_role('button', name='Поставить на паузу', exact=True)).to_be_visible()
                admin.locator('#engine-mode').select_option('manual')
                expect(admin.locator('#preferred-engine')).to_be_visible()
                admin.locator('#preferred-engine').select_option('official_mtproxy')
                admin.once('dialog', lambda dialog: dialog.accept())
                admin.get_by_role('button', name='Изменить план движка', exact=True).click()
                expect(admin.locator('#notice')).to_contain_text('План сохранён')
                admin.get_by_label('ad_tag из MTProxyBot', exact=True).fill('a' * 32)
                admin.locator('#sponsor-form button[type=submit]').click()
                expect(admin.locator('[data-page=sponsors]')).to_contain_text('окончанием aaaa')
                assert admin.get_by_label('ad_tag из MTProxyBot', exact=True).input_value() == ''

                advertiser = new_page("advertiser")
                expect(advertiser.locator('[data-page=dashboard]')).to_contain_text('Пока нет данных')
                go(advertiser, "settings")
                expect(advertiser.locator('[data-page=dashboard]')).to_be_visible()
                assert advertiser.locator('nav a[data-route=settings]').is_hidden()
                forbidden = advertiser.request.get(base + '/api/v1/admin/payments/status',
                    headers={"Authorization": "Bearer " + sessions['advertiser']})
                assert forbidden.status == 403
                go(advertiser, "channels")
                advertiser.locator('#channel-form [name=title]').fill('Тестовый канал')
                advertiser.locator('#channel-form [name=telegram_url]').fill('https://example.org/not-telegram')
                advertiser.locator('#channel-form button').click()
                expect(advertiser.locator('#notice')).to_contain_text('https://t.me/channel')
                advertiser.locator('#channel-form [name=telegram_url]').fill('https://t.me/pushok_browser_test')
                advertiser.locator('#channel-form button').click()
                expect(advertiser.locator('[data-page=channels] table')).to_contain_text('Тестовый канал')
                advertiser.locator('#channel-form [name=title]').fill('Дубликат')
                advertiser.locator('#channel-form [name=telegram_url]').fill('https://t.me/pushok_browser_test')
                advertiser.locator('#channel-form button').click()
                expect(advertiser.locator('#notice')).to_contain_text('уже добавлен')

                go(advertiser, "create")
                advertiser.locator('#channel-select').select_option(index=1)
                tomorrow = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
                advertiser.locator('#calendar-from').fill(tomorrow)
                advertiser.locator('#calendar-from').dispatch_event('change')
                expect(advertiser.locator('#availability-days button:enabled').first).to_be_visible()
                advertiser.locator('#availability-days button:enabled').first.click()
                advertiser.locator('#campaign-form button[type=submit]').click()
                expect(advertiser.locator('[data-page=campaign]')).to_be_visible()
                campaign_id = advertiser.evaluate("location.hash.split('/')[2]")
                advertiser.get_by_role('button', name='Отправить на модерацию', exact=True).click()
                expect(advertiser.locator('[data-page=campaign] .meta')).to_contain_text('На модерации')

                operator = new_page('operator')
                go(operator, 'moderation')
                operator.once('dialog', lambda dialog: dialog.accept('Уточните название канала'))
                operator.get_by_role('button', name='Запросить исправления', exact=True).click()
                expect(operator.locator('#queue')).to_contain_text('Нужны исправления')
                refresh(advertiser)
                expect(advertiser.locator('[data-page=campaign]')).to_contain_text('Уточните название канала')
                advertiser.get_by_role('button', name='Исправить канал', exact=True).click()
                advertiser.locator('.correction-form input').first.fill('Исправленный канал')
                advertiser.get_by_role('button', name='Сохранить исправления', exact=True).click()
                expect(advertiser.locator('.correction-form')).to_have_count(0)
                advertiser.get_by_role('button', name='Отправить повторно', exact=True).click()
                expect(advertiser.locator('.meta')).to_contain_text('На модерации')
                refresh(operator)
                operator.get_by_role('button', name='Одобрить канал', exact=True).click()
                expect(operator.locator('#queue')).to_contain_text('Ожидается оплата')
                refresh(advertiser)
                go(advertiser, 'payments/' + campaign_id)
                advertiser.get_by_role('button', name='Реквизиты и сумма', exact=True).click()
                expect(advertiser.locator('.payment-instructions')).to_contain_text('ТЕСТОВАЯ ИНСТРУКЦИЯ')
                advertiser.get_by_role('button', name='Бронь на 15 минут', exact=True).click()
                advertiser.get_by_role('button', name='Найти свободные прокси', exact=True).click()
                expect(advertiser.get_by_label('Свободный прокси', exact=True)).to_be_enabled()
                advertiser.get_by_label('Свободный прокси', exact=True).select_option(index=1)
                advertiser.get_by_role('button', name='Забронировать на 15 минут', exact=True).click()
                expect(advertiser.locator('.hold-timer')).to_contain_text('До освобождения слота')
                advertiser.once('dialog', lambda dialog: dialog.accept())
                advertiser.get_by_role('button', name='Отменить бронь', exact=True).click()
                expect(advertiser.get_by_role('button', name='Найти свободные прокси', exact=True)).to_be_visible()

                image_bytes = io.BytesIO()
                Image.new('RGB', (64, 64), 'teal').save(image_bytes, format='PNG')
                advertiser.get_by_label('Подтверждение оплаты', exact=True).set_input_files(
                    {"name": "test-receipt.png", "mimeType": "image/png", "buffer": image_bytes.getvalue()})
                advertiser.get_by_role('button', name='Загрузить чек', exact=True).click()
                expect(advertiser.locator('[data-page=payments]')).to_contain_text('Чек на проверке')
                refresh(operator)
                operator.get_by_role('button', name='Посмотреть чеки', exact=True).click()
                with operator.expect_download() as receipt:
                    operator.get_by_role('button', name='test-receipt.png', exact=True).click()
                assert Path(receipt.value.path()).read_bytes() == image_bytes.getvalue()
                operator.once('dialog', lambda dialog: dialog.accept('Нужен читаемый чек'))
                operator.get_by_role('button', name='Отклонить чек', exact=True).click()
                expect(operator.locator('#queue')).to_contain_text('Нужен читаемый чек')
                refresh(advertiser)
                advertiser.get_by_label('Подтверждение оплаты', exact=True).set_input_files(
                    {"name": "test-receipt-again.png", "mimeType": "image/png", "buffer": image_bytes.getvalue()})
                advertiser.get_by_role('button', name='Загрузить чек', exact=True).click()
                expect(advertiser.locator('[data-page=payments]')).to_contain_text('Чек на проверке')
                refresh(operator)
                operator.get_by_role('button', name='Подтвердить оплату', exact=True).click()
                expect(operator.locator('#queue')).to_contain_text('Оплата подтверждена')
                operator.get_by_label('Прокси-узел', exact=True).select_option(index=1)
                operator.get_by_label('Начало размещения', exact=True).fill(tomorrow + 'T12:00')
                operator.get_by_role('button', name='Назначить слот', exact=True).click()
                expect(operator.locator('#queue')).to_contain_text('Запланировано')

                # Move the isolated fixture into its active window; no VPS is used.
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    connection.execute("UPDATE campaigns SET scheduled_start_at=now()-interval '1 minute', scheduled_end_at=now()+interval '1 hour' WHERE id=%s", (campaign_id,))
                operator.once('dialog', lambda dialog: dialog.accept('Тест: спонсор не найден в отдельном клиенте'))
                operator.get_by_role('button', name='Показ не обнаружен', exact=True).click()
                expect(operator.locator('#notice')).to_contain_text('Отрицательное наблюдение')
                operator.once('dialog', lambda dialog: dialog.accept())
                operator.get_by_role('button', name='Активировать после проверки', exact=True).click()
                expect(operator.locator('#notice')).to_contain_text('Активация доступна')
                operator.get_by_label('Скриншот ручной проверки (необязательно)', exact=True).set_input_files(
                    {"name": "test-display.png", "mimeType": "image/png", "buffer": image_bytes.getvalue()})
                operator.once('dialog', lambda dialog: dialog.accept('Тестовое наблюдение в отдельном клиенте'))
                operator.get_by_role('button', name='Показ виден — записать', exact=True).click()
                expect(operator.locator('#notice')).to_contain_text('Отчёт оператора сохранён')
                operator.get_by_role('button', name='Журнал проверок', exact=True).click()
                expect(operator.locator('.observation-panel')).to_contain_text('Показ виден')
                operator.once('dialog', lambda dialog: dialog.accept())
                operator.get_by_role('button', name='Активировать после проверки', exact=True).click()
                expect(operator.locator('#queue')).to_contain_text('Размещение активно')
                operator.once('dialog', lambda dialog: dialog.accept())
                operator.get_by_role('button', name='Тег выключен — завершить', exact=True).click()
                expect(operator.locator('#queue')).to_contain_text('Завершено')
                refresh(advertiser)
                go(advertiser, 'reports')
                advertiser.get_by_role('button', name='Открыть отчёт', exact=True).click()
                expect(advertiser.locator('.campaign-report')).to_contain_text('История действий')
                with advertiser.expect_download() as csv:
                    advertiser.locator('.campaign-report').get_by_role('button', name='Скачать CSV', exact=True).click()
                assert campaign_id in Path(csv.value.path()).read_text(encoding='utf-8-sig')

                go(admin, 'settings')
                admin.locator('#settings-tariffs-tab').click()
                expect(admin.locator('#tariff-admin-name')).to_have_value('1 день')
                admin.locator('#tariff-admin-rub').fill('450.55')
                admin.locator('#tariff-admin-stars').fill('200')
                admin.locator('#tariff-admin-form button').click()
                expect(admin.locator('#notice')).to_contain_text('Настройки тарифа сохранены')
                refresh(advertiser)
                go(advertiser, 'campaign/' + campaign_id)
                expect(advertiser.locator('[data-page=campaign] .meta')).to_contain_text('300')
                assert '450' not in advertiser.locator('[data-page=campaign] .meta').inner_text()

                outsider = new_page('outsider')
                go(outsider, 'campaign/' + campaign_id)
                expect(outsider.locator('[data-page=campaign]')).to_contain_text('недоступна')
                response = outsider.request.get(base + '/api/v1/campaigns/' + campaign_id + '/report',
                    headers={"Authorization": "Bearer " + sessions['outsider']})
                assert response.status == 404

                # Search, Back/Forward and help remain usable after mutations.
                advertiser.get_by_label('Поиск кампаний', exact=True).fill('несуществующий канал')
                advertiser.get_by_label('Поиск кампаний', exact=True).press('Enter')
                expect(advertiser.locator('#campaigns')).to_contain_text('Поиск не дал результатов')
                advertiser.get_by_role('button', name='Сбросить поиск и фильтр', exact=True).click()
                expect(advertiser.locator('#campaigns')).to_contain_text('Исправленный канал')
                go(advertiser, 'help')
                advertiser.locator('[data-page=help] summary').first.click()
                expect(advertiser.locator('[data-page=help] details').first).to_have_attribute('open', '')
                advertiser.go_back()
                expect(advertiser.locator('[data-page=campaigns]')).to_be_visible()

                refresh(admin)
                screens = [('01-dashboard', 'dashboard'), ('02-channels', 'channels'),
                    ('03-create', 'create'), ('04-campaigns', 'campaigns'),
                    ('05-detail', 'campaign/' + campaign_id), ('06-payment', 'payments/' + campaign_id),
                    ('07-calendar', 'calendar'), ('08-reports', 'reports'),
                    ('09-moderation', 'moderation'), ('10-sponsors', 'sponsors'),
                    ('11-settings', 'settings'), ('12-help', 'help'), ('13-bot', 'bot')]
                # The admin owns no advertiser campaigns: capture own detail/payment as advertiser.
                for name, route in screens:
                    page = admin if route.split('/')[0] in ('moderation', 'sponsors', 'settings') else advertiser
                    go(page, route)
                    if route == 'settings':
                        expect(page.locator('#settings-stars-counts')).to_contain_text('Тарифов')
                        page.locator('#settings-payments-tab').click()
                    page.screenshot(path=str(output / (name + '.png')), full_page=True)
                for width in (320, 390, 768):
                    admin.set_viewport_size({"width": width, "height": 844})
                    for _, route in screens:
                        go(admin, route)
                        assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), (width, route)
                    if width == 390:
                        go(admin, 'dashboard')
                        expect(admin.locator('#cabinet-sidebar')).to_have_attribute('inert', '')
                        admin.locator('.cabinet-topbar .menu-toggle').click()
                        expect(admin.locator('#cabinet-sidebar')).to_have_class('cabinet-sidebar is-open')
                        admin.keyboard.press('Escape')
                        expect(admin.locator('#cabinet-sidebar')).to_have_attribute('inert', '')
                        admin.screenshot(path=str(output / '14-mobile.png'), full_page=True)
                assert not failures, failures
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    connection.execute('UPDATE user_sessions SET revoked_at=now() WHERE id=%s', (session_ids['advertiser'],))
                advertiser.locator('#refresh').click()
                expect(advertiser.locator('#login')).to_be_visible()
                expect(advertiser.locator('#workspace')).to_be_hidden()
                print('PASS: real API/SQL forms, roles, lifecycle, upload/download, reports, routing and mobile widths')
            except Exception:
                for page in browser.contexts:
                    for tab in page.pages:
                        if not tab.is_closed():
                            tab.screenshot(path=str(output / ('failure-' + str(id(tab)) + '.png')), full_page=True)
                raise
            finally:
                browser.close()
    finally:
        if server:
            server.should_exit = True
        if thread:
            thread.join(timeout=10)
        if sock:
            sock.close()
        PostgresIntegration.tearDownClass()


if __name__ == '__main__':
    main()
