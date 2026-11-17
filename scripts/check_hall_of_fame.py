"""Рейтинг и админ-настройки в браузере с настоящей БД/API."""
from uuid import uuid4
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from playwright.sync_api import expect


def check_hall_of_fame(browser,base,output,failures,dsn):
    from app import main as api,hall_of_fame as hall
    admin,owner,friend=[uuid4() for _ in range(3)];tokens={}
    start,end=hall.bounds('month',datetime.now(timezone.utc));previous,_=hall.bounds('month',start-timedelta(days=1))
    with psycopg.connect(dsn,row_factory=dict_row) as conn:
        for uid,role in ((admin,'admin'),(owner,'advertiser'),(friend,'advertiser')):
            conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name,username) VALUES(%s,%s,%s,'Лидер','leader_test')",(uid,int(uid.hex[:12],16),role))
            sid=uuid4();conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid));tokens[role]=api._create_session_token(uid,sid)
        conn.execute("UPDATE hall_settings SET started_at=%s",(previous,))
        conn.execute("INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days,created_at) VALUES(%s,%s,10,90,now())",(friend,owner))
        p=conn.execute("INSERT INTO hall_periods(kind,starts_at,ends_at,settings,closed_at) VALUES('month',%s,%s,%s,now()) RETURNING id",(previous,start,Jsonb({'top_size':50,'rewards':{}}))).fetchone()['id']
        conn.execute("INSERT INTO hall_results(period_id,metric,user_id,rank,score) VALUES(%s,'invited',%s,1,8)",(p,owner))
    contexts=[]
    try:
        with patch('app.hall_of_fame.user_avatar',return_value=None):
            for role in ('advertiser','admin'):
                context=browser.new_context(viewport={'width':390,'height':844});contexts.append(context)
                context.add_init_script('sessionStorage.setItem("pushok-session",'+repr(tokens[role])+')')
                page=context.new_page();page.on('pageerror',lambda e:failures.append(str(e)));page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
                page.goto(base+'/#/hall');expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true')
                board=page.locator('[data-page=hall]');expect(board.locator('.hall-row')).not_to_have_count(0)
                expect(board.locator('.hall-row').filter(has_text='Лидер').first).to_be_visible()
                for width in (320,390):
                    page.set_viewport_size({'width':width,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                if role=='admin':
                    board.get_by_text('Управление и статистика',exact=True).click();form=board.locator('.hall-admin form')
                    form.get_by_label('Размер топа',exact=True).fill('25');form.get_by_label('Место 1',exact=True).fill('100')
                    form.get_by_role('button',name='Сохранить настройки').click();expect(form.get_by_role('status')).to_contain_text('Сохранено')
                else:expect(board.locator('.hall-admin')).to_have_count(0)
                board.get_by_label('Период рейтинга').select_option(str(p));expect(board.locator('.hall-heading')).to_contain_text('Итоги');expect(board.locator('.hall-gold')).to_have_count(1)
                page.screenshot(path=str(output/f'hall-{role}-390.png'))
            page.evaluate('window.cabinet.go('+repr('user/'+str(owner))+')')
            profile=page.locator('[data-page=user]');expect(profile.locator('.hall-medals')).to_be_visible();expect(profile.locator('.hall-gold')).to_have_count(1)
            profile.locator('.hall-medals summary').click();expect(profile.locator('.hall-medal')).to_contain_text('#1')
    finally:
        for context in contexts:context.close()
