"""Администратор меняет реальные условия заявки в мобильном браузере."""
from uuid import uuid4
import psycopg
from playwright.sync_api import expect


def check_campaign_pricing(browser, base, output, failures, dsn):
    from app import main as api
    owner, admin, operator, channel, cid, first, second = [uuid4() for _ in range(7)]
    tokens = {}
    with psycopg.connect(dsn) as conn:
        for uid, role in ((owner, 'advertiser'), (admin, 'admin'), (operator, 'operator')):
            sid = uuid4()
            conn.execute('INSERT INTO users(id,telegram_user_id,role) VALUES(%s,%s,%s)', (uid, int(uid.hex[:12],16), role))
            conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')", (sid,uid))
            tokens[role] = api._create_session_token(uid,sid)
        for tid, name, duration, rub, stars in ((first,'День для проверки',86400,30000,151), (second,'Три дня для проверки',259200,90000,451)):
            conn.execute('INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,%s,%s,%s,%s)', (tid,name,duration,rub,stars))
        conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Канал клиента',%s)", (channel,owner,'https://t.me/pricing_'+cid.hex))
        conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
            duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status)
            VALUES(%s,%s,%s,%s,'День для проверки',86400,30000,'RUB','approved','awaiting_payment')""", (cid,owner,channel,first))
    contexts = []
    def launch(role, route):
        context = browser.new_context(viewport={'width':390,'height':844}, reduced_motion='reduce')
        contexts.append(context)
        context.add_init_script('sessionStorage.setItem("pushok-session", '+repr(tokens[role])+')')
        page = context.new_page()
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.route('**/*', lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
        page.goto(base)
        expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true')
        page.evaluate('route=>window.cabinet.go(route)',route)
        return page
    try:
        page = launch('admin','moderation/'+str(cid))
        editor = page.locator('.campaign-pricing')
        editor.locator(':scope > summary').click()
        editor.get_by_label('Изменить условия').select_option('tariff')
        editor.get_by_label('Новый тариф').select_option(str(second))
        expect(editor.locator('.pricing-preview')).to_contain_text('900')
        editor.get_by_label('Причина изменения').fill('Согласован срок три дня')
        editor.get_by_role('button',name='Сохранить',exact=True).click()
        expect(page.locator('.approval-card .campaign-tile-facts')).to_contain_text('Три дня для проверки')
        editor.locator(':scope > summary').click()
        editor.get_by_label('Скидка, %').fill('20')
        editor.get_by_label('Причина изменения').fill('Скидка постоянному клиенту')
        expect(editor.locator('.pricing-preview')).to_contain_text('720')
        expect(editor.locator('.pricing-preview')).to_contain_text('361 XTR')
        # Фоновый опрос не уничтожает редактируемую форму.
        page.evaluate('refreshStatuses()')
        expect(editor.get_by_label('Причина изменения')).to_have_value('Скидка постоянному клиенту')
        for width in (390,320):
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            editor.screenshot(path=str(output/f'48-campaign-pricing-{width}.png'))
        editor.get_by_role('button',name='Сохранить',exact=True).click()
        expect(page.locator('.approval-card .campaign-tile-facts')).to_contain_text('720')
        editor.locator(':scope > summary').click()
        editor.get_by_text('История изменений',exact=True).click()
        expect(editor.locator('.pricing-history')).to_contain_text('Скидка постоянному клиенту')
        client = launch('advertiser','campaign/'+str(cid))
        expect(client.locator('[data-page=campaign]')).to_contain_text('720')
        expect(client.locator('[data-page=campaign]')).to_contain_text('Индивидуальная скидка')
        operator_page = launch('operator','moderation/'+str(cid))
        expect(operator_page.locator('.approval-card .campaign-tile-facts')).to_contain_text('720')
        expect(operator_page.locator('.campaign-pricing')).to_have_count(0)
        with psycopg.connect(dsn) as conn:
            conn.execute("INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency,status,expires_at) VALUES(%s,'telegram_stars','telegram_bot',361,'XTR','pending',now()+interval '10 minutes')", (cid,))
        editor.locator(':scope > summary').click()
        editor.locator(':scope > summary').click()
        expect(editor).to_contain_text('уже открыт счёт')
        expect(editor.locator('form')).to_have_count(0)
    finally:
        for context in contexts:
            context.close()
