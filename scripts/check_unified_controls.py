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
        # Открытый список следует за живым полем, а блокировка родителя закрывает его.
        select.click();select.evaluate("s=>s.add(new Option('Новый вариант','new'))")
        expect(page.get_by_role('dialog').get_by_role('option',name='Новый вариант',exact=True)).to_be_visible()
        select.evaluate("s=>s.lastElementChild.remove()")
        expect(page.get_by_role('dialog').get_by_role('option',name='Новый вариант',exact=True)).to_have_count(0)
        select.evaluate("s=>s.disabled=true");expect(page.get_by_role('dialog')).to_have_count(0)
        select.evaluate("s=>s.disabled=false");select.focus();page.keyboard.type('test-no-results')
        expect(page.get_by_role('dialog').get_by_role('status')).to_have_text('Ничего не найдено')
        page.get_by_role('searchbox',name='Поиск по вариантам').fill('Оператор');page.keyboard.press('ArrowDown');page.keyboard.press('Enter');expect(select).to_have_value('operator')
        go('wallet');area=page.locator('[data-wallet-section=admin]')
        # Справка должна читаться с первого нажатия, даже если раздел закрыт.
        help_button=area.get_by_role('button',name='Пояснения: Управление балансами',exact=True)
        help_button.click();expect(area.locator('.block-help')).to_be_visible();expect(area).to_have_attribute('open','');help_button.click()
        field=area.get_by_label('Пользователь: @никнейм или ID',exact=True);search=area.get_by_role('button',name='Найти пользователя',exact=True)
        for width in (320,390,768):
            page.set_viewport_size({'width':width,'height':844});a,b=field.bounding_box(),search.bounding_box();assert b['x']>=a['x']+a['width']+7;assert abs(a['y']+a['height']/2-b['y']-b['height']/2)<2
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        help_button=area.get_by_role('button',name='Пояснения: Управление балансами',exact=True);expect(area.locator('.block-help')).to_be_hidden();help_button.click();expect(area.locator('.block-help')).to_be_visible();expect(area).to_have_attribute('open','');help_button.click()
        field.fill('@controls_user');search.click();expect(area.get_by_role('button',name='Открыть баланс',exact=True)).to_be_visible()
        review=page.locator('[data-wallet-section=review]');review.locator(':scope>summary').click();expect(review.locator('.wallet-topup-review').filter(has_text='@controls_user')).to_be_visible();expect(review.locator('.wallet-topup-review').filter(has_text='@controls_user').locator('.wallet-payment-details')).to_be_visible();expect(review.locator('.wallet-review details')).to_have_count(0)
        history=page.locator('[data-wallet-section=history]');history.locator(':scope>summary').click();expect(history.locator('.wallet-entry')).to_have_count(5)
        page.set_viewport_size({'width':390,'height':844});assert history.bounding_box()['height']<470
        history.screenshot(path=str(output/'controls-history-390.png'));review.screenshot(path=str(output/'controls-payments-390.png'));area.screenshot(path=str(output/'controls-balance-search-390.png'))
        go('moderation/'+str(cid))
        assistant_panel = page.locator('#queue .campaign-assistant')
        saved_review = assistant_panel.locator('.assistant-saved')
        expect(page.locator('#queue .ai-decision')).to_have_text('Одобрено: Агент ИИ')
        expect(saved_review).to_contain_text('Образовательный канал')
        expect(assistant_panel).to_have_attribute('open','')
        expect(assistant_panel.locator('a[href*="management"]')).to_have_count(0)
        # Автозагрузка истории сообщает об ошибке на месте и повторяется без закрытия блока.
        report_url='**/api/v1/campaigns/'+str(cid)+'/report'
        page.route(report_url,lambda r:r.fulfill(status=503,json={'detail':'test_unavailable'}))
        page.locator('summary').filter(has_text='История и документы').click()
        report=page.locator('.campaign-report')
        expect(report.get_by_role('status')).to_contain_text('Не удалось загрузить историю и документы.')
        expect(report).to_have_attribute('aria-busy','false')
        page.unroute(report_url)
        report.get_by_role('button',name='Повторить загрузку отчёта',exact=True).click()
        expect(report).to_contain_text('История действий')
        page.route(report_url,lambda r:r.fulfill(status=503,json={'detail':'test_unavailable'}))
        page.get_by_role('button',name='Обновить историю и документы',exact=True).click()
        expect(report.get_by_role('status')).to_contain_text('Показаны ранее загруженные данные.')
        expect(report).to_contain_text('История действий')
        page.unroute(report_url)
        for width in (320,390,768):
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        report.screenshot(path=str(output/'controls-report-retry-768.png'))
        report.get_by_role('button',name='Повторить загрузку отчёта',exact=True).click()
        expect(report.locator('.report-status')).to_have_count(0)
        page.set_viewport_size({'width':390,'height':844})
        page.locator('.ai-decision a').click();expect(page.locator('.ai-agent-stats')).to_contain_text('Одобрено');expect(page.locator('.ai-topic-table')).to_contain_text('Образование');page.screenshot(path=str(output/'controls-ai-profile-390.png'),full_page=True)
        # Нажатие на собственную карточку агента не меняет путь возврата к заявке.
        page.locator('.ai-agent-profile .ai-agent-card .person-name').click()
        assert page.evaluate('pushokProfiles.backRoute()')=='moderation/'+str(cid)
        # Сбой старого запроса не подменяет уже открытый профиль рекламодателя.
        pending=[]
        page.route('**/api/v1/users/ai-agent/profile',lambda r:pending.append(r))
        page.evaluate("""uid=>{const target=document.querySelector('.ai-agent-profile');pushokProfiles.show(target,'ai-agent');cabinet.go('user/'+uid);} """,str(member))
        expect(page.locator('[data-page=user] .profile-identity')).to_contain_text('Тестовый пользователь')
        assert pending
        with page.expect_response(lambda r:r.url.endswith('/users/ai-agent/profile')):
            pending[0].fulfill(status=503,json={'detail':'test_unavailable'})
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(resolve))')
        expect(page.locator('[data-page=user] .profile-identity')).to_be_visible()
        page.unroute('**/api/v1/users/ai-agent/profile')
        # Кнопка сохранения не перепрыгивает через обязательные подтверждения.
        from ui_test_helpers import open_management
        open_management(page,'technical')
        save=page.locator('#launch-check-save')
        assert save.evaluate("b=>Boolean(document.querySelector('#launch-check-vps').compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING)")
        assert save.evaluate("b=>Boolean(document.querySelector('#launch-check-steps').compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING)")
        # Тематика использует тот же диалог, поиск и навигацию стрелками.
        go('create');topic=page.get_by_role('button',name='Тематика канала',exact=True)
        topic.click();dialog=page.get_by_role('dialog',name='Тематика канала',exact=True)
        expect(dialog).to_have_class('select-dialog');query=dialog.get_by_role('searchbox',name='Найти тематику')
        query.fill('Образование');page.keyboard.press('ArrowDown');page.keyboard.press('Enter')
        expect(topic).to_contain_text('Образование');expect(topic).to_be_focused();expect(page.locator('#order-topic')).to_have_value('education')
        topic.click();page.locator('#campaign-form fieldset').first.evaluate('f=>f.disabled=true')
        expect(page.get_by_role('dialog')).to_have_count(0)
        page.locator('#campaign-form fieldset').first.evaluate('f=>f.disabled=false')
        for width in (320,390,768):
            page.set_viewport_size({'width':width,'height':600});topic.click()
            dialog=page.get_by_role('dialog');box=dialog.bounding_box()
            assert box['x']>=0 and box['x']+box['width']<=width+1 and box['y']+box['height']<=601
            page.screenshot(path=str(output/f'controls-topic-{width}.png'));page.keyboard.press('Escape')
        # Отказ ИИ виден в той же карточке, связан с профилем и попадает в историю.
        rejected=uuid4()
        with psycopg.connect(dsn,row_factory=dict_row) as c:
            c.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
                duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,moderator_comment)
                VALUES(%s,%s,%s,%s,'День',86400,30000,'RUB','rejected','Причина отказа: Продвижение азартных игр.')""",(rejected,member,channel,tariff))
            c.execute("""INSERT INTO ai_moderation_checks(campaign_id,snapshot,revision,decision,reason,description)
                SELECT id,updated_at,'test','rejected','Продвижение азартных игр.','Канал публикует рекламу казино.'
                FROM campaigns WHERE id=%s""",(rejected,))
            c.execute("""INSERT INTO ai_moderation_events(campaign_id,decision,reason,revision,topic,description,evidence)
                VALUES(%s,'rejected','Продвижение азартных игр.','test','entertainment','Канал публикует рекламу казино.',
                '[{"source":"post_1","quote":"Реклама казино"}]'::jsonb)""",(rejected,))
        page.reload();expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true')
        go('moderation/'+str(rejected))
        expect(page.locator('.ai-decision')).to_have_text('Отклонено: Агент ИИ')
        expect(saved_review).to_contain_text('Описание канала: Канал публикует рекламу казино.')
        expect(saved_review).to_contain_text('Причина отказа: Продвижение азартных игр.')
        saved_review.locator('summary').click()
        expect(saved_review.locator('blockquote')).to_have_text('Реклама казино')
        page.locator('summary').filter(has_text='История и документы').click()
        expect(page.locator('.campaign-report')).to_contain_text('Отклонено: Агент ИИ')
        for width in (320,390,768):
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(output/'controls-ai-rejection-390.png'),full_page=True)
        page.locator('.ai-decision a').click()
        expect(page.locator('.ai-agent-stats div').filter(has_text='Отклонено').locator('dd')).to_have_text('1')
        assert page.evaluate('pushokProfiles.backRoute()')=='moderation/'+str(rejected)
        assert not failures,failures
    finally:context.close()
