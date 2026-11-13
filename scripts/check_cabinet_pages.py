#!/usr/bin/env python3
"""Chromium + real FastAPI + disposable PostgreSQL; no Telegram or payment calls.

Requires an explicitly approved TEST_ADMIN_DSN and PUSHOK_TEST_DB_ALLOWED=1.
Sessions are seeded only into a fresh test database; no login bypass is added to API.
"""
import argparse
import base64
import io
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "api"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / ".build/cabinet-pages")
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    # main.py captures this setting when imported, before test sessions are issued.
    os.environ["SESSION_SIGNING_KEY"] = "browser-test-only-" * 8
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

                def go(page, route, expected_page=None):
                    page.evaluate("route => window.cabinet.go(route)", route)
                    expect(page.locator('[data-page="' + (expected_page or route.split('/')[0]) + '"]')).to_be_visible()

                def check_navigation_icons(page):
                    # Проверяем внешний SVG через настоящий static route и отрисованный <use>.
                    import xml.etree.ElementTree as ET
                    response = page.request.get(base + '/assets/pushok-icons.svg')
                    assert response.status == 200, 'SVG sprite is not served'
                    assert 'image/svg+xml' in response.headers.get('content-type', '')
                    symbols = {node.get('id') for node in ET.fromstring(response.body()).iter()
                               if node.tag.endswith('symbol')}
                    menu = {'create': ('campaign', 'Разместить рекламу'), 'dashboard': ('home', 'Главная'), 'channels': ('channel', 'Мои каналы'),
                            'campaigns': ('campaign', 'Кампании'), 'payments': ('wallet', 'Оплата'),
                            'calendar': ('calendar', 'Календарь'), 'reports': ('chart', 'Отчёты'),
                            'moderation': ('moderation', 'Модерация'), 'sponsors': ('server', 'Узлы'),
                            'settings': ('settings', 'Настройки'), 'help': ('support', 'Помощь'),
                            'bot': ('channel', 'Telegram-бот')}
                    expect(page.locator('#cabinet-sidebar nav a')).to_have_count(len(menu))
                    for route, (icon, title) in menu.items():
                        assert 'pi-' + icon in symbols
                        link = page.locator('#cabinet-sidebar nav a[data-route="' + route + '"]')
                        expect(link).to_have_attribute('href', '#/' + route)
                        expect(link).to_have_text(title)
                        expect(link.locator('svg')).to_have_attribute('aria-hidden', 'true')
                        expect(link.locator('svg')).to_have_attribute('focusable', 'false')
                        expect(link.locator('use')).to_have_attribute('href', '/assets/pushok-icons.svg#pi-' + icon)
                        if link.is_visible():
                            expect(link).to_have_accessible_name(title)
                    page.wait_for_function("""() => {
                        const icons = [...document.querySelectorAll('#cabinet-sidebar nav svg')]
                          .filter(svg => svg.getBoundingClientRect().width > 0);
                        return icons.length > 0 && icons.every(svg => {
                          const box = svg.getBBox();
                          return box.width > 0 && box.height > 0;
                        });
                    }""")

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
                go(advertiser, "settings", expected_page="dashboard")
                expect(advertiser.locator('[data-page=dashboard]')).to_be_visible()
                assert advertiser.locator('nav a[data-route=settings]').is_hidden()
                forbidden = advertiser.request.get(base + '/api/v1/admin/payments/status',
                    headers={"Authorization": "Bearer " + sessions['advertiser']})
                assert forbidden.status == 403
                go(advertiser, "channels")
                advertiser.locator('[data-page=channels] .page-heading a').click()
                expect(advertiser.locator('#channel-form [name=title]')).to_be_focused()
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
                advertiser.locator('#order-date summary').click()
                advertiser.locator('#calendar-from').fill(tomorrow)
                advertiser.locator('#calendar-from').dispatch_event('change')
                expect(advertiser.locator('#availability-days button:enabled').first).to_be_visible()
                advertiser.locator('#availability-days button:enabled').first.click()
                advertiser.locator('#campaign-form button[type=submit]').click()
                expect(advertiser.locator('#order-result')).to_be_visible()
                campaign_id = advertiser.evaluate("location.hash.split('/')[2]")
                go(advertiser, 'campaign/'+campaign_id)
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
                advertiser.locator('[data-page=payments]').get_by_label('Подтверждение оплаты', exact=True).set_input_files(
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
                advertiser.locator('[data-page=payments]').get_by_label('Подтверждение оплаты', exact=True).set_input_files(
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
                go(advertiser, 'create')
                expect(advertiser.locator('[data-page=create] .metrics')).to_contain_text('450,55')

                outsider = new_page('outsider')
                go(outsider, 'campaign/' + campaign_id)
                expect(outsider.locator('[data-page=campaign]')).to_contain_text('недоступна')
                response = outsider.request.get(base + '/api/v1/campaigns/' + campaign_id + '/report',
                    headers={"Authorization": "Bearer " + sessions['outsider']})
                assert response.status == 404

                # Search is intentionally limited to campaign screens.
                go(advertiser, 'campaigns')
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

                # Настоящие HTTP/SQL; только транспорт эквайринга заменён фикстурой.
                from app import sbp_provider
                go(advertiser,'create')
                advertiser.locator('#channel-select').select_option(index=1)
                advertiser.evaluate('clearRequestedDate()')
                advertiser.locator('#campaign-form button[type=submit]').click()
                expect(advertiser.locator('#order-result')).to_be_visible()
                sbp_cid=advertiser.evaluate("location.hash.split('/')[2]")
                go(advertiser, 'campaign/'+sbp_cid)
                expect(advertiser.locator('[data-page=campaign] .meta')).to_contain_text('На модерации')
                refresh(operator)
                operator.get_by_role('button',name='Одобрить канал',exact=True).click()
                expect(operator.locator('#queue')).to_contain_text('Ожидается оплата')
                refresh(advertiser)
                go(advertiser,'payments/'+sbp_cid)
                expect(advertiser.locator('.sbp-checkout')).to_contain_text('Новые счета СБП выключены')
                bank_state={'status':'pending','paid':False}
                invoice_calls=[]
                def sbp_create(**kwargs):
                    invoice_calls.append(kwargs)
                    return {'payment_id':'sbp-e2e-'+sbp_cid,'confirmation_url':'https://pay.example/fixture','status':'pending'}
                def sbp_get(identifier):
                    return {'id':identifier,**bank_state,'test':True,'captured_at':'2026-11-11T12:00:00Z',
                            'amount':{'currency':'RUB','value':'450.55'},'metadata':{'campaign_id':sbp_cid},
                            'payment_method':{'type':'sbp'}}
                env={'PUSHOK_SBP_PAYMENTS_ENABLED':'1','PUSHOK_SBP_TEST_MODE':'1','PUSHOK_SBP_RECONCILIATION_ENABLED':'1',
                     'PUSHOK_PUBLIC_PAYMENT_HOST':'ads.example.org','YOOKASSA_SHOP_ID':'fixture-shop','YOOKASSA_SECRET_KEY':'fixture-secret'}
                with patch.dict(os.environ,env),patch.object(sbp_provider,'create_sbp_payment',side_effect=sbp_create),patch.object(sbp_provider,'get_sbp_payment',side_effect=sbp_get):
                    refresh(advertiser)
                    advertiser.get_by_role('button',name='Выставить счёт СБП',exact=True).click()
                    expect(advertiser.get_by_role('link',name='Перейти к оплате СБП',exact=True)).to_have_attribute('href','https://pay.example/fixture')
                    assert invoice_calls[0]['amount_kopecks']==45055
                    assert invoice_calls[0]['return_url']=='https://ads.example.org/?sbp_campaign='+sbp_cid
                    with advertiser.expect_response(lambda r:r.url.endswith('/sbp/reconcile')):
                        advertiser.goto(base+'/?sbp_campaign='+sbp_cid)
                    expect(advertiser.locator('.sbp-checkout')).to_contain_text('Ожидается результат платежа')
                    expect(advertiser.locator('[data-page=payments] .meta')).to_contain_text('Ожидается оплата')
                    with psycopg.connect(PostgresIntegration.dsn) as connection:
                        aid=connection.execute("UPDATE payment_attempts SET sbp_requires_review=true WHERE campaign_id=%s AND provider='sbp' RETURNING id",(sbp_cid,)).fetchone()[0]
                    go(admin,'settings')
                    admin.locator('#settings-payments-tab').click()
                    review=admin.locator('[data-attempt-id="'+str(aid)+'"]')
                    expect(review).to_contain_text('Ответ провайдера не прошёл проверку')
                    review.get_by_label('Причина повторной сверки',exact=True).fill('short')
                    review.get_by_role('button',name='Повторить серверную проверку',exact=True).click()
                    assert not review.locator('textarea').evaluate('(field)=>field.validity.valid')
                    review.get_by_label('Причина повторной сверки',exact=True).fill('Проверены настройки тестового магазина')
                    expect(admin.locator('#notice')).to_be_hidden()
                    admin.set_viewport_size({'width':320,'height':900})
                    assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'SBP review overflows mobile'
                    admin.set_viewport_size({'width':1440,'height':1000})
                    admin.evaluate('window.scrollTo(0,0)')
                    admin.screenshot(path=str(output/'16-sbp-review.png'),full_page=True)
                    review.get_by_role('button',name='Повторить серверную проверку',exact=True).click()
                    expect(admin.locator('#notice')).to_contain_text('Повтор поставлен в очередь')
                    with psycopg.connect(PostgresIntegration.dsn) as connection:
                        assert connection.execute("SELECT status,sbp_requires_review FROM payment_attempts WHERE id=%s",(aid,)).fetchone()==('pending',False)
                        assert connection.execute("SELECT count(*) FROM audit_events WHERE campaign_id=%s AND event_type='sbp_admin_retry_requested'",(sbp_cid,)).fetchone()[0]==1
                    bank_state.update(status='succeeded',paid=True)
                    advertiser.get_by_role('button',name='Проверить платёж СБП',exact=True).click()
                    expect(advertiser.locator('.sbp-checkout')).to_contain_text('Платёж подтверждён серверной сверкой')
                    expect(advertiser.locator('[data-page=payments] .meta')).to_contain_text('Оплата подтверждена')
                    advertiser.screenshot(path=str(output/'15-sbp-verified.png'),full_page=True)

                refresh(admin)
                check_navigation_icons(advertiser)
                check_navigation_icons(admin)
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
                    page.evaluate("window.scrollTo({top:0,left:0,behavior:'instant'})")
                    page.wait_for_function('window.scrollY === 0')
                    page.screenshot(path=str(output / (name + '.png')), full_page=True)
                for width in (320, 390, 768):
                    admin.set_viewport_size({"width": width, "height": 844})
                    for _, route in screens:
                        go(admin, route)
                        assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), (width, route)
                    if width in (320, 390):
                        go(admin, 'dashboard')
                        expect(admin.locator('#cabinet-sidebar')).to_have_attribute('inert', '')
                        admin.locator('.cabinet-topbar .menu-toggle').click()
                        expect(admin.locator('#cabinet-sidebar')).to_have_class('cabinet-sidebar is-open')
                        check_navigation_icons(admin)
                        assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
                        admin.screenshot(path=str(output / f'20-mobile-menu-{width}.png'), full_page=True)
                        admin.locator('#cabinet-sidebar nav a[data-route="channels"]').click()
                        expect(admin.locator('[data-page="channels"]')).to_be_visible()
                        expect(admin.locator('#cabinet-sidebar')).to_have_attribute('inert', '')
                        admin.locator('.cabinet-topbar .menu-toggle').click()
                        admin.keyboard.press('Escape')
                        expect(admin.locator('#cabinet-sidebar')).to_have_attribute('inert', '')
                        admin.screenshot(path=str(output / '14-mobile.png'), full_page=True)
                # Настоящая серверная авторизация; подменяется только оболочка SDK Telegram.
                from test_miniapp_auth import signed_data
                mini_requests=[]
                def mini_page(raw):
                    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
                    context.add_init_script("sessionStorage.setItem('pushok-session', "+repr(sessions['outsider'])+");")
                    context.add_init_script("if(!sessionStorage.getItem('mini-fixture-seeded')){sessionStorage.setItem('pushok-miniapp-session', "+repr(sessions['outsider'])+");sessionStorage.setItem('pushok-miniapp-init-hash','old-account-hash');sessionStorage.setItem('mini-fixture-seeded','1');}")
                    context.add_init_script("""window.__miniEvents={};window.__miniBackVisible=false;
                        window.Telegram={WebApp:{initData:RAW_DATA,initDataUnsafe:{user:{id:999,role:'admin'}},
                        colorScheme:'light',safeAreaInset:{top:18,bottom:24},contentSafeAreaInset:{top:12,bottom:16},
                        ready(){window.__miniReady=true},expand(){window.__miniExpanded=true},
                        onEvent(name,fn){window.__miniEvents[name]=fn},openTelegramLink(url){window.__miniLink=url},
                        openInvoice(url,callback){window.__miniInvoiceUrl=url;window.__miniInvoiceCallback=callback},
                        BackButton:{show(){window.__miniBackVisible=true},hide(){window.__miniBackVisible=false},onClick(fn){window.__miniBack=fn}}}};
                    """.replace('RAW_DATA',json.dumps(raw)))
                    page=context.new_page();page.on('pageerror',lambda error:failures.append(str(error)))
                    page.on('request',lambda request:mini_requests.append((request.method,request.url)))
                    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                    page.goto(base+'/miniapp');return page
                with patch.dict(os.environ,{'PUSHOK_MINIAPP_ENABLED':'1','TELEGRAM_BOT_USERNAME':'mtproxy_pushok_bot'}),patch.object(api,'TELEGRAM_BOT_TOKEN','fixture-token'):
                    raw=signed_data(user=json.dumps({'id':99116000,'first_name':'Mini App тест'}),query_id='mini-client-first')
                    mini=mini_page(raw)
                    expect(mini.locator('body')).to_have_class('miniapp-mode cabinet-mode')
                    expect(mini.locator('.cabinet-account')).to_contain_text('Mini App тест')
                    assert mini.evaluate('window.__miniReady&&window.__miniExpanded')
                    assert not mini.evaluate('window.__miniBackVisible')
                    go(mini,'channels')
                    assert mini.evaluate('window.__miniBackVisible')
                    mini.locator('.cabinet-topbar .menu-toggle').click()
                    mini.evaluate('window.__miniBack()')
                    expect(mini.locator('#cabinet-sidebar')).to_have_attribute('inert','')
                    mini.evaluate('window.__miniBack()')
                    expect(mini).to_have_url(base+'/miniapp#/create')
                    mini.reload()
                    expect(mini.locator('body')).to_have_class('miniapp-mode cabinet-mode')
                    assert len([x for x in mini_requests if x[0]=='POST' and x[1].endswith('/auth/telegram/miniapp')])==1
                    go(mini,'payments/'+sbp_cid)
                    expect(mini.locator('.sbp-checkout')).to_contain_text('Оплата Telegram Stars')
                    expect(mini.get_by_role('button',name='Реквизиты и сумма',exact=True)).to_have_count(0)
                    expect(mini.get_by_role('button',name='Загрузить чек',exact=True)).to_have_count(0)
                    assert not any('/sbp' in url or '/payment-instructions' in url or '/payment-proofs' in url for _,url in mini_requests)
                    mini.get_by_role('link',name='Открыть @mtproxy_pushok_bot',exact=True).click()
                    assert mini.evaluate('window.__miniLink')=='https://t.me/mtproxy_pushok_bot'
                    go(mini,'create')
                    mini.locator('#order-channel-mode').click()
                    mini.locator('#order-channel-url').fill('@pushok_onepage')
                    mini.locator('#order-channel-title').fill('Канал из одной формы')
                    expect(mini.locator('#order-summary')).to_contain_text('Канал из одной формы')
                    mini.screenshot(path=str(output/'27-onepage-form.png'),full_page=True)
                    lost=[]
                    def lose_order_response(route):
                        response=route.fetch()
                        assert response.status == 201, response.text()
                        lost.append(response.json()['id'])
                        route.abort()
                    mini.route('**/api/v1/orders',lose_order_response)
                    mini.locator('#order-submit').click()
                    expect(mini.locator('#order-submit')).to_have_text('Проверить отправку')
                    mini.unroute('**/api/v1/orders',lose_order_response)
                    mini.reload()
                    expect(mini.locator('#order-submit')).to_have_text('Проверить отправку')
                    mini.locator('#order-submit').click()
                    expect(mini.locator('#order-result')).to_contain_text('Заявка на проверке')
                    assert mini.evaluate("location.hash.split('/')[2]")==lost[0]
                    with psycopg.connect(PostgresIntegration.dsn) as connection:
                        assert connection.execute("SELECT count(*) FROM campaigns c JOIN sponsor_channels ch ON ch.id=c.channel_id WHERE ch.telegram_url='https://t.me/pushok_onepage'").fetchone()[0]==1
                    mini.screenshot(path=str(output/'28-onepage-submitted.png'),full_page=True)
                    # Используем эту заявку для проверки Stars на той же странице.
                    stars_cid=mini.evaluate("location.hash.split('/')[2]")
                    with psycopg.connect(PostgresIntegration.dsn) as connection:
                        connection.execute("UPDATE campaigns SET moderation_status='approved',payment_status='awaiting_payment' WHERE id=%s",(stars_cid,))
                    refresh(mini);go(mini,'create/'+stars_cid)
                    expect(mini.locator('.sbp-checkout')).to_contain_text('Оплата Stars выключена')
                    from app import stars_checkout
                    from app.bot_stars import parse_attempt
                    prepared=[]
                    def invoice_link(token,invoice):
                        prepared.append(invoice);return 'https://t.me/$stars-fixture'
                    with patch.dict(os.environ,{'PUSHOK_STARS_PAYMENTS_ENABLED':'1','TELEGRAM_BOT_TOKEN':'12345:fixture-secret',
                                               'BOT_INTERNAL_SECRET':'b'*64,'DATABASE_URL':PostgresIntegration.dsn}), \
                            patch.object(stars_checkout,'create_invoice_link',side_effect=invoice_link):
                        refresh(mini)
                        expect(mini.locator('.sbp-checkout')).to_contain_text('200 XTR')
                        mini.get_by_role('button',name='Оплатить Stars',exact=True).click()
                        mini.wait_for_function('typeof window.__miniInvoiceCallback==="function"')
                        assert mini.evaluate('window.__miniInvoiceUrl')=='https://t.me/$stars-fixture'
                        mini.evaluate("window.__miniInvoiceCallback('cancelled');window.__miniInvoiceCallback=null")
                        expect(mini.get_by_role('button',name='Оплатить Stars',exact=True)).to_be_enabled()
                        mini.get_by_role('button',name='Оплатить Stars',exact=True).click()
                        mini.wait_for_function('typeof window.__miniInvoiceCallback==="function"')
                        mini.evaluate("window.__miniInvoiceCallback('paid');window.__miniInvoiceCallback=null")
                        expect(mini.get_by_role('button',name='Оплатить Stars',exact=True)).to_be_enabled()
                        expect(mini.locator('.sbp-checkout')).not_to_contain_text('Оплата подтверждена сервером')
                        assert len(prepared)==1 and prepared[0].stars_amount==200
                        with psycopg.connect(PostgresIntegration.dsn) as connection:
                            assert connection.execute('SELECT payment_status FROM campaigns WHERE id=%s',(stars_cid,)).fetchone()[0]=='awaiting_payment'
                        headers={'X-Pushok-Bot-Secret':'b'*64}
                        pc={'query_id':'mini-browser-checkout','telegram_user_id':99116000,
                            'invoice_payload':prepared[0].invoice_payload,'currency':'XTR','total_amount':200}
                        response=PostgresIntegration.client.post('/api/v1/internal/bot/stars/precheckout',json=pc,headers=headers)
                        assert response.status_code==200 and response.json()['ok']
                        receipt={k:v for k,v in pc.items() if k!='query_id'}|{'telegram_payment_charge_id':'mini-browser-charge'}
                        response=PostgresIntegration.client.post('/api/v1/internal/bot/stars/settle',json=receipt,headers=headers)
                        assert response.status_code==200 and response.json()['status']=='applied'
                        mini.get_by_role('button',name='Обновить статус оплаты',exact=True).click()
                        expect(mini.locator('.sbp-checkout')).to_contain_text('Оплата подтверждена сервером')
                        mini.screenshot(path=str(output/'19-miniapp-stars.png'),full_page=True)
                    for width in (320,390):
                        mini.set_viewport_size({'width':width,'height':844})
                        assert mini.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                    go(mini,'dashboard');mini.screenshot(path=str(output/'17-miniapp-light.png'),full_page=True)
                    mini.locator('.cabinet-topbar .menu-toggle').click()
                    check_navigation_icons(mini)
                    mini.screenshot(path=str(output/'21-miniapp-menu-light.png'),full_page=True)
                    mini.keyboard.press('Escape')
                    mini.evaluate("window.Telegram.WebApp.colorScheme='dark';window.__miniEvents.themeChanged()")
                    expect(mini.locator('body')).to_have_attribute('data-miniapp-theme','dark')
                    assert mini.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--mini-safe-top").trim()')=='18px'
                    mini.screenshot(path=str(output/'18-miniapp-dark.png'),full_page=True)
                    mini.locator('.cabinet-topbar .menu-toggle').click()
                    check_navigation_icons(mini)
                    mini.screenshot(path=str(output/'22-miniapp-menu-dark.png'),full_page=True)
                    mini.keyboard.press('Escape')
                    mini_token=mini.evaluate("sessionStorage.getItem('pushok-miniapp-session')")
                    _,mini_sid,_=api._decode_session_token(mini_token)
                    with psycopg.connect(PostgresIntegration.dsn) as connection:
                        connection.execute('UPDATE user_sessions SET revoked_at=now() WHERE id=%s',(mini_sid,))
                    mini.locator('#refresh').click()
                    expect(mini.locator('#miniapp-login')).to_contain_text('Сессия завершена')
                    expect(mini.locator('#workspace')).to_be_hidden()
                    assert mini.evaluate("sessionStorage.getItem('pushok-miniapp-session')")==None
                    invalid=mini_page(raw+'tampered')
                    expect(invalid.locator('#miniapp-login')).to_contain_text('Вход не завершён')
                    expect(invalid.locator('#workspace')).to_be_hidden()
                    outside=mini_page('')
                    expect(outside.locator('#miniapp-login')).to_contain_text('Откройте Mini App из меню')
                    expect(outside.locator('#workspace')).to_be_hidden()
                    other=mini_page(signed_data(user=json.dumps({'id':99116000,'first_name':'Mini App тест'}),query_id='mini-client-logout'))
                    expect(other.locator('body')).to_have_class('miniapp-mode cabinet-mode')
                    other.locator('.cabinet-topbar .menu-toggle').click()
                    other.locator('#logout').click()
                    expect(other.locator('#miniapp-login')).to_contain_text('Вы вышли')
                    other.reload()
                    expect(other.locator('#miniapp-login')).to_contain_text('Вы вышли')
                    expect(other.locator('#workspace')).to_be_hidden()
                    disabled_raw=signed_data(query_id='mini-disabled')
                    with patch.dict(os.environ,{'PUSHOK_MINIAPP_ENABLED':'0'}):
                        disabled=mini_page(disabled_raw)
                        expect(disabled.locator('#miniapp-login')).to_contain_text('пока не включён')
                        expect(disabled.locator('#workspace')).to_be_hidden()
                # Служебный тариф виден только администратору и остаётся бесплатным.
                private_tariff = '7f5b60e4-ecf1-4bc3-9e3a-cd5153f4a001'
                go(admin, 'create')
                admin.locator('#tariff-select').select_option(private_tariff)
                expect(admin.locator('#tariff-access-hint')).to_be_visible()
                expect(advertiser.locator('#tariff-select option[value="'+private_tariff+'"]')).to_have_count(0)
                # v0.051: compact settings, free tariff, and payment control form.
                go(admin, 'settings')
                expect(admin.get_by_label('Поиск кампаний', exact=True)).to_be_hidden()
                admin.locator('#settings-tariffs-tab').click()
                admin.locator('#tariff-admin-select').select_option(private_tariff)
                expect(admin.locator('#tariff-admin-access')).to_be_visible()
                expect(admin.locator('#tariff-admin-rub')).to_have_attribute('readonly','')
                expect(admin.locator('#tariff-admin-stars')).to_have_attribute('readonly','')
                expect(admin.locator('#tariff-admin-stars')).to_have_attribute('placeholder','Не требуется')
                admin.locator('#tariff-admin-hours').fill('48')
                with admin.expect_response(lambda r:'/admin/tariffs/' in r.url and r.request.method=='PUT') as private_saved:
                    admin.locator('#tariff-admin-form button').click()
                assert private_saved.value.status==200 and private_saved.value.json()['admin_only'] is True
                assert private_saved.value.json()['price_kopecks']==0 and private_saved.value.json()['duration_seconds']==172800
                expect(admin.locator('#tariff-admin-hours')).to_have_value('48')
                expect(admin.locator('#tariff-admin-form button')).to_be_enabled()
                admin.set_viewport_size({'width':390,'height':844})
                admin.screenshot(path=str(output/'26-admin-free-tariff-mobile.png'), full_page=True)
                admin.locator('#tariff-admin-select').select_option('00000000-0000-4000-8000-000000000001')
                admin.locator('#tariff-admin-rub').fill('0')
                admin.locator('#tariff-admin-stars').fill('0')
                with admin.expect_response(lambda r:'/admin/tariffs/' in r.url and r.request.method=='PUT') as saved:
                    admin.locator('#tariff-admin-form button').click()
                assert saved.value.status==200 and saved.value.json()['price_kopecks']==0
                expect(admin.locator('#notice')).to_contain_text('Настройки тарифа сохранены')
                expect(admin.locator('#tariff-admin-rub')).to_have_value('0.00')
                toggle = admin.locator('#tariff-admin-active').bounding_box()
                assert toggle['width'] <= 48 and toggle['height'] <= 32, toggle
                admin.set_viewport_size({'width':390,'height':844})
                admin.screenshot(path=str(output/'23-settings-free-mobile.png'), full_page=True)
                assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth')
                admin.locator('#settings-payments-tab').click()
                with patch.dict(os.environ, {'PUSHOK_CONTROL_DIR':str(Path(PostgresIntegration.storage.name)/'control'),
                                           'TELEGRAM_BOT_TOKEN':'123456:fixture_token_for_browser'}):
                    admin.locator('#enable-stars').check()
                    admin.locator('#payment-switches button').click()
                    expect(admin.locator('#notice')).to_contain_text('Настройки оплаты сохранены')
                    expect(admin.locator('#enable-stars')).to_be_checked()
                    admin.screenshot(path=str(output/'24-payment-controls-mobile.png'), full_page=True)
                    # Каталог искусственный: проверяем выбор настоящей картинки в форме без Telegram.
                    icon_bytes=io.BytesIO();Image.new('RGBA',(48,48),'orange').save(icon_bytes,format='PNG')
                    catalog=[{'id':'123456789','emoji':'🐱','image':'data:image/png;base64,'+base64.b64encode(icon_bytes.getvalue()).decode()}]
                    api.control.write('emoji-catalog.json',catalog)
                    admin.locator('#settings-system-tab').click()
                    with patch.object(api.emoji_settings,'fetch',return_value=catalog):
                        admin.locator('#icons-import').click()
                        expect(admin.locator('.emoji-gallery img')).to_have_count(1)
                        admin.locator('#icons-mapping select[name=help]').select_option('123456789')
                        admin.locator('#icons-form button[type=submit]').click()
                        expect(admin.locator('#icons-status')).to_contain_text('Сохранено')
                        assert api.control.read('emoji-map.json')['help']=='123456789'
                        admin.screenshot(path=str(output/'25-icon-settings-mobile.png'),full_page=True)
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
