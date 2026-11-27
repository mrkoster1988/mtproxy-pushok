"""Реальные изменения прав из профиля и каталога; ограничения оператора."""
from uuid import uuid4
import psycopg
from playwright.sync_api import expect


def check_user_access(browser,base,output,failures,dsn):
    from app import main as api
    ids={role:uuid4() for role in ('admin','operator','advertiser','target')}
    tokens={};tgs={}
    with psycopg.connect(dsn) as conn:
        for i,(role,uid) in enumerate(ids.items()):
            tgs[role]=99883600+i
            conn.execute('INSERT INTO users(id,telegram_user_id,role,first_name,username) VALUES(%s,%s,%s,%s,%s)',(uid,tgs[role],'advertiser' if role=='target' else role,'Проверка '+role,'access_'+role))
            sid=uuid4();conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid))
            tokens[role]=api._create_session_token(uid,sid)
        conn.execute("UPDATE users SET developer_granted=true WHERE id=%s",(ids['admin'],))
    contexts=[]
    def launch(role):
        ctx=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce');contexts.append(ctx)
        ctx.add_init_script("sessionStorage.setItem('pushok-session',"+repr(tokens[role])+")")
        page=ctx.new_page();page.on('pageerror',lambda e:failures.append(str(e)))
        page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
        page.goto(base);expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true');return page
    def go(page,route):
        page.evaluate('r=>window.cabinet.go(r)',route)
        expect(page.locator('[data-page='+route.split('/')[0]+']')).to_be_visible()
    def profile(page,role):
        go(page,'user/'+str(ids[role]));expect(page.locator('[data-page=user] .profile-identity')).to_contain_text('Проверка '+role)
    def state():
        with psycopg.connect(dsn) as conn:return conn.execute("SELECT CASE WHEN is_developer THEN 'developer' ELSE role END,blocked_at FROM users WHERE id=%s",(ids['target'],)).fetchone()
    def save(page,container,role):
        container.locator('.role-button').click();editor=container.locator('.role-editor')
        editor.get_by_label('Роль пользователя',exact=True).select_option(role)
        page.once('dialog',lambda d:d.accept())
        editor.get_by_role('button',name='Сохранить права',exact=True).click()
        expect(editor).to_be_hidden();assert state()[0]==role
    try:
        admin=launch('admin');profile(admin,'target');card=admin.locator('[data-page=user] .profile-identity')
        card.get_by_role('button',name='Изменить права: Пользователь',exact=True).click()
        expect(card.locator('.role-editor option')).to_have_count(5)
        expect(card.get_by_role('button',name='Сохранить права',exact=True)).to_be_disabled()
        for width in (320,390):
            admin.set_viewport_size({'width':width,'height':844})
            assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        card.screenshot(path=str(output/'57-profile-access-admin.png'))
        card.get_by_role('button',name='Отмена',exact=True).click();expect(card.locator('.role-editor')).to_be_hidden()
        card.locator('.role-button').click()
        card.get_by_role('button',name='Добавить в «Друзья»',exact=True).click()
        expect(card.locator('.role-button')).to_contain_text('Пользователь · Друзья')
        save(admin,card,'developer')
        expect(card.locator('.role-button')).to_contain_text('Разработчик · Друзья')
        save(admin,card,'moderator')
        expect(card.locator('.role-button')).to_contain_text('Модератор · Друзья')
        card.locator('.role-button').click()
        card.get_by_role('button',name='Снять отметку «Друзья»',exact=True).click()
        expect(card.locator('.role-button')).to_have_text('Модератор')
        card.locator('.role-button').click()
        admin.once('dialog',lambda d:d.accept())
        card.get_by_role('button',name='Назначить статус «Тамада»',exact=True).click()
        expect(card.locator('.role-button')).to_contain_text('Модератор · 🎩 Тамада')
        with psycopg.connect(dsn) as conn:
            assert conn.execute('SELECT role,is_tamada FROM users WHERE id=%s',(ids['target'],)).fetchone()==('moderator',True)
            sid=uuid4()
            conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,ids['target']))
            tokens['target']=api._create_session_token(ids['target'],sid)
        tamada=launch('target')
        go(tamada,'profile')
        expect(tamada.locator('[data-page=profile] .profile-role')).to_contain_text('Модератор · 🎩 Тамада')
        expect(tamada.get_by_role('link',name='Модерация',exact=True).first).to_be_visible()
        go(tamada,'events')
        panel=tamada.locator('[data-page=events]')
        panel.get_by_text('Создать мероприятие и выделить призы',exact=True).click()
        panel.get_by_label('Название мероприятия',exact=True).fill('Проверка Тамады')
        panel.get_by_label('От кого подарок',exact=True).fill('Pushok')
        panel.get_by_label('Количество призов на месяц',exact=True).fill('1')
        panel.get_by_role('button',name='Создать мероприятие',exact=True).click()
        expect(panel.get_by_role('heading',name='Проверка Тамады',exact=True)).to_be_visible()
        panel.get_by_role('button',name='Создать подарок',exact=True).click()
        expect(panel.get_by_label('Ссылка на подарок',exact=True)).to_have_count(1)
        expect(panel.get_by_role('button',name='Создать подарок',exact=True)).to_be_disabled()
        card.locator('.role-button').click()
        admin.once('dialog',lambda d:d.accept())
        card.get_by_role('button',name='Снять статус «Тамада»',exact=True).click()
        expect(card.locator('.role-button')).to_have_text('Модератор')
        go(tamada,'events')
        expect(panel.get_by_text('Создать мероприятие и выделить призы',exact=True)).to_have_count(0)
        expect(panel.get_by_role('link',name='Проверка Тамады',exact=True)).to_be_visible()
        for role in ('operator','admin','advertiser'):save(admin,card,role)
        old=admin.request.get(base+'/api/v1/me',headers={'Authorization':'Bearer '+tokens['target']});assert old.status==401
        profile(admin,'admin');expect(admin.locator('[data-page=user] .profile-identity .role-button')).to_have_count(0)

        operator=launch('operator');go(operator,'profile');operator.get_by_role('link',name='Пользователи',exact=True).click()
        operator.get_by_label('Поиск пользователей',exact=True).fill(str(tgs['target']));operator.locator('.directory-search button[type=submit]').click()
        row=operator.locator('.user-directory .directory-user');expect(row).to_have_count(1);expect(row).not_to_contain_text('@access_target')
        row.locator('.role-button').click();expect(row.locator('.role-editor option')).to_have_count(2)
        expect(row.locator('.role-editor option[value=admin]')).to_have_count(0)
        row.get_by_label('Роль пользователя',exact=True).press('Escape');expect(row.locator('.role-editor')).to_be_hidden()
        save(operator,row,'operator');save(operator,row,'advertiser')
        row.get_by_role('link',name='Открыть профиль пользователя',exact=True).click()
        card=operator.locator('[data-page=user] .profile-identity');expect(card).not_to_contain_text('@access_target');expect(operator.locator('.profile-wallet')).to_have_count(0)
        card.locator('.role-button').click();operator.once('dialog',lambda d:d.dismiss())
        card.get_by_role('button',name='Заблокировать',exact=True).click();assert state()[1] is None
        expect(card.locator('.role-editor')).to_be_visible()
        operator.once('dialog',lambda d:d.accept());card.get_by_role('button',name='Заблокировать',exact=True).click()
        expect(card.locator('.role-button')).to_contain_text('Заблокирован');assert state()[1] is not None
        card.locator('.role-button').click()
        for width in (320,390):
            operator.set_viewport_size({'width':width,'height':844})
            assert operator.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        card.screenshot(path=str(output/'58-profile-access-operator.png'))
        operator.once('dialog',lambda d:d.accept());card.get_by_role('button',name='Разблокировать',exact=True).click()
        expect(card.locator('.role-editor')).to_be_hidden();expect(card.locator('.role-button')).not_to_contain_text('Заблокирован');assert state()[1] is None
        for role in ('admin','operator'):
            profile(operator,role);expect(operator.locator('[data-page=user] .profile-identity .role-button')).to_have_count(0)
        response=operator.request.put(base+'/api/v1/admin/team',headers={'Authorization':'Bearer '+tokens['operator']},data={'telegram_user_id':tgs['target'],'role':'admin'})
        assert response.status==403
        response=operator.request.put(base+'/api/v1/admin/team/access',headers={'Authorization':'Bearer '+tokens['operator']},data={'telegram_user_id':tgs['admin'],'blocked':True})
        assert response.status==403
        advertiser=launch('advertiser');go(advertiser,'profile');expect(advertiser.get_by_role('link',name='Пользователи',exact=True)).to_have_count(0)
        response=advertiser.request.get(base+'/api/v1/admin/users',headers={'Authorization':'Bearer '+tokens['advertiser']});assert response.status==403
        assert not failures,failures
    finally:
        for ctx in contexts:ctx.close()
