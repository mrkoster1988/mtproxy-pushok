"""Профили в Chromium: реальная БД/API, синтетическое фото вместо Telegram."""
import io
from unittest.mock import patch
from uuid import uuid4

import psycopg
from PIL import Image
from playwright.sync_api import expect


def check_user_profiles(browser, base, output, failures, dsn):
    from app import main as api
    owner,staff,channel,tariff=[uuid4() for _ in range(4)]
    campaigns=[uuid4() for _ in range(12)]
    tokens={}
    with psycopg.connect(dsn) as conn:
        for uid,role,first,last,username in ((owner,'advertiser','Мария','Зиброва','ZibrovaM'),(staff,'admin','Администратор',None,'profile_admin'),
            (uuid4(),'moderator','Модератор',None,'profile_moderator'),(uuid4(),'operator','Оператор',None,'profile_operator')):
            conn.execute('INSERT INTO users(id,telegram_user_id,role,first_name,last_name,username) VALUES(%s,%s,%s,%s,%s,%s)',(uid,int(uid.hex[:12],16),role,first,last,username))
            sid=uuid4();conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid))
            tokens[role]=api._create_session_token(uid,sid)
        conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Планета Excel','https://t.me/planeta_excell')",(channel,owner))
        conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,'Тестирование',3600,100,1)",(tariff,))
        for i,cid in enumerate(campaigns):
            conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
                duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status,placement_status)
                VALUES(%s,%s,%s,%s,'Тестирование',3600,100,'RUB','approved',%s,%s)""",(cid,owner,channel,tariff,'verified' if i==11 else 'awaiting_payment','completed' if i==11 else 'not_scheduled'))
    photo=io.BytesIO();Image.new('RGB',(160,160),(35,119,132)).save(photo,format='JPEG')
    contexts=[]
    def launch(role):
        context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce');contexts.append(context)
        context.add_init_script('sessionStorage.setItem("pushok-session",'+repr(tokens[role])+')')
        page=context.new_page();page.on('pageerror',lambda error:failures.append(str(error)))
        page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
        page.goto(base);expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true');return page
    def go(page,route):
        page.evaluate('route=>window.cabinet.go(route)',route)
    try:
        with patch('app.community.user_avatar',return_value=photo.getvalue()) as avatar:
            admin=launch('admin')
            vpn_row={'id':str(owner),'name':'Мария Зиброва','state':'active','online':True,'phone_verified':True}
            admin.route('**/api/v1/vpn/dashboard',lambda route:route.fulfill(json={'can_manage':True,'personal':None,'members':[vpn_row]}))
            admin.evaluate('window.pushokVpn.reset()')
            go(admin,'moderation/'+str(campaigns[0]))
            card=admin.locator('.approval-advertiser');expect(card.locator('.user-avatar img')).to_have_count(1)
            def check_avatar(container,verified=True):
                face=container.locator('.user-avatar').first
                expect(face.locator(':scope > .avatar-verification')).to_have_count(int(verified))
                expect(face.locator(':scope > .avatar-vpn[data-signal=online]')).to_have_count(1)
                expect(container.locator('.person-name .phone-verified-badge,.person-name .vpn-signal')).to_have_count(0)
                if verified:
                    assert face.evaluate("""e=>{const a=e.getBoundingClientRect(),v=e.querySelector('.avatar-verification').getBoundingClientRect(),p=e.querySelector('.avatar-vpn').getBoundingClientRect();
                        return v.left>=a.left+a.width/2-2&&v.bottom>=a.bottom-2&&p.left<=a.left+2&&p.bottom>=a.bottom-2&&p.right<=v.left+1;}""")
            check_avatar(card)
            assert card.inner_text().count('@ZibrovaM')==1
            expect(card).not_to_contain_text('Telegram ID')
            assert card.bounding_box()['height']<115
            card.screenshot(path=str(output/'49-advertiser-identity.png'))
            card.get_by_role('link',name='Открыть профиль рекламодателя').click()
            profile=admin.locator('[data-page=user]');expect(profile.locator('.person-name')).to_have_text('Мария Зиброва')
            expect(profile.locator('.user-avatar img')).to_have_count(1)
            check_avatar(profile.locator('.profile-identity'))
            expect(profile.get_by_role('link',name='@ZibrovaM')).to_have_attribute('href','https://t.me/ZibrovaM')
            expect(profile.locator('.profile-invited-block summary')).to_have_text('Приглашено0')
            assert profile.locator('.profile-identity').inner_text().count('@ZibrovaM')==1
            expect(profile.locator('.profile-campaign-row')).to_have_count(10)
            profile.locator('.profile-campaigns').get_by_role('button',name='Следующая страница').click()
            expect(profile.locator('.profile-campaign-row')).to_have_count(1)
            profile.get_by_role('button',name='Архив · 1',exact=True).click()
            expect(profile.locator('.profile-campaign-row')).to_contain_text('Завершена')
            profile.get_by_role('button',name='Отчёт кампании '+str(campaigns[11])[:8]).click()
            expect(profile.locator('.campaign-report')).to_contain_text('Тестирование')
            profile.get_by_role('button',name='Текущие · 11',exact=True).click()
            for width in (390,320):
                admin.set_viewport_size({'width':width,'height':844})
                assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                check_avatar(profile.locator('.profile-identity'))
                admin.screenshot(path=str(output/f'50-advertiser-profile-{width}.png'))
            admin.locator('.screen-back').click()
            expect(admin.locator('.approval-card')).to_have_attribute('data-campaign-id',str(campaigns[0]))
            client=launch('advertiser');go(client,'profile')
            go(client,'member/'+str(staff))
            expect(client.locator('.public-person span.person-handle')).to_have_count(0)
            expect(client.locator('.public-person a.person-handle')).to_have_count(0)
            moderator=launch('moderator');go(moderator,'member/'+str(owner))
            expect(moderator.locator('.public-person a.person-handle')).to_have_attribute('href','https://t.me/ZibrovaM')
            expect(moderator.locator('.public-person .user-avatar>.avatar-verification')).to_have_count(1)
            expect(moderator.locator('.public-person .user-avatar>.avatar-vpn')).to_have_count(0)
            operator=launch('operator');go(operator,'user/'+str(staff))
            expect(operator.locator('[data-page=user] span.person-handle')).to_have_count(0)
            expect(operator.locator('[data-page=user] a.person-handle')).to_have_count(0)
            go(client,'profile')
            own=client.locator('[data-page=profile]')
            expect(own.locator('.profile-stats')).to_contain_text('11')
            expect(own.locator('.profile-personal')).to_have_count(0)
            client.screenshot(path=str(output/'51-own-profile.png'))
            for label,route in (('Каналы: 1','channels'),('Кампании: 11','campaigns/mine'),('Архив: 1','archive')):
                own.get_by_role('button',name=label,exact=True).click()
                expect(client).to_have_url(base+'/#/'+route)
                go(client,'profile')
            forbidden=client.request.get(base+f'/api/v1/users/{staff}/profile',headers={'Authorization':'Bearer '+tokens['advertiser']})
            assert forbidden.status==404
            # Нет фотографии и имя совпадает с ником — без дубля и сломанного <img>.
            avatar.return_value=None
            with psycopg.connect(dsn) as conn:
                conn.execute("UPDATE users SET first_name='@ZibrovaM',last_name=NULL WHERE id=%s",(owner,))
            admin.evaluate('window.pushokViews.clearLogos()');admin.evaluate('refresh()')
            expect(card.locator('.person-handle')).to_have_text('@ZibrovaM')
            assert card.inner_text().count('@ZibrovaM')==1
            expect(card.locator('.user-avatar img')).to_have_count(0)
            expect(card.locator('.user-avatar')).not_to_have_text('')
            check_avatar(card)
            # Запоздалый профиль после выхода не возвращает персональные данные в DOM.
            held=[]
            def hold_profile(route):
                held.append(route)
                admin.evaluate('window.__profileRequestHeld=true')
            admin.route('**/api/v1/users/*/profile?*',hold_profile)
            card.get_by_role('link',name='Открыть профиль рекламодателя').click()
            admin.wait_for_function("document.querySelector('[data-page=user]').textContent.includes('Загрузка профиля')")
            admin.wait_for_function('window.__profileRequestHeld===true')
            assert held, 'Profile request must be held before logout'
            admin.locator('#logout').evaluate('(button)=>button.click()')
            for route in held:route.fulfill(json={'user':{'first_name':'LATE_PRIVATE'},'counts':{}})
            expect(admin.locator('[data-page=user] .page-content')).to_be_empty()
            expect(admin.locator('#workspace')).to_be_hidden()
    finally:
        for context in contexts:context.close()
