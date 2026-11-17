"""Возвраты через браузер: настоящие API/SQL, только тестовый внутренний баланс."""
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from playwright.sync_api import expect


def check_campaign_refunds(browser, base, output, failures, dsn):
    from app import main, wallet_ledger as ledger

    admin, owner, sid, cid, channel, tariff = [uuid4() for _ in range(6)]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO users(id,telegram_user_id,first_name,role) VALUES(%s,99884901,'Возвраты','admin'),(%s,99884902,'Рекламодатель','advertiser')", (admin, owner))
        conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')", (sid, admin))
        conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Тест возврата','https://t.me/refund_fixture')", (channel, owner))
        conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks) VALUES(%s,'День',86400,30000)", (tariff,))
        conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status)
            VALUES(%s,%s,%s,%s,'День',86400,30000,'RUB','approved','awaiting_payment')""", (cid, owner, channel, tariff))
        for currency, amount in [('RUB',25000), ('BONUS',5000)]:
            ledger.post(conn, owner, currency, amount, 'manual_credit', 'refund-ui:'+str(cid)+currency, 'Тестовый баланс', actor=admin)
        ledger.charge(conn, cid, owner, 'RUB')
    token = main._create_session_token(admin, sid)
    context = browser.new_context(viewport={'width':390, 'height':844}, reduced_motion='reduce')
    context.add_init_script('sessionStorage.setItem("pushok-session",'+repr(token)+')')
    page = context.new_page()
    page.on('pageerror', lambda e: failures.append(str(e)))
    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base+'/') else route.abort())
    page.on('dialog', lambda dialog: dialog.accept())
    try:
        page.goto(base+'/#/moderation/'+str(cid))
        expect(page.locator('body')).to_have_attribute('data-cabinet-loaded', 'true')
        area = page.locator('.campaign-refunds')
        area.locator(':scope > summary').click()
        expect(area).to_contain_text('Доступно к возврату: 300')
        area.get_by_role('button', name='Вернуть средства', exact=True).click()
        area.get_by_label('Вид возврата', exact=True).click()
        page.get_by_role('dialog').get_by_role('option', name='Частичный возврат', exact=True).click()
        area.get_by_label('Сумма возврата', exact=True).fill('70')
        area.get_by_label('Причина возврата', exact=True).fill('Перенос размещения по просьбе клиента')
        for width in (320,390,768,1280):
            page.set_viewport_size({'width':width, 'height':900})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            area.screenshot(path=str(output/f'refund-form-{width}.png'))
        with page.expect_response(lambda r: r.url.endswith('/refunds') and r.request.method=='POST') as response:
            area.get_by_role('button', name='Подтвердить возврат', exact=True).click()
        assert response.value.ok, response.value.text()
        expect(area).to_contain_text('Доступно к возврату: 230')
        expect(area).to_contain_text('Возвращено · 70')
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            assert ledger.accounts(conn, owner)['RUB'] == 2000
            assert ledger.accounts(conn, owner)['BONUS'] == 5000
            campaign = conn.execute('SELECT * FROM campaigns WHERE id=%s', (cid,)).fetchone()
            assert campaign['placement_status']=='cancelled' and campaign['auto_schedule_paused']
            assert campaign['refunded_amount']==7000
        # Новый полный возврат забирает только остаток, после перезагрузки повторной формы нет.
        area.get_by_role('button', name='Вернуть средства', exact=True).click()
        area.get_by_label('Причина возврата', exact=True).fill('Окончательная отмена размещения')
        area.get_by_role('button', name='Подтвердить возврат', exact=True).click()
        expect(area).to_contain_text('Доступно к возврату: 0')
        expect(area.get_by_role('button', name='Вернуть средства', exact=True)).to_have_count(0)
        page.reload()
        expect(page.locator('body')).to_have_attribute('data-cabinet-loaded', 'true')
        if not area.evaluate('(e)=>e.open'): area.locator(':scope > summary').click()
        expect(area).to_contain_text('Доступно к возврату: 0')
        expect(area.get_by_role('button', name='Вернуть средства', exact=True)).to_have_count(0)
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            assert ledger.accounts(conn, owner)['RUB']==25000
            assert ledger.accounts(conn, owner)['BONUS']==5000
            assert conn.execute('SELECT count(*) n FROM campaign_refunds WHERE campaign_id=%s', (cid,)).fetchone()['n']==2
        assert not failures, failures
    finally:
        context.close()
