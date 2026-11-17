"""Регрессии приглашённых, компактных форм и категорий настроек."""
from uuid import uuid4
import psycopg
from playwright.sync_api import expect
from ui_test_helpers import open_settings, open_management

def check_interface(browser,base,output,failures,dsn):
    from app import main as api
    uid,sid=uuid4(),uuid4()
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO users(id,telegram_user_id,role,first_name,username) VALUES(%s,99199191,'admin','Интерфейс','interface_admin')",(uid,))
        c.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid))
        for n in range(22):
            friend=uuid4();c.execute("INSERT INTO users(id,telegram_user_id,role,first_name,username) VALUES(%s,%s,'advertiser',%s,%s)",(friend,99199200+n,'Приглашённый '+str(n),'invite_ui_'+str(n)))
            c.execute('INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days) VALUES(%s,%s,10,90)',(friend,uid))
    ctx=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce');ctx.add_init_script('sessionStorage.setItem("pushok-session",'+repr(api._create_session_token(uid,sid))+')')
    page=ctx.new_page();page.on('pageerror',lambda e:failures.append(e.stack))
    page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
    attempted=[]
    def interrupted(r):
        attempted.append(r.request.url)
        if len(attempted)==1:r.fulfill(status=503,content_type='application/json',body='{"detail":"database_unavailable"}')
        else:r.continue_()
    page.route('**/community/users/*/invited?page=1',interrupted)
    try:
        page.goto(base+'/#/profile');expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true')
        box=page.locator('.profile-invited-block');box.locator(':scope>summary').click();expect(box.locator('.invitee')).to_have_count(20)
        box.get_by_role('button',name='Ещё приглашённые').click();expect(box.get_by_role('button',name='Повторить загрузку приглашённых')).to_be_visible();expect(box.locator('.invitee')).to_have_count(20)
        box.get_by_role('button',name='Повторить загрузку приглашённых').click();expect(box.locator('.invitee')).to_have_count(22);assert len(attempted)==2
        assert len(set(box.locator('[data-user-id]').evaluate_all('(ns)=>ns.map(n=>n.dataset.userId)')))==22
        box.locator('.user-avatar').first.click();expect(page.locator('[data-page=user]')).to_be_visible();page.locator('.screen-back').click();expect(page).to_have_url(base+'/#/profile')
        expect(page.locator('[data-page=profile] .profile-identity .person-handle')).to_have_css('color','rgb(255, 140, 152)')
        expect(page.locator('[data-page=profile] .profile-identity .person-role')).to_have_css('border-top-width','0px')
        expect(page.locator('.recent-releases .release-item')).to_have_count(5)
        # Переход по имени задаёт другой источник, чем предыдущий переход по аватарке.
        page.evaluate("window.cabinet.go('users')")
        page.get_by_role('searchbox',name='Поиск пользователей').fill('interface_admin')
        page.get_by_role('button',name='Найти',exact=True).click()
        page.locator('.user-directory .person-handle').filter(has_text='@interface_admin').click()
        expect(page).to_have_url(base+'/#/user/'+str(uid))
        nested=page.locator('[data-page=user] .profile-invited-block')
        nested.locator(':scope>summary').click()
        expect(nested.locator('.invitee')).to_have_count(20)
        friend_id=nested.locator('.user-avatar').first.get_attribute('data-user-id')
        nested.locator('.user-avatar').first.click()
        expect(page).to_have_url(base+'/#/user/'+friend_id)
        page.locator('.screen-back').click()
        expect(page).to_have_url(base+'/#/users')
        for route in ('channels','campaigns'):
            page.evaluate('r=>window.cabinet.go(r)',route);row=page.locator('[data-page='+route+'] .search-action-row');expect(row).to_be_visible()
            a=row.locator('input[type=search]').bounding_box();b=row.locator(':scope>.icon-action').bounding_box();assert abs(a['y']+a['height']/2-b['y']-b['height']/2)<3
        page.evaluate("window.cabinet.go('dashboard')");expect(page.locator('.home-leaders h2')).to_have_text('Лидеры месяца')
        page.evaluate("window.cabinet.go('profile')")
        menu=page.get_by_role('navigation',name='Управление',exact=True)
        expect(menu.get_by_role('link',name='Настройки',exact=True)).to_have_count(0)
        menu.get_by_role('link',name='Пользователи и награды',exact=True).click()
        expect(page.locator('[data-page=management] h1')).to_have_text('Пользователи и награды')
        expect(page.locator('.settings-category:visible')).to_have_count(1)
        assert page.locator('.settings-section[open]:visible').count()==0
        page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(output/'interface-management-collapsed-390.png'))
        open_settings(page);ref=page.locator('#referral-settings');ref.get_by_label('Награда за приглашённого',exact=True).select_option('coupon');expect(ref.get_by_label('Скидка за приглашённого, %')).to_be_visible()
        for width in (320,390):
            page.set_viewport_size({'width':width,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');page.screenshot(path=str(output/f'interface-management-{width}.png'))
        page.evaluate("window.cabinet.go('profile')");expect(page.locator('[data-page=profile] .profile-stats')).to_be_visible();page.locator('[data-page=profile] .profile-personal>summary').click();page.screenshot(path=str(output/'interface-profile-390.png'))
    finally:
        ctx.close()
        with psycopg.connect(dsn) as c:c.execute('DELETE FROM growth_referrals WHERE inviter_user_id=%s',(uid,))
