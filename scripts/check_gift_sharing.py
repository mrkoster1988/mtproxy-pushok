"""Реальный API/БД и Chromium; Telegram shareMessage заменён локальным callback."""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit
import psycopg
from playwright.sync_api import expect


def check_gift_sharing(browser,base,output,failures,dsn):
    from app import main as api, gift_sharing
    uid,sid=uuid4(),uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name) VALUES(%s,99883999,'admin','Сертификаты')",(uid,))
        conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid))
        conn.execute('UPDATE wallet_settings SET enabled=true')
    token=api._create_session_token(uid,sid)
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce',accept_downloads=True)
    context.add_init_script("sessionStorage.setItem('pushok-session',"+repr(token)+")")
    page=context.new_page();page.on('pageerror',lambda error:failures.append(str(error)))
    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
    def request(method,path,body=None):
        r=page.request.fetch(base+'/api/v1'+path,method=method,headers={'Authorization':'Bearer '+token},data=body)
        assert r.ok,(path,r.status,r.text())
        return r.json()
    try:
        with patch.dict(os.environ,{'TELEGRAM_BOT_USERNAME':'pushok_test_bot'}):
            gift=request('POST','/admin/wallet/gifts',dict(request_id=str(uuid4()),name='Подарок на рекламу',amount=100000,max_uses=1,expires_at=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()))
            page.goto(base+'#/wallet');expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true')
            area=page.locator('[data-wallet-section=gifts]');area.locator(':scope > summary').click()
            row=area.locator('.wallet-gift').filter(has=page.get_by_role('heading',name='Подарок на рекламу',exact=True))
            expect(row).to_be_visible();share=row.get_by_role('button',name='Поделиться',exact=True)
            for width in (320,390):
                page.set_viewport_size({'width':width,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                positions=[]
                for button in row.locator('.gift-actions>button').all():
                    expect(button).to_have_text('');box=button.bounding_box();positions.append(box['y'])
                    assert box['width']>=44 and box['height']>=44
                    assert button.get_attribute('aria-label') and button.get_attribute('title')
                assert len(positions)==5 and max(positions)-min(positions)<1
                assert row.bounding_box()['height']<260
                row.screenshot(path=str(output/('gift-icons-'+str(width)+'.png')))
            # Клиент без Telegram 8.0 показывает картинку, понятный текст и загрузку файла.
            share.click();preview=row.locator('.gift-share-preview');expect(preview).to_be_visible()
            expect(preview.locator('.gift-share-text')).to_contain_text('Подарочный сертификат puShok на 1 000 ₽')
            expect(preview.locator('.gift-share-text')).to_contain_text('в веб-кабинете')
            expect(preview.locator('img')).to_be_visible();page.wait_for_function("document.querySelector('.gift-share-preview img')?.naturalWidth===1200")
            url=preview.get_by_role('link',name='Поделиться текстом',exact=True).get_attribute('href')
            assert 'wallet_'+gift['code'] in parse_qs(urlsplit(url).query)['url'][0]
            with page.expect_download() as download:preview.get_by_role('link',name='Скачать сертификат',exact=True).click()
            assert download.value.suggested_filename=='pushok-certificate.jpg'
            preview.screenshot(path=str(output/'gift-certificate-preview.png'))
            preview.get_by_role('button',name='Закрыть сертификат',exact=True).click()
            page.evaluate("window.Telegram={WebApp:{isVersionAtLeast:()=>true,shareMessage:(id,callback)=>{window.preparedShare=id;callback(false);}}}")
            with patch.object(gift_sharing,'prepare_message',return_value='prepared-fixture') as prepare:
                share.click();page.wait_for_function("window.preparedShare==='prepared-fixture'")
                assert prepare.call_args.args[0]==99883999
                expect(row.locator('.gift-share-preview')).to_have_count(0)
                # Отмена диалога не выдаёт сообщение об успешной отправке.
                expect(page.locator('#notice')).not_to_contain_text('Сертификат отправлен')
            row.get_by_role('button',name='Выключить код',exact=True).click();expect(share).to_be_disabled()
            row.get_by_role('button',name='Включить код',exact=True).click();expect(share).to_be_enabled()
            request('POST','/wallet/gifts/redeem',{'code':gift['code']})
            row.get_by_role('button',name='История активаций',exact=True).click()
            expect(row.locator('.wallet-gift-status')).to_contain_text('Лимит исчерпан');expect(share).to_be_disabled()
            assert not failures,failures
    finally:
        context.close()
