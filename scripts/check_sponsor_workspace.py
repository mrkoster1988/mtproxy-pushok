"""Все вкладки спонсоров: реальные формы/API, роли, черновики и сбои чтения."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from playwright.sync_api import expect


def check_sponsor_workspace(browser, base, output, failures, dsn):
    from app import main
    users = {role: uuid4() for role in ('admin', 'operator', 'advertiser')}
    tokens, contexts = {}, []
    channel, other_channel, tariff, node_id = [uuid4() for _ in range(4)]
    campaigns = [uuid4() for _ in range(3)]
    long_title = 'ОченьДлинноеНазваниеКаналаБезПробелов · технологии и путешествия'
    with psycopg.connect(dsn) as conn:
        for role, uid in users.items():
            conn.execute('INSERT INTO users(id,telegram_user_id,role,first_name) VALUES(%s,%s,%s,%s)',
                         (uid, int(uid.hex[:12], 16), role, 'Проверка ' + role))
            sid = uuid4()
            conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')", (sid, uid))
            tokens[role] = main._create_session_token(uid, sid)
        for cid, title, username in ((channel, 'Новости puShok', 'full_sponsors_one'),
                                     (other_channel, long_title, 'full_sponsors_two')):
            conn.execute('INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,%s,%s)',
                         (cid, users['admin'], title, 'https://t.me/' + username))
        conn.execute("INSERT INTO proxy_nodes(id,name,host,port,target_engine) VALUES(%s,'Основной MTProxy','workspace.example.org',8443,'official_mtproxy')", (node_id,))
        for index in range(2):
            extra = uuid4()
            host = 'saved-' + str(index) + '.example.org'
            conn.execute("INSERT INTO proxy_nodes(id,name,host,port,target_engine) VALUES(%s,%s,%s,8443,'official_mtproxy')", (extra, 'Резервный узел ' + str(index + 1), host))
            conn.execute('''INSERT INTO channel_promotion_bindings(proxy_node_id,channel_id,ad_tag,confirmed_by,channel_url,node_host,node_port)
                         VALUES(%s,%s,%s,%s,%s,%s,8443)''', (extra, other_channel, extra.hex, users['admin'], 'https://t.me/full_sponsors_two', host))
        conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,'Сутки',86400,30000,150)", (tariff,))
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        for index, cid in enumerate(campaigns):
            conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,
                         duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status,payment_status)
                         VALUES(%s,%s,%s,%s,'Сутки · расширенный тариф',86400,30000,'RUB','approved','verified')""",
                         (cid, users['admin'], channel if index == 0 else other_channel, tariff))
            if index < 2:
                left = start + timedelta(days=index)
                conn.execute('UPDATE campaigns SET placement_status=%s,proxy_node_id=%s,scheduled_start_at=%s,scheduled_end_at=%s WHERE id=%s',
                             ('active' if index == 0 else 'scheduled', node_id, left, left + timedelta(days=1), cid))

    def launch(role):
        context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce', timezone_id='Europe/Moscow')
        contexts.append(context)
        context.add_init_script('sessionStorage.setItem("pushok-session",' + repr(tokens[role]) + ')')
        page = context.new_page()
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page.goto(base + '/#/sponsors')
        expect(page.locator('body')).to_have_attribute('data-cabinet-loaded', 'true')
        return page

    def tab(page, label):
        page.get_by_role('tab', name=label, exact=True).click()

    def refresh(page):
        page.evaluate('refresh()')

    def snapshot(page, name, target=None):
        if page.get_by_role('button', name='Закрыть уведомление', exact=True).is_visible():
            page.get_by_role('button', name='Закрыть уведомление', exact=True).click()
        for width in (320, 390, 768, 1280):
            page.set_viewport_size({'width': width, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), (name, width)
            for container in page.locator('.diagnostic-stack:visible,.sponsor-placement-list:visible,#nodes-list:visible').all():
                boxes = [item.bounding_box() for item in container.locator(':scope>.card:visible').all()]
                for index, box in enumerate(boxes):
                    for other in boxes[:index]:
                        vertical = max(box['y'] - other['y'] - other['height'], other['y'] - box['y'] - box['height'])
                        horizontal = max(box['x'] - other['x'] - other['width'], other['x'] - box['x'] - box['width'])
                        assert max(vertical, horizontal) >= 11, (name, width, box, other)
            for group in page.locator('.sponsor-form-actions:visible,.node-action-buttons:visible').all():
                boxes = [item.bounding_box() for item in group.locator(':scope>button:visible').all()]
                for index, box in enumerate(boxes):
                    if index:
                        assert abs(box['y'] - boxes[0]['y']) < 1, (name, width, boxes)
                        assert box['x'] >= boxes[index-1]['x'] + boxes[index-1]['width'] + 1, (name, boxes)
            for action in page.locator('[data-page=sponsors] .icon-action:visible').all():
                assert action.get_attribute('aria-label'), name
                expect(action.locator('svg use')).to_have_count(1)
            if width in (320, 390):
                if target:
                    target.screenshot(path=str(output / f'full-{name}-{width}.png'))
                else:
                    page.evaluate('scrollTo(0,0)')
                    page.screenshot(path=str(output / f'full-{name}-{width}.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})

    try:
        admin = launch('admin')
        page = admin.locator('[data-page=sponsors]')
        assert page.locator('.sponsor-placement').count()>=3
        snapshot(admin, 'placements')
        # Клавиатура переключает вкладки; действие карточки открывает нужную кампанию.
        tab(admin, 'Размещения')
        admin.get_by_role('tab', name='Размещения', exact=True).focus()
        admin.keyboard.press('End')
        expect(admin.get_by_role('tab', name='Настройка', exact=True)).to_be_focused()
        admin.keyboard.press('Home')
        page.get_by_role('link', name='Открыть кампанию Новости puShok', exact=True).click()
        expect(admin).to_have_url(base + '/#/moderation/' + str(campaigns[0]))
        admin.evaluate("window.cabinet.go('sponsors')")
        tab(admin, 'Узлы')
        admin.get_by_role('button', name='О реестре узлов', exact=True).click()
        expect(admin.locator('#sponsor-node-hint')).to_be_visible()
        admin.get_by_role('button', name='О реестре узлов', exact=True).click()
        expect(admin.locator('#sponsor-node-hint')).to_be_hidden()
        snapshot(admin, 'nodes')
        add = admin.locator('#node-admin>.panel-heading').get_by_role('button', name='Добавить узел', exact=True)
        add.click()
        form = admin.locator('#node-form')
        form.locator('[name=name]').fill('Дополнительный узел')
        form.locator('[name=host]').fill('https://wrong.example.org')
        form.get_by_role('button', name='Добавить узел', exact=True).click()
        expect(admin.locator('#notice')).to_contain_text('без протокола')
        expect(form.locator('[name=name]')).to_have_value('Дополнительный узел')
        form.locator('[name=host]').fill('second-workspace.example.org')
        form.locator('#node-engine-settings>summary').click()
        form.locator('#engine-mode').select_option('manual')
        expect(form.locator('#preferred-engine')).to_be_visible()
        form.locator('#preferred-engine').select_option('official_mtproxy')
        form.locator('#engine-preview').click()
        expect(form.locator('#engine-advice')).to_contain_text('План:')
        snapshot(admin, 'node-add', admin.locator('#node-create'))
        form.get_by_role('button', name='Отмена', exact=True).click()
        expect(form).to_be_hidden()
        add.click()
        expect(form.locator('[name=name]')).to_have_value('Дополнительный узел')
        form.get_by_role('button', name='Добавить узел', exact=True).click()
        row = admin.locator('#nodes-list .proxy-node').filter(has_text='Дополнительный узел')
        expect(row).to_be_visible()
        new_node = row.get_attribute('data-node-id')
        row.get_by_role('button', name='Изменить узел', exact=True).click()
        editor = row.locator('.management-form')
        editor.get_by_label('Название узла', exact=True).fill('Редактируемый узел с длинным названием')
        refresh(admin)
        expect(row.locator('.management-form').get_by_label('Название узла', exact=True)).to_have_value('Редактируемый узел с длинным названием')
        snapshot(admin, 'node-edit', row)
        row.get_by_role('button', name='Сохранить изменения', exact=True).click()
        row = admin.locator('#nodes-list .proxy-node[data-node-id="' + new_node + '"]')
        expect(row).to_contain_text('Редактируемый узел с длинным названием')
        row.get_by_role('button', name='Изменить план движка', exact=True).click()
        plan = row.locator('.node-plan-form')
        plan.locator('[name=engine_mode]').select_option('manual')
        plan.locator('[name=preferred_engine]').select_option('official_mtproxy')
        snapshot(admin, 'node-plan', row)
        plan.get_by_role('button', name='Отмена', exact=True).click()
        expect(plan).to_have_count(0)
        row.get_by_role('button', name='Изменить план движка', exact=True).click()
        row.get_by_role('button', name='Сохранить план', exact=True).click()
        expect(row.locator('.node-plan-form')).to_have_count(0)
        row.get_by_role('button', name='Поставить на паузу', exact=True).click()
        expect(row).to_contain_text('На паузе')
        snapshot(admin, 'node-paused', row)
        row.get_by_role('button', name='Возобновить', exact=True).click()
        expect(row).to_contain_text('В расписании')
        tab(admin, 'Настройка')
        snapshot(admin, 'setup')
        admin.locator('#sponsor-registration>summary').click()
        registration = admin.locator('#sponsor-form')
        registration.get_by_label('Узел спонсорского тега', exact=True).select_option(new_node)
        registration.get_by_label('ad_tag из MTProxyBot', exact=True).fill('z' * 32)
        assert not registration.evaluate('(e)=>e.checkValidity()')
        registration.get_by_label('ad_tag из MTProxyBot', exact=True).fill('c' * 32)
        refresh(admin)
        expect(registration.get_by_label('Узел спонсорского тега', exact=True)).to_have_value(new_node)
        expect(registration.get_by_label('ad_tag из MTProxyBot', exact=True)).to_have_value('c' * 32)
        snapshot(admin, 'registration', admin.locator('#sponsor-registration'))
        write_failure = lambda route: route.fulfill(status=503, json={'detail': 'database_unavailable'}) if route.request.method == 'PUT' else route.continue_()
        registration_url = '**/api/v1/admin/proxy-nodes/*/sponsor-registration'
        admin.route(registration_url, write_failure)
        registration.get_by_role('button', name='Сохранить регистрацию', exact=True).click()
        expect(admin.locator('#sponsor-registration .form-feedback')).to_have_attribute('data-tone', 'error')
        expect(registration.get_by_label('ad_tag из MTProxyBot', exact=True)).to_have_value('c' * 32)
        admin.unroute(registration_url, write_failure)
        registration.get_by_role('button', name='Сохранить регистрацию', exact=True).click()
        expect(admin.locator('#sponsor-registration')).to_contain_text('окончанием cccc')
        manual = admin.locator('#sponsor-manual-binding')
        # Ошибка проверки канала по умолчанию раньше навсегда блокировала
        # ручную привязку другого канала на том же узле.
        with psycopg.connect(dsn) as conn:
            conn.execute('''INSERT INTO telegram_binding_jobs(proxy_node_id,channel_id,channel_url,node_host,node_port,error_code)
                VALUES(%s,%s,'https://t.me/full_sponsors_two','second-workspace.example.org',8443,'proxy_not_found')''', (new_node, other_channel))
        manual.locator('summary').click()
        manual.locator('[name=node]').select_option(new_node)
        manual.locator('[name=channel]').select_option(str(channel))
        manual.locator('[name=tag]').fill('d' * 32)
        assert not manual.locator('form').evaluate('(e)=>e.checkValidity()')
        manual.locator('[name=confirmed]').check()
        tab(admin, 'Размещения')
        admin.get_by_role('button', name='Обновить диагностику', exact=True).click()
        tab(admin, 'Настройка')
        with admin.expect_response(lambda r: r.url.endswith('/promotion-bindings')):
            admin.get_by_role('button', name='Обновить состояние подключения', exact=True).click()
        expect(manual.locator('[name=tag]')).to_have_value('d' * 32)
        expect(manual.locator('[name=node]')).to_have_value(new_node)
        expect(manual.locator('[name=confirmed]')).to_be_checked()
        expect(manual).to_have_attribute('open', '')
        snapshot(admin, 'manual-binding', manual)
        admin.set_viewport_size({'width': 390, 'height': 430})
        manual.locator('[name=tag]').focus()
        manual.get_by_role('button', name='Включить для канала', exact=True).scroll_into_view_if_needed()
        assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        busy_failure = lambda route: route.fulfill(status=409, json={'detail': 'promotion_binding_busy'}) if route.request.method == 'PUT' else route.continue_()
        admin.route('**/api/v1/admin/promotion-bindings', busy_failure)
        manual.get_by_role('button', name='Включить для канала', exact=True).click()
        expect(manual.locator('.form-feedback')).to_contain_text('ожидает подтверждения Telegram')
        expect(manual.locator('[name=tag]')).to_have_value('d' * 32)
        expect(manual.locator('[name=confirmed]')).to_be_checked()
        admin.unroute('**/api/v1/admin/promotion-bindings', busy_failure)
        admin.route('**/api/v1/admin/promotion-bindings', write_failure)
        manual.get_by_role('button', name='Включить для канала', exact=True).click()
        expect(manual.locator('.form-feedback')).to_have_attribute('data-tone', 'error')
        expect(manual.locator('[name=tag]')).to_have_value('d' * 32)
        expect(manual.locator('[name=confirmed]')).to_be_checked()
        snapshot(admin, 'binding-save-error', manual)
        admin.unroute('**/api/v1/admin/promotion-bindings', write_failure)
        manual.get_by_role('button', name='Включить для канала', exact=True).click()
        saved = admin.locator('.sponsor-binding').filter(has_text='Новости puShok')
        expect(saved).to_contain_text('dddd')
        expect(manual.locator('[name=tag]')).to_have_value('')
        with psycopg.connect(dsn) as conn:
            assert conn.execute('SELECT 1 FROM telegram_binding_jobs WHERE proxy_node_id=%s', (new_node,)).fetchone() is None
        snapshot(admin, 'saved-binding', admin.locator('#promotion-automation'))
        # Команды копируются кнопками без вывода их в основную карточку.
        admin.locator('.owner-connect>summary').click()
        admin.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.__copied=text}}})")
        admin.evaluate("window.__copyClicks=[];addEventListener('click',e=>window.__copyClicks.push(e.target.closest('button')?.getAttribute('aria-label')),true)")
        for label, ending in [('Скопировать команду подключения', ' enable'), ('Скопировать команду проверки', ' status')]:
            admin.get_by_role('button', name=label, exact=True).click()
            try:
                admin.wait_for_function('(ending)=>window.__copied?.endsWith(ending)', arg=ending, timeout=5000)
            except Exception:
                print('Clipboard failure:', label, admin.evaluate('({value:window.__copied,clicks:window.__copyClicks,notice:document.getElementById("notice").innerText})'), flush=True)
                admin.screenshot(path=str(output / 'copy-failure.png'))
                raise
        snapshot(admin, 'connection-commands', admin.locator('.owner-connect'))
        # Ответы с ошибкой не уничтожают ручной черновик; повтор восстанавливает панель.
        manual.locator('summary').click()
        manual.locator('[name=tag]').fill('e' * 32)
        failure_route = lambda route: route.fulfill(status=503, json={'detail': 'database_unavailable'})
        admin.route('**/api/v1/admin/promotion-bindings', failure_route)
        admin.get_by_role('button', name='Обновить состояние подключения', exact=True).click()
        expect(admin.locator('.sponsor-load-error')).to_be_visible()
        expect(manual.locator('[name=tag]')).to_have_value('e' * 32)
        snapshot(admin, 'setup-error')
        admin.unroute('**/api/v1/admin/promotion-bindings', failure_route)
        admin.get_by_role('button', name='Повторить загрузку настроек', exact=True).click()
        expect(admin.locator('.sponsor-load-error')).to_have_count(0)
        expect(manual.locator('[name=tag]')).to_have_value('e' * 32)
        # Первая неудачная загрузка также оставляет явное действие повтора.
        admin.route('**/api/v1/admin/promotion-bindings', failure_route)
        admin.reload()
        tab(admin, 'Настройка')
        expect(admin.locator('.sponsor-load-error')).to_be_visible()
        snapshot(admin, 'initial-error')
        admin.unroute('**/api/v1/admin/promotion-bindings', failure_route)
        admin.get_by_role('button', name='Повторить загрузку настроек', exact=True).click()
        expect(admin.locator('#telegram-owner-status')).to_be_visible()
        # Готовность, ожидание и ошибки Telegram — данные тестового ответа, не живой вход.
        data = admin.request.get(base + '/api/v1/admin/promotion-bindings', headers={'Authorization': 'Bearer ' + tokens['admin']}).json()
        fixture = deepcopy(data)
        def binding_route(route):
            route.fulfill(json=fixture)
        admin.route('**/api/v1/admin/promotion-bindings', binding_route)
        for code, title, tone in [('ready', 'Автопривязка включена', 'success'), ('not_connected', 'Нужен вход в Telegram', 'warning'),
                                  ('revoked', 'Вход в Telegram отозван', 'danger'), ('agent_stale', 'Нет свежего отчёта агента', 'warning')]:
            fixture['telegram_owner'] = dict(code=code, title=title, tone=tone, next_action='Откройте подключение и выполните проверку на VPS.')
            fixture['telegram_automation'] = [dict(name='Основной MTProxy', channel_title=long_title, phase='bind', retry_at=datetime.now(timezone.utc).isoformat(), error_code='telegram_flood_wait' if code == 'revoked' else None)]
            admin.get_by_role('button', name='Обновить состояние подключения', exact=True).click()
            expect(admin.locator('#telegram-owner-status')).to_contain_text(title)
            snapshot(admin, 'owner-' + code)
        fixture['telegram_automation'][0]['error_code'] = 'unrecognized_remote_error'
        admin.get_by_role('button', name='Обновить состояние подключения', exact=True).click()
        expect(admin.locator('.binding-status')).to_contain_text('требует проверки')
        expect(page).not_to_contain_text('unrecognized_remote_error')
        admin.unroute('**/api/v1/admin/promotion-bindings', binding_route)
        tab(admin, 'Размещения')
        admin.route('**/api/v1/admin/promotion-diagnostics', failure_route)
        admin.get_by_role('button', name='Обновить диагностику', exact=True).click()
        expect(admin.locator('.sponsor-health')).to_contain_text('недоступна')
        snapshot(admin, 'diagnostics-error')
        admin.unroute('**/api/v1/admin/promotion-diagnostics', failure_route)
        admin.get_by_role('button', name='Обновить диагностику', exact=True).click()
        admin.locator('#sponsor-diagnostics-details>summary').click()
        expect(admin.locator('#promotion-agent-report')).to_be_visible()
        snapshot(admin, 'diagnostics-expanded')
        tab(admin, 'Настройка')
        saved.get_by_role('button', name='Отключить привязку Новости puShok', exact=True).click()
        expect(saved).to_have_count(0)
        tab(admin, 'Узлы')
        row = admin.locator('#nodes-list .proxy-node[data-node-id="' + new_node + '"]')
        admin.once('dialog', lambda dialog: dialog.dismiss())
        row.get_by_role('button', name='Удалить узел', exact=True).click()
        expect(row).to_be_visible()
        admin.once('dialog', lambda dialog: dialog.accept())
        row.get_by_role('button', name='Удалить узел', exact=True).click()
        expect(row).to_have_count(0)
        # Пустые состояния: нет ложных действий сохранения и тяжёлых плашек.
        admin.route('**/api/v1/operator/proxy-nodes', lambda route: route.fulfill(json=[]))
        admin.route('**/api/v1/operator/campaigns*', lambda route: route.fulfill(json=[]))
        fixture = dict(channels=[], bindings=[], runtime=[], telegram_automation=[], telegram_owner={})
        admin.route('**/api/v1/admin/promotion-bindings', binding_route)
        refresh(admin)
        expect(admin.locator('#nodes-list')).to_contain_text('Узлов пока нет')
        snapshot(admin, 'empty-nodes')
        tab(admin, 'Настройка')
        admin.locator('#sponsor-registration>summary').click()
        expect(admin.locator('#sponsor-form button[type=submit]')).to_be_disabled()
        admin.locator('#sponsor-manual-binding>summary').click()
        expect(admin.locator('#sponsor-manual-binding button[type=submit]')).to_be_disabled()
        snapshot(admin, 'empty-setup')
        tab(admin, 'Размещения')
        snapshot(admin, 'empty-placements')
        operator = launch('operator')
        expect(operator.get_by_role('tab', name='Настройка', exact=True)).to_have_count(0)
        expect(operator.locator('#promotion-diagnostics')).to_be_hidden()
        tab(operator, 'Узлы')
        expect(operator.locator('#nodes-list .proxy-node')).not_to_have_count(0)
        expect(operator.locator('#nodes-list button')).to_have_count(0)
        expect(operator.get_by_role('button', name='Добавить узел', exact=True)).to_have_count(0)
        snapshot(operator, 'operator-nodes')
        user = launch('advertiser')
        expect(user.locator('[data-page=sponsors]')).to_be_hidden()
        # Прямой переход выбирает нужные канал и узел, открывая ручную форму.
        direct = launch('admin')
        direct.evaluate('route=>window.cabinet.go(route)', 'sponsors/' + str(channel) + '/' + str(node_id))
        expect(direct.locator('#sponsor-manual-binding')).to_be_visible()
        expect(direct.locator('#sponsor-manual-binding [name=channel]')).to_have_value(str(channel))
        expect(direct.locator('#sponsor-manual-binding [name=node]')).to_have_value(str(node_id))
        expect(direct.get_by_role('tab', name='Настройка', exact=True)).to_have_attribute('aria-selected', 'true')
        assert not failures, failures
        print('PASS: sponsors — 3 tabs, all forms, CRUD, drafts, clipboard, retry/errors, owner states, roles, direct links; 320/390/768/1280px', flush=True)
    finally:
        for context in contexts:
            context.close()
