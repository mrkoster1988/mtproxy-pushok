"""Лицензия: настоящие формы, API и квоты PostgreSQL, без списания реальных Stars."""
from uuid import uuid4
import psycopg
from playwright.sync_api import expect


def check_licenses(browser,base,output,failures,dsn):
    from app import main as api
    ids={role:uuid4() for role in ('admin','advertiser')};tokens={};contexts=[]
    with psycopg.connect(dsn) as conn:
        conn.execute('UPDATE license_settings SET enabled=false')
        for i,(role,uid) in enumerate(ids.items()):
            conn.execute('INSERT INTO users(id,telegram_user_id,role,first_name) VALUES(%s,%s,%s,%s)',(uid,99883500+i,role,'Лицензия '+role))
            sid=uuid4();conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid));tokens[role]=api._create_session_token(uid,sid)
    def launch(role):
        ctx=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce');contexts.append(ctx)
        ctx.add_init_script("sessionStorage.setItem('pushok-session',"+repr(tokens[role])+")")
        p=ctx.new_page();p.on('pageerror',lambda e:failures.append(str(e)));p.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort());p.goto(base)
        expect(p.locator('body')).to_have_attribute('data-cabinet-loaded','true');return p
    def go(p,route):
        p.evaluate('r=>window.cabinet.go(r)',route);expect(p.locator('[data-page='+route+']')).to_be_visible()
    try:
        admin=launch('admin');go(admin,'profile');admin.get_by_role('link',name='Лицензия',exact=True).click()
        expect(admin.locator('.licenses-page')).to_contain_text('Лицензирование отключено')
        admin.get_by_role('button',name='Добавить тариф',exact=True).click()
        editor=admin.locator('.licenses-page > section').filter(has=admin.get_by_role('heading',name='Новый тариф',exact=True))
        editor.get_by_label('Название',exact=True).fill('Пробный кабинет')
        editor.get_by_label('Срок, дней',exact=True).fill('7')
        editor.get_by_label('Цена, Stars',exact=True).fill('0')
        editor.get_by_label('Каналы',exact=True).fill('1')
        editor.get_by_label('Незавершённые кампании',exact=True).fill('2')
        editor.get_by_label('Новые кампании за срок лицензии',exact=True).fill('5')
        editor.get_by_label('Тариф доступен для покупки',exact=True).check()
        editor.get_by_role('button',name='Сохранить',exact=True).click()
        expect(admin.locator('.license-plan-admin')).to_contain_text('Пробный кабинет')
        admin.get_by_label('Лицензия нужна для новых каналов и кампаний',exact=True).check()
        admin.get_by_role('button',name='Сохранить режим',exact=True).click()
        expect(admin.locator('.license-plans')).to_contain_text('Пробный кабинет')
        for width in (320,390):
            admin.set_viewport_size({'width':width,'height':844});assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        admin.evaluate("document.getElementById('notice').hidden=true")
        admin.screenshot(path=str(output/'57-license-admin.png'),full_page=True)
        client=launch('advertiser');go(client,'profile');client.get_by_role('link',name='Лицензия',exact=True).click()
        expect(client.locator('.licenses-page')).to_contain_text('Нет действующей лицензии')
        expect(client.get_by_role('button',name='Добавить тариф',exact=True)).to_have_count(0)
        client.get_by_role('button',name='Получить период',exact=True).click()
        expect(client.locator('.licenses-page > section').first).to_contain_text('Пробный кабинет')
        expect(client.locator('.licenses-page > section').first).to_contain_text('До ')
        expect(client.get_by_role('button',name='Уже использован',exact=True)).to_be_disabled()
        headers={'Authorization':'Bearer '+tokens['advertiser']}
        for i,expected in ((1,201),(2,409)):
            r=client.request.post(base+'/api/v1/channels',headers=headers,data={'title':'Канал лицензии','telegram_url':'https://t.me/license_ui_'+str(i)})
            assert r.status==expected,r.text()
            if i==2:assert r.json()['detail']=='license_channels_limit'
        client.get_by_role('button',name='Обновить статус',exact=True).click()
        expect(client.locator('.licenses-page')).to_contain_text('Каналы · использовано')
        for width in (320,390):
            client.set_viewport_size({'width':width,'height':844});assert client.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        client.evaluate("document.getElementById('notice').hidden=true")
        client.screenshot(path=str(output/'58-license-client.png'),full_page=True)
        admin.get_by_role('button',name='Изменить тариф Пробный кабинет',exact=True).click()
        editor=admin.locator('.licenses-page > section').filter(has=admin.get_by_role('heading',name='Изменить тариф',exact=True))
        editor.get_by_label('Цена, Stars',exact=True).fill('150')
        editor.get_by_label('Каналы',exact=True).fill('10')
        editor.get_by_role('button',name='Сохранить',exact=True).click()
        expect(admin.locator('.license-plan-admin')).to_contain_text('150 Stars')
        client.get_by_role('button',name='Обновить статус',exact=True).click()
        expect(client.locator('.license-plans')).to_contain_text('150 Stars')
        with psycopg.connect(dsn) as conn:
            assert conn.execute("SELECT terms->>'max_channels' FROM license_grants WHERE user_id=%s",(ids['advertiser'],)).fetchone()[0]=='1'
        assert not failures,failures
    finally:
        for ctx in contexts:ctx.close()
        with psycopg.connect(dsn) as conn:conn.execute('UPDATE license_settings SET enabled=false')
