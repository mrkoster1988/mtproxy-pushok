"""Native contact popup is simulated; proof still passes the real private API and SQL."""
import io
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import main as bot
from bot.verified_profiles import verification_response

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
            # Остановить ожидание можно без выхода; отсутствие бота не держит кнопку минуту.
            card.get_by_role('button', name='Отменить ожидание').click()
            expect(card.get_by_role('button', name='Подтвердить номер', exact=True)).to_be_enabled()
            expect(card.locator('.verification-status')).to_contain_text('Ожидание остановлено')
            expect(card.get_by_role('link', name='Подтвердить через бота')).to_be_visible()
            page.clock.install()
            card.get_by_role('button', name='Подтвердить номер', exact=True).click()
            page.wait_for_function("__nativeEvents.filter(e=>e.name==='web_app_request_phone').length===3")
            page.evaluate("Telegram.WebView.receiveEvent('phone_requested',{status:'sent'})")
            expect(card.locator('.verification-status')).to_contain_text('Ждём подтверждения бота')
            page.clock.fast_forward(13000)
            expect(card.locator('.verification-status')).to_contain_text('Бот пока не подтвердил')
            expect(card.get_by_role('button', name='Подтвердить номер', exact=True)).to_be_enabled()
            expect(card.locator('.phone-verified-badge')).to_have_count(0)
            # Соединение, которое не отвечает, прерывается по AbortSignal.
            page.evaluate("""() => {
              window.__realFetch=window.fetch;
              window.fetch=(url,options)=>String(url).endsWith('/me/verification/request')?
                new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('timeout')),{once:true})):
                window.__realFetch(url,options);
            }""")
            card.get_by_role('button', name='Подтвердить номер', exact=True).click()
            expect(card.locator('.verification-status')).to_contain_text('Подготавливаем запрос')
            page.clock.fast_forward(9000)
            expect(card.locator('.verification-status')).to_contain_text('Нет связи с сервером')
            expect(card.get_by_role('button', name='Подтвердить номер', exact=True)).to_be_enabled()
            page.evaluate('window.fetch=window.__realFetch')
            card.screenshot(path=str(output/'verification-recovery-390.png'))
            card.get_by_role('button', name='Подтвердить номер', exact=True).click()
            page.wait_for_function("__nativeEvents.filter(e=>e.name==='web_app_request_phone').length===4")
            page.evaluate("Telegram.WebView.receiveEvent('phone_requested',{status:'sent'})")
            # Контакт идёт через обработчик бота и настоящий конструктор HTTP-запроса.
            def bridge(request, **kwargs):
                response = page.request.post(base+urlsplit(request.full_url).path,
                    headers=dict(request.header_items()), data=request.data)
                if not response.ok:
                    raise HTTPError(request.full_url, response.status, 'test response', {}, io.BytesIO(response.body()))
                return io.BytesIO(response.body())
            with patch.object(bot, 'urlopen', side_effect=bridge):
                reply = verification_response({'update_id': 99881521, 'message': {
                    'from': {'id': tg}, 'chat': {'id': tg, 'type': 'private'}, 'date': int(time.time()),
                    'contact': {'user_id': tg, 'phone_number': '+79990001122'}}},
                    bot._internal_api, lambda method,payload: {'birthdate': {'day': 11, 'month': 11}})
            assert 'Номер подтверждён' in reply['text']
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
