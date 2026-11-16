"""Signed Mini App launches and actual SQL: personal gifts, offers and native Stars bridge."""
import json
from urllib.parse import urlencode,parse_qs,urlsplit
from uuid import uuid4
import psycopg
from playwright.sync_api import expect
from test_miniapp_auth import signed_data


def check_bonus_links(browser,base,output,failures,dsn):
    recipient=99881311
    admin,tariff,offer,gift=[uuid4() for _ in range(4)]
    code='GIFT-'+uuid4().hex[:24].upper()
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name) VALUES(%s,99881310,'admin','Проверка бонусов')",(admin,))
        conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr,currency) VALUES(%s,'Тариф по ссылке',3600,10000,151,'RUB')",(tariff,))
        conn.execute("INSERT INTO growth_offers(id,kind,name,code,percent,tariff_id,per_user_limit,created_by) VALUES(%s,'promo','Скидка по ссылке','LINK40',40,%s,1,%s)",(offer,tariff,admin))
        conn.execute("INSERT INTO wallet_gifts(id,created_by,request_id,code,name,amount,max_uses,expires_at,recipient_telegram_id) VALUES(%s,%s,%s,%s,'Подарок по ссылке',12345,1,now()+interval '1 day',%s)",(gift,admin,uuid4(),code,recipient))

    def launch(tg,payload,page=None,before=None):
        context=page.context if page else browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
        context.add_init_script("window.__nativeEvents=[];window.TelegramWebviewProxy={postEvent(name,data){__nativeEvents.push({name,data:JSON.parse(data||'{}')});}};")
        if page:page.goto('about:blank')
        else:
            page=context.new_page();page.on('pageerror',lambda e:failures.append(str(e)))
        page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base+'/') else route.abort())
        if before:before(page)
        raw=signed_data(user=json.dumps({'id':tg,'first_name':'Получатель','username':'bonus_receiver'}),query_id=str(uuid4()),start_param=payload)
        fragment=urlencode({'tgWebAppData':raw,'tgWebAppVersion':'8.0','tgWebAppPlatform':'android','tgWebAppThemeParams':'{}'})
        page.goto(base+'/miniapp#'+fragment,wait_until='domcontentloaded')
        expect(page.locator('body')).to_have_class('miniapp-mode cabinet-mode')
        return page

    wrong=launch(recipient+1,'wallet_'+code)
    try:
        notice=wrong.locator('#notice')
        expect(notice).to_contain_text('Подарок предназначен другому аккаунту Telegram')
        expect(notice).to_contain_text('ID '+str(recipient+1))
        expect(notice).not_to_contain_text('ID '+str(recipient)+')')
        expect(notice.get_by_role('button',name='Повторить',exact=True)).to_have_count(0)
        for width in (320,390):
            wrong.set_viewport_size({'width':width,'height':844})
            assert wrong.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            box=notice.bounding_box();message=notice.locator('.notice-message').bounding_box()
            assert message['width']>=200 and box['x']>=0 and box['x']+box['width']<=width
            wrong.screenshot(path=str(output/f'57-gift-wrong-account-{width}.png'))
        wrong.locator('.mobile-bottom-nav a[data-route=wallet]').click()
        expect(wrong.locator('[data-page=wallet] .wallet-page')).to_be_visible()
        with psycopg.connect(dsn) as conn:
            assert conn.execute('SELECT count(*) FROM wallet_gift_redemptions WHERE gift_id=%s',(gift,)).fetchone()[0]==0
        # Новый подписанный запуск в том же WebView должен сменить аккаунт,
        # даже если sessionStorage содержит сессию другого получателя.
        correct=launch(recipient,'wallet_'+code,page=wrong)
        expect(correct.locator('[data-page=wallet]')).to_be_visible()
        expect(correct.locator('.wallet-entry').filter(has_text='Подарок по ссылке')).to_have_count(1)
    finally:wrong.context.close()

    page=launch(recipient,'wallet_'+code)
    try:
        expect(page.locator('[data-page=wallet]')).to_be_visible()
        expect(page.locator('.wallet-entry').filter(has_text='Подарок по ссылке')).to_have_count(1)
        page.reload()
        expect(page.locator('.wallet-entry').filter(has_text='Подарок по ссылке')).to_have_count(1)
        with psycopg.connect(dsn) as conn:
            assert conn.execute('SELECT count(*) FROM wallet_gift_redemptions WHERE gift_id=%s',(gift,)).fetchone()[0]==1
        page.locator('[data-wallet-section=telegram] > summary').click()
        page.get_by_role('link',name='Открыть мои Stars через бота ↗').click()
        assert page.evaluate("__nativeEvents.some(e=>e.name==='web_app_open_tg_link'&&e.data.path_full==='/mtproxy_pushok_bot?start=stars')")
        page.screenshot(path=str(output/'51-gift-deeplink-mobile.png'),full_page=True)
    finally:page.context.close()

    retry_code='GIFT-'+uuid4().hex[:24].upper()
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO wallet_gifts(created_by,request_id,code,name,amount,max_uses,expires_at,recipient_telegram_id) VALUES(%s,%s,%s,'Подарок после повтора',100,1,now()+interval '1 day',%s)",(admin,uuid4(),retry_code,recipient))
    def lost_response(page):
        def intercept(route):
            response=route.fetch();assert response.ok
            route.fulfill(status=503,json={'detail':'database_unavailable'})
        page.route('**/api/v1/wallet/gifts/redeem',intercept,times=1)
    page=launch(recipient,'wallet_'+retry_code,before=lost_response)
    try:
        notice=page.locator('#notice');retry=notice.get_by_role('button',name='Повторить',exact=True)
        expect(retry).to_be_visible()
        for width in (320,390):
            page.set_viewport_size({'width':width,'height':844})
            message=notice.locator('.notice-message').bounding_box();action=retry.bounding_box()
            assert message['width']>=200 and action['y']>=message['y']+message['height']
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.screenshot(path=str(output/f'58-gift-retry-{width}.png'))
        retry.click()
        expect(page.locator('[data-page=wallet]')).to_be_visible()
        expect(page.locator('.wallet-entry').filter(has_text='Подарок после повтора')).to_have_count(1)
        expect(page.locator('#notice')).to_contain_text('уже на вашем балансе')
    finally:page.context.close()

    page=launch(recipient,'offer_'+offer.hex)
    try:
        expect(page.locator('[data-page=create]')).to_be_visible()
        expect(page.locator('#order-promo')).to_have_value('LINK40')
        expect(page.locator('#tariff-select')).to_have_value(str(tariff))
        expect(page.locator('#order-summary')).to_contain_text('91 XTR')
        with psycopg.connect(dsn) as conn:
            assert conn.execute('SELECT count(*) FROM growth_redemptions WHERE offer_id=%s',(offer,)).fetchone()[0]==0
        share=page.evaluate("""() => {const a=pushokLinks.share('offer_"""+offer.hex+"""','Подарок');document.body.append(a);return a.href;}""")
        shared_url=parse_qs(urlsplit(share).query)['url'][0]
        assert shared_url=='https://t.me/mtproxy_pushok_bot?startapp=offer_'+offer.hex
        for width in (320,390):
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
        page.screenshot(path=str(output/'52-promo-deeplink-mobile.png'),full_page=True)
    finally:page.context.close()

    with psycopg.connect(dsn) as conn:
        uid=conn.execute('SELECT id FROM users WHERE telegram_user_id=%s',(recipient,)).fetchone()[0]
        conn.execute("INSERT INTO wallet_topups(user_id,request_id,provider,currency,amount,provider_account,test_mode) VALUES(%s,%s,'telegram_stars','XTR',123,'test-only',false)",(uid,uuid4()))
    page=launch(99881310,'')
    try:
        page.locator('.mobile-bottom-nav a[data-route=wallet]').click()
        page.locator('[data-wallet-section=review]>summary').click()
        page.get_by_role('button',name='Показать последние 30 пополнений').click()
        row=page.locator('.wallet-topup-review').filter(has_text='ID '+str(recipient)).first
        expect(row).to_contain_text('123 Stars')
        expect(row.locator('.person-handle')).to_have_text('@bonus_receiver')
        expect(row.locator('.user-avatar')).to_be_visible()
        for width in (320,390):
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
        row.evaluate("node=>node.scrollIntoView({block:'center',behavior:'instant'})")
        row.screenshot(path=str(output/'53-compact-topup-profile.png'))
        row.get_by_role('link',name='Открыть профиль пользователя').click()
        expect(page.locator('[data-page=user]')).to_be_visible()
        page.get_by_role('button',name='Назад',exact=True).click()
        expect(page.locator('[data-page=wallet]')).to_be_visible()
    finally:page.context.close()
