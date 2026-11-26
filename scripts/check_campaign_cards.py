"""Одинаковая карточка на реальных маршрутах, мобильная геометрия и действия."""
import json
import os
from unittest.mock import patch
from uuid import uuid4

import psycopg
from playwright.sync_api import expect


def check_campaign_cards(browser, base, output, failures, dsn):
    from app import main, stars_checkout
    users = {role: uuid4() for role in ('advertiser', 'operator', 'outsider')}
    tokens, contexts = {}, []
    channel, tariff, node, paid, free, archived = [uuid4() for _ in range(6)]
    title = 'ОченьДлинноеНазваниеКаналаБезПробелов · Новости puShok'
    with psycopg.connect(dsn) as conn:
        for role, uid in users.items():
            sid = uuid4()
            conn.execute('INSERT INTO users(id,telegram_user_id,role) VALUES(%s,%s,%s)',
                         (uid, int(uid.hex[:12], 16), 'advertiser' if role == 'outsider' else role))
            conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')", (sid, uid))
            tokens[role] = main._create_session_token(uid, sid)
        conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,%s,'https://t.me/unified_cards')", (channel, users['advertiser'], title))
        conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,'Новые условия тарифа',86400,99900,999)", (tariff,))
        conn.execute("INSERT INTO proxy_nodes(id,name,host,port) VALUES(%s,'Карточки','cards.example.org',8443)", (node,))
        for cid in (paid, free, archived):
            conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
                duration_seconds_snapshot,price_kopecks_snapshot,stars_price_snapshot,currency_snapshot,
                moderation_status,payment_status,topic) VALUES(%s,%s,%s,%s,%s,10800,%s,37,'RUB','approved',%s,'adult')""",
                         (cid, users['advertiser'], channel, tariff, 'Пробник' if cid == free else 'Сохранённый тариф',
                          0 if cid == free else 30000, 'verified' if cid == free else 'awaiting_payment'))
        conn.execute("""UPDATE campaigns SET placement_status='scheduled',proxy_node_id=%s,
            scheduled_start_at=now()+interval '1 day',scheduled_end_at=now()+interval '1 day 3 hours' WHERE id=%s""", (node, free))
        conn.execute("UPDATE campaigns SET archived_at=now(),placement_status='cancelled' WHERE id=%s", (archived,))

    def launch(role, mini=False):
        context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        contexts.append(context)
        context.add_init_script('sessionStorage.setItem("pushok-session",' + repr(tokens[role]) + ')')
        if mini:
            from test_miniapp_auth import signed_data
            raw = signed_data(user=json.dumps({'id': int(users[role].hex[:12], 16), 'first_name': 'Тест'}), query_id='receipt-mobile')
            context.add_init_script('window.Telegram={WebApp:{initData:'+json.dumps(raw)+',colorScheme:"dark",ready(){},expand(){},onEvent(){},BackButton:{onClick(){},show(){},hide(){}}}}')
        page = context.new_page()
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(base + '/') else r.abort())
        page.goto(base + ('/miniapp' if mini else ''))
        expect(page.locator('body')).to_have_attribute('data-cabinet-loaded', 'true')
        return page

    def go(page, route):
        page.evaluate('route => window.cabinet.go(route)', route)
        expect(page.locator('[data-page="' + route.split('/')[0] + '"]')).to_be_visible()

    reference = None

    def check(page, route, cid, destination):
        nonlocal reference
        go(page, route)
        card = page.locator('[data-page="' + route.split('/')[0] + '"] .campaign-tile[data-campaign-id="' + str(cid) + '"]')
        expect(card).to_have_count(1)
        expect(card.locator('.tile-title')).to_have_text(title)
        expect(card.locator('.tile-heading > .icon-action')).to_have_attribute('href', '#/' + destination)
        expect(card.locator('.campaign-channel-link')).to_have_attribute('href', 'https://t.me/unified_cards')
        expect(card.locator('.campaign-topic')).to_contain_text('18+')
        expect(card.locator('.campaign-steps dt')).to_have_text(['Модерация', 'Оплата', 'Размещение'])
        expect(card.locator('.campaign-tile-facts dt')).to_have_text(['Тариф', 'Срок', 'Стоимость'])
        expect(card.locator('.campaign-tile-facts')).to_contain_text('3 ч')
        expect(card.locator('.campaign-tile-facts')).to_contain_text('Пробник' if cid == free else 'Сохранённый тариф')
        expect(card.locator('.campaign-tile-facts')).to_contain_text('Без оплаты' if cid == free else '300')
        for width in (320, 390, 768, 1280):
            page.set_viewport_size({'width': width, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (route, width)
            style = card.evaluate('''card => {
                const css=getComputedStyle(card),head=card.querySelector('.tile-heading'),
                      title=getComputedStyle(card.querySelector('.tile-title')),
                      parts=[...head.children].map(n=>n.getBoundingClientRect());
                for(let i=1;i<parts.length;i++)if(parts[i].x<parts[i-1].right-1)throw Error('Пересечение шапки');
                const steps=card.querySelector('.campaign-steps').getBoundingClientRect(),
                      facts=card.querySelector('.campaign-tile-facts').getBoundingClientRect();
                if(facts.y-steps.bottom<9)throw Error('Слипшиеся блоки');
                return [css.padding,css.borderRadius,css.backgroundImage,title.fontSize,
                        getComputedStyle(card.querySelector('.campaign-tile-facts')).gridTemplateColumns.split(' ').length];
            }''')
            if width == 390:
                if reference is None:
                    reference = style
                assert style == reference, (route, style, reference)
                card.screenshot(path=str(output / ('unified-' + route.replace('/', '-') + '.png')))
            for action in card.locator('.icon-action').all():
                assert action.get_attribute('aria-label')
                expect(action.locator('svg use')).to_have_count(1)
        page.set_viewport_size({'width': 390, 'height': 844})
        return card

    try:
        client, operator, outsider = launch('advertiser'), launch('operator'), launch('outsider')
        for route in ('campaigns', 'reports', 'create/' + str(paid)):
            card = check(client, route, paid, 'campaign/' + str(paid))
            if route == 'campaigns':
                remove=card.get_by_role('button',name='Удалить',exact=True)
                pay=card.get_by_role('link',name='Оплатить',exact=True)
                left,right=remove.bounding_box(),pay.bounding_box()
                assert left['x']+left['width']<=right['x']+1 and abs(left['y']-right['y'])<2
                client.once('dialog', lambda popup: popup.dismiss())
                remove.click()
                expect(card).to_be_visible()
                pay.click()
                expect(client.locator('[data-page=payments]')).to_be_visible()
            if route == 'reports':
                card.get_by_role('button', name='История кампании').click()
                expect(card.locator('.campaign-report')).to_contain_text('История действий')
        go(client, 'campaign/' + str(paid))
        detail = client.locator('[data-page=campaign] .campaign-page')
        hero = detail.locator('.campaign-overview')
        expect(hero.get_by_role('heading', name=title)).to_be_visible()
        expect(hero.locator('.campaign-channel-link')).to_have_attribute('href', 'https://t.me/unified_cards')
        expect(hero.locator('.campaign-tile-facts dt')).to_have_text(['Тариф', 'Срок', 'Стоимость'])
        expect(hero.locator('.campaign-current')).to_be_visible()
        expect(hero.locator('.campaign-topic')).to_contain_text('18+')
        expect(detail.get_by_role('link', name='Перейти к оплате')).to_be_visible()
        expect(detail.locator('.management-actions .danger-text')).to_be_hidden()
        for width in (320, 390, 768, 1280):
            client.set_viewport_size({'width': width, 'height': 844})
            assert client.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            if width in (390, 768):
                detail.screenshot(path=str(output / f'campaign-overview-{width}.png'))
        check(client, 'campaigns', free, 'campaign/' + str(free))
        foreign=check(operator, 'campaigns', paid, 'moderation/' + str(paid))
        expect(foreign.get_by_text('Поделиться ссылкой на оплату',exact=True)).to_be_visible()
        check(operator, 'moderation', paid, 'moderation/' + str(paid)).get_by_role('link', name='Разобрать заявку').click()
        check(operator, 'moderation/' + str(paid), paid, 'moderation')
        check(operator, 'sponsors', free, 'moderation/' + str(free))
        check(operator, 'user/' + str(users['advertiser']), paid, 'moderation/' + str(paid))
        card = check(client, 'archive', archived, 'archive/' + str(archived))
        card.get_by_role('button', name='Отчёт по кампании').click()
        expect(card.locator('.campaign-report')).to_contain_text('История действий')
        response = outsider.request.get(base + '/api/v1/management/archive', headers={'Authorization': 'Bearer ' + tokens['outsider']})
        assert str(archived) not in {item['id'] for item in response.json()['items']}
        card.get_by_role('button', name='Восстановить', exact=True).click()
        expect(client.locator('[data-archive-id="' + str(archived) + '"]')).to_have_count(0)
        check(client, 'campaigns', archived, 'campaign/' + str(archived))
        from app import wallet_ledger
        from psycopg.rows import dict_row
        with psycopg.connect(dsn,row_factory=dict_row) as conn:
            previous=conn.execute('SELECT enabled,bonus_spending_enabled FROM wallet_settings WHERE singleton').fetchone()
            conn.execute('UPDATE wallet_settings SET enabled=true,bonus_spending_enabled=true WHERE singleton')
            wallet_ledger.post(conn,users['advertiser'],'BONUS',30000,'manual_credit','ui-bonus:'+str(paid),'Тест бонусов')
        try:
            go(client,'wallet')
            expect(client.locator('.wallet-group h2').first).to_have_text('Мой кошелёк')
            client.locator('.wallet-bonus-shortcut').click()
            expect(client.locator('[data-page=payments] .payment-group h2').first).to_contain_text('К оплате')
            client.locator('[data-page=payments] .receipt-list-item[data-campaign-id="'+str(paid)+'"] .campaign-pay-action').click()
            redeem=client.get_by_role('button',name='Оплатить бонусами',exact=False)
            expect(redeem).to_be_enabled()
            expect(client.locator('.payment-receipt h2')).to_have_text(title)
            expect(client.locator('[data-page=payments] .campaign-tile')).to_have_count(0)
            expect(client.get_by_role('link',name='Открыть кампанию',exact=True)).to_have_attribute('href','#/campaign/'+str(paid))
            with patch.dict(os.environ,{'PUSHOK_MINIAPP_ENABLED':'1','TELEGRAM_BOT_USERNAME':'mtproxy_pushok_bot'}), patch.object(main,'TELEGRAM_BOT_TOKEN','fixture-token'), patch.object(stars_checkout,'stars_invoices_enabled',return_value=False):
                mobile=launch('advertiser',mini=True)
                go(mobile,'payments/'+str(paid))
                expect(mobile.locator('.receipt-status')).to_have_text('К оплате')
                expect(mobile.locator('.payment-receipt .sbp-checkout .method-state')).to_have_text('Недоступно')
                expect(mobile.locator('.payment-receipt .sbp-checkout button')).to_have_count(0)
                use=mobile.get_by_role('button',name='Оплатить бонусами',exact=False)
                for width in (320,390,768,1280):
                    mobile.set_viewport_size({'width':width,'height':720})
                    expect(use).to_be_in_viewport()
                    assert mobile.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                with psycopg.connect(dsn,row_factory=dict_row) as conn:
                    conn.execute("UPDATE sponsor_channels SET title='Атлас путешествий' WHERE id=%s",(channel,))
                    conn.execute("UPDATE campaigns SET price_kopecks_snapshot=39900,tariff_name_snapshot='Суточник',duration_seconds_snapshot=86400 WHERE id=%s",(paid,))
                    wallet_ledger.post(conn,users['advertiser'],'BONUS',130000,'manual_credit','ui-receipt:'+str(paid),'Проверка остатка')
                mobile.evaluate('() => refresh()')
                expect(use).to_contain_text('399')
                expect(mobile.locator('.bonus-remainder')).to_contain_text('1 201')
                mobile.set_viewport_size({'width':390,'height':720})
                mobile.screenshot(path=str(output/'bonus-checkout-mobile.png'))
                use.click()
                expect(mobile.locator('.receipt-status')).to_have_text('Оплачено')
                expect(mobile.locator('.wallet-checkout')).to_contain_text('Оплачено с баланса')
                expect(mobile.get_by_role('button',name='Оплатить бонусами',exact=False)).to_have_count(0)
        finally:
            with psycopg.connect(dsn) as conn:
                conn.execute('UPDATE sponsor_channels SET title=%s WHERE id=%s',(title,channel))
                conn.execute('UPDATE wallet_settings SET enabled=%s,bonus_spending_enabled=%s WHERE singleton',(previous['enabled'],previous['bonus_spending_enabled']))
    finally:
        for context in contexts:
            context.close()
