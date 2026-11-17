"""Модерация на реальном API: закрытые/раскрытые блоки, формы и мобильная геометрия."""
import json
import os
import re
from unittest.mock import patch
from uuid import uuid4

import psycopg
from playwright.sync_api import expect


def check_moderation_layout(browser, base, output, failures, dsn):
    from app import main
    tokens, contexts, telegram_ids = {}, [], {}
    owner, channel, tariff, node = [uuid4() for _ in range(4)]
    cases = {state: uuid4() for state in ('pending', 'payment', 'schedule', 'scheduled', 'active', 'completed', 'archived')}
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name) VALUES(%s,%s,'advertiser','Рекламодатель')", (owner, int(owner.hex[:12], 16)))
        for role in ('admin', 'operator'):
            uid, sid = uuid4(), uuid4()
            if role == 'admin':
                uid = conn.execute('SELECT id FROM users WHERE is_owner').fetchone()[0]
            else:
                conn.execute("INSERT INTO users(id,telegram_user_id,role) VALUES(%s,%s,'operator')", (uid, int(uid.hex[:12], 16)))
            conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')", (sid, uid))
            tokens[role] = main._create_session_token(uid, sid)
            telegram_ids[role] = conn.execute("SELECT telegram_user_id FROM users WHERE id=%s", (uid,)).fetchone()[0]
        conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'puShok Новости','https://t.me/moderation_layout')", (channel, owner))
        conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,'Пробник',10800,0,1)", (tariff,))
        conn.execute("INSERT INTO proxy_nodes(id,name,host,port) VALUES(%s,'Модерация','layout.example.org',8443)", (node,))
        for offset, (state, cid) in enumerate(cases.items(), 1):
            placement = 'active' if state == 'archived' else state if state in ('scheduled', 'active', 'completed') else 'not_scheduled'
            conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
                duration_seconds_snapshot,price_kopecks_snapshot,stars_price_snapshot,currency_snapshot,
                moderation_status,payment_status,placement_status) VALUES(%s,%s,%s,%s,'Пробник',10800,0,1,'RUB',%s,%s,%s)""",
                         (cid, owner, channel, tariff, 'pending' if state == 'pending' else 'approved',
                          'awaiting_payment' if state in ('pending', 'payment') else 'verified', 'not_scheduled'))
            if placement != 'not_scheduled':
                conn.execute("INSERT INTO proxy_nodes(id,name,host,port) VALUES(%s,%s,'layout.example.org',%s)", (cid, 'Узел ' + state, 8443 + offset))
                conn.execute("UPDATE campaigns SET placement_status=%s,proxy_node_id=%s,scheduled_start_at=now()+%s*interval '1 day',scheduled_end_at=now()+%s*interval '1 day'+interval '3 hours' WHERE id=%s", (placement, cid, offset, offset, cid))
            if state == 'archived':
                conn.execute('UPDATE campaigns SET archived_at=now() WHERE id=%s', (cid,))

    def launch(role, mini=False):
        context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        contexts.append(context)
        context.add_init_script('sessionStorage.setItem("pushok-session",' + repr(tokens[role]) + ')')
        if mini:
            from test_miniapp_auth import signed_data
            raw = signed_data(user=json.dumps({'id': telegram_ids[role], 'first_name': 'Оператор Mini App'}))
            context.add_init_script("""window.Telegram={WebApp:{initData:RAW_DATA,colorScheme:'dark',
                safeAreaInset:{top:18,bottom:24},contentSafeAreaInset:{top:12,bottom:16},
                ready(){},expand(){},onEvent(){},BackButton:{show(){},hide(){},onClick(){}}}};
            """.replace('RAW_DATA', json.dumps(raw)))
        page = context.new_page()
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(base + '/') else r.abort())
        page.goto(base + ('/miniapp' if mini else ''))
        expect(page.locator('body')).to_have_attribute('data-cabinet-loaded', 'true')
        return page

    def go(page, state):
        page.evaluate('route=>cabinet.go(route)', 'moderation/' + str(cases[state]))
        card = page.locator('.approval-card[data-campaign-id="' + str(cases[state]) + '"]')
        expect(card).to_be_visible()
        return card

    def geometry(page, card):
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'Горизонтальная прокрутка'
        card.evaluate('''card=>{
            const visible=n=>!!n.getClientRects().length;
            const blocks=[...card.children].filter(visible).map(n=>({box:n.getBoundingClientRect(),name:n.className}));
            for(let i=1;i<blocks.length;i++){
                const gap=blocks[i].box.top-blocks[i-1].box.bottom;
                if(gap<11||gap>13)throw Error('Интервал разделов '+gap+' '+blocks[i].name);
            }
            for(const tools of card.querySelectorAll('.campaign-action-tools,.assistant-actions,.analytics-actions')){
                const buttons=[...tools.querySelectorAll(':scope>button')].filter(visible);
                for(let i=0;i<buttons.length;i++){
                    const r=buttons[i].getBoundingClientRect();
                    if(r.width<44||r.height<44)throw Error('Малая кнопка '+buttons[i].ariaLabel);
                    if(i){const prev=buttons[i-1].getBoundingClientRect();
                        if(Math.abs(r.y-prev.y)<1&&r.left-prev.right<9)throw Error('Слипшиеся кнопки');}
                }
            }
            const steps=[...card.querySelectorAll('.approval-progress li')].map(n=>n.getBoundingClientRect());
            if(steps.some(r=>Math.abs(r.y-steps[0].y)>1))throw Error('Перенос этапов');
        }''')
        for action in card.locator('button.icon-action:visible').all():
            assert action.get_attribute('aria-label')
            expect(action.locator('svg use')).to_have_count(1)

    try:
        admin = launch('admin')
        card = go(admin, 'scheduled')
        for width in (320, 390, 768, 1280):
            admin.set_viewport_size({'width': width, 'height': 844})
            geometry(admin, card)
        admin.set_viewport_size({'width': 390, 'height': 844})
        admin.screenshot(path=str(output / 'moderation-scheduled-closed.png'), full_page=True)
        # Click real disclosure controls: DOM rewrites must not break toggle handlers.
        for title in ('Тариф и скидка', 'ИИ-помощник', 'Изменить или удалить заявку', 'История и документы'):
            card.locator('summary').filter(has_text=title).click()
        expect(card.locator('.campaign-pricing')).to_contain_text('Условия можно менять только до оплаты')
        tools = card.get_by_role('group', name='Действия заявки', exact=True)
        expect(tools.locator('button')).to_have_count(3)
        for name, icon in (('Изменить слот', 'edit'), ('Снять будущий слот', 'close'), ('Удалить', 'trash'), ('Отчёт по кампании', 'chart'), ('Журнал проверок', 'history')):
            expect(card.get_by_role('button', name=name, exact=True).locator('use')).to_have_attribute('href', re.compile(r'/assets/pushok-icons\.svg(?:\?v=\d+)?#pi-' + icon + '$'))
        card.get_by_role('button', name='Изменить слот', exact=True).click()
        expect(card.get_by_label('Новое начало слота', exact=True)).to_be_visible()
        for width in (320, 390, 768, 1280):
            admin.set_viewport_size({'width': width, 'height': 844})
            geometry(admin, card)
        card.get_by_role('button', name='Отмена', exact=True).click()
        card.get_by_role('button', name='Отчёт по кампании', exact=True).click()
        expect(card.locator('.campaign-report')).to_contain_text('История действий')
        expect(card.locator('.report-cell').filter(has_text='Оплата')).to_contain_text('Без оплаты')
        card.get_by_role('button', name='Журнал проверок', exact=True).click()
        expect(card.locator('.observation-panel')).to_contain_text('Проверок пока нет')
        admin.set_viewport_size({'width': 390, 'height': 844})
        admin.evaluate('scrollTo(0,0)')
        admin.screenshot(path=str(output / 'moderation-scheduled-open.png'), full_page=True)
        card.locator('summary').filter(has_text='Дополнительная ручная проверка').click()
        expect(card.get_by_label('Что увидели при проверке', exact=True)).to_be_visible()
        admin.set_viewport_size({'width': 320, 'height': 844})
        geometry(admin, card)
        for state in ('pending', 'payment', 'schedule', 'active', 'archived'):
            card = go(admin, state)
            # Include forms, nested panels and archived completion; no real mutations here.
            for disclosure in card.locator('details').all():
                disclosure.evaluate('node=>node.open=true')
            geometry(admin, card)
            if state == 'archived':
                expect(card.get_by_role('button', name='Удалить', exact=True)).to_have_count(0)
            admin.screenshot(path=str(output / ('moderation-' + state + '-open.png')), full_page=True)
        operator = launch('operator')
        card = go(operator, 'scheduled')
        expect(card.locator('.campaign-pricing')).to_have_count(0)
        expect(card.get_by_role('link', name='Настройки запуска')).to_have_count(0)
        card.locator('summary').filter(has_text='Изменить или удалить заявку').click()
        card.locator('summary').filter(has_text='История и документы').click()
        geometry(operator, card)
        operator.locator('.archived-review > summary').click()
        expect(operator.get_by_role('link', name='Открыть для завершения', exact=True)).to_be_visible()
        with patch.dict(os.environ, {'PUSHOK_MINIAPP_ENABLED': '1', 'TELEGRAM_BOT_USERNAME': 'mtproxy_pushok_bot'}), patch.object(main, 'TELEGRAM_BOT_TOKEN', 'fixture-token'):
            mini = launch('admin', mini=True)
            expect(mini.locator('body')).to_have_class('miniapp-mode cabinet-mode')
            card = go(mini, 'scheduled')
            card.locator('summary').filter(has_text='Изменить или удалить заявку').click()
            card.locator('summary').filter(has_text='История и документы').click()
            for width in (320, 390):
                mini.set_viewport_size({'width': width, 'height': 844})
                geometry(mini, card)
                remove = card.get_by_role('button', name='Удалить', exact=True)
                remove.evaluate("node=>node.scrollIntoView({block:'center',behavior:'instant'})")
                box = remove.bounding_box()
                header = mini.locator('.cabinet-topbar').bounding_box()
                footer = mini.locator('.mobile-bottom-nav').bounding_box()
                assert box['y'] >= header['y'] + header['height'] and box['y'] + box['height'] <= footer['y']
            mini.screenshot(path=str(output / 'moderation-miniapp-actions.png'))
    finally:
        for context in contexts:
            context.close()
