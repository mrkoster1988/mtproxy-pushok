"""Native contact popup is simulated; proof still passes the real private API and SQL."""
import json
import os
import time
from urllib.parse import urlencode
from uuid import uuid4
from unittest.mock import patch

import psycopg
from playwright.sync_api import expect
from test_miniapp_auth import signed_data


def check_verified_profiles(browser, base, output, failures, dsn):
    from app import main as api
    tg = 99881521
    context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
    context.add_init_script("window.__nativeEvents=[];window.TelegramWebviewProxy={postEvent(name,data){__nativeEvents.push({name,data:JSON.parse(data||'{}')});}};")
    page = context.new_page()
    page.on('pageerror', lambda e: failures.append(str(e)))
    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base+'/') else route.abort())
    try:
        with patch.dict(os.environ, {'PUSHOK_MINIAPP_ENABLED': '1', 'TELEGRAM_BOT_USERNAME': 'mtproxy_pushok_bot',
                                    'BOT_INTERNAL_SECRET': 'e'*64}), patch.object(api, 'TELEGRAM_BOT_TOKEN', 'fixture-token'):
            raw = signed_data(user=json.dumps({'id': tg, 'first_name': 'Мария', 'username': 'verified_profile'}), query_id=str(uuid4()))
            fragment = urlencode({'tgWebAppData': raw, 'tgWebAppVersion': '8.0', 'tgWebAppPlatform': 'android', 'tgWebAppThemeParams': '{}'})
            page.goto(base+'/miniapp#'+fragment)
            card = page.locator('#dashboard-verification')
            expect(card.get_by_role('button', name='Подтвердить номер', exact=True)).to_be_visible()
            for width in (320, 390):
                page.set_viewport_size({'width': width, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                card.screenshot(path=str(output/f'verification-home-{width}.png'))
            card.get_by_role('button', name='Подтвердить номер', exact=True).click()
            page.wait_for_function("__nativeEvents.some(e=>e.name==='web_app_request_phone')")
            page.evaluate("Telegram.WebView.receiveEvent('phone_requested',{status:'cancelled'})")
            expect(card.locator('.verification-status')).to_contain_text('Номер не передан')
            expect(card.locator('.phone-verified-badge')).to_have_count(0)
            card.get_by_role('button', name='Подтвердить номер', exact=True).click()
            page.wait_for_function("__nativeEvents.filter(e=>e.name==='web_app_request_phone').length===2")
            page.evaluate("Telegram.WebView.receiveEvent('phone_requested',{status:'sent'})")
            expect(card.locator('.verification-status')).to_contain_text('Ждём подтверждения бота')
            expect(card.locator('.phone-verified-badge')).to_have_count(0)  # Client callback cannot grant a badge.
            result = page.request.post(base+'/api/v1/internal/bot/verification/contact',
                headers={'X-Bot-Secret': 'e'*64, 'X-Pushok-Telegram-Environment': 'production'},
                data={'telegram_user_id': tg, 'contact_user_id': tg, 'chat_id': tg, 'private_chat': True,
                      'phone_number': '+79990001122', 'update_id': 99881521, 'message_date': int(time.time()),
                      'birthday': {'day': 11, 'month': 11}})
            assert result.ok, result.text()
            expect(card.locator('.phone-verified-badge')).to_have_count(1, timeout=10000)
            page.evaluate("window.cabinet.go('profile')")
            personal = page.locator('.profile-personal')
            expect(personal.locator('.profile-phone')).to_contain_text('+79990001122')
            expect(page.locator('.profile-identity .phone-verified-badge')).to_have_count(1)
            expect(personal.get_by_label('День', exact=True)).to_have_value('11')
            expect(personal).to_contain_text('Добавлено из профиля Telegram')
            personal.get_by_label('День', exact=True).fill('29')
            personal.get_by_label('Месяц', exact=True).select_option('2')
            personal.get_by_role('button', name='Сохранить', exact=True).click()
            expect(personal.locator('.verification-status')).to_contain_text('Сведения сохранены')
            for width in (320, 390):
                page.set_viewport_size({'width': width, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                personal.screenshot(path=str(output/f'verification-profile-{width}.png'))
            page.reload()
            expect(page.locator('.profile-personal').get_by_label('День', exact=True)).to_have_value('29')
            expect(page.locator('.profile-personal').get_by_label('Месяц', exact=True)).to_have_value('2')
            with psycopg.connect(dsn) as conn:
                record = conn.execute('''SELECT v.birth_day,v.birth_month,v.birth_year,v.phone_number FROM verified_profiles v
                    JOIN users u ON u.id=v.user_id WHERE u.telegram_user_id=%s''', (tg,)).fetchone()
                assert record == (29, 2, None, '+79990001122')
                conn.execute("UPDATE users SET role='admin' WHERE telegram_user_id=%s", (tg,))
            page.reload()
            expect(page.locator('.profile-identity')).to_contain_text('Администратор')
            page.evaluate("window.cabinet.go('settings')")
            page.locator('#settings-system-tab').click()
            settings = page.locator('#birthday-settings')
            amount = settings.get_by_label('Ежегодный подарок, бонусных ₽', exact=True)
            expect(amount).to_have_value('0')
            amount.fill('125')
            settings.get_by_role('button', name='Сохранить', exact=True).click()
            expect(settings.locator('.verification-status')).to_contain_text('Настройки сохранены')
            with psycopg.connect(dsn) as conn:
                assert conn.execute('SELECT bonus_amount FROM birthday_settings WHERE singleton').fetchone()[0] == 12500
                conn.execute('UPDATE birthday_settings SET bonus_amount=0')
            settings.screenshot(path=str(output/'birthday-admin-settings.png'))
            page.locator('#logout').evaluate('(button)=>button.click()')
            expect(page.locator('#dashboard-verification')).to_be_empty()
            expect(page.locator('.profile-personal')).to_have_count(0)
    finally:
        context.close()
