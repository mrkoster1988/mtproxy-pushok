"""Мобильные карточки, поиск, счета и история: Chromium с настоящей БД/API."""
import base64
import io
from unittest.mock import patch
from uuid import uuid4
import psycopg
from PIL import Image
from playwright.sync_api import expect


def check_mobile_polish(browser,base,output,failures,dsn):
    from app import main as api, channel_directory, wallet_ledger, wallet_topups
    ids={role:uuid4() for role in ('admin','advertiser','empty')};tokens={}
    cids=[uuid4() for _ in range(2)];campaigns=[uuid4() for _ in range(2)];tariff=uuid4();invoice=uuid4()
    with psycopg.connect(dsn) as conn:
        for i,(role,uid) in enumerate(ids.items()):
            conn.execute('INSERT INTO users(id,telegram_user_id,role,first_name,username) VALUES(%s,%s,%s,%s,%s)',(uid,99883800+i,'advertiser' if role=='empty' else role,'Проверка '+role,'polish_'+role))
            sid=uuid4();conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid));tokens[role]=api._create_session_token(uid,sid)
        for i,cid in enumerate(cids):conn.execute('INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,%s,%s)',(cid,ids['advertiser'],['Планета Excel','Новости puShok'][i],['https://t.me/polish_excel','https://t.me/polish_news'][i]))
        conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,'Мобильная проверка',3600,10000,5)",(tariff,))
        for cid in campaigns:
            conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status,placement_status,archived_at)
                VALUES(%s,%s,%s,%s,'Мобильная проверка',3600,10000,'RUB','approved','verified','completed',now())""",(cid,ids['advertiser'],cids[0],tariff))
        conn.execute("INSERT INTO payment_attempts(campaign_id,provider,payment_channel,amount,currency,status,provider_payment_id,settled_at) VALUES(%s,'telegram_stars','telegram_bot',5,'XTR','succeeded',%s,now())",(campaigns[0],str(uuid4())))
        conn.execute("INSERT INTO wallet_topups(id,user_id,request_id,provider,currency,amount,provider_account,test_mode,status) VALUES(%s,%s,%s,'telegram_stars','XTR',100,'test-only',false,'failed')",(invoice,ids['advertiser'],uuid4()))
    with wallet_topups.connect(dsn) as conn:
        wallet_ledger.post(conn,ids['advertiser'],'RUB',49900,'manual_credit','polish:'+str(uuid4()),'Начисление для проверки',actor=ids['admin'])
        wallet_ledger.post(conn,ids['advertiser'],'BONUS',100000,'manual_credit','polish:'+str(uuid4()),'Бонус для проверки',actor=ids['admin'])
    contexts=[]
    def launch(role):
        ctx=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce');contexts.append(ctx)
        ctx.add_init_script("sessionStorage.setItem('pushok-session',"+repr(tokens[role])+")")
        page=ctx.new_page();page.on('pageerror',lambda error:failures.append(str(error)))
        page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
        page.goto(base);expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true');return page
    def go(page,route):
        page.evaluate('r=>window.cabinet.go(r)',route)
        expect(page.locator('[data-page='+route.split('/')[0]+']')).to_be_visible()
    def narrow(page):
        for width in (320,390):
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    def section(page,key):
        node=page.locator('[data-wallet-section='+key+']');expect(node).to_be_visible()
        if node.get_attribute('open') is None:node.locator(':scope > summary').click()
        return node
    def lookup(value):
        username=channel_directory.reference(value)
        return dict(title='Найденный канал',username=username,telegram_url='https://t.me/'+username)
    try:
        client=launch('advertiser');go(client,'channels')
        search=client.get_by_role('searchbox',name='Поиск каналов')
        search.fill('@POLISH_EXCEL');expect(client.locator('.channel-tile')).to_have_count(1)
        expect(client.locator('.channel-tile .channel-avatar img')).to_be_visible()
        search.fill('неизвестный');expect(client.locator('.channel-manager .empty')).to_have_text('Каналы не найдены. Измените запрос.')
        search.fill('');row=client.locator('.channel-tile').filter(has_text='Планета Excel')
        row.get_by_role('button',name='Изменить канал',exact=True).click();row.get_by_label('Название канала',exact=True).fill('Планета Excel · обновлено')
        row.get_by_role('button',name='Сохранить изменения',exact=True).click();expect(client.locator('.channel-tile').filter(has_text='Планета Excel · обновлено')).to_have_count(1)
        row.get_by_role('button',name='Отчёт по каналу',exact=True).click();expect(row.locator('.channel-report')).to_contain_text('Завершённых: 2')
        narrow(client);client.screenshot(path=str(output/'60-channel-actions-390.png'),full_page=True)
        go(client,'create');client.locator('#order-channel-mode').click()
        with patch.object(channel_directory,'lookup',side_effect=lookup):
            client.locator('#order-channel-url').fill('@public_channel');client.locator('#order-new-channel').get_by_role('button',name='Найти канал',exact=True).click()
            expect(client.locator('#order-channel-title')).to_have_value('Найденный канал')
            expect(client.locator('#order-new-channel .channel-preview .channel-avatar img')).to_be_visible()
            expect(client.locator('#order-channel-url')).to_have_value('https://t.me/public_channel')
            narrow(client);client.screenshot(path=str(output/'61-channel-search-390.png'),full_page=True)
            client.set_viewport_size({'width':390,'height':430});client.locator('#order-channel-url').focus();client.locator('#order-channel-url').scroll_into_view_if_needed()
            assert client.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            client.set_viewport_size({'width':390,'height':844})
            client.locator('#order-channel-url').fill('@another_channel');expect(client.locator('#order-channel-title')).to_have_value('');expect(client.locator('#order-new-channel .channel-preview')).to_have_count(0)
        go(client,'archive');archive=client.locator('[data-page=archive] .archive-list');expect(archive.locator('[data-archive-kind=campaigns]')).to_have_count(2)
        rows=archive.locator('[data-archive-kind=campaigns]');a=rows.nth(0).bounding_box();b=rows.nth(1).bounding_box();assert b['y']-a['y']-a['height']>=10
        expect(rows.nth(0).get_by_role('button',name='Отчёт по кампании',exact=True)).to_be_visible()
        expect(rows.nth(0).locator('.channel-avatar img')).to_be_visible();narrow(client);client.screenshot(path=str(output/'62-archive-spacing-390.png'),full_page=True)
        go(client,'wallet');expect(client.locator('.wallet-entry')).to_have_count(3)
        expect(client.locator('.wallet-entry').filter(has_text='Telegram Stars')).to_contain_text('5 Stars')
        expect(client.locator('.wallet-entry').filter(has_text='Telegram Stars').get_by_role('link',name='Открыть заявку →')).to_have_attribute('href','#/archive/'+str(campaigns[0]))
        for width in (320,390):
            client.set_viewport_size({'width':width,'height':844})
            left=client.locator('.wallet-balances [data-currency=RUB]').bounding_box();right=client.locator('.wallet-balances [data-currency=BONUS]').bounding_box()
            assert abs(left['y']-right['y'])<1 and right['x']>=left['x']+left['width']
            assert client.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        invoices=section(client,'topups');client.screenshot(path=str(output/'63-wallet-history-390.png'),full_page=True)
        client.once('dialog',lambda d:d.dismiss());invoices.get_by_role('button',name='Удалить счёт',exact=True).click();expect(invoices.locator('.wallet-invoice')).to_have_count(1)
        client.once('dialog',lambda d:d.accept());invoices.get_by_role('button',name='Удалить счёт',exact=True).click();expect(invoices.locator('.wallet-invoice')).to_have_count(0)
        expect(client.locator('.wallet-entry')).to_have_count(3)
        invoices.get_by_role('button',name='Удалённые счета',exact=True).click();expect(invoices.locator('.wallet-invoice')).to_have_count(1)
        invoices.get_by_role('button',name='Восстановить счёт',exact=True).click();expect(invoices.locator('.wallet-invoice')).to_have_count(0)
        invoices.get_by_role('button',name='Текущие счета',exact=True).click();expect(invoices.locator('.wallet-invoice')).to_have_count(1)
        stars=section(client,'telegram');expect(stars.get_by_role('link',name='Открыть мои Stars в Telegram ↗')).to_have_attribute('href','tg://settings/stars')
        expect(stars.locator('a[href^="https://t.me/"]')).to_have_count(0)
        admin=launch('admin');go(admin,'wallet');area=section(admin,'admin')
        area.get_by_label('Пользователь: @никнейм или ID',exact=True).fill('@polish_advertiser');area.get_by_role('button',name='Найти пользователя',exact=True).click()
        area.get_by_role('button',name='Открыть баланс',exact=True).click();expect(area.locator('.wallet-admin-result .wallet-balances')).to_be_visible()
        back=area.get_by_role('button',name='К моему балансу',exact=True).bounding_box();identity=area.locator('.wallet-admin-result .user-summary').bounding_box()
        assert identity['y']>=back['y']+back['height']+10
        narrow(admin);area.screenshot(path=str(output/'64-balance-search-390.png'))
        area.get_by_role('button',name='К моему балансу',exact=True).click();expect(area).not_to_have_attribute('open','')
        go(admin,'user/'+str(ids['empty']));empty=admin.locator('[data-page=user] .empty');expect(empty).to_have_count(2)
        for node in empty.all():
            assert node.evaluate("e=>getComputedStyle(e).backgroundColor==='rgba(0, 0, 0, 0)' && getComputedStyle(e).display==='flex'")
            assert node.bounding_box()['height']<65
        narrow(admin);admin.screenshot(path=str(output/'65-empty-profile-390.png'),full_page=True)
        go(admin,'settings');admin.locator('#settings-payments-tab').click()
        info=admin.get_by_role('button',name='О внутреннем балансе',exact=True);body=admin.locator('#'+info.get_attribute('aria-controls'));expect(body).to_be_hidden();info.click();expect(body).to_be_visible();info.click();expect(body).to_be_hidden()
        admin.locator('#settings-system-tab').click();expect(admin.locator('#team-list .team-member')).not_to_have_count(0)
        narrow(admin);admin.locator('#team-list').screenshot(path=str(output/'66-team-cards-390.png'))
        png=io.BytesIO();Image.new('RGB',(48,48),'turquoise').save(png,format='PNG');catalog=[dict(id=str(123456700+i),emoji='⭐',image='data:image/png;base64,'+base64.b64encode(png.getvalue()).decode()) for i in range(12)]
        with patch.object(api.emoji_settings,'fetch',return_value=catalog):
            admin.locator('#icons-import').click();expect(admin.locator('#icons-mapping select')).to_have_count(6)
            for select in admin.locator('#icons-mapping select').all():
                assert '1234567' not in select.inner_text()
                assert select.bounding_box()['width']>150
            admin.locator('.emoji-catalog > summary').click();expect(admin.locator('.emoji-gallery img')).to_have_count(12)
            narrow(admin);admin.locator('#icons-form').screenshot(path=str(output/'67-emoji-settings-390.png'))
        assert not failures,failures
    finally:
        for context in contexts:context.close()
