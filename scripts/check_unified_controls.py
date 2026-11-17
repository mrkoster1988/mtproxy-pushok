"""Реальные формы, выбор мышью/клавиатурой, плотность платежей и профиль ИИ."""
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from playwright.sync_api import expect


def check_unified_controls(browser,base,output,failures,dsn):
    from app import main, wallet_ledger
    admin,member,cid,channel,tariff,sid=[uuid4() for _ in range(6)]
    with psycopg.connect(dsn,row_factory=dict_row) as c:
        c.execute("INSERT INTO users(id,telegram_user_id,role,first_name,username) VALUES(%s,%s,'admin','Администратор','controls_admin'),(%s,%s,'advertiser','Тестовый пользователь','controls_user')",(admin,int(admin.hex[:12],16),member,int(member.hex[:12],16)))
        c.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,admin))
        c.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Канал проверки','https://t.me/controls_channel')",(channel,member))
        c.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks) VALUES(%s,'День',86400,30000)",(tariff,))
        c.execute("INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,moderator_comment) VALUES(%s,%s,%s,%s,'День',86400,30000,'RUB','approved','Автоматически одобрено ИИ: Образовательный канал')",(cid,member,channel,tariff))
        c.execute("INSERT INTO ai_moderation_checks(campaign_id,snapshot,revision,decision,reason) SELECT id,updated_at,'test','approved','Образовательный канал' FROM campaigns WHERE id=%s",(cid,))
        c.execute("INSERT INTO ai_moderation_events(campaign_id,decision,reason,revision,topic) VALUES(%s,'approved','Образовательный канал','test','education')",(cid,))
        for i in range(5):wallet_ledger.post(c,admin,'BONUS',10000,'manual_credit','controls:'+str(uuid4()),'Проверка компактной истории',actor=admin)
        c.execute("INSERT INTO wallet_topups(user_id,currency,amount,provider,status,request_id,provider_account,test_mode) VALUES(%s,'XTR',100,'telegram_stars','failed',%s,'controls',false)",(member,str(uuid4())))
    context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    context.add_init_script("sessionStorage.setItem('pushok-session',"+repr(main._create_session_token(admin,sid))+")")
    page=context.new_page();page.on('pageerror',lambda e:failures.append(str(e)))
    page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
    def go(route):page.evaluate('r=>cabinet.go(r)',route)
    try:
        page.goto(base);expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true')
        go('users');select=page.get_by_label('Роль пользователя',exact=True).first
        select.click();dialog=page.get_by_role('dialog');expect(dialog).to_be_visible();dialog.get_by_role('option',name='Оператор',exact=True).click();expect(select).to_have_value('operator');expect(dialog).to_have_count(0)
        select.focus();select.press('ArrowDown');expect(page.get_by_role('dialog')).to_be_visible();page.keyboard.press('Home');page.keyboard.press('Enter');expect(select).to_have_value('');expect(page.get_by_role('dialog')).to_have_count(0)
        select.click();page.screenshot(path=str(output/'controls-select-390.png'));page.keyboard.press('Escape');expect(select).to_be_focused()
        go('wallet');area=page.locator('[data-wallet-section=admin]');area.locator(':scope>summary').click()
        field=area.get_by_label('Пользователь: @никнейм или ID',exact=True);search=area.get_by_role('button',name='Найти пользователя',exact=True)
        for width in (320,390,768):
            page.set_viewport_size({'width':width,'height':844});a,b=field.bounding_box(),search.bounding_box();assert b['x']>=a['x']+a['width']+7;assert abs(a['y']+a['height']/2-b['y']-b['height']/2)<2
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        help_button=area.get_by_role('button',name='Пояснения: Управление балансами',exact=True);expect(area.locator('.block-help')).to_be_hidden();help_button.click();expect(area.locator('.block-help')).to_be_visible();expect(area).to_have_attribute('open','');help_button.click()
        field.fill('@controls_user');search.click();expect(area.get_by_role('button',name='Открыть баланс',exact=True)).to_be_visible()
        review=page.locator('[data-wallet-section=review]');review.locator(':scope>summary').click();expect(review.locator('.wallet-topup-review').filter(has_text='@controls_user')).to_be_visible();expect(review.locator('.wallet-topup-review').filter(has_text='@controls_user').locator('.wallet-payment-details')).to_be_visible();expect(review.locator('.wallet-review details')).to_have_count(0)
        history=page.locator('[data-wallet-section=history]');history.locator(':scope>summary').click();expect(history.locator('.wallet-entry')).to_have_count(5)
        page.set_viewport_size({'width':390,'height':844});assert history.bounding_box()['height']<640
        history.screenshot(path=str(output/'controls-history-390.png'));review.screenshot(path=str(output/'controls-payments-390.png'));area.screenshot(path=str(output/'controls-balance-search-390.png'))
        go('moderation/'+str(cid));expect(page.locator('.ai-decision')).to_have_text('Одобрено: Агент ИИ');expect(page.locator('.assistant-saved')).to_contain_text('Образовательный канал');expect(page.locator('.campaign-assistant')).to_have_attribute('open','')
        expect(page.locator('.campaign-assistant a[href*="management"]')).to_have_count(0)
        page.locator('.ai-decision a').click();expect(page.locator('.ai-agent-stats')).to_contain_text('Одобрено');expect(page.locator('.ai-topic-table')).to_contain_text('Образование');page.screenshot(path=str(output/'controls-ai-profile-390.png'),full_page=True)
        assert not failures,failures
    finally:context.close()
