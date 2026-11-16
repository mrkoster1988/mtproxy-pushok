"""Продажа и покупка лицензии владельцем через реальные формы/API/SQL.

Синтетическими остаются Telegram invoice и HTTPS-транспорт между экземплярами.
Транспорт возвращает настоящий подписанный ответ сервера продавца.
"""
from io import BytesIO
import json
import os
from unittest.mock import patch
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from playwright.sync_api import expect


def check_application_licenses(browser,base,output,failures,dsn):
    from app import main,application_licenses as lic,license_crypto as crypto
    uid,user=uuid4(),uuid4();tg=int(uid.hex[:12],16);tokens={};contexts=[]
    with psycopg.connect(dsn) as conn:
        conn.execute('UPDATE app_license_state SET required=false,envelope=NULL')
        for owner,role in ((uid,'admin'),(user,'advertiser')):
            conn.execute("INSERT INTO users(id,telegram_user_id,first_name,role) VALUES(%s,%s,'Владелец',%s)",(owner,tg if owner==uid else tg+1,role))
            sid=uuid4();conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,owner));tokens[role]=main._create_session_token(owner,sid)
    api=TestClient(main.app)
    def launch(role,width):
        ctx=browser.new_context(viewport={'width':width,'height':844},reduced_motion='reduce');contexts.append(ctx)
        ctx.add_init_script('sessionStorage.setItem("pushok-session",'+repr(tokens[role])+')')
        page=ctx.new_page();page.on('pageerror',lambda e:failures.append(str(e)))
        page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
        page.goto(base);expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true');return page
    def go(page):page.evaluate("window.cabinet.go('license')");return page.locator('[data-page=license]')
    def transport(req,timeout):
        with patch.dict(os.environ,{'PUSHOK_LICENSE_MODE':'seller'}):r=api.post('/api/v1/license-store/requests',json=json.loads(req.data))
        assert r.status_code==200,r.text
        return BytesIO(r.content)
    try:
        with patch.dict(os.environ,{'PUSHOK_LICENSE_MODE':'seller','PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'0','BOT_INTERNAL_SECRET':'e'*64}),patch.object(lic,'stars_invoices_enabled',return_value=True),patch.object(lic.stars_checkout,'credentials',return_value=('123456:synthetic_only_token',123456)),patch.object(lic.stars_checkout,'create_invoice_link',return_value='https://t.me/$app_license_browser'):
            staff=launch('admin',390);area=go(staff)
            area.get_by_role('button',name='Добавить тариф',exact=True).click()
            area.get_by_label('Название',exact=True).fill('Для владельца')
            area.get_by_label('Цена, Stars',exact=True).fill('100')
            area.get_by_label('Узлы',exact=True).fill('10')
            area.get_by_label('Администраторы',exact=True).fill('10')
            area.get_by_label('Незавершённые кампании',exact=True).fill('100')
            area.get_by_label('Доступен для покупки',exact=True).check()
            area.get_by_role('button',name='Сохранить тариф',exact=True).click()
            expect(area.locator('.license-plan-admin')).to_contain_text('Для владельца')
            area.get_by_label('Продажа включена',exact=True).check()
            area.get_by_role('button',name='Сохранить режим',exact=True).click()
            expect(area.get_by_label('Продажа включена',exact=True)).to_be_checked()
            staff.screenshot(path=str(output/'57-installation-license-seller-390.png'))
            customer=launch('advertiser',320);customer.evaluate("window.cabinet.go('profile')")
            expect(customer.get_by_role('link',name='Лицензия приложения',exact=True)).to_have_count(0)
            denied=customer.request.get(base+'/api/v1/admin/license',headers={'Authorization':'Bearer '+tokens['advertiser']});assert denied.status==403
            public=lic.public_key();iid,_=crypto.identity()
            with patch.dict(os.environ,{'PUSHOK_LICENSE_MODE':'client','PUSHOK_LICENSE_SERVER':'https://licenses.example.test','PUSHOK_LICENSE_PUBLIC_KEY':public}),patch.object(lic,'build_opener') as opener:
                opener.return_value.open.side_effect=transport
                lic.sync(dsn)
                with psycopg.connect(dsn) as conn:conn.execute('UPDATE app_license_state SET required=true,installation_id=%s',(iid,))
                staff.reload();expect(staff.locator('body')).to_have_attribute('data-cabinet-loaded','true');area=go(staff)
                expect(area).to_contain_text('Нужна покупка или продление')
                expect(area.get_by_role('button',name='Добавить тариф',exact=True)).to_have_count(0)
                staff.evaluate('window.open = url => { window.licenseInvoiceUrl = url; return null; }')
                with staff.expect_response(lambda r:r.url.endswith('/api/v1/admin/license/checkout')) as invoice:
                    area.get_by_role('button',name='Купить',exact=True).click()
                assert invoice.value.status==200,invoice.value.text()
                expect(area).to_contain_text('Ожидает оплаты · 100 Stars')
                assert staff.evaluate('window.licenseInvoiceUrl')=='https://t.me/$app_license_browser'
                with psycopg.connect(dsn) as conn:order=conn.execute('SELECT id FROM app_license_orders WHERE installation_id=%s ORDER BY created_at DESC',(iid,)).fetchone()[0]
                data=dict(invoice_payload='license:'+str(order),telegram_user_id=tg,total_amount=100,currency='XTR')
                with patch.dict(os.environ,{'PUSHOK_LICENSE_MODE':'seller'}):
                    headers={'X-Pushok-Bot-Secret':'e'*64}
                    r=api.post('/api/v1/internal/bot/license/precheckout',headers=headers,json=dict(data,query_id='browser-'+str(order)));assert r.status_code==200 and r.json()['ok'],r.text
                    r=api.post('/api/v1/internal/bot/license/settle',headers=headers,json=dict(data,telegram_payment_charge_id='browser-'+str(order)));assert r.status_code==200 and r.json()['status']=='applied',r.text
                # Имитируем один проход фонового клиента; интерфейс замечает оплату сам.
                lic.sync(dsn)
                expect(area).not_to_contain_text('Нужна покупка или продление',timeout=12000)
                expect(area).to_contain_text('Лицензия этой установки')
                for width in (320,390):
                    staff.set_viewport_size({'width':width,'height':844})
                    assert staff.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                staff.screenshot(path=str(output/'58-installation-license-client-390.png'))
                # Серверная квота работает для всех пользователей одной установки.
                with psycopg.connect(dsn) as conn:assert conn.execute('SELECT required,envelope IS NOT NULL FROM app_license_state').fetchone()==(True,True)
    finally:
        with psycopg.connect(dsn) as conn:conn.execute('UPDATE app_license_state SET required=false')
        for ctx in contexts:ctx.close()
        api.close()
