"""Настоящие API/SQL, браузерная страница ссылки; Telegram заменён локальным ответом."""
import os
import time
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import urlsplit
import psycopg
from playwright.sync_api import expect


def check_campaign_analytics(browser,base,output,failures,dsn):
    from app import main,channel_logos
    uid,sid,cid,channel,tariff=[uuid4() for _ in range(5)]
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO users(id,telegram_user_id,first_name) VALUES(%s,99884701,'Аналитика')",(uid,))
        conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid))
        conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Статистика канала','https://t.me/analytics_fixture')",(channel,uid))
        conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,'День',86400,30000,150)",(tariff,))
        conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status)
            VALUES(%s,%s,%s,%s,'День',86400,30000,'RUB','approved','verified')""",(cid,uid,channel,tariff))
    token=main._create_session_token(uid,sid)
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce');public=browser.new_context(viewport={'width':390,'height':844})
    context.add_init_script('sessionStorage.setItem("pushok-session",'+repr(token)+')')
    page=context.new_page();page.on('pageerror',lambda e:failures.append(str(e)))
    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
    def transport(route):
        url=route.request.url
        if url.startswith('https://analytics.example.org/'):
            path=urlsplit(url).path+'?'+urlsplit(url).query
            response=public.request.fetch(base+path,method=route.request.method,headers=route.request.all_headers(),max_redirects=0)
            if response.status==303:
                assert response.headers['location']=='https://t.me/+analyticsFixture123'
                route.fulfill(status=200,body='<main data-invitation-fixture>Telegram invitation fixture</main>',content_type='text/html')
            else:route.fulfill(response=response)
        elif url.startswith('https://t.me/+analyticsFixture'):
            route.fulfill(status=200,body='<html>Telegram invitation fixture</html>',content_type='text/html')
        else:route.abort()
    public.route('**/*',transport);landing=public.new_page();landing.on('pageerror',lambda e:failures.append(str(e)))
    env={'TELEGRAM_BOT_TOKEN':'123456:analytics_fixture','SESSION_SIGNING_KEY':'s'*64,'PUSHOK_MINIAPP_URL':'https://analytics.example.org/miniapp','PUSHOK_TELEGRAM_TEST_ENVIRONMENT':'0','BOT_INTERNAL_SECRET':'e'*64}
    try:
        with patch.dict(os.environ,env):
            page.goto(base+'/#/campaign/'+str(cid));expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true')
            area=page.locator('[data-page=campaign] .campaign-analytics');expect(area).to_contain_text('Статистика ссылки')
            expect(area.locator('dd')).to_have_text(['—','—','—','—'])
            footer=area.locator('.analytics-footer')
            expect(footer.get_by_role('button',name='Отчёт по кампании')).to_be_visible()
            expect(footer.get_by_role('button',name='Подключить подсчёт')).to_have_text('Подключить подсчёт')
            area.screenshot(path=str(output/'campaign-link-actions-390.png'),
                            style='.cabinet-topbar,.mobile-bottom-nav{visibility:hidden!important}')
            # Always return the answer for the requested Telegram method.
            # A shared sequential side_effect list is brittle when unrelated
            # background logo refreshes overlap with this browser operation.
            observed=[]
            def telegram_fixture(token, method, params):
                observed.append(method)
                if method=='getChat' and params.get('chat_id')=='@analytics_fixture':
                    return {'id':-10084701,'type':'channel','username':'analytics_fixture'}
                if method=='getChatMember' and params.get('chat_id')==-10084701:
                    return {'status':'administrator','can_invite_users':True}
                if method=='createChatInviteLink' and params.get('chat_id')==-10084701:
                    return {'invite_link':'https://t.me/+analyticsFixture123'}
                raise ValueError('unexpected_telegram_fixture_request')
            with patch.object(channel_logos,'_call',side_effect=telegram_fixture):
                with page.expect_response(lambda r:r.url.endswith('/analytics/enable')) as enabled:
                    area.get_by_role('button',name='Подключить подсчёт',exact=True).click()
                assert enabled.value.ok,enabled.value.text()
                assert {'getChat','getChatMember','createChatInviteLink'} <= set(observed)
            link=area.get_by_role('link',name='Ссылка кампании',exact=True);expect(link).to_be_visible();url=link.get_attribute('href')
            expect(area.locator('dd')).to_have_text(['0','0','0','—'])
            landing.goto(url);expect(landing.get_by_role('button',name='Открыть канал',exact=True)).to_be_visible()
            landing.reload();landing.get_by_role('button',name='Открыть канал',exact=True).click();expect(landing.locator('[data-invitation-fixture]')).to_be_visible()
            payload=dict(chat_id=-10084701,telegram_user_id=99884702,invite_link='https://t.me/+analyticsFixture123',date=int(time.time()))
            for _ in range(2):
                r=page.request.post(base+'/api/v1/internal/bot/analytics/join',data=payload,headers={'X-Pushok-Bot-Secret':'e'*64,'X-Pushok-Telegram-Environment':'production'});assert r.ok,r.text()
            area.get_by_role('button',name='Обновить статистику кампании',exact=True).click();expect(area.locator('dd')).to_have_text(['1','1','1','100%'])
            area.locator('.analytics-history>summary').click();expect(area.locator('tbody tr')).to_have_count(1)
            page.evaluate("window.analyticsTable=document.querySelector('.campaign-analytics .analytics-days')")
            with page.expect_response(lambda r:r.url.endswith('/analytics')):
                area.get_by_role('button',name='Обновить статистику кампании',exact=True).click()
            expect(area.locator('.analytics-history')).to_have_attribute('open','')
            assert page.evaluate("window.analyticsTable===document.querySelector('.campaign-analytics .analytics-days')")
            for width in (320,390,768):
                page.set_viewport_size({'width':width,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                area.screenshot(path=str(output/f'analytics-{width}.png'))
            assert not failures,failures
    finally:
        context.close();public.close()
