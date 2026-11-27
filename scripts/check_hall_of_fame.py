"""Рейтинг и админ-настройки в браузере с настоящей БД/API."""
from uuid import uuid4
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from playwright.sync_api import expect


def check_hall_of_fame(browser,base,output,failures,dsn):
    from app import main as api,hall_of_fame as hall,community
    admin,owner,friend=[uuid4() for _ in range(3)];tokens={}
    start,end=hall.bounds('month',datetime.now(timezone.utc));previous,_=hall.bounds('month',start-timedelta(days=1))
    with psycopg.connect(dsn,row_factory=dict_row) as conn:
        for uid,role in ((admin,'admin'),(owner,'advertiser'),(friend,'advertiser')):
            conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name,username) VALUES(%s,%s,%s,'Лидер','leader_test')",(uid,int(uid.hex[:12],16),role))
            sid=uuid4();conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid));tokens[role]=api._create_session_token(uid,sid)
        conn.execute("UPDATE hall_settings SET started_at=%s",(previous,))
        conn.execute("INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days,created_at) VALUES(%s,%s,10,90,now())",(friend,owner))
        for inviter,count in ((owner,3),(friend,2),(admin,1)):
            for _ in range(count):
                referred=uuid4()
                conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name) VALUES(%s,%s,'advertiser','Друг')",(referred,int(referred.hex[:12],16)))
                conn.execute("INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days) VALUES(%s,%s,10,90)",(referred,inviter))
        # More than ten eligible inviters: the dashboard must truncate independently.
        for _ in range(9):
            inviter,referred=uuid4(),uuid4()
            for uid in (inviter,referred):
                conn.execute("INSERT INTO users(id,telegram_user_id,role,first_name) VALUES(%s,%s,'advertiser','Друг')",(uid,int(uid.hex[:12],16)))
            conn.execute("INSERT INTO growth_referrals(referred_user_id,inviter_user_id,reward_percent,reward_days) VALUES(%s,%s,10,90)",(referred,inviter))
        p=conn.execute("INSERT INTO hall_periods(kind,starts_at,ends_at,settings,closed_at) VALUES('month',%s,%s,%s,now()) RETURNING id",(previous,start,Jsonb({'top_size':50,'rewards':{}}))).fetchone()['id']
        conn.execute("INSERT INTO hall_results(period_id,metric,user_id,rank,score) VALUES(%s,'invited',%s,1,8)",(p,owner))
    # Fixture записывает данные напрямую: начинаем отдельный сценарий без
    # 20-секундного снимка рейтинга предыдущего браузерного сценария.
    with community._frames_lock:community._frames.clear()
    contexts=[]
    try:
        with patch('app.community.user_avatar',return_value=None):
            for role in ('advertiser','admin'):
                context=browser.new_context(viewport={'width':390,'height':844});contexts.append(context)
                context.add_init_script('sessionStorage.setItem("pushok-session",'+repr(tokens[role])+')')
                page=context.new_page();page.on('pageerror',lambda e:failures.append(str(e)));page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
                page.goto(base+'/#/hall');expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true')
                board=page.locator('[data-page=hall]');expect(board.locator('.hall-podium-person')).not_to_have_count(0)
                expect(board.locator('.hall-podium-person').filter(has_text='Лидер').first).to_be_visible()
                for width in (320,390):
                    page.set_viewport_size({'width':width,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=str(output/f'hall-podium-{role}-390.png'))
                expect(page.locator('.mobile-brand')).to_contain_text('Доска почёта')
                expect(page.locator('.mobile-bottom-nav [data-route=hall]')).to_have_attribute('aria-current','page')
                expect(board.locator('.hall-category')).to_have_count(1)
                expect(board.locator('.hall-podium-person .hall-rank')).to_have_count(0)
                expect(board.locator('.hall-podium-person .hall-person a')).to_have_count(0)
                board.locator('.hall-rest summary').click()
                expect(board.locator('.hall-row').first).to_be_visible()
                expect(board.locator('.avatar-verification,.avatar-vpn,.user-online,.birthday-avatar')).to_have_count(0)
                for width in (320,390,768):
                    page.set_viewport_size({'width':width,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    podium_size=48 if width<=360 else 56
                    assert board.evaluate("""(e,size)=>{
                        const podium=[...e.querySelectorAll('.hall-podium-person')],rows=[...e.querySelectorAll('.hall-row')];
                        const fits=(item,diameter)=>{
                            const a=item.querySelector('.user-avatar'),r=a.getBoundingClientRect(),b=item.getBoundingClientRect();
                            return a.dataset.avatarIndicators==='off'&&Math.abs(r.width-diameter)<1&&Math.abs(r.height-diameter)<1&&r.left>=b.left&&r.right<=b.right&&r.top>=b.top&&r.bottom<=b.bottom;
                        };
                        return podium.length===3&&rows.length>0&&podium.every(item=>fits(item,size))&&rows.every(item=>fits(item,48));
                    }""",podium_size)
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(output/f'hall-large-avatars-{role}.png'))
                board.locator('.hall-rest summary').click()
                page.evaluate("window.cabinet.go('dashboard')")
                home=page.locator('.dashboard-intro > .home-leaders')
                avatars=home.locator('.home-leader-avatars > .user-avatar')
                expect(avatars).to_have_count(10)
                expect(home.locator('.avatar-verification,.avatar-vpn,.user-online,.birthday-avatar')).to_have_count(0)
                assert avatars.evaluate_all("items=>items.every((a,i)=>a.dataset.hallRank===String(i+1)&&a.dataset.avatarIndicators==='off')")
                for rank,frame in ((1,'hall-gold'),(2,'hall-silver'),(3,'hall-bronze')):
                    expect(avatars.nth(rank-1)).to_have_class(__import__('re').compile(r'\b'+frame+r'\b'))
                    expect(avatars.nth(rank-1)).to_have_attribute('data-hall-rank',str(rank))
                for width,height in ((320,640),(390,844),(768,844)):
                    page.set_viewport_size({'width':width,'height':height})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    assert home.evaluate("""e=>{
                        const panel=e.getBoundingClientRect(),strip=e.querySelector('.home-leader-avatars'),box=strip.getBoundingClientRect();
                        const items=[...strip.querySelectorAll(':scope > .user-avatar')].map(a=>a.getBoundingClientRect());
                        return items.length===10&&strip.scrollWidth>strip.clientWidth&&box.left>=panel.left&&box.right<=panel.right&&items.every((r,i)=>Math.abs(r.width-48)<1&&Math.abs(r.height-48)<1&&r.top>=panel.top&&r.bottom<=panel.bottom&&Math.abs(r.top-items[0].top)<1&&(!i||r.left-items[i-1].right>=11));
                    }""")
                page.set_viewport_size({'width':390,'height':844})
                strip=home.locator('.home-leader-avatars')
                strip.evaluate("e=>{e.scrollLeft=e.scrollWidth}")
                page.wait_for_function("""()=>{const e=document.querySelector('.home-leader-avatars'),last=e.lastElementChild.getBoundingClientRect(),box=e.getBoundingClientRect();return e.scrollLeft>0&&last.right<=box.right+1&&last.left>=box.left-1}""")
                expect(avatars.last).to_have_attribute('data-hall-rank','10')
                strip.evaluate("e=>{e.scrollLeft=0}")
                home.screenshot(path=str(output/f'home-leaders-large-{role}.png'))
                page.evaluate("window.cabinet.go('hall')")
                expect(board).to_be_visible()
                expect(board.locator('.hall-podium-person')).not_to_have_count(0)
                if role=='advertiser':
                    avatar=board.locator('.hall-place-1 .user-avatar').first
                    uid=avatar.get_attribute('data-user-id');avatar.click()
                    expect(page).to_have_url(base+'/#/member/'+uid)
                    expect(page.locator('.public-person')).to_be_visible()
                    expect(page.locator('.public-person')).not_to_contain_text('Telegram ID')
                    page.locator('.screen-back').click()
                    expect(page).to_have_url(base+'/#/hall')
                if role=='admin':
                    board.get_by_text('Управление и статистика',exact=True).click();form=board.locator('.hall-admin form')
                    form.get_by_label('Размер топа',exact=True).fill('25');form.get_by_label('Место 1',exact=True).fill('100')
                    form.get_by_role('button',name='Сохранить настройки').click();expect(form.get_by_role('status')).to_contain_text('Сохранено')
                else:expect(board.locator('.hall-admin')).to_have_count(0)
                board.get_by_label('Период рейтинга').select_option(str(p));expect(board.locator('.hall-heading')).to_contain_text('Итоги');expect(board.locator('.hall-gold')).to_have_count(1)
                archived=board.locator('.hall-podium-person .user-avatar')
                expect(archived).to_have_count(1)
                expect(archived).to_have_attribute('data-avatar-indicators','off')
                expect(archived).to_have_css('width','56px')
                expect(board.locator('.avatar-verification,.avatar-vpn,.user-online,.birthday-avatar')).to_have_count(0)
                page.screenshot(path=str(output/f'hall-{role}-390.png'))
            # Старые валютные результаты остаются читаемыми без живого пересчёта.
            with psycopg.connect(dsn) as conn:
                conn.execute("INSERT INTO hall_results(period_id,metric,user_id,rank,score) VALUES(%s,'spend_rub',%s,1,12345),(%s,'spend_xtr',%s,1,25)",(p,friend,p,friend))
            board.get_by_label('Период рейтинга').select_option('')
            expect(board.locator('.hall-heading')).to_contain_text('Места обновляются')
            board.get_by_label('Период рейтинга').select_option(str(p))
            legacy=board.locator('.hall-category').filter(has=page.get_by_role('heading',name='Расходы на рекламу',exact=True))
            expect(legacy).to_have_count(0)
            page.evaluate('window.cabinet.go('+repr('user/'+str(owner))+')')
            profile=page.locator('[data-page=user]');expect(profile.locator('.hall-medals')).to_be_visible();expect(profile.locator('.hall-gold')).to_have_count(1)
            profile.locator('.hall-medals summary').click();expect(profile.locator('.hall-medal')).to_contain_text('#1')
    finally:
        for context in contexts:context.close()
        with community._frames_lock:community._frames.clear()
