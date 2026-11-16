"""Клиент выбирает время, оплата запускает подбор; узлы доступны в админке."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
from playwright.sync_api import expect


def check_automatic_schedule(browser, base, output, failures, dsn):
    from app import main as api
    from app.auto_schedule import service_once
    owner,admin,node,channel,tariff,busy,campaign=[uuid4() for _ in range(7)]
    start=datetime.now(timezone.utc).replace(second=0,microsecond=0)+timedelta(days=3)
    host='schedule-'+node.hex+'.example.org';url='https://t.me/automatic_slot_test'
    tokens={};contexts=[];replacement_node=uuid4();replacement_busy=uuid4()
    with psycopg.connect(dsn) as conn:
        for uid,role in ((owner,'advertiser'),(admin,'admin')):
            conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name) VALUES(%s,%s,%s,'Автоподбор')",(uid,int(uid.hex[:12],16),role))
            sid=uuid4();conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid));tokens[role]=api._create_session_token(uid,sid)
        conn.execute("INSERT INTO proxy_nodes(id,name,host,port) VALUES(%s,'Автоподбор',%s,8443)",(node,host))
        conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Тест расписания',%s)",(channel,owner,url))
        conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,'Час',3600,100,1)",(tariff,))
        for cid in (busy,campaign):
            conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,
                price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status,requested_start_at)
                VALUES(%s,%s,%s,%s,'Час',3600,100,'RUB','approved',%s,%s)""",(cid,owner,channel,tariff,'verified' if cid==busy else 'awaiting_payment',start))
        conn.execute("UPDATE campaigns SET placement_status='scheduled',proxy_node_id=%s,scheduled_start_at=%s,scheduled_end_at=%s WHERE id=%s",(node,start,start+timedelta(hours=1),busy))
        conn.execute("INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,ad_tag,confirmed_by,channel_url,node_host,node_port) VALUES(%s,%s,%s,%s,%s,%s,8443)",(node,channel,uuid4().hex,admin,url,host))
    def launch(role,width):
        ctx=browser.new_context(viewport={'width':width,'height':844},reduced_motion='reduce');contexts.append(ctx)
        ctx.add_init_script('sessionStorage.setItem("pushok-session",'+repr(tokens[role])+')')
        page=ctx.new_page();page.on('pageerror',lambda e:failures.append(str(e)))
        page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
        page.goto(base);expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true');return page
    def go(page,route):page.evaluate('route=>window.cabinet.go(route)',route)
    try:
        client=launch('advertiser',320)
        client.route('**/api/v1/availability?*',lambda r:r.fulfill(json={'days':[{'start_at':start.isoformat(),'status':'occupied','available_nodes':0}]}))
        go(client,'calendar');client.evaluate('loadAvailability()')
        day=client.locator('[data-availability=occupied]');expect(day).to_be_enabled();day.click()
        expect(client.locator('#requested-start')).to_have_value(start.isoformat())
        go(client,'campaign/'+str(campaign));expect(client.get_by_role('button',name='Бронь на 15 минут',exact=True)).to_have_count(0)
        with psycopg.connect(dsn) as conn:conn.execute("UPDATE campaigns SET payment_status='verified' WHERE id=%s",(campaign,))
        service_once(dsn,limit=100)
        response=client.request.get(base+'/api/v1/campaigns',headers={'Authorization':'Bearer '+tokens['advertiser']})
        row=next(c for c in response.json() if c['id']==str(campaign))
        assert datetime.fromisoformat(row['scheduled_start_at'].replace('Z','+00:00'))==start+timedelta(hours=1)
        assert datetime.fromisoformat(row['scheduled_end_at'].replace('Z','+00:00'))==start+timedelta(hours=2)
        client.evaluate('refresh()');expect(client.locator('[data-page=campaign] .campaign-steps')).to_contain_text('Запланировано')
        assert client.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        client.screenshot(path=str(output/'52-automatic-schedule-client-320.png'))
        staff=launch('admin',390);go(staff,'moderation/'+str(campaign))
        expect(staff.locator('.approval-period')).to_contain_text('Начало:')
        expect(staff.locator('.approval-current')).to_contain_text('Ожидается автоматический запуск')
        staff.screenshot(path=str(output/'53-automatic-schedule-admin-390.png'))
        staff.get_by_role('link',name='Настройки запуска',exact=True).click()
        expect(staff).to_have_url(base+'/#/sponsors/'+str(channel)+'/'+str(node))
        expect(staff.locator('#promotion-automation form')).to_be_visible()
        expect(staff.locator('#promotion-automation').get_by_label('Канал',exact=True)).to_have_value(str(channel))
        expect(staff.locator('#promotion-automation').get_by_label('Узел',exact=True)).to_have_value(str(node))
        staff.screenshot(path=str(output/'54-automatic-schedule-settings-390.png'))
        expect(staff.locator('#promotion-automation').get_by_label('Узел',exact=True)).to_be_in_viewport()
        # Готовая связь на другом узле должна сама заменить неподготовленный прогноз.
        with psycopg.connect(dsn) as conn:
            conn.execute('DELETE FROM channel_promotion_bindings WHERE proxy_node_id=%s AND channel_id=%s',(node,channel))
            replacement_host='replacement-'+replacement_node.hex+'.example.org'
            conn.execute("INSERT INTO proxy_nodes(id,name,host,port) VALUES(%s,'Готовый узел',%s,8443)",(replacement_node,replacement_host))
            conn.execute("INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,ad_tag,confirmed_by,channel_url,node_host,node_port) VALUES(%s,%s,%s,%s,%s,%s,8443)",(replacement_node,channel,uuid4().hex,admin,url,replacement_host))
            conn.execute('''INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,
                price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status,placement_status,
                proxy_node_id,scheduled_start_at,scheduled_end_at)
                VALUES(%s,%s,%s,%s,'Занято',10800,0,'RUB','approved','verified','scheduled',%s,%s,%s)''',(replacement_busy,owner,channel,tariff,replacement_node,start,start+timedelta(hours=3)))
        client.evaluate('refresh()');expect(client.locator('[data-page=campaign] [data-promotion-summary]')).to_contain_text('Подбираем готовый тег')
        service_once(dsn,limit=100);client.evaluate('refresh()')
        expect(client.locator('[data-page=campaign] [data-promotion-summary]')).to_contain_text('Ожидается автоматический запуск')
        response=client.request.get(base+'/api/v1/campaigns/'+str(campaign)+'/promotion-status',headers={'Authorization':'Bearer '+tokens['advertiser']})
        assert response.json()['prepared'] is True
        assert datetime.fromisoformat(response.json()['scheduled_start_at'].replace('Z','+00:00'))==start+timedelta(hours=3)
        client.screenshot(path=str(output/'75-prepared-tag-auto-selection-320.png'))

    finally:
        for ctx in contexts:ctx.close()
        with psycopg.connect(dsn) as conn:
            conn.execute("UPDATE campaigns SET placement_status='cancelled' WHERE id=ANY(%s)",([campaign,busy,replacement_busy],))
            conn.execute("UPDATE proxy_nodes SET status='paused' WHERE id=ANY(%s)",([node,replacement_node],))
