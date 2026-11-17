#!/usr/bin/env python3
"""Chromium + real FastAPI + disposable PostgreSQL; no Telegram or payment calls.

Requires an explicitly approved TEST_ADMIN_DSN and PUSHOK_TEST_DB_ALLOWED=1.
Sessions are seeded only into a fresh test database; no login bypass is added to API.
"""
from ui_test_helpers import open_settings
import argparse
import base64
import io
import json
import os
import socket
import sys
import threading
import time
import tempfile
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
    parser.add_argument("--sponsors-only", action="store_true", help="Полный сценарий страницы спонсоров")
    parser.add_argument("--interface-only", action="store_true")
    parser.add_argument("--campaign-cards-only", action="store_true", help="Карточки кампаний во всех разделах")
    parser.add_argument("--verification-only", action="store_true", help="Подтверждение контакта, профиль и настройки подарка")
    parser.add_argument("--ai-only", action="store_true", help="Подключение ИИ, хранение ключа и роли")
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

    # Public GitHub is isolated too; HTTP parsing/cache/auth have dedicated API tests.
    release_patcher = patch('app.releases.check', return_value={
        'state':'current', 'installed_version':api.app.version, 'available_version':api.app.version,
        'commit':'a'*40, 'checked_at':'2026-11-15T15:00:00+00:00',
        'attempted_at':'2026-11-15T15:00:00+00:00'})
    release_fixture = release_patcher.start()
    logo_image=io.BytesIO();Image.new('RGB',(160,160),(38,198,184)).save(logo_image,format='JPEG')
    logo_patcher=patch('app.channel_logos.logo',return_value=logo_image.getvalue());logo_patcher.start()
    avatar_patcher=patch('app.user_profiles.user_avatar',return_value=logo_image.getvalue());avatar_patcher.start()
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
                    "INSERT INTO users(id,telegram_user_id,role,first_name,is_owner) VALUES (%s,%s,%s,%s,%s)",
                    (user_id, 99116000 + i, "advertiser" if role == "outsider" else role,
                     "Тест " + role,role == "admin"))
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
                    page.on("pageerror", lambda error: (failures.append(str(error)), print("BROWSER_ERROR:", error, flush=True)))
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

                def open_wallet_tools(page):
                    # После проверки компактного вида раскрываем нужные формы старых сценариев.
                    expect(page.locator('.wallet-balances').first).to_be_visible()
                    for summary in page.locator('.wallet-disclosure > summary').all():
                        if not summary.evaluate('(s) => s.parentElement.open'):
                            summary.click()

                def check_navigation_icons(page):
                    # Проверяем внешний SVG через настоящий static route и отрисованный <use>.
                    import xml.etree.ElementTree as ET
                    response = page.request.get(base + '/assets/pushok-icons.svg')
                    assert response.status == 200, 'SVG sprite is not served'
                    assert 'image/svg+xml' in response.headers.get('content-type', '')
                    symbols = {node.get('id') for node in ET.fromstring(response.body()).iter()
                               if node.tag.endswith('symbol')}
                    expect(page.locator('#cabinet-sidebar')).to_have_count(0)
                    expect(page.locator('.menu-toggle')).to_have_count(0)
                    for route, icon in [('dashboard','home'),('campaigns','campaign'),('wallet','wallet'),('profile','user')]:
                        link = page.locator('.mobile-bottom-nav a[data-route="'+route+'"]')
                        expect(link).to_be_visible()
                        expect(link).to_have_attribute('href','#/'+route)
                        assert 'pi-'+icon in symbols
                        expect(link.locator('use')).to_have_attribute('href','/assets/pushok-icons.svg#pi-'+icon)
                    privileged=page.evaluate("['admin','operator'].includes(me.role)")
                    routes=page.locator('.mobile-bottom-nav a:visible').evaluate_all('(items)=>items.map(a=>a.dataset.route)')
                    assert routes==(['dashboard','moderation','campaigns','wallet','hall','profile'] if privileged else ['dashboard','campaigns','wallet','hall','profile'])
                    if privileged:
                        previous=page.evaluate('location.hash.slice(2)') or 'dashboard'
                        page.locator('.mobile-bottom-nav [data-route=moderation]').click()
                        expect(page.locator('[data-page=moderation]')).to_be_visible()
                        expect(page.locator('.mobile-bottom-nav [data-route=moderation]')).to_have_attribute('aria-current','page')
                        go(page,previous)

                def refresh(page):
                    with page.expect_response(lambda r: r.url.endswith('/campaigns') and r.request.method == 'GET'):
                        page.locator('#refresh').click()
                    expect(page.locator('#refresh')).to_be_enabled()

                if args.campaign_cards_only:
                    from check_campaign_cards import check_campaign_cards
                    check_campaign_cards(browser, base, output, failures, PostgresIntegration.dsn)
                    if failures:raise AssertionError(failures)
                    print('PASS: unified campaign cards, actions, archive and mobile widths')
                    return
                if args.interface_only:
                    from check_interface import check_interface
                    check_interface(browser,base,output,failures,PostgresIntegration.dsn)
                    from check_hall_of_fame import check_hall_of_fame
                    from check_user_profiles import check_user_profiles
                    from check_admin_users import check_admin_users
                    from check_channel_layout import check_channel_layout
                    from check_mobile_polish import check_mobile_polish
                    for check in (check_hall_of_fame,check_user_profiles,check_admin_users,check_channel_layout,check_mobile_polish):
                        check(browser,base,output,failures,PostgresIntegration.dsn)
                    if failures:raise AssertionError(failures)
                    print('PASS: compact interface and invite retry')
                    return
                if args.verification_only:
                    from check_verified_profiles import check_verified_profiles
                    check_verified_profiles(browser, base, output, failures, PostgresIntegration.dsn)
                    if failures:
                        raise AssertionError(failures)
                    print('Verified profile browser checks passed')
                    return
                if args.sponsors_only:
                    from check_sponsor_workspace import check_sponsor_workspace
                    check_sponsor_workspace(browser, base, output, failures, PostgresIntegration.dsn)
                    return
                if args.ai_only:
                    from check_channel_ai_ui import check_channel_ai_ui
                    admin = new_page('admin',390)
                    go(admin,'settings');admin.locator('#settings-system-tab').click();open_settings(admin)
                    check_channel_ai_ui(admin,new_page('advertiser'),new_page('operator'),new_page,go,output,PostgresIntegration.dsn)
                    from check_campaign_assistant_ui import check_campaign_assistant_ui
                    check_campaign_assistant_ui(new_page,go,output,PostgresIntegration.dsn)
                    if failures:raise AssertionError(failures)
                    print('PASS: AI settings — real API, private key, roles, logout, mobile widths')
                    return
                admin = new_page("admin")
                go(admin, "sponsors")
                expect(admin.get_by_role('tab', name='Размещения', exact=True)).to_have_attribute('aria-selected', 'true')
                admin.get_by_role('tab', name='Размещения', exact=True).focus()
                admin.keyboard.press('ArrowRight')
                expect(admin.get_by_role('tab', name='Узлы', exact=True)).to_be_focused()
                expect(admin.get_by_role('tab', name='Узлы', exact=True)).to_have_attribute('aria-selected','true')
                admin.keyboard.press('Home')
                expect(admin.get_by_role('tab', name='Размещения', exact=True)).to_be_focused()
                expect(admin.locator('#node-form')).to_be_hidden()
                expect(admin.locator('#sponsor-form')).to_be_hidden()
                admin.get_by_role('tab', name='Узлы', exact=True).click()
                admin.locator('#node-admin>.panel-heading').get_by_role('button',name='Добавить узел',exact=True).click()
                admin.locator('#node-form [name=name]').fill('Тестовый узел')
                admin.locator('#node-form [name=host]').fill('node.example.org')
                admin.locator('#node-engine-settings > summary').click()
                admin.locator('#engine-preview').click()
                expect(admin.locator('#engine-advice')).to_contain_text('План:')
                admin.locator('#node-form button[type=submit]').click()
                expect(admin.locator('#nodes-list')).to_contain_text('Тестовый узел')
                admin.get_by_role('tab', name='Размещения', exact=True).click()
                expect(admin.locator('#promotion-diagnostics')).to_be_visible()
                admin.locator('#sponsor-diagnostics-details > summary').click()
                expect(admin.locator('#promotion-agent-report')).to_have_attribute('data-status', 'unknown')
                expect(admin.locator('#promotion-diagnostics [data-diagnostic-code=no_current_campaign]')).to_be_visible()
                admin.get_by_role('tab', name='Узлы', exact=True).click()
                admin.get_by_role('button', name='Поставить на паузу', exact=True).click()
                expect(admin.get_by_role('button', name='Возобновить', exact=True)).to_be_visible()
                expect(admin.locator('#promotion-diagnostics [data-diagnostic-code=node_paused]')).to_have_count(1)
                admin.get_by_role('button', name='Возобновить', exact=True).click()
                expect(admin.get_by_role('button', name='Поставить на паузу', exact=True)).to_be_visible()
                admin.get_by_role('button', name='Изменить план движка', exact=True).click()
                admin.locator('.node-plan-form [name=engine_mode]').select_option('manual')
                expect(admin.locator('.node-plan-form [name=preferred_engine]')).to_be_visible()
                admin.locator('.node-plan-form [name=preferred_engine]').select_option('official_mtproxy')
                admin.get_by_role('button', name='Сохранить план', exact=True).click()
                expect(admin.locator('#notice')).to_contain_text('План сохранён')
                admin.get_by_role('tab', name='Настройка', exact=True).click()
                admin.locator('#sponsor-registration > summary').click()
                admin.get_by_label('ad_tag из MTProxyBot', exact=True).fill('a' * 32)
                admin.locator('#sponsor-form button[type=submit]').click()
                expect(admin.locator('[data-page=sponsors]')).to_contain_text('окончанием aaaa')
                assert admin.get_by_label('ad_tag из MTProxyBot', exact=True).input_value() == ''
                # Обновление статусов не стирает введённый, ещё не сохранённый тег.
                admin.get_by_text('Ручная привязка готового тега', exact=True).click()
                draft_tag = admin.get_by_label('Тег этого канала из @MTProxyBot', exact=True)
                draft_tag.fill('b' * 32)
                admin.get_by_role('tab', name='Размещения', exact=True).click()
                admin.get_by_role('button', name='Обновить диагностику', exact=True).click()
                expect(admin.locator('#promotion-diagnostics [data-diagnostic-code=no_current_campaign]')).to_be_visible()
                expect(admin.locator('#promotion-automation-runtime')).to_contain_text('Подтверждения от агента пока нет')
                expect(draft_tag).to_have_value('b' * 32)
                admin.get_by_role('tab', name='Настройка', exact=True).click()
                expect(draft_tag).to_be_visible()
                expect(draft_tag).to_have_value('b' * 32)

                advertiser = new_page("advertiser")
                expect(advertiser.locator('[data-page=dashboard] .empty-state')).to_contain_text('Здесь появятся кампании')
                expect(advertiser.locator('[data-page=dashboard] .campaign-tile')).to_have_count(0)
                proxy=advertiser.get_by_role('link',name='Подключить прокси',exact=True)
                expect(proxy).to_be_visible()
                advertiser.evaluate("window.cabinet.config({telegram_bot_username:'fixture_bot',proxy_url:'https://t.me/proxy?server=proxy.example.org&port=443&secret=ddaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'})")
                expect(proxy).to_have_attribute('href','https://t.me/proxy?server=proxy.example.org&port=443&secret=ddaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
                assert proxy.evaluate("e=>getComputedStyle(e).backgroundColor==='rgb(217, 71, 80)'")
                expect(advertiser.locator(".dashboard-hero-actions").get_by_role("link",name="Продвигать канал",exact=True)).to_be_visible()
                assert proxy.evaluate("e=>e.nextElementSibling.textContent==='Продвигать канал'")
                assert advertiser.locator(".dashboard-help").evaluate("e=>e.previousElementSibling.id==='dashboard-referral'")
                expect(advertiser.locator('[data-page=dashboard] .payment-counts')).to_have_count(0)
                go(advertiser, "settings", expected_page="dashboard")
                expect(advertiser.locator('[data-page=dashboard]')).to_be_visible()
                assert advertiser.locator('nav a[data-route=settings]').is_hidden()
                forbidden = advertiser.request.get(base + '/api/v1/admin/payments/status',
                    headers={"Authorization": "Bearer " + sessions['advertiser']})
                assert forbidden.status == 403
                # Компактная стартовая раскладка: история доступна, формы открываются по запросу.
                go(advertiser, 'wallet')
                history=advertiser.locator('[data-wallet-section=history]')
                expect(history).not_to_have_attribute('open','')
                history.locator(':scope > summary').click()
                expect(advertiser.locator('.wallet-journal')).to_be_visible()
                expect(advertiser.locator('[data-wallet-section=topup]')).not_to_have_attribute('open', '')
                expect(advertiser.get_by_label('Сумма пополнения, ₽',exact=True)).to_be_hidden()
                advertiser.locator('[data-wallet-section=topup] > summary').click()
                expect(advertiser.get_by_label('Сумма пополнения, ₽',exact=True)).to_be_visible()
                advertiser.locator('[data-wallet-section=topup] > summary').click()
                expect(advertiser.get_by_label('Автоматически оплачивать мои одобренные заявки')).not_to_be_checked()
                go(advertiser, 'profile')
                expect(advertiser.locator('.profile-group')).to_have_count(2)
                expect(advertiser.locator('.profile-menu a[href="#/wallet"]')).to_have_count(0)
                go(advertiser, 'create')
                tariff_help=advertiser.locator('#tariff-select').locator('xpath=../..').locator('.field-hint')
                expect(tariff_help.locator('.field-help')).to_be_hidden()
                tariff_help.locator('summary').click()
                expect(tariff_help.locator('.field-help')).to_be_visible()
                advertiser.locator('#order-channel-url').click()
                expect(tariff_help.locator('.field-help')).to_be_hidden()
                tariff_help.locator('summary').click()
                advertiser.keyboard.press('Escape')
                expect(tariff_help.locator('.field-help')).to_be_hidden()
                from check_public_channel_ui import check_public_channel_ui
                check_public_channel_ui(advertiser, output)
                from check_channel_topics_ui import check_channel_topics_ui
                check_channel_topics_ui(advertiser, output)
                advertiser.locator('#order-channel-url').fill('@unsaved_order_channel')
                go(advertiser, "channels")
                advertiser.locator('[data-page=channels] .search-action-row>a').click()
                expect(advertiser.locator('#channel-form [name=telegram_url]')).to_be_focused()
                advertiser.locator('#channel-form [name=title]').fill('Тестовый канал')
                advertiser.locator('#channel-form [name=telegram_url]').fill('https://example.org/not-telegram')
                advertiser.locator('#channel-form button[type=submit]').click()
                expect(advertiser.locator('#notice')).to_contain_text('https://t.me/channel')
                advertiser.locator('#channel-form [name=telegram_url]').fill('https://t.me/pushok_browser_test')
                advertiser.locator('#channel-form button[type=submit]').click()
                expect(advertiser.locator('[data-page=channels] .channel-manager')).to_contain_text('Тестовый канал')
                advertiser.locator('[data-page=channels] .search-action-row>a').click()
                advertiser.locator('#channel-form [name=title]').fill('Дубликат')
                advertiser.locator('#channel-form [name=telegram_url]').fill('https://t.me/pushok_browser_test')
                advertiser.locator('#channel-form button[type=submit]').click()
                expect(advertiser.locator('#notice')).to_contain_text('уже добавлен')

                go(advertiser, "create")
                expect(advertiser.locator('#order-channel-url')).to_have_value('@unsaved_order_channel')
                advertiser.locator('#order-channel-mode').click()
                advertiser.locator('#channel-select').select_option(index=1)
                expect(advertiser.locator('#order-selected-preview')).to_have_attribute('data-state','verified')
                tomorrow = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
                advertiser.locator('#order-date > summary').click()
                advertiser.locator('#calendar-from').fill(tomorrow)
                advertiser.locator('#calendar-from').dispatch_event('change')
                expect(advertiser.locator('#availability-days button:enabled').first).to_be_visible()
                advertiser.locator('#availability-days button:enabled').first.click()
                advertiser.get_by_role('button',name='Тематика канала',exact=True).click();advertiser.get_by_role('button',name='Образование',exact=True).click()
                advertiser.locator('#campaign-form button[type=submit]').click()
                expect(advertiser.locator('#order-result')).to_be_visible()
                campaign_id = advertiser.evaluate("location.hash.split('/')[2]")
                go(advertiser, 'campaigns')
                first_tile = advertiser.locator('#campaigns .campaign-tile')
                expect(first_tile.locator('.campaign-topic')).to_have_text('Образование')
                expect(first_tile.locator('.campaign-steps dt')).to_have_text(['Модерация', 'Оплата', 'Размещение'])
                expect(first_tile.locator('.campaign-tile-facts dt')).to_have_text(['Тариф','Срок','Стоимость'])
                expect(first_tile.locator('.campaign-current')).to_be_visible()
                expect(first_tile.locator('.tile-period')).to_contain_text('Желаемое начало:')
                expect(first_tile.locator('.badge--active')).to_have_count(0)
                advertiser.set_viewport_size({'width':390,'height':844})
                assert first_tile.bounding_box()['height'] < 270, first_tile.bounding_box()
                assert float(first_tile.locator('.tile-title').evaluate('(e)=>getComputedStyle(e).fontSize').replace('px','')) <= 16
                expect(advertiser.locator('[data-page=campaigns] .campaign-tabs a[href="#/archive"]')).to_be_visible()
                expect(advertiser.locator('[data-page=campaigns] .campaign-tabs a[href="#/channels"]')).to_be_visible()
                advertiser.locator('[data-page=campaigns] .campaign-tabs a[href="#/channels"]').click()
                advertiser.locator('.screen-back').click()
                expect(advertiser.locator('[data-page=campaigns]')).to_be_visible()

                expect(first_tile.get_by_role('link', name='Перейти к оплате', exact=True)).to_have_count(0)
                first_tile.locator('.tile-heading > .icon-action').click()
                expect(advertiser.locator('[data-page=campaign]')).to_be_visible()
                go(advertiser, 'campaign/'+campaign_id)
                expect(advertiser.locator('[data-page=campaign] .campaign-steps')).to_contain_text('На проверке')
                expect(advertiser.locator('[data-page=campaign] .campaign-progress [aria-current=step]')).to_contain_text('Модерация')

                operator = new_page('operator')
                go(operator, 'moderation')
                expect(operator.locator('#queue .campaign-topic')).to_have_text('Образование')
                operator.get_by_text('Исправления или отказ', exact=True).click()
                operator.get_by_label('Комментарий рекламодателю', exact=True).fill('Уточните название канала')
                operator.get_by_role('button', name='Отправить решение', exact=True).click()
                expect(operator.locator('#queue .campaign-steps')).to_contain_text('Исправить')
                refresh(advertiser)
                expect(advertiser.locator('[data-page=campaign]')).to_contain_text('Уточните название канала')
                advertiser.get_by_role('button', name='Исправить канал', exact=True).click()
                advertiser.locator('.correction-form input').first.fill('Исправленный канал')
                advertiser.get_by_role('button', name='Сохранить исправления', exact=True).click()
                expect(advertiser.locator('.correction-form')).to_have_count(0)
                advertiser.get_by_role('button', name='Отправить повторно', exact=True).click()
                expect(advertiser.locator('[data-page=campaign] .campaign-steps')).to_contain_text('На проверке')
                refresh(operator)
                expect(operator.get_by_role('button', name='Одобрить канал', exact=True)).to_be_visible()
                operator.set_viewport_size({'width': 390, 'height': 844})
                operator.screenshot(path=str(output / '30-approval-onepage-mobile.png'), full_page=True)
                assert operator.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
                operator.set_viewport_size({'width': 1440, 'height': 1000})
                operator.get_by_role('button', name='Одобрить канал', exact=True).click()
                expect(operator.locator('#queue .campaign-steps')).to_contain_text('К оплате')
                refresh(advertiser)
                go(advertiser, 'payments/' + campaign_id)
                advertiser.get_by_role('button', name='Реквизиты и сумма', exact=True).click()
                expect(advertiser.locator('.payment-instructions')).to_contain_text('ТЕСТОВАЯ ИНСТРУКЦИЯ')
                expect(advertiser.get_by_role('button', name='Бронь на 15 минут', exact=True)).to_have_count(0)
                expect(advertiser.get_by_label('Свободный прокси', exact=True)).to_have_count(0)

                image_bytes = io.BytesIO()
                Image.new('RGB', (64, 64), 'teal').save(image_bytes, format='PNG')
                advertiser.locator('[data-page=payments]').get_by_label('Подтверждение оплаты', exact=True).set_input_files(
                    {"name": "test-receipt.png", "mimeType": "image/png", "buffer": image_bytes.getvalue()})
                advertiser.get_by_role('button', name='Загрузить чек', exact=True).click()
                expect(advertiser.locator('[data-page=payments] .campaign-steps')).to_contain_text('Проверка')
                refresh(operator)
                operator.get_by_role('button', name='Посмотреть чеки', exact=True).click()
                with operator.expect_download() as receipt:
                    operator.get_by_role('button', name='test-receipt.png', exact=True).click()
                assert Path(receipt.value.path()).read_bytes() == image_bytes.getvalue()
                operator.locator('summary').filter(has_text='Отклонить чек').click()
                operator.get_by_label('Причина отклонения чека', exact=True).fill('Нужен читаемый чек')
                operator.get_by_role('button', name='Отклонить чек', exact=True).click()
                expect(operator.locator('#queue')).to_contain_text('Нужен читаемый чек')
                refresh(advertiser)
                advertiser.locator('[data-page=payments]').get_by_label('Подтверждение оплаты', exact=True).set_input_files(
                    {"name": "test-receipt-again.png", "mimeType": "image/png", "buffer": image_bytes.getvalue()})
                advertiser.get_by_role('button', name='Загрузить чек', exact=True).click()
                expect(advertiser.locator('[data-page=payments] .campaign-steps')).to_contain_text('Проверка')
                refresh(operator)
                operator.get_by_label('Проверил поступление денег, сумму и назначение платежа', exact=True).check()
                operator.get_by_role('button', name='Подтвердить оплату', exact=True).click()
                expect(operator.locator('#queue')).to_contain_text('Оплачено')
                # Чужая заявка из очереди спонсоров открывается в представлении оператора.
                for reviewer, width in ((operator, 320), (admin, 390)):
                    refresh(reviewer)
                    reviewer.set_viewport_size({'width': width, 'height': 844})
                    go(reviewer, 'sponsors')
                    reviewer.get_by_role('tab', name='Размещения', exact=True).click()
                    title_link = reviewer.locator('.sponsor-queue .tile-title').first
                    expect(title_link).to_have_attribute('href', '#/moderation/' + campaign_id)
                    title_link.click()
                    expect(reviewer.locator('[data-page=moderation]')).to_be_visible()
                    expect(reviewer.locator('#queue .approval-card')).to_have_attribute('data-campaign-id', campaign_id)
                    reviewer.locator('summary').filter(has_text='Изменить расписание').click()
                    expect(reviewer.get_by_role('button', name='Назначить слот', exact=True)).to_be_visible()
                    reviewer.set_viewport_size({'width': 1440, 'height': 1000})
                # Пустой выбор объясняется рядом с формой, а не выглядит как молчание.
                operator.get_by_label('Прокси-узел', exact=True).select_option('')
                operator.get_by_role('button', name='Назначить слот', exact=True).click()
                expect(operator.locator('.approval-feedback')).to_contain_text('Заполните поле')
                operator.get_by_role('button', name='Проверить свободные узлы', exact=True).click()
                expect(operator.get_by_label('Прокси-узел', exact=True).locator('option')).to_have_count(2)
                operator.get_by_label('Прокси-узел', exact=True).select_option(index=1)
                operator.get_by_label('Начало размещения', exact=True).fill(tomorrow + 'T12:00')
                operator.get_by_role('button', name='Назначить слот', exact=True).click()
                expect(operator.locator('#queue .campaign-steps')).to_contain_text('В очереди')

                operator.locator('summary').filter(has_text='Изменить или удалить заявку').click()
                operator.get_by_role('button', name='Изменить слот', exact=True).click()
                operator.get_by_label('Новое начало слота', exact=True).fill(tomorrow + 'T15:00')
                operator.get_by_role('button', name='Сохранить изменения', exact=True).click()
                expect(operator.locator('#notice')).to_contain_text('Изменения сохранены')
                operator.locator('summary').filter(has_text='Изменить или удалить заявку').click()
                operator.once('dialog', lambda dialog: dialog.accept())
                operator.get_by_role('button', name='Снять будущий слот', exact=True).click()
                expect(operator.locator('#queue .campaign-steps')).to_contain_text('Не начата')
                expect(operator.locator('#queue')).to_contain_text('Оплачено')
                # После снятия слота пауза снимается видимой кнопкой, без поиска в details.
                resume = operator.get_by_role('button', name='Возобновить автоподбор', exact=True)
                expect(resume).to_be_visible()
                resume.click()
                expect(operator.locator('#notice')).to_contain_text('Автоподбор включён')
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    assert connection.execute('SELECT auto_schedule_paused FROM campaigns WHERE id=%s', (campaign_id,)).fetchone()[0] is False
                operator.locator('summary').filter(has_text='Изменить расписание').click()
                operator.get_by_label('Начало размещения', exact=True).fill(tomorrow + 'T12:00')
                operator.get_by_role('button', name='Назначить слот', exact=True).click()
                expect(operator.locator('#queue .campaign-steps')).to_contain_text('В очереди')

                # Move the isolated fixture into its active window; no VPS is used.
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    connection.execute("UPDATE campaigns SET scheduled_start_at=now()-interval '1 minute', scheduled_end_at=now()+interval '1 hour' WHERE id=%s", (campaign_id,))
                # Настоящий API сверяет имитированный отчёт с планом в тестовой БД.
                # Проверяем обе карточки и обновление без потери черновика формы.
                from psycopg.rows import dict_row
                from app.promotion_runtime import handle
                with psycopg.connect(PostgresIntegration.dsn, row_factory=dict_row) as connection:
                    fixture = connection.execute('''SELECT c.channel_id,n.id,n.host,n.port
                        FROM campaigns c JOIN proxy_nodes n ON n.id=c.proxy_node_id
                        WHERE c.id=%s''', (campaign_id,)).fetchone()
                binding = admin.request.put(base + '/api/v1/admin/promotion-bindings',
                    headers={'Authorization': 'Bearer ' + sessions['admin']},
                    data=dict(node_id=str(fixture['id']), channel_id=str(fixture['channel_id']),
                              ad_tag='c'*32, telegram_binding_confirmed=True))
                assert binding.status == 200, binding.text()
                with psycopg.connect(PostgresIntegration.dsn, row_factory=dict_row) as connection:
                    request = dict(host=fixture['host'], port=fixture['port'])
                    plan = handle(connection, request)
                    assert plan['state'] == 'apply'
                    assert handle(connection, dict(request, action='ack', revision=plan['revision']))['accepted']
                with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'PUSHOK_CONTROL_DIR': directory}):
                    report = dict(code='tag_applied', checked_at=datetime.now(timezone.utc).isoformat(),
                                  node_id=plan['node_id'], plan_revision=plan['revision'])
                    report['telegram_owner']=dict(state='connected',enabled=True,session_saved=True,helper_available=True,
                        checked_at=datetime.now(timezone.utc).isoformat(),username='test_owner')
                    report_path = Path(directory) / 'promotion-agent.json'
                    report_path.write_text(json.dumps(report))
                    go(admin, 'sponsors')
                    node_card = admin.locator('#promotion-diagnostics [data-diagnostic-code=tag_applied]')
                    expect(node_card).to_contain_text('Тег применён; показ не проверен')
                    expect(admin.locator('#promotion-automation-runtime')).to_contain_text('Тег применён; показ не проверен')
                    expect(node_card).not_to_contain_text('Дождитесь')
                    admin.get_by_role('tab', name='Настройка', exact=True).click()
                    owner_card=admin.locator('#telegram-owner-status')
                    expect(owner_card).to_contain_text('Автопривязка включена')
                    expect(owner_card).to_contain_text('@test_owner')
                    report['telegram_owner']['enabled']=False
                    report_path.write_text(json.dumps(report))
                    admin.get_by_role('button',name='Обновить состояние подключения',exact=True).click()
                    expect(owner_card).to_contain_text('Автопривязка выключена')
                    admin.get_by_text('Подключение и проверка',exact=True).click()
                    for width in (320,390):
                        admin.set_viewport_size({'width':width,'height':844})
                        owner_card.scroll_into_view_if_needed()
                        assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                    admin.screenshot(path=str(output/'47-owner-connection-mobile.png'))
                    if not admin.locator('#sponsor-manual-binding').evaluate('(e)=>e.open'):
                        admin.get_by_text('Ручная привязка готового тега', exact=True).click()
                    draft_tag = admin.get_by_label('Тег этого канала из @MTProxyBot', exact=True)
                    draft_tag.fill('d' * 32)
                    admin.set_viewport_size({'width': 390, 'height': 844})
                    admin.screenshot(path=str(output / '31-promotion-confirmed-mobile.png'), full_page=True)
                    refresh(advertiser)
                    go(advertiser,'campaign/'+campaign_id)
                    advertiser.get_by_role('button',name='Статус автоматического размещения',exact=True).click()
                    expect(advertiser.locator('.automation-panel')).to_contain_text('Размещение запущено автоматически')
                    advertiser.get_by_role('button',name='Отчёт по кампании',exact=True).click()
                    expect(advertiser.locator('.campaign-report')).to_contain_text('История действий')
                    expect(advertiser.locator('.report-observations')).not_to_have_attribute('open','')
                    advertiser.evaluate("""() => {
                        window.__openAutomation=document.querySelector('.automation-panel');
                        window.__openReport=document.querySelector('.campaign-report');
                    }""")
                    advertiser.evaluate('() => refreshStatuses()')
                    assert advertiser.evaluate("() => window.__openAutomation===document.querySelector('.automation-panel') && window.__openReport===document.querySelector('.campaign-report')")
                    with psycopg.connect(PostgresIntegration.dsn) as connection:
                        connection.execute("UPDATE campaigns SET moderator_comment='Одобрено для автоматического размещения',updated_at=now() WHERE id=%s",(campaign_id,))
                    advertiser.evaluate('() => refreshStatuses()')
                    expect(advertiser.locator('[data-page=campaign]')).to_contain_text('Одобрено для автоматического размещения')
                    expect(advertiser.locator('.automation-panel')).to_contain_text('Размещение запущено автоматически')
                    assert advertiser.evaluate("() => window.__openAutomation===document.querySelector('.automation-panel') && window.__openReport===document.querySelector('.campaign-report')")
                    for width in (320,390):
                        advertiser.set_viewport_size({'width':width,'height':844})
                        assert advertiser.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                        assert advertiser.locator('.campaign-detail > .actions > *').evaluate_all('(actions)=>actions.every(action=>action.getBoundingClientRect().height<=52)'), 'Campaign actions squeezed into vertical text'
                    advertiser.screenshot(path=str(output/'46-campaign-automation-mobile.png'),full_page=True)
                    advertiser.locator('.automation-panel').get_by_role('button',name='Закрыть',exact=True).click()
                    advertiser.get_by_role('button',name='Закрыть отчёт',exact=True).click()
                    report_path.write_text(json.dumps(dict(report, code='apply_failed')))
                    admin.get_by_role('tab', name='Размещения', exact=True).click()
                    admin.get_by_role('button', name='Обновить диагностику', exact=True).click()
                    expect(admin.locator('#promotion-diagnostics [data-diagnostic-code=ready]')).to_contain_text('Подтверждения применения текущего плана пока нет')
                    expect(admin.locator('#promotion-automation-runtime')).to_contain_text('Применение текущего плана не подтверждено')
                    expect(draft_tag).to_have_value('d' * 32)
                    # Отказ проверки канала не должен выглядеть как работающий запуск.
                    with psycopg.connect(PostgresIntegration.dsn) as connection:
                        connection.execute('''INSERT INTO telegram_binding_jobs(proxy_node_id,campaign_id,channel_id,channel_url,node_host,node_port,error_code,retry_at)
                            VALUES(%s,%s,%s,'https://t.me/example_public_channel',%s,%s,'channel_not_broadcast',now()+interval '5 minutes')''',
                            (fixture['id'],campaign_id,fixture['channel_id'],fixture['host'],fixture['port']))
                    report_path.write_text(json.dumps(dict(report,code='telegram_binding_pending')))
                    admin.get_by_role('tab', name='Настройка', exact=True).click()
                    admin.get_by_role('button',name='Обновить состояние подключения',exact=True).click()
                    binding_card=admin.locator('.binding-status').filter(has_text='По ссылке находится не канал')
                    expect(binding_card).to_contain_text('https://t.me/example_public_channel')
                    expect(binding_card).to_contain_text('Telegram вернул группу или личный аккаунт')
                    expect(admin.locator('.sponsor-health')).to_contain_text('По ссылке находится не канал')
                    for width in (320,390):
                        admin.set_viewport_size({'width':width,'height':844})
                        binding_card.scroll_into_view_if_needed()
                        assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                    admin.screenshot(path=str(output/'48-binding-channel-error-mobile.png'))
                    with psycopg.connect(PostgresIntegration.dsn) as connection:
                        connection.execute('DELETE FROM telegram_binding_jobs WHERE proxy_node_id=%s',(fixture['id'],))
                    admin.set_viewport_size({'width': 1440, 'height': 1000})
                # ACK агента автоматически активирует кампанию, без формы проверки.
                refresh(operator)
                expect(operator.locator('#queue .campaign-steps')).to_contain_text('Активна')
                # Вернуть только эту изолированную кампанию к отдельному ручному сценарию.
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    connection.execute('DELETE FROM channel_promotion_bindings WHERE proxy_node_id=%s', (fixture['id'],))
                    connection.execute('DELETE FROM promotion_runtime WHERE proxy_node_id=%s', (fixture['id'],))
                    connection.execute("UPDATE campaigns SET placement_status='scheduled' WHERE id=%s", (campaign_id,))
                refresh(operator)
                operator.locator('summary').filter(has_text='Дополнительная ручная проверка').click()
                operator.locator('summary').filter(has_text='Показ не обнаружен').click()
                operator.get_by_label('Что проверили', exact=True).fill('Тест: в подключённом Telegram без подписки спонсор не найден')
                operator.get_by_role('button', name='Записать: показ не обнаружен', exact=True).click()
                expect(operator.locator('#notice')).to_contain_text('Отрицательное наблюдение')
                operator.locator('summary').filter(has_text='Дополнительная ручная проверка').click()
                operator.get_by_label('Что увидели при проверке', exact=True).fill('Тест: в своём подключённом Telegram без подписки видна отметка спонсора')
                # Без явного подтверждения показа форма не отправляется.
                operator.get_by_role('button', name='Подтвердить показ и активировать', exact=True).click()
                expect(operator.locator('#queue .campaign-steps')).to_contain_text('В очереди')
                assert operator.get_by_label('Я подключён к этому прокси, не подписан на канал и вижу отметку «Спонсор прокси»', exact=True).evaluate('(el) => el.validity.valueMissing')
                operator.get_by_label('Скриншот проверки (необязательно)', exact=True).set_input_files(
                    {"name": "test-display.png", "mimeType": "image/png", "buffer": image_bytes.getvalue()})
                operator.get_by_label('Я подключён к этому прокси, не подписан на канал и вижу отметку «Спонсор прокси»', exact=True).check()
                operator.get_by_role('button', name='Подтвердить показ и активировать', exact=True).click()
                expect(operator.locator('#queue .campaign-steps')).to_contain_text('Активна')
                operator.locator('summary').filter(has_text='История и документы').click()
                operator.get_by_role('button', name='Журнал проверок', exact=True).click()
                expect(operator.locator('.observation-panel')).to_contain_text('Показ виден')
                operator.screenshot(path=str(output / '29-approval-onepage-active.png'), full_page=True)
                operator.locator('summary').filter(has_text='Ручное завершение и диагностика').click()
                confirmation = operator.get_by_label('Тег кампании снят; в подключённом Telegram без подписки канал больше не показан как спонсор', exact=True)
                bounds = confirmation.bounding_box()
                assert 18 <= bounds['width'] <= 24 and 18 <= bounds['height'] <= 24, bounds
                confirmation.check()
                operator.get_by_role('button', name='Подтвердить завершение', exact=True).click()
                expect(operator.locator('#queue')).not_to_contain_text('Активна')
                refresh(advertiser)
                go(advertiser, 'archive/'+campaign_id)
                expect(advertiser.locator('.campaign-report')).to_contain_text('История действий')
                with advertiser.expect_download() as downloaded:
                    advertiser.get_by_role('button',name='Скачать CSV',exact=True).click()
                assert downloaded.value.suggested_filename.endswith('.csv')
                advertiser.set_viewport_size({'width':390,'height':844})
                advertiser.screenshot(path=str(output/'completed-archive-mobile.png'),full_page=True)

                go(admin, 'settings')
                admin.locator('#settings-system-tab').click();open_settings(admin)
                from check_channel_ai_ui import check_channel_ai_ui
                check_channel_ai_ui(admin, advertiser, operator, new_page, go, output,PostgresIntegration.dsn)
                expect(admin.locator('#team-fields')).to_be_enabled()
                for field in ('loading-title-fields','team-fields','default-sponsor-fields'):
                    assert admin.locator('#'+field).evaluate('(e)=>getComputedStyle(e).borderTopWidth')=='0px'
                admin.locator('#loading-title-form').screenshot(path=str(output/'loading-form-no-frame.png'))
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    connection.execute("INSERT INTO users(telegram_user_id,first_name) VALUES(991199001,'Тест команды')")
                admin.get_by_label('Пользователь для назначения роли',exact=True).fill('991199001')
                admin.locator('#team-role').select_option('admin')
                admin.once('dialog',lambda dialog:dialog.accept())
                admin.locator('#team-form button[type=submit]').click()
                expect(admin.locator('#team-status')).to_contain_text('Роль сохранена')
                expect(admin.locator('#team-list')).to_contain_text('Тест команды')
                admin.locator('#team-role').select_option('advertiser')
                admin.once('dialog',lambda dialog:dialog.accept())
                admin.locator('#team-form button[type=submit]').click()
                expect(admin.locator('#team-list')).not_to_contain_text('Тест команды')
                admin.locator('#default-sponsor-channel').select_option(str(fixture['channel_id']))
                admin.locator('#default-sponsor-form details summary').click()
                admin.locator('#default-sponsor-node').select_option(str(fixture['id']))
                admin.locator('#default-sponsor-tag').fill('e'*32)
                admin.locator('#default-sponsor-confirmed').check()
                admin.locator('#default-sponsor-form button[type=submit]').click()
                expect(admin.locator('#default-sponsor-status')).to_contain_text('Канал подготовлен')
                assert admin.request.get(base+'/api/v1/admin/default-sponsor',headers={'Authorization':'Bearer '+sessions['admin']}).json()['channel_id']==str(fixture['channel_id'])
                admin.locator('#default-sponsor-channel').select_option('')
                admin.locator('#default-sponsor-form button[type=submit]').click()
                expect(admin.locator('#default-sponsor-status')).to_contain_text('не выбран')
                admin.locator('#settings-tariffs-tab').click();open_settings(admin)
                expect(admin.locator('#tariff-admin-name')).to_have_value('1 день')
                admin.locator('#tariff-admin-rub').fill('450.55')
                admin.locator('#tariff-admin-stars').fill('200')
                admin.locator('#tariff-admin-form button[type=submit]').click()
                expect(admin.locator('#notice')).to_contain_text('Настройки тарифа сохранены')
                refresh(advertiser)
                go(advertiser, 'archive/' + campaign_id)
                expect(advertiser.locator('[data-page=archive] .campaign-report')).to_contain_text('300')
                assert '450' not in advertiser.locator('[data-page=archive] .campaign-report').inner_text()
                go(advertiser, 'create')
                expect(advertiser.locator('#order-summary')).to_contain_text('450,55')

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
                expect(advertiser.locator('#campaigns')).to_contain_text('У вас нет текущих личных кампаний')
                expect(advertiser.locator('#campaigns')).not_to_contain_text('Исправленный канал')
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
                expect(advertiser.locator('[data-page=campaign] .campaign-steps')).to_contain_text('На проверке')
                refresh(operator)
                go(operator, 'moderation/'+sbp_cid)
                operator.get_by_role('button',name='Одобрить канал',exact=True).click()
                expect(operator.locator('#queue .campaign-steps')).to_contain_text('К оплате')
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
                    expect(advertiser.locator('[data-page=payments] .campaign-steps')).to_contain_text('К оплате')
                    with psycopg.connect(PostgresIntegration.dsn) as connection:
                        aid=connection.execute("UPDATE payment_attempts SET sbp_requires_review=true WHERE campaign_id=%s AND provider='sbp' RETURNING id",(sbp_cid,)).fetchone()[0]
                    go(admin,'settings')
                    admin.locator('#settings-payments-tab').click();open_settings(admin)
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
                    expect(advertiser.locator('[data-page=payments] .campaign-steps')).to_contain_text('Оплачено')
                    advertiser.screenshot(path=str(output/'15-sbp-verified.png'),full_page=True)

                refresh(admin)
                check_navigation_icons(advertiser)
                check_navigation_icons(admin)
                screens = [('01-dashboard', 'dashboard'), ('02-channels', 'channels'),
                    ('03-create', 'create'), ('04-campaigns', 'campaigns'),
                    ('05-detail', 'campaign/' + sbp_cid), ('06-payment', 'payments/' + sbp_cid),
                    ('07-calendar', 'calendar'), ('08-reports', 'reports'),
                    ('09-moderation', 'moderation'), ('10-sponsors', 'sponsors'),
                    ('11-settings', 'settings'), ('12-help', 'help'), ('13-bot', 'bot'), ('35-profile', 'profile')]
                # The admin owns no advertiser campaigns: capture own detail/payment as advertiser.
                for name, route in screens:
                    page = admin if route.split('/')[0] in ('moderation', 'sponsors', 'settings') else advertiser
                    go(page, route)
                    if route == 'sponsors':
                        page.get_by_role('tab', name='Размещения', exact=True).click()
                        page.locator('#sponsor-diagnostics-details').evaluate('(d) => d.open = false')
                    if route == 'settings':
                        expect(page.locator('#settings-stars-counts')).to_contain_text('Тарифов')
                        page.locator('#settings-payments-tab').click();open_settings(page)
                    page.evaluate("window.scrollTo({top:0,left:0,behavior:'instant'})")
                    page.wait_for_function('window.scrollY === 0')
                    page.screenshot(path=str(output / (name + '.png')), full_page=True)
                for width in (320, 390, 768):
                    admin.set_viewport_size({"width": width, "height": 844})
                    for _, route in screens:
                        go(admin, route)
                        assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), (width, route)
                        if route == 'sponsors':
                            for view, title in [('placements','Размещения'),('nodes','Узлы'),('setup','Настройка')]:
                                admin.get_by_role('tab', name=title, exact=True).click()
                                expect(admin.locator('#sponsor-pane-'+view)).to_be_visible()
                                assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'), (width, view)
                                admin.evaluate('window.scrollTo(0,0)')
                                admin.screenshot(path=str(output / f'sponsors-{view}-{width}.png'), full_page=True)
                    if width in (320, 390):
                        go(admin, 'profile')
                        check_navigation_icons(admin)
                        admin.locator('.profile-menu a[href="#/channels"]').click()
                        expect(admin.locator('[data-page="channels"]')).to_be_visible()
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
                    from check_miniapp_sdk import check_miniapp_sdk
                    check_miniapp_sdk(browser, base, output, failures)
                    raw=signed_data(user=json.dumps({'id':99116000,'first_name':'Mini App тест'}),query_id='mini-client-first')
                    mini=mini_page(raw)
                    expect(mini.locator('body')).to_have_class('miniapp-mode cabinet-mode')
                    expect(mini.locator('.cabinet-account')).to_contain_text('Mini App тест')
                    assert mini.evaluate('window.__miniReady&&window.__miniExpanded')
                    assert not mini.evaluate('window.__miniBackVisible')
                    expect(mini.locator('[data-page=dashboard]')).to_be_visible()
                    expect(mini.locator('.mobile-bottom-nav a:visible')).to_have_count(5)
                    expect(mini.locator('.mobile-bottom-nav [data-route=moderation]')).to_be_hidden()
                    mini.locator('.mobile-bottom-nav a[data-route=profile]').click()
                    expect(mini.locator('[data-page=profile]')).to_be_visible()
                    expect(mini.locator('.profile-menu a[href="#/settings"]')).to_have_count(0)
                    expect(mini.locator('.profile-menu a[href="#/moderation"]')).to_have_count(0)
                    mini.locator('.mobile-bottom-nav a[data-route=dashboard]').click()
                    expect(mini.locator('[data-page=dashboard]')).to_be_visible()
                    go(mini,'channels')
                    assert mini.evaluate('window.__miniBackVisible')
                    mini.evaluate('window.__miniBack()')
                    expect(mini).to_have_url(base+'/miniapp#/campaigns')
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
                    expect(mini.locator('#tariff-select option:checked')).to_contain_text('200 XTR')
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
                    expect(mini.locator('#order-submit')).to_have_attribute('aria-label','Проверить отправку')
                    mini.unroute('**/api/v1/orders',lose_order_response)
                    mini.reload()
                    expect(mini.locator('#order-submit')).to_have_attribute('aria-label','Проверить отправку')
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
                        go(mini,'campaigns')
                        mini.locator('#campaigns .ui-button[href="#/payments/'+stars_cid+'"]').click()
                        expect(mini.locator('.sbp-checkout')).to_contain_text('200 XTR')
                        mini.get_by_role('button',name='Оплатить 200 XTR',exact=True).click()
                        mini.wait_for_function('typeof window.__miniInvoiceCallback==="function"')
                        assert mini.evaluate('window.__miniInvoiceUrl')=='https://t.me/$stars-fixture'
                        mini.evaluate("window.__miniInvoiceCallback('cancelled');window.__miniInvoiceCallback=null")
                        expect(mini.get_by_role('button',name='Оплатить 200 XTR',exact=True)).to_be_enabled()
                        mini.get_by_role('button',name='Оплатить 200 XTR',exact=True).click()
                        mini.wait_for_function('typeof window.__miniInvoiceCallback==="function"')
                        mini.evaluate("window.__miniInvoiceCallback('paid');window.__miniInvoiceCallback=null")
                        expect(mini.get_by_role('button',name='Оплатить 200 XTR',exact=True)).to_be_enabled()
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
                    mini_screens = [('home','dashboard'),('campaigns','campaigns'),('channels','channels'),
                                    ('create','create'),('calendar','calendar'),('detail','campaign/'+stars_cid),
                                    ('payments','payments'),('stars','payments/'+stars_cid),
                                    ('profile','profile'),('help','help'),('wallet','wallet'),('growth','growth'),('archive','archive')]
                    for width in (320,390):
                        mini.set_viewport_size({'width':width,'height':844})
                        for screen, route in mini_screens:
                            go(mini,route)
                            if screen=='wallet':
                                expect(mini.locator('.wallet-balances .wallet-primary')).to_have_attribute('data-currency','RUB')
                                expect(mini.locator('.wallet-tools a')).to_have_attribute('href','#/payments')
                            if screen=='growth':
                                expect(mini.locator('.growth-page')).to_be_visible()
                            if screen=='home':
                                expect(mini.locator('#dashboard-wallet')).to_have_count(0)
                            assert mini.evaluate('document.documentElement.scrollWidth<=innerWidth+2'), (width,route)
                            if screen=='stars':
                                expect(mini.locator('.sbp-checkout')).to_contain_text('Оплата подтверждена сервером')
                            if screen=='detail':
                                expect(mini.locator('[data-page=campaign]')).not_to_contain_text('Проверяем применение тега')
                            if screen=='home':
                                assert mini.evaluate('''() => {
                                    const title=document.querySelector('.dashboard-hero h2');
                                    const image=document.querySelector('.dashboard-hero>img').getBoundingClientRect();
                                    const text=document.createRange();text.selectNodeContents(title);
                                    return [...text.getClientRects()].every(rect=>rect.right<=image.left+1);
                                }'''), ('Hero text overlaps mascot',width)
                                if width==320:
                                    mini.screenshot(path=str(output/'miniapp-home-320.png'),full_page=True)
                            if width==390:
                                mini.screenshot(path=str(output/('miniapp-'+screen+'.png')),full_page=True)
                                mini.screenshot(path=str(output/('viewport-'+screen+'.png')),full_page=False)
                    go(mini,'dashboard');mini.screenshot(path=str(output/'17-miniapp-light.png'),full_page=True)
                    check_navigation_icons(mini)
                    mini.screenshot(path=str(output/'21-miniapp-menu-light.png'),full_page=True)
                    mini.keyboard.press('Escape')
                    mini.evaluate("window.Telegram.WebApp.colorScheme='dark';window.__miniEvents.themeChanged()")
                    expect(mini.locator('body')).to_have_attribute('data-miniapp-theme','dark')
                    assert mini.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--mini-safe-top").trim()')=='30px'
                    mini.screenshot(path=str(output/'18-miniapp-dark.png'),full_page=True)
                    check_navigation_icons(mini)
                    mini.screenshot(path=str(output/'22-miniapp-menu-dark.png'),full_page=True)
                    mini.keyboard.press('Escape')
                    # Полноэкранный Telegram: системная строка + собственные кнопки клиента.
                    go(mini,'channels')
                    mini.evaluate("""() => {
                        const app=window.Telegram.WebApp;app.isFullscreen=true;
                        app.safeAreaInset.top=24;app.contentSafeAreaInset.top=56;
                        window.__miniEvents.fullscreenChanged();
                        const native=document.createElement('div');native.id='telegram-controls-fixture';
                        native.style.cssText='position:fixed;inset:0 0 auto;height:80px;z-index:10000;background:#071526;color:#9ab4c9;font:12px sans-serif;display:flex;align-items:center;justify-content:space-between;padding:24px 16px 0;box-sizing:border-box';
                        native.textContent='← Назад (Telegram)                         ⋯';document.body.append(native);
                    }""")
                    for width in (320,390):
                        mini.set_viewport_size({'width':width,'height':844})
                        for scroll in (0,500):
                            mini.evaluate('(y)=>window.scrollTo(0,y)',scroll)
                            expect(mini.locator('.cabinet-topbar')).to_be_visible()
                            rect=mini.locator('.cabinet-topbar').bounding_box()
                            assert abs(rect['y']-80)<=1,('Fullscreen header overlaps Telegram',width,scroll,rect)
                            assert mini.evaluate("""() => ['#refresh','.screen-back'].every(selector=>{
                                const button=document.querySelector(selector),r=button.getBoundingClientRect();
                                return r.top>=80&&button.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
                            })""")
                        mini.evaluate('window.scrollTo(0,0)')
                        mini.screenshot(path=str(output/('41-miniapp-safe-header-'+str(width)+'.png')))
                    mini.evaluate("window.Telegram.WebApp.contentSafeAreaInset.top=64;window.__miniEvents.contentSafeAreaChanged()")
                    assert abs(mini.locator('.cabinet-topbar').bounding_box()['y']-88)<=1
                    mini.evaluate("window.Telegram.WebApp.safeAreaInset.top=32;window.__miniEvents.safeAreaChanged()")
                    assert abs(mini.locator('.cabinet-topbar').bounding_box()['y']-96)<=1
                    mini.evaluate("""() => {
                        document.getElementById('telegram-controls-fixture').remove();
                        const app=window.Telegram.WebApp;app.isFullscreen=false;
                        app.safeAreaInset.top=18;app.contentSafeAreaInset.top=0;
                        window.__miniEvents.fullscreenChanged();window.scrollTo(0,0);
                    }""")
                    assert abs(mini.locator('.cabinet-topbar').bounding_box()['y']-18)<=1
                    mini.evaluate("window.Telegram.WebApp.contentSafeAreaInset.top=12;window.__miniEvents.contentSafeAreaChanged()")
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
                    go(other,'profile')
                    other.get_by_role('button',name='Выйти из аккаунта',exact=True).click()
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
                admin.locator('#settings-tariffs-tab').click();open_settings(admin)
                admin.locator('#tariff-admin-select').select_option(private_tariff)
                expect(admin.locator('#tariff-admin-access')).to_be_visible()
                expect(admin.locator('#tariff-admin-rub')).to_have_attribute('readonly','')
                expect(admin.locator('#tariff-admin-stars')).to_have_attribute('readonly','')
                expect(admin.locator('#tariff-admin-stars')).to_have_attribute('placeholder','Не требуется')
                admin.locator('#tariff-admin-hours').fill('48')
                with admin.expect_response(lambda r:'/admin/tariffs/' in r.url and r.request.method=='PUT') as private_saved:
                    admin.locator('#tariff-admin-form button[type=submit]').click()
                assert private_saved.value.status==200 and private_saved.value.json()['admin_only'] is True
                assert private_saved.value.json()['price_kopecks']==0 and private_saved.value.json()['duration_seconds']==172800
                expect(admin.locator('#tariff-admin-hours')).to_have_value('48')
                expect(admin.locator('#tariff-admin-form button[type=submit]')).to_be_enabled()
                admin.set_viewport_size({'width':390,'height':844})
                admin.screenshot(path=str(output/'26-admin-free-tariff-mobile.png'), full_page=True)
                admin.locator('#tariff-admin-select').select_option('00000000-0000-4000-8000-000000000001')
                admin.locator('#tariff-admin-rub').fill('0')
                admin.locator('#tariff-admin-stars').fill('0')
                with admin.expect_response(lambda r:'/admin/tariffs/' in r.url and r.request.method=='PUT') as saved:
                    admin.locator('#tariff-admin-form button[type=submit]').click()
                assert saved.value.status==200 and saved.value.json()['price_kopecks']==0
                expect(admin.locator('#notice')).to_contain_text('Настройки тарифа сохранены')
                expect(admin.locator('#tariff-admin-rub')).to_have_value('0.00')
                toggle = admin.locator('#tariff-admin-active').bounding_box()
                assert toggle['width'] <= 48 and toggle['height'] <= 32, toggle
                admin.locator('#notice>button').click()
                admin.set_viewport_size({'width':390,'height':844})
                admin.screenshot(path=str(output/'23-settings-free-mobile.png'), full_page=True)
                assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth')
                for width in (320,390):
                    admin.set_viewport_size({'width':width,'height':844})
                    admin.locator('#tariff-admin-actions').screenshot(path=str(output/f'tariff-actions-{width}.png'))
                admin.locator('#settings-payments-tab').click();open_settings(admin)
                with patch.dict(os.environ, {'PUSHOK_CONTROL_DIR':str(Path(PostgresIntegration.storage.name)/'control'),
                                           'TELEGRAM_BOT_TOKEN':'123456:fixture_token_for_browser'}):
                    admin.locator('#enable-stars').check()
                    admin.locator('#payment-switches button').click()
                    expect(admin.locator('#notice')).to_contain_text('Настройки оплаты сохранены')
                    expect(admin.locator('#enable-stars')).to_be_checked()
                    connection=admin.locator('#sbp-connection')
                    expect(connection.locator('summary')).to_contain_text('Подключение СБП')
                    connection.locator('details').evaluate('(node)=>node.open=true')
                    expect(connection.get_by_label('Shop ID магазина',exact=True)).to_be_visible()
                    expect(connection.get_by_label('Секретный ключ ЮKassa',exact=True)).to_have_value('')
                    expect(connection.get_by_label('Секретный ключ ЮKassa',exact=True)).to_have_attribute('type','password')
                    expect(connection.get_by_role('button',name='Проверить и сохранить',exact=True)).to_be_visible()
                    connection.screenshot(path=str(output/'46-sbp-connection-mobile.png'))
                    admin.screenshot(path=str(output/'24-payment-controls-mobile.png'), full_page=True)
                    # Каталог искусственный: проверяем выбор настоящей картинки в форме без Telegram.
                    icon_bytes=io.BytesIO();Image.new('RGBA',(48,48),'orange').save(icon_bytes,format='PNG')
                    catalog=[{'id':'123456789','emoji':'🐱','image':'data:image/png;base64,'+base64.b64encode(icon_bytes.getvalue()).decode()}]
                    api.control.write('emoji-catalog.json',catalog)
                    admin.locator('#settings-system-tab').click();open_settings(admin)
                    with patch.object(api.emoji_settings,'fetch',return_value=catalog):
                        admin.locator('#icons-import').click()
                        expect(admin.locator('.emoji-gallery img')).to_have_count(1)
                        admin.locator('#icons-mapping select[name=help]').select_option('123456789')
                        admin.locator('#icons-form button[type=submit]').click()
                        expect(admin.locator('#icons-status')).to_contain_text('Сохранено')
                        assert api.control.read('emoji-map.json')['help']=='123456789'
                        admin.screenshot(path=str(output/'25-icon-settings-mobile.png'),full_page=True)
                # CRUD через реальный API, сохранённые сессии и видимые формы.
                def authenticated(role, method, path, body=None):
                    page = admin if role == 'admin' else advertiser
                    response = page.request.fetch(base + '/api/v1' + path, method=method,
                        headers={'Authorization':'Bearer '+sessions[role]}, data=body)
                    assert response.ok, (path, response.status, response.text())
                    return response.json()

                managed_channel=authenticated('advertiser','POST','/channels',{'title':'Канал управления','telegram_url':'https://t.me/pushok_management_ui'})
                advertiser.set_viewport_size({'width':390,'height':844})
                refresh(advertiser)
                go(advertiser,'channels')
                row=advertiser.locator('[data-page=channels] .channel-tile').filter(has_text='Канал управления')
                expect(row.locator('.channel-avatar img')).to_be_visible()
                assert row.locator('.channel-avatar img').evaluate('(img)=>img.complete && img.naturalWidth===160')
                advertiser.screenshot(path=str(output/'47-channel-logo-mobile.png'),full_page=True)
                row.get_by_role('button',name='Изменить канал',exact=True).click()
                row.get_by_label('Название канала',exact=True).fill('Изменённый канал управления')
                row.get_by_role('button',name='Сохранить изменения',exact=True).click()
                expect(advertiser.locator('[data-page=channels]')).to_contain_text('Изменённый канал управления')
                row=advertiser.locator('[data-page=channels] .channel-tile').filter(has_text='Изменённый канал управления')
                advertiser.once('dialog',lambda dialog:dialog.accept())
                row.get_by_role('button',name='Удалить канал',exact=True).click()
                expect(advertiser.locator('[data-page=channels]')).not_to_contain_text('Изменённый канал управления')
                go(advertiser,'archive')
                advertiser.get_by_role('button',name='Восстановить',exact=True).click()
                expect(advertiser.locator('[data-page=archive]')).to_contain_text('Удалённых объектов нет')
                public_tariffs=authenticated('advertiser','GET','/me/tariffs')
                edited=authenticated('advertiser','POST','/campaigns',{'channel_id':managed_channel['id'],'tariff_id':public_tariffs[0]['id']})
                refresh(advertiser)
                go(advertiser,'create/'+edited['id'])
                advertiser.locator('summary').filter(has_text='Изменить или удалить заявку').click()
                # Keep the real pointer above the fixed mobile navigation during focus scroll.
                advertiser.get_by_role('button',name='Изменить тариф и дату',exact=True).evaluate("node=>node.scrollIntoView({block:'center',behavior:'instant'})")
                advertiser.get_by_role('button',name='Изменить тариф и дату',exact=True).click()
                advertiser.get_by_label('Желаемое начало',exact=True).fill(tomorrow+'T16:00')
                advertiser.get_by_role('button',name='Сохранить изменения',exact=True).click()
                expect(advertiser.locator('#notice')).to_contain_text('Изменения сохранены')
                advertiser.locator('summary').filter(has_text='Изменить или удалить заявку').click()
                advertiser.once('dialog',lambda dialog:dialog.accept())
                advertiser.get_by_role('button',name='🗑️ Удалить',exact=True).click()
                expect(advertiser.locator('#notice')).to_contain_text('Объект перемещён')
                go(advertiser,'archive')
                advertiser.get_by_role('button',name='Восстановить',exact=True).click()
                expect(advertiser.locator('[data-page=archive]')).to_contain_text('Удалённых объектов нет')

                admin.set_viewport_size({'width':390,'height':844})
                go(admin,'sponsors')
                admin.get_by_role('tab', name='Узлы', exact=True).click()
                admin.locator('#node-admin>.panel-heading').get_by_role('button',name='Добавить узел',exact=True).click()
                admin.locator('#node-form [name=name]').fill('Узел управления')
                admin.locator('#node-form [name=host]').fill('management-ui.example.org')
                admin.locator('#engine-mode').select_option('auto')
                admin.locator('#node-form button[type=submit]').click()
                node=admin.locator('#nodes-list .card').filter(has_text='Узел управления')
                node.get_by_role('button',name='Изменить узел',exact=True).click()
                node.get_by_label('Название узла',exact=True).fill('Изменённый узел управления')
                node.get_by_role('button',name='Сохранить изменения',exact=True).click()
                node=admin.locator('#nodes-list .card').filter(has_text='Изменённый узел управления')
                expect(node).to_be_visible()
                admin.once('dialog',lambda dialog:dialog.accept())
                node.get_by_role('button',name='Удалить узел',exact=True).click()
                expect(admin.locator('#notice')).to_contain_text('Объект перемещён')
                go(admin,'archive')
                admin.get_by_role('button',name='Восстановить',exact=True).click()
                expect(admin.locator('[data-page=archive]')).to_contain_text('Удалённых объектов нет')

                # Удалённый счёт не возвращается в рабочую карточку после refresh/reload.
                archive_channel=authenticated('advertiser','POST','/channels',{'title':'Счёт в архиве','telegram_url':'https://t.me/pushok_archive_invoice_ui'})
                archive_order=authenticated('advertiser','POST','/campaigns',{'channel_id':archive_channel['id'],'tariff_id':public_tariffs[0]['id']})
                archive_id=archive_order['id']
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    connection.execute("UPDATE campaigns SET moderation_status='approved',payment_status='awaiting_payment',price_kopecks_snapshot=30000 WHERE id=%s",(archive_id,))
                    attempt_id=connection.execute("INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency) VALUES(%s,'telegram_stars','telegram_bot',37,'XTR') RETURNING id",(archive_id,)).fetchone()[0]
                refresh(admin)
                go(admin,'moderation/'+archive_id)
                archive_card=admin.locator('.approval-card[data-campaign-id="'+archive_id+'"]')
                expect(archive_card).to_contain_text('Ожидаем оплату')
                archive_card.locator('summary').filter(has_text='Изменить или удалить заявку').click()
                admin.once('dialog',lambda dialog:dialog.accept())
                delete_archive=archive_card.get_by_role('button',name='🗑️ Удалить',exact=True)
                expect(delete_archive).to_be_visible()
                expect(delete_archive).to_be_enabled()
                # После DELETE карточка исчезает до завершения pointer-action Playwright.
                # Проверяем один настоящий обработчик и его HTTP-результат без повторного клика.
                with admin.expect_response(lambda r:r.url.endswith('/campaigns/'+archive_id) and r.request.method=='DELETE') as removed:
                    delete_archive.evaluate('(button)=>button.click()')
                assert removed.value.ok
                expect(admin.locator('#notice')).to_contain_text('Объект перемещён')
                expect(archive_card).to_have_count(0)
                assert archive_id not in {c['id'] for c in authenticated('admin','GET','/operator/campaigns')}
                refresh(admin)
                expect(archive_card).to_have_count(0)
                admin.reload()
                expect(admin.locator('[data-page=moderation]')).to_be_visible()
                expect(archive_card).to_have_count(0)
                pending=admin.locator('.archived-review')
                pending.locator('summary').click()
                pending_row=pending.locator('.approval-summary').filter(has_text='Счёт в архиве')
                expect(pending_row).to_contain_text('Счёт ещё не закрыт')
                pending_row.get_by_role('link',name='Открыть для завершения').click()
                expect(archive_card).to_contain_text('Заявка уже удалена')
                expect(archive_card.get_by_role('button',name='🗑️ Удалить',exact=True)).to_have_count(0)
                expect(archive_card.get_by_role('button',name='Назначить слот',exact=True)).to_have_count(0)
                for width in (320,390):
                    admin.set_viewport_size({'width':width,'height':844})
                    assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
                    admin.screenshot(path=str(output/('40-archived-invoice-'+str(width)+'.png')),full_page=True)
                go(admin,'archive')
                row=admin.locator('[data-page=archive] [data-archive-id="'+archive_id+'"]')
                expect(row).to_contain_text('Счёт в архиве')
                row.get_by_role('button',name='Незавершённые действия',exact=True).click()
                expect(archive_card).to_contain_text('Заявка уже удалена')
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    assert connection.execute('SELECT status FROM payment_attempts WHERE id=%s',(attempt_id,)).fetchone()[0]=='created'
                    connection.execute("UPDATE payment_attempts SET status='failed' WHERE id=%s",(attempt_id,))
                refresh(admin)
                go(admin,'moderation')
                expect(admin.locator('.archived-review').filter(has_text='Счёт в архиве')).to_have_count(0)
                go(admin,'archive')
                row=admin.locator('[data-page=archive] [data-archive-id="'+archive_id+'"]')
                row.get_by_role('button',name='Восстановить',exact=True).click()
                expect(admin.locator('[data-page=archive]')).not_to_contain_text('Счёт в архиве')
                go(admin,'moderation/'+archive_id)
                expect(archive_card).to_contain_text('Ожидаем оплату')
                expect(archive_card.locator('.approval-archive-notice')).to_have_count(0)
                admin.set_viewport_size({'width':390,'height':844})

                go(admin,'sponsors')
                admin.evaluate('window.scrollTo(0,600)')
                expect(admin.locator('.cabinet-topbar')).to_be_visible()
                assert abs(admin.locator('.cabinet-topbar').bounding_box()['y']) <= 2
                admin.locator('.mobile-bottom-nav a[data-route=profile]').click()
                expect(admin.locator('[data-page=profile]')).to_be_visible()
                expect(admin.locator('.profile-menu a[href="#/help"]')).to_be_visible()
                assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
                admin.screenshot(path=str(output/'31-sticky-menu-help-mobile.png'),full_page=True)
                # Update discovery and installation are independent, with no host worker in this test.
                expect(admin.locator('#software-installed')).to_have_text('v'+api.app.version)
                expect(admin.locator('#software-release-status')).to_contain_text('актуальная')
                expect(admin.locator('#software-update')).to_be_disabled()
                expect(advertiser.locator('#software-updates')).to_be_hidden()
                control_fixture={'version':api.app.version,'stars':False,'sbp':False,'sbp_configured':False,
                                 'sbp_test_mode':False,'updater_installed':True,'requested':False,
                                 'update':{'state':'succeeded','at':'2026-11-15T14:00:00+00:00'}}
                admin.route('**/api/v1/admin/control',lambda route:route.fulfill(json=control_fixture))
                release_fixture.return_value={**release_fixture.return_value,'state':'available','available_version':'1.05'}
                admin.locator('#software-check').click()
                expect(admin.locator('#software-update')).to_be_enabled()
                expect(admin.locator('#software-update')).to_have_attribute('aria-label','Установить v1.05')
                expect(admin.locator('#software-update-status')).to_contain_text('Последняя установка завершена')
                expect(admin.locator('#software-available')).to_have_text('v1.05')
                for width in (390,320):
                    admin.set_viewport_size({'width':width,'height':844})
                    assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    for action in admin.locator('.update-actions button').all():
                        rect=action.bounding_box()
                        assert rect['x']>=0 and rect['x']+rect['width']<=width+1,rect
                    admin.locator('#software-updates').screenshot(path=str(output/f'updates-profile-{width}.png'))
                go(admin,'dashboard')
                expect(admin.locator('#software-update-notice')).to_be_visible()
                admin.screenshot(path=str(output/'updates-notification-mobile.png'),full_page=True)
                admin.locator('#software-update-notice a').click()
                expect(admin.locator('[data-page=profile]')).to_be_visible()
                expect(admin.locator('#software-updates-title')).to_be_in_viewport()
                admin.route('**/api/v1/admin/control/release',lambda route:route.fulfill(status=503,json={'detail':'unavailable'}))
                admin.locator('#software-check').click()
                expect(admin.locator('#software-release-status')).to_contain_text('Не удалось проверить')
                expect(admin.locator('#software-update')).to_be_disabled()
                expect(admin.locator('#software-update-notice')).to_be_hidden()
                expect(admin.locator('#software-available')).to_have_text('v1.05 (ранее)')
                admin.unroute('**/api/v1/admin/control/release')
                admin.locator('#software-check').click()
                expect(admin.locator('#software-update')).to_be_enabled()
                requests=[]
                def request_update(route):
                    requests.append(route.request.method)
                    control_fixture['requested']=True
                    route.fulfill(status=202,json={'queued':True})
                admin.route('**/api/v1/admin/control/update',request_update)
                admin.locator('#software-update').click()
                expect(admin.locator('#software-update')).to_be_disabled()
                expect(admin.locator('#software-update-status')).to_contain_text('Запрос принят')
                assert requests==['POST']
                control_fixture['requested']=False
                control_fixture['update']={'state':'failed','stage':'build'}
                admin.locator('#software-check').click()
                expect(admin.locator('#software-update-status')).to_contain_text('сборка')
                expect(admin.locator('#software-update')).to_be_enabled()
                release_fixture.return_value={**release_fixture.return_value,'state':'current','available_version':api.app.version}
                admin.locator('#software-check').click()
                expect(admin.locator('#software-update')).to_be_disabled()
                expect(admin.locator('#software-update-notice')).to_be_hidden()
                admin.unroute('**/api/v1/admin/control')
                admin.unroute('**/api/v1/admin/control/update')

                # Constructor creates real free/public and paid/inactive tariffs through the API.
                go(admin,'settings')
                admin.locator('#settings-tariffs-tab').click();open_settings(admin)
                admin.locator('#tariff-admin-new').click()
                expect(admin.locator('#tariff-admin-delete')).to_be_hidden()
                admin.locator('#tariff-admin-name').fill('Бесплатные два дня')
                admin.locator('#tariff-admin-hours').fill('2')
                admin.locator('#tariff-admin-unit').select_option('86400')
                admin.locator('#tariff-admin-free').check()
                expect(admin.locator('#tariff-admin-rub')).to_have_value('0.00')
                expect(admin.locator('#tariff-admin-preview')).to_contain_text('Бесплатно')
                admin.locator('#settings-payments-tab').click();open_settings(admin)
                admin.locator('#settings-tariffs-tab').click();open_settings(admin)
                expect(admin.locator('#tariff-admin-name')).to_have_value('Бесплатные два дня')
                for width in (390,320):
                    admin.set_viewport_size({'width':width,'height':844})
                    assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    admin.screenshot(path=str(output/f'tariff-builder-{width}.png'),full_page=True)
                with admin.expect_response(lambda r:r.url.endswith('/admin/tariffs') and r.request.method=='POST') as created_tariff:
                    admin.locator('#tariff-admin-save').click()
                assert created_tariff.value.status==201
                free_tariff=created_tariff.value.json()
                assert free_tariff['price_kopecks']==0 and free_tariff['stars_price_xtr'] is None
                assert free_tariff['duration_seconds']==172800 and not free_tariff['admin_only']
                expect(admin.locator('#tariff-admin-select')).to_have_value(free_tariff['id'])
                expect(admin.locator('#notice')).to_contain_text('Тариф создан')
                refresh(advertiser)
                go(advertiser,'create')
                advertiser.locator('#tariff-select').select_option(free_tariff['id'])
                expect(advertiser.locator('#order-summary')).to_contain_text('Бесплатно')
                admin.locator('#tariff-admin-new').click()
                admin.locator('#tariff-admin-name').fill('Неделя рекламы')
                admin.locator('#tariff-admin-hours').fill('7')
                admin.locator('#tariff-admin-rub').fill('900')
                admin.locator('#tariff-admin-stars').fill('500')
                admin.locator('#tariff-admin-active').uncheck()
                with admin.expect_response(lambda r:r.url.endswith('/admin/tariffs') and r.request.method=='POST') as created_paid:
                    admin.locator('#tariff-admin-save').click()
                assert created_paid.value.status==201
                paid_tariff=created_paid.value.json()
                assert paid_tariff['price_kopecks']==90000 and paid_tariff['stars_price_xtr']==500
                assert paid_tariff['duration_seconds']==604800 and not paid_tariff['is_active']
                expect(admin.locator('#notice')).to_contain_text('Тариф создан')
                assert paid_tariff['id'] not in [t['id'] for t in authenticated('advertiser','GET','/me/tariffs')]
                admin.locator('#tariff-admin-new').click()
                admin.locator('#tariff-admin-audience').select_option('admin')
                expect(admin.locator('#tariff-admin-rub')).to_have_value('0.00')
                expect(admin.locator('#tariff-admin-free')).to_be_checked()
                expect(admin.locator('#tariff-admin-free')).to_be_disabled()
                admin.locator('#tariff-admin-cancel').click()
                expect(admin.locator('#tariff-admin-select')).to_have_value(paid_tariff['id'])

                # История приёмки: настоящая форма/API, сохранность черновика и отрицательный результат.
                admin.locator('#settings-system-tab').click();open_settings(admin)
                admin.locator('#launch-refresh').click()
                expect(admin.locator('#launch-readiness')).to_contain_text('Бесплатный пилот')
                expect(admin.locator('#launch-readiness')).to_contain_text('Платный запуск')
                admin.locator('#launch-check-details>summary').click()
                admin.locator('#launch-check-type').select_option('backup_restore')
                admin.locator('#launch-check-result').select_option('passed')
                local_check=admin.evaluate("new Date(Date.now()-120000-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16)")
                admin.locator('#launch-check-at').fill(local_check)
                report_note='Тестовая база и файлы восстановлены, количества записей и вход проверены. <b>Текст, не HTML</b>'
                admin.locator('#launch-check-note').fill(report_note)
                admin.locator('#launch-check-vps').check()
                admin.locator('#launch-check-steps').check()
                with admin.expect_response(lambda r:r.url.endswith('/admin/launch-readiness') and r.request.method=='GET'):
                    admin.locator('#launch-refresh').click()
                expect(admin.locator('#launch-check-note')).to_have_value(report_note)
                expect(admin.locator('#launch-check-type')).to_have_value('backup_restore')
                for width in (390,320):
                    admin.set_viewport_size({'width':width,'height':844})
                    assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    admin.locator('#launch-check-details').screenshot(path=str(output/f'acceptance-form-{width}.png'))
                with admin.expect_response(lambda r:r.url.endswith('/admin/launch-readiness/checks') and r.request.method=='POST') as acceptance:
                    admin.locator('#launch-check-save').click()
                assert acceptance.value.status==201,acceptance.value.text()
                expect(admin.locator('#notice')).to_contain_text('Отчёт сохранён')
                backup=admin.locator('#launch-readiness [data-check=backup_restore]')
                expect(backup).to_have_attribute('data-status','reported')
                backup.locator('summary').click()
                expect(backup).to_contain_text('<b>Текст, не HTML</b>')
                expect(backup.locator('b')).to_have_count(0)
                admin.locator('#launch-check-details>summary').click()
                admin.locator('#launch-check-type').select_option('backup_restore')
                admin.locator('#launch-check-result').select_option('failed')
                admin.locator('#launch-check-at').fill(local_check)
                admin.locator('#launch-check-note').fill('Тест: повторное восстановление не прошло, обнаружено расхождение записей.')
                admin.locator('#launch-check-vps').check()
                with admin.expect_response(lambda r:r.url.endswith('/admin/launch-readiness/checks') and r.request.method=='POST') as failed_acceptance:
                    admin.locator('#launch-check-save').click()
                assert failed_acceptance.value.status==201
                expect(backup).to_have_attribute('data-status','failed')
                for width in (390,320):
                    admin.set_viewport_size({'width':width,'height':844})
                    assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    admin.locator('#launch-readiness').screenshot(path=str(output/f'acceptance-status-{width}.png'))
                admin.locator('[aria-label="Готовность Pushok Core"]>summary').click()
                expect(admin.locator('#core-readiness')).to_contain_text('не описывают работающий MTProxy Classic')
                expect(admin.locator('#core-readiness button')).to_have_count(0)


                admin.keyboard.press('Escape')
                expect(admin.locator('.help-popup')).to_be_hidden()
                admin.evaluate('window.scrollTo(0,0)')
                go(advertiser,'channels')
                # Четвёртый канал добавлен сценарием удаления неоплаченной заявки.
                expect(advertiser.locator('.channel-manager').get_by_role('button',name='Изменить канал',exact=True)).to_have_count(4)
                for width in (390,320):
                    advertiser.set_viewport_size({'width':width,'height':844})
                    for channel_tools in advertiser.locator('.channel-tools').all():
                        if channel_tools.get_attribute('open') is None:
                            channel_tools.locator('summary').click()
                    for action in advertiser.locator('.channel-manager .management-actions>button').all():
                        rect=action.bounding_box()
                        assert rect['x']>=0 and rect['x']+rect['width']<=width+1,rect
                    assert advertiser.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                advertiser.set_viewport_size({'width':390,'height':844})
                advertiser.screenshot(path=str(output/'32-management-actions-mobile.png'),full_page=True)
                advertiser.set_viewport_size({'width':1440,'height':1000})
                for width in (320,390):
                    advertiser.set_viewport_size({'width':width,'height':844})
                    go(advertiser,'payments')
                    assert advertiser.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                    for cell in advertiser.locator('[data-page=payments] .campaign-tile').all():
                        rect=cell.bounding_box()
                        assert rect['x']>=0 and rect['x']+rect['width']<=width+1,rect
                advertiser.screenshot(path=str(output/'33-compact-payments-mobile.png'),full_page=True)
                expect(advertiser.locator('.cabinet-sidebar')).to_have_count(0)
                advertiser.screenshot(path=str(output/'34-flat-menu-mobile.png'),full_page=True)
                advertiser.evaluate("notice('Проверка закрытия')")
                advertiser.locator('#notice').get_by_role('button',name='Закрыть уведомление').click()
                expect(advertiser.locator('#notice')).to_be_hidden()
                advertiser.evaluate("notice('Проверка таймера')")
                expect(advertiser.locator('#notice')).to_be_hidden(timeout=7000)
                # Промокод из конструктора администратора — реальный расчёт API на мобильном.
                go(admin,'growth')
                create=admin.locator('[data-page=growth] section').filter(has=admin.get_by_role('heading',name='Создать предложение',exact=True))
                expect(create).to_be_visible()
                create.get_by_label('Название',exact=True).fill('Браузерный промокод')
                create.get_by_label('Промокод',exact=True).fill('BROWSER20')
                create.get_by_label('Скидка, %',exact=True).fill('20')
                create.get_by_role('button',name='Создать предложение',exact=True).click()
                expect(admin.locator('[data-page=growth] .growth-code').filter(has_text='BROWSER20')).to_be_visible()
                admin.set_viewport_size({'width':390,'height':844})
                admin.screenshot(path=str(output/'35-growth-admin-mobile.png'),full_page=True)
                assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                go(advertiser,'growth')
                expect(advertiser.locator('[data-page=growth]')).to_contain_text('Мои купоны')
                expect(advertiser.get_by_role('heading',name='Создать предложение',exact=True)).to_have_count(0)
                advertiser.screenshot(path=str(output/'36-growth-user-mobile.png'),full_page=True)
                growth_tariff=uuid4()
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    connection.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr,currency) VALUES(%s,'Проверка скидки',3600,10000,100,'RUB')",(growth_tariff,))
                refresh(advertiser)
                go(advertiser,'create')
                advertiser.locator('#tariff-select').select_option(str(growth_tariff))
                advertiser.locator('#order-promo-section > summary').click()
                advertiser.locator('#order-promo').fill('BROWSER20')
                advertiser.locator('#order-quote').click()
                expect(advertiser.locator('#order-quote-status')).to_contain_text('Браузерный промокод')
                expect(advertiser.locator('#order-summary')).to_contain_text('20%')
                expect(advertiser.locator('#order-summary')).to_contain_text('80')
                expect(advertiser.locator('#order-summary')).to_contain_text('80 XTR')
                advertiser.screenshot(path=str(output/'37-growth-quote-mobile.png'),full_page=True)
                assert advertiser.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                advertiser.locator('#order-submit').click()
                expect(advertiser.locator('#order-result')).to_contain_text('Заявка на проверке')
                growth_cid=advertiser.evaluate("location.hash.split('/')[2]")
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    assert connection.execute('SELECT price_kopecks_snapshot,stars_price_snapshot FROM campaigns WHERE id=%s',(growth_cid,)).fetchone()==(8000,80)
                    assert connection.execute('SELECT count(*) FROM growth_redemptions WHERE campaign_id=%s',(growth_cid,)).fetchone()[0]==1
                # Баланс: реальная запись администратора, оплата владельца и возврат.
                go(admin,'wallet')
                open_wallet_tools(admin)
                expect(admin.locator('[data-page=wallet]')).to_contain_text('Управление балансами')
                admin.get_by_label('Пользователь: @никнейм или ID',exact=True).fill('7909549877')
                admin.get_by_role('button',name='Найти пользователя',exact=True).click()
                expect(admin.locator('.wallet-lookup-error')).to_contain_text('Пользователь не найден')
                go(admin,'campaigns');go(admin,'wallet');open_wallet_tools(admin)
                expect(admin.locator('.wallet-lookup-error')).to_be_visible()
                expect(admin.get_by_role('button',name='Найти пользователя',exact=True)).to_be_visible()
                admin.get_by_role('button',name='К моему балансу',exact=True).click()
                expect(admin.locator('.wallet-lookup-error')).to_have_count(0)
                open_wallet_tools(admin)
                admin.get_by_label('Пользователь: @никнейм или ID',exact=True).fill('99116000')
                admin.get_by_role('button',name='Найти пользователя',exact=True).click()
                expect(admin.get_by_role('heading',name='Ручная операция',exact=True)).to_be_visible()
                admin.get_by_label('Сумма операции',exact=True).fill('300')
                admin.get_by_label('Основание (видно пользователю)',exact=True).fill('Подтверждённый перевод для браузерной проверки')
                admin.get_by_role('button',name='Провести операцию',exact=True).click()
                expect(admin.locator('.wallet-entry')).to_contain_text('Подтверждённый перевод')
                expect(admin.locator('[data-page=wallet]')).to_contain_text('Сверка: остатки совпадают с журналом.')
                admin.screenshot(path=str(output/'38-wallet-admin-mobile.png'),full_page=True)
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    connection.execute("UPDATE campaigns SET moderation_status='approved',payment_status='awaiting_payment' WHERE id=%s",(growth_cid,))
                refresh(advertiser)
                go(advertiser,'payments/'+growth_cid)
                expect(advertiser.get_by_role('button',name='Оплатить с баланса',exact=True)).to_be_enabled()
                advertiser.get_by_role('button',name='Оплатить с баланса',exact=True).click()
                expect(advertiser.locator('[data-page=payments]')).to_contain_text('Оплачено с баланса')
                go(advertiser,'wallet')
                open_wallet_tools(advertiser)
                expect(advertiser.locator('.wallet-balances').first).to_contain_text('220')
                expect(advertiser.locator('[data-wallet-section=admin]')).to_have_count(0)
                expect(advertiser.get_by_label('Автоматически оплачивать мои одобренные заявки')).not_to_be_checked()
                for width in (320,390):
                    advertiser.set_viewport_size({'width':width,'height':844})
                    assert advertiser.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                advertiser.screenshot(path=str(output/'39-wallet-user-mobile.png'),full_page=True)
                # Главная показывает тот же серверный остаток; ошибка API не превращается в ноль.
                go(advertiser,'dashboard')
                expect(advertiser.locator('#dashboard-wallet')).to_have_count(0)
                expect(advertiser.locator('.home-leaders')).to_have_attribute('aria-busy','false')
                # Регрессия видео: задержанный ответ не меняет геометрию и DOM карточек.
                expect(advertiser.locator('#dashboard-referral .referral-overview')).to_have_attribute('aria-busy','false')
                advertiser.evaluate("""() => {
                    window.__originalFetch=window.fetch;
                    window.__pendingRefresh=[];
                    window.fetch=(url,options)=>['/api/v1/wallet','/api/v1/growth/me'].includes(String(url))?new Promise(resolve=>{
                        window.__pendingRefresh.push(()=>window.__originalFetch(url,options).then(resolve));
                    }):window.__originalFetch(url,options);
                    const tile=document.querySelector('[data-page=dashboard] .campaign-tile');
                    const y=tile.getBoundingClientRect().top,scroll=scrollY;
                    const referral=document.querySelector('#dashboard-referral .referral-overview'),invite=referral.querySelector('.referral-link');
                    window.__stable={tile,referral,invite,y,scroll,samples:[],stop:false};
                    const sample=()=>{const s=window.__stable;s.samples.push([s.tile.getBoundingClientRect().top,scrollY]);if(!s.stop)requestAnimationFrame(sample);};
                    requestAnimationFrame(sample);void refreshStatuses();
                }""")
                advertiser.wait_for_function('window.__pendingRefresh.length===1')
                advertiser.wait_for_timeout(250)
                advertiser.evaluate('() => Promise.all(window.__pendingRefresh.map(release=>release()))')
                assert advertiser.evaluate("""() => {
                    const s=window.__stable;s.stop=true;window.fetch=window.__originalFetch;
                    return s.referral===document.querySelector('#dashboard-referral .referral-overview') &&
                      s.invite===document.querySelector('#dashboard-referral .referral-link') &&
                      s.tile===document.querySelector('[data-page=dashboard] .campaign-tile') &&
                      s.samples.length>2 && s.samples.every(([y,scroll])=>Math.abs(y-s.y)<1&&Math.abs(scroll-s.scroll)<1);
                }"""), 'Background refresh moved the dashboard or recreated its cards'
                for width in (320,390,1280):
                    advertiser.set_viewport_size({'width':width,'height':900})
                    assert advertiser.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                    advertiser.screenshot(path=str(output/('dashboard-v122-'+str(width)+'.png')),full_page=False)
                advertiser.set_viewport_size({'width':390,'height':844})
                advertiser.locator('.mobile-bottom-nav a[data-route=wallet]').click()
                expect(advertiser.locator('.wallet-balances')).to_be_visible()
                go(admin,'wallet')
                open_wallet_tools(admin)
                expect(admin.get_by_label('Основание возврата',exact=True)).to_be_visible()
                admin.get_by_label('Основание возврата',exact=True).fill('Отмена тестового размещения')
                admin.get_by_role('button',name='Вернуть на баланс',exact=True).click()
                expect(admin.locator('[data-page=wallet]')).to_contain_text('Возвращено на баланс')
                advertiser.get_by_role('button',name='Обновить баланс',exact=True).click()
                expect(advertiser.locator('.wallet-balances').first).to_contain_text('300')
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    assert connection.execute('SELECT status FROM wallet_payments WHERE campaign_id=%s',(growth_cid,)).fetchone()[0]=='refunded'
                # Реферальная награда: настройки через UI, приглашение друга и реальная проводка.
                go(admin,'growth')
                admin.locator('[data-page=growth]').get_by_label('Награда за приглашённого',exact=True).select_option('bonus')
                expect(admin.locator('[data-page=growth]').get_by_label('Скидка за приглашённого, %',exact=True)).to_be_hidden()
                admin.locator('[data-page=growth]').get_by_label('Награда на бонусный баланс, ₽',exact=True).fill('125.50')
                admin.locator('[data-page=growth]').get_by_label('Принимать новые приглашения',exact=True).check()
                # Значения формы уже заполнены: они не подтверждают завершение сохранения.
                # Другой пользователь должен читать условия после ответа настоящего API.
                with admin.expect_response(lambda r:r.url.endswith('/api/v1/admin/growth/settings') and r.request.method=='PUT') as referral_saved:
                    admin.get_by_role('button',name='Сохранить условия',exact=True).click()
                assert referral_saved.value.status==200, referral_saved.value.text()
                expect(admin.locator('[data-page=growth]').get_by_label('Награда за приглашённого',exact=True)).to_have_value('bonus')
                expect(admin.locator('[data-page=growth]').get_by_label('Награда на бонусный баланс, ₽',exact=True)).to_have_value('125.50')
                go(advertiser,'growth')
                invite_link=advertiser.locator('[data-page=growth]').get_by_label('Ваша ссылка для друзей',exact=True).get_attribute('href')
                assert len(invite_link.split('r_')[1])==8
                go(advertiser,'dashboard')
                expect(advertiser.locator('#dashboard-referral .referral-overview')).to_be_visible()
                expect(advertiser.locator('#dashboard-referral').get_by_label('Ваша ссылка для друзей',exact=True)).to_have_attribute('href',invite_link)
                assert advertiser.locator('#dashboard-referral').evaluate("e=>e.nextElementSibling.classList.contains('dashboard-help')&&e.nextElementSibling===e.parentElement.lastElementChild")
                share=advertiser.locator('#dashboard-referral').get_by_role('link',name='Поделиться реферальной ссылкой',exact=True)
                expect(share).to_be_visible()
                from urllib.parse import urlparse,parse_qs
                assert parse_qs(urlparse(share.get_attribute('href')).query)['url']==[invite_link]
                advertiser.evaluate("window.__copied='';Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.__copied=value}}})")
                advertiser.locator('#dashboard-referral').get_by_role('button',name='Скопировать реферальную ссылку',exact=True).click()
                assert advertiser.evaluate('window.__copied')==invite_link
                for width in (320,390):
                    advertiser.set_viewport_size({'width':width,'height':844})
                    assert advertiser.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                    advertiser.screenshot(path=str(output/f'48-home-referral-{width}.png'),full_page=True)
                check_navigation_icons(operator)
                go(outsider,'growth')
                outsider.get_by_label('Код пригласившего вас пользователя',exact=True).fill(invite_link.split('r_')[1])
                outsider.get_by_role('button',name='Принять приглашение',exact=True).click()
                expect(outsider.get_by_label('Код пригласившего вас пользователя',exact=True)).to_have_count(0)
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    friend_id=connection.execute('SELECT id FROM users WHERE telegram_user_id=99116003').fetchone()[0]
                    friend_channel=connection.execute("INSERT INTO sponsor_channels(owner_user_id,title,telegram_url) VALUES(%s,'Друг','https://t.me/browser_friend') RETURNING id",(friend_id,)).fetchone()[0]
                    referral_cid=connection.execute("""INSERT INTO campaigns(advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot)
                        VALUES(%s,%s,%s,'Реферальный тест',3600,10000,'RUB') RETURNING id""",(friend_id,friend_channel,growth_tariff)).fetchone()[0]
                    connection.execute("""INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency,status,provider_payment_id,sbp_test_mode)
                        VALUES(%s,'sbp','external_web',10000,'RUB','succeeded',%s,false)""",(referral_cid,'browser-referral-'+str(referral_cid)))
                    connection.execute("UPDATE campaigns SET moderation_status='approved',payment_status='verified',placement_status='completed' WHERE id=%s",(referral_cid,))
                go(advertiser,'wallet')
                open_wallet_tools(advertiser)
                expect(advertiser.locator('.wallet-entry').filter(has_text='Реферальная награда')).to_have_count(1)
                expect(advertiser.locator('.wallet-entry').filter(has_text='Реферальная награда').get_by_role('link',name='Открыть заявку →')).to_have_count(0)
                go(advertiser,'growth')
                expect(advertiser.get_by_role('heading',name='Награда выдана',exact=True)).to_be_visible()
                for page in (admin,advertiser):
                    for width in (320,390):
                        page.set_viewport_size({'width':width,'height':844})
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                admin.screenshot(path=str(output/'40-referral-settings-mobile.png'),full_page=True)
                advertiser.screenshot(path=str(output/'41-referral-reward-mobile.png'),full_page=True)
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    connection.execute("UPDATE campaigns SET payment_status='refunded' WHERE id=%s",(referral_cid,))
                go(advertiser,'wallet')
                open_wallet_tools(advertiser)
                expect(advertiser.locator('.wallet-entry').filter(has_text='Отмена реферальной награды')).to_have_count(1)
                go(advertiser,'growth')
                expect(advertiser.get_by_role('heading',name='Награда отменена после возврата',exact=True)).to_be_visible()
                # Подарочный бонус: конструктор, повтор активации и общий выключатель.
                go(admin,'wallet')
                open_wallet_tools(admin)
                admin.get_by_label('Название подарка',exact=True).fill('Подарок для проверки')
                admin.get_by_label('Подарок, бонусных ₽',exact=True).fill('25.50')
                admin.get_by_label('Количество получателей',exact=True).fill('1')
                admin.get_by_label('Действует до (ваше время)',exact=True).fill((datetime.now(timezone.utc)+timedelta(days=30)).strftime('%Y-%m-%dT%H:%M'))
                admin.get_by_role('button',name='Создать подарочный код',exact=True).click()
                gift_row=admin.locator('.wallet-gift').filter(has_text='Подарок для проверки')
                expect(gift_row).to_be_visible()
                gift_code=gift_row.locator('.growth-code').inner_text()
                admin.screenshot(path=str(output/'42-wallet-gifts-admin-mobile.png'),full_page=True)
                go(advertiser,'wallet')
                open_wallet_tools(advertiser)
                expect(advertiser.get_by_role('link',name='Открыть мои Stars в Telegram ↗')).to_have_attribute('href','tg://settings/stars')
                expect(advertiser.get_by_label('Валюта пополнения',exact=True)).to_have_count(0)
                expect(advertiser.get_by_role('button',name='Пополнить через СБП',exact=True)).to_be_visible()
                for _ in range(2):
                    advertiser.get_by_label('Подарочный код на баланс',exact=True).fill(gift_code)
                    advertiser.evaluate("window.previousGiftPage=document.querySelector('[data-page=wallet] .wallet-page')")
                    advertiser.get_by_role('button',name='Получить подарок',exact=True).click()
                    advertiser.wait_for_function("document.querySelector('[data-page=wallet] .wallet-page') && document.querySelector('[data-page=wallet] .wallet-page') !== window.previousGiftPage")
                    expect(advertiser.locator('.wallet-entry').filter(has_text='Подарок: Подарок для проверки')).to_have_count(1)
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    assert connection.execute("SELECT count(*) FROM wallet_entries WHERE kind='gift_credit'").fetchone()[0]==1
                for width in (320,390):
                    advertiser.set_viewport_size({'width':width,'height':844})
                    assert advertiser.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                advertiser.screenshot(path=str(output/'43-wallet-gift-user-mobile.png'),full_page=True)
                go(admin,'settings')
                admin.locator('#settings-payments-tab').click();open_settings(admin)
                expect(admin.get_by_label('Включить внутренний баланс',exact=True)).to_be_checked()
                admin.get_by_label('Включить внутренний баланс',exact=True).uncheck()
                admin.get_by_role('button',name='Сохранить настройку баланса',exact=True).click()
                expect(admin.locator('.wallet-settings-state')).to_have_text('Баланс выключен')
                go(advertiser,'wallet')
                open_wallet_tools(advertiser)
                expect(advertiser.locator('[data-page=wallet]')).to_contain_text('Внутренний баланс выключен.')
                expect(advertiser.get_by_role('button',name='Пополнить через СБП',exact=True)).to_have_count(0)
                expect(advertiser.get_by_role('button',name='Получить подарок',exact=True)).to_have_count(0)
                expect(advertiser.locator('.wallet-entry').filter(has_text='Подарок: Подарок для проверки')).to_have_count(1)
                advertiser.screenshot(path=str(output/'44-wallet-disabled-mobile.png'),full_page=True)
                admin.get_by_label('Включить внутренний баланс',exact=True).check()
                admin.get_by_role('button',name='Сохранить настройку баланса',exact=True).click()
                expect(admin.locator('.wallet-settings-state')).to_have_text('Баланс включён')
                go(advertiser,'wallet')
                open_wallet_tools(advertiser)
                expect(advertiser.get_by_label('Автоматически оплачивать мои одобренные заявки',exact=True)).not_to_be_checked()
                # Персональный подарок и реальная выгрузка журнала активаций.
                go(outsider,'profile')
                expect(outsider.locator('.profile-personal')).to_contain_text('Telegram ID: 99116003')
                outsider.locator('.profile-personal>summary').click()
                expect(outsider.get_by_role('button',name='Скопировать ID',exact=True)).to_be_visible()
                go(admin,'wallet')
                open_wallet_tools(admin)
                admin.get_by_label('Название подарка',exact=True).fill('Персональный подарок')
                admin.get_by_label('Подарок, бонусных ₽',exact=True).fill('50.25')
                admin.get_by_label('Только одному пользователю Telegram',exact=True).check()
                admin.get_by_label('Получатель подарка: @никнейм или ID',exact=True).fill('99116003')
                expect(admin.get_by_label('Количество получателей',exact=True)).to_be_disabled()
                expect(admin.get_by_label('Количество получателей',exact=True)).to_have_value('1')
                admin.get_by_label('Действует до (ваше время)',exact=True).fill((datetime.now(timezone.utc)+timedelta(days=30)).strftime('%Y-%m-%dT%H:%M'))
                admin.get_by_role('button',name='Создать подарочный код',exact=True).click()
                personal_row=admin.locator('.wallet-gift').filter(has=admin.get_by_role('heading',name='Персональный подарок',exact=True))
                expect(personal_row).to_contain_text('Получатель: Telegram ID 99116003')
                personal_code=personal_row.locator('.growth-code').inner_text()
                personal_row.get_by_role('button',name='История активаций',exact=True).click()
                expect(personal_row.locator('.wallet-gift-report')).to_contain_text('Этот код ещё никто не активировал.')
                go(outsider,'wallet')
                open_wallet_tools(outsider)
                outsider.get_by_label('Подарочный код на баланс',exact=True).fill(personal_code)
                outsider.evaluate("window.previousGiftPage=document.querySelector('[data-page=wallet] .wallet-page')")
                outsider.get_by_role('button',name='Получить подарок',exact=True).click()
                outsider.wait_for_function("document.querySelector('[data-page=wallet] .wallet-page') && document.querySelector('[data-page=wallet] .wallet-page') !== window.previousGiftPage")
                expect(outsider.locator('.wallet-entry').filter(has_text='Подарок: Персональный подарок')).to_have_count(1)
                personal_row.get_by_role('button',name='Обновить активации',exact=True).click()
                expect(personal_row.locator('.wallet-gift-report')).to_contain_text('Получателей: 1')
                expect(personal_row.locator('.wallet-gift-summary')).to_contain_text('активаций 1 из 1')
                expect(personal_row.locator('.wallet-gift-status')).to_contain_text('Лимит исчерпан')
                expect(personal_row.locator('.wallet-gift-report')).to_contain_text('Telegram ID 99116003')
                with admin.expect_download() as download_info:
                    personal_row.get_by_role('button',name='Скачать активации CSV',exact=True).click()
                download=download_info.value
                assert download.suggested_filename.startswith('gift-') and download.suggested_filename.endswith('.csv')
                csv_text=Path(download.path()).read_text(encoding='utf-8-sig')
                assert '99116003' in csv_text and '5025' in csv_text and 'Персональный подарок' in csv_text
                for width in (320,390):
                    admin.set_viewport_size({'width':width,'height':844})
                    assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                if admin.get_by_role('button',name='Закрыть уведомление',exact=True).is_visible():
                    admin.get_by_role('button',name='Закрыть уведомление',exact=True).click()
                admin.set_viewport_size({'width':390,'height':1500})
                personal_row.evaluate('(node) => scrollTo(0, node.getBoundingClientRect().top + scrollY - 140)')
                personal_row.screenshot(path=str(output/'45-personal-gift-report-mobile.png'))
                from check_campaign_cards import check_campaign_cards
                check_campaign_cards(browser, base, output, failures, PostgresIntegration.dsn)
                from check_campaign_pricing import check_campaign_pricing
                check_campaign_pricing(browser, base, output, failures, PostgresIntegration.dsn)
                from check_bonus_links import check_bonus_links
                with patch.dict(os.environ,{'PUSHOK_MINIAPP_ENABLED':'1','TELEGRAM_BOT_USERNAME':'mtproxy_pushok_bot'}),patch.object(api,'TELEGRAM_BOT_TOKEN','fixture-token'):
                    check_bonus_links(browser,base,output,failures,PostgresIntegration.dsn)

                from check_verified_profiles import check_verified_profiles
                check_verified_profiles(browser, base, output, failures, PostgresIntegration.dsn)
                from check_interface import check_interface
                check_interface(browser,base,output,failures,PostgresIntegration.dsn)
                from check_hall_of_fame import check_hall_of_fame
                check_hall_of_fame(browser, base, output, failures, PostgresIntegration.dsn)
                from check_user_profiles import check_user_profiles
                check_user_profiles(browser, base, output, failures, PostgresIntegration.dsn)
                from check_admin_users import check_admin_users
                check_admin_users(browser,base,output,failures,PostgresIntegration.dsn)
                from check_user_access import check_user_access
                check_user_access(browser,base,output,failures,PostgresIntegration.dsn)
                from check_automatic_schedule import check_automatic_schedule
                check_automatic_schedule(browser, base, output, failures, PostgresIntegration.dsn)
                from check_channel_layout import check_channel_layout
                check_channel_layout(browser,base,output,failures,PostgresIntegration.dsn)
                from check_mobile_polish import check_mobile_polish
                check_mobile_polish(browser,base,output,failures,PostgresIntegration.dsn)
                from check_gift_sharing import check_gift_sharing
                check_gift_sharing(browser,base,output,failures,PostgresIntegration.dsn)
                from check_campaign_analytics import check_campaign_analytics
                check_campaign_analytics(browser,base,output,failures,PostgresIntegration.dsn)
                from check_sponsor_workspace import check_sponsor_workspace
                check_sponsor_workspace(browser, base, output, failures, PostgresIntegration.dsn)
                from check_campaign_assistant_ui import check_campaign_assistant_ui
                check_campaign_assistant_ui(new_page,go,output,PostgresIntegration.dsn)
                from check_application_licenses import check_application_licenses
                check_application_licenses(browser,base,output,failures,PostgresIntegration.dsn)
                assert not failures, failures
                with psycopg.connect(PostgresIntegration.dsn) as connection:
                    connection.execute('UPDATE user_sessions SET revoked_at=now() WHERE id=%s', (session_ids['advertiser'],))
                # Фоновое обновление может уже обработать 401 и скрыть кнопку.
                # Проверяем сброс сессии, независимо от того, какой запрос пришёл первым.
                advertiser.evaluate("async () => { try { await refresh(); } catch (error) { if (token) throw error; } }")
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
        release_patcher.stop()
        logo_patcher.stop()
        avatar_patcher.stop()
        PostgresIntegration.tearDownClass()


if __name__ == '__main__':
    main()
