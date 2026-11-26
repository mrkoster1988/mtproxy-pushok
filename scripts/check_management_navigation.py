"""Прямые пункты управления: реальные формы, роли, ссылки и сохранение черновика."""
import re
from uuid import uuid4

import psycopg
from playwright.sync_api import expect
from ui_test_helpers import open_management, open_settings


def check_management_navigation(browser, base, output, failures, dsn):
    from app import main
    tokens, contexts = {}, []
    with psycopg.connect(dsn) as conn:
        for role in ('admin', 'operator', 'advertiser', 'developer', 'plain_admin'):
            uid, sid = uuid4(), uuid4()
            if role == 'admin':
                uid = conn.execute('SELECT id FROM users WHERE is_owner').fetchone()[0]
            else:
                stored_role = {'developer':'admin','plain_admin':'admin'}.get(role,role)
                conn.execute('INSERT INTO users(id,telegram_user_id,role,developer_granted) VALUES(%s,%s,%s,%s)',
                             (uid, int(uid.hex[:12], 16), stored_role, role=='developer'))
            conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')", (sid, uid))
            tokens[role] = main._create_session_token(uid, sid)

    def launch(role, route='profile'):
        context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        contexts.append(context)
        context.add_init_script('sessionStorage.setItem("pushok-session",' + repr(tokens[role]) + ')')
        page = context.new_page()
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(base + '/') else r.abort())
        page.goto(base + '/#/' + route)
        expect(page.locator('body')).to_have_attribute('data-cabinet-loaded', 'true')
        return page

    sections = {
        'vpn': ('Настройки VPN', ['#vpn-settings-center']),
        'payments': ('Платежи', ['#payment-switches', '#sbp-connection', '#wallet-settings', '#sbp-review-list']),
        'tariffs': ('Тарифы', ['#tariff-admin-form']),
        'design': ('Оформление', ['#home-banner-form', '#loading-title-form', '#icons-form']),
        'people': ('Пользователи и награды', ['#team-form', '#birthday-settings', '#referral-settings']),
        'automation': ('ИИ и прокси', ['#channel-ai-settings', '#proxy-provisioning-settings', '#default-sponsor-form']),
        'technical': ('Диагностика', ['#launch-readiness', '[aria-label="Готовность Pushok Core"]']),
    }
    try:
        admin = launch('admin')
        settings = admin.get_by_role('navigation', name='Настройки', exact=True)
        settings.get_by_role('link', name='Бот Pushok ИИ', exact=True).click()
        form = admin.locator('#support-bot-settings')
        secret = form.get_by_label('Токен бота из BotFather')
        expect(secret).to_have_attribute('type','password')
        expect(form.get_by_role('link',name='@server_pushok_bot')).to_have_attribute('href','https://t.me/server_pushok_bot')
        appearance=form.locator('.support-appearance')
        welcome=appearance.get_by_label('Текст приветствия')
        initial=welcome.input_value()
        welcome.fill('Добро пожаловать в **Pushok Flow**!')
        expect(appearance.locator('.support-bubble strong').last).to_have_text('Pushok Flow')
        expect(appearance.locator('.support-bubble img')).to_be_visible()
        appearance.locator('summary').click()
        appearance.get_by_label('Кнопка предложения').fill('Есть идея')
        expect(appearance.locator('.support-preview-button').nth(1)).to_have_text('💡 Есть идея')
        appearance.get_by_label('Картинка приветствия').select_option('none')
        expect(appearance.locator('.support-bubble img')).to_have_count(0)
        appearance.get_by_role('button',name='Сохранить оформление').click()
        expect(appearance.get_by_role('status')).to_have_text('Оформление сохранено')
        for width in (320,390,768):
            admin.set_viewport_size({'width':width,'height':844})
            assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), (width, admin.evaluate(
                "Array.from(document.querySelectorAll('body *')).filter(n=>n.getBoundingClientRect().right>innerWidth+1).slice(0,16).map(n=>[n.tagName,n.id,n.className,Math.round(n.getBoundingClientRect().width)])"))
        admin.reload()
        expect(welcome).to_have_value('Добро пожаловать в **Pushok Flow**!')
        welcome.fill(initial)
        appearance.get_by_label('Картинка приветствия').select_option('mascot')
        appearance.locator('summary').click()
        appearance.get_by_label('Кнопка предложения').fill('Предложить идею')
        appearance.get_by_role('button',name='Сохранить оформление').click()
        expect(appearance.get_by_role('status')).to_have_text('Оформление сохранено')
        admin.set_viewport_size({'width':390,'height':844})
        secret.fill('synthetic-invalid')
        form.get_by_role('button', name='Подключить бота').click()
        expect(form.locator('form').first.get_by_role('status')).to_have_text('Проверьте формат токена из BotFather.')
        admin.locator('.screen-back').click()
        expect(admin).to_have_url(base + '/#/profile/settings')
        settings.get_by_role('link',name='Бот Pushok ИИ').click()
        expect(secret).to_have_value('')
        admin.evaluate("window.cabinet.go('help')")
        admin.get_by_role('link',name='Настроить бота Pushok ИИ').click()
        expect(secret).to_be_visible()
        admin.locator('.screen-back').click()
        developer = launch('developer','management/ai-bot')
        expect(developer.locator('#support-bot-settings input[type=password]')).to_be_visible()
        ordinary = launch('plain_admin','management/ai-bot')
        expect(ordinary.locator('[data-page=dashboard]')).to_be_visible()
        expect(ordinary.locator('#support-bot-settings input')).to_have_count(0)
        menu = admin.get_by_role('navigation', name='Управление', exact=True)
        expect(menu.get_by_role('link', name='Настройки', exact=True)).to_have_count(0)
        expect(menu.locator('a[href^="#/management/"]')).to_have_count(len(sections))
        expect(menu.get_by_role('link',name='Бонусы и акции',exact=True)).to_have_attribute('href','#/growth')
        expect(admin.locator('.profile-menu a[href="#/growth"]')).to_have_count(1)
        expect(menu.get_by_role('link',name='Версия и обновления',exact=True)).to_have_attribute('href','#/profile/updates')
        for key, (label, selectors) in sections.items():
            menu.get_by_role('link', name=label, exact=True).click()
            expect(admin).to_have_url(base + '/#/management/' + key)
            expect(admin.locator('[data-page=management] h1')).to_have_text(label)
            expect(admin.locator('.cabinet-topbar .mobile-brand strong')).to_have_text(label)
            expect(admin.locator('.settings-nav:visible')).to_have_count(0)
            if key == 'automation':
                group=admin.locator('.automation-settings-list')
                expect(group.locator('.ai-settings-card')).to_have_count(4)
                assert group.locator('.ai-settings-card').evaluate_all(
                    "cards=>cards.every(card=>!card.parentElement.closest('details'))")
            open_settings(admin)
            for selector in selectors:
                expect(admin.locator(selector)).to_have_count(1)
                expect(admin.locator(selector)).to_be_visible()
            expect(admin.locator('.settings-panel:visible')).to_have_count(1)
            for width in (320, 390, 768, 1280):
                admin.set_viewport_size({'width': width, 'height': 844})
                assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), (key, width)
                if key == 'payments':
                    section = admin.locator('#settings-payments')
                    expect(section.locator(':scope > section > header > h2')).to_have_text(
                        ['Способы оплаты', 'Внутренний баланс', 'Сверка платежей'])
                    expect(section.locator('details details')).to_have_count(0)
                    expect(section.get_by_role('link', name='Цены тарифов')).to_have_attribute('href', '#/management/tariffs')
                    expect(section.locator('#payment-stars-success')).to_have_text(re.compile(r'^\d+$'))
                    for method in section.locator('.payment-method').all():
                        text = method.locator('.payment-copy').bounding_box()
                        toggle = method.locator('input').bounding_box()
                        assert toggle['x'] >= text['x'] + text['width'], (width, text, toggle)
                    if width in (390, 768):
                        details = section.locator('details.payment-fold[open]')
                        for fold in details.all():
                            fold.locator('summary').click()
                        section.screenshot(path=str(output / f'payment-methods-{width}.png'),
                                           style='.cabinet-topbar,.mobile-bottom-nav{visibility:hidden!important}')
                if key == 'tariffs':
                    field, action = admin.locator('#tariff-admin-select').bounding_box(), admin.locator('#tariff-admin-new').bounding_box()
                    assert action['x'] >= field['x'] + field['width'] + 7
                    assert abs(field['y'] + field['height']/2 - action['y'] - action['height']/2) < 2
                if width == 390:
                    admin.evaluate('window.scrollTo(0,0)')
                    admin.screenshot(path=str(output / ('management-' + key + '.png')), full_page=True)
            admin.locator('.screen-back').click()
            expect(admin).to_have_url(base + '/#/profile/management')
            expect(menu).to_be_visible()
        # Переключение раздела не пересоздаёт форму и не теряет незаписанный тариф.
        open_management(admin, 'tariffs')
        admin.locator('#tariff-admin-new').click()
        admin.locator('#tariff-admin-name').fill('Новый тариф — черновик')
        open_management(admin, 'payments')
        open_management(admin, 'tariffs')
        expect(admin.locator('#tariff-admin-name')).to_have_value('Новый тариф — черновик')
        admin.locator('#tariff-admin-cancel').click()
        # Обработчик сохранения и API остались у той же формы.
        open_management(admin, 'design')
        old_title = admin.locator('#loading-title-input').input_value()
        admin.locator('#loading-title-input').fill('Проверка раздела управления')
        admin.locator('#loading-title-form').get_by_role('button', name='Сохранить надпись').click()
        expect(admin.locator('#loading-title-status')).to_contain_text('Сохранено')
        response = admin.request.get(base + '/api/v1/admin/miniapp/loading', headers={'Authorization': 'Bearer ' + tokens['admin']})
        assert response.json()['loading_title'] == 'Проверка раздела управления'
        admin.locator('#loading-title-input').fill(old_title)
        admin.locator('#loading-title-form').get_by_role('button', name='Сохранить надпись').click()
        expect(admin.locator('#loading-title-status')).to_contain_text('Сохранено')
        # Закладки старой версии переводятся на новый адрес.
        admin.goto(base + '/#/settings/system')
        expect(admin).to_have_url(base + '/#/management/automation')
        open_settings(admin)
        expect(admin.locator('#channel-ai-settings')).to_be_visible()
        admin.reload()
        expect(admin.locator('[data-page=management] h1')).to_have_text('ИИ и прокси')
        for role in ('operator', 'advertiser'):
            page = launch(role)
            expect(page.locator('.profile-menu a[href^="#/management/"]')).to_have_count(0)
            for key in (*sections, 'ai-bot'):
                page.evaluate('key => window.cabinet.go("management/"+key)', key)
                expect(page.locator('[data-page=dashboard]')).to_be_visible()
                expect(page.locator('[data-page=management]')).to_be_hidden()
        admin.evaluate("window.cabinet.go('profile/management')")
        admin.set_viewport_size({'width': 390, 'height': 844})
        admin.locator('#profile-management').screenshot(path=str(output/'management-menu.png'), animations='disabled')

        # Exercise the cabinet's automatic button styling as well as the VPN renderer.
        states={'active':'Активна','expired':'Истекла','revoked':'Отозвана','pending':'Готовится'}
        members=[dict(id=str(uuid4()), name=label, state=state, subscription_id=str(uuid4()),
                      country_code='RU' if state=='active' else 'CH',city='Москва' if state=='active' else 'Берн',
                      endpoint_ip='192.0.2.1',tunnel_ips=['10.0.0.7'],tariff_name='Тестовый тариф')
                 for state,label in states.items()]
        admin.route('**/api/v1/vpn/dashboard', lambda route: route.fulfill(json={'can_manage':True,'members':members,'personal':None}))
        admin.evaluate("window.cabinet.go('vpn')")
        directory=admin.locator('.vpn-directory')
        expect(directory.locator('.vpn-card')).to_have_count(len(states))
        admin.evaluate('window.pushokInterface.actions()')
        for state,label in states.items():
            badge=directory.locator(f'button[data-subscription="{state}"]')
            expect(badge).to_have_text(label)
            expect(badge).to_be_visible()
            badge.click()
            panel=admin.locator('#'+badge.get_attribute('aria-controls'))
            expect(panel.get_by_label('Новый статус')).to_be_visible()
            badge.click()
            expect(panel).to_be_hidden()
        for width in (320,390,768):
            admin.set_viewport_size({'width':width,'height':844})
            for badge in directory.locator('button[data-subscription]').all():
                assert badge.evaluate('b=>b.scrollWidth<=b.clientWidth+1 && b.getBoundingClientRect().right<=innerWidth')
        first=directory.locator('.vpn-card').filter(has=admin.locator('[data-subscription=active]'))
        address=first.locator('.vpn-addresses dd')
        expect(address.nth(1)).to_have_text('Москва')
        expect(address.nth(1)).to_have_css('font-style','italic')
        expect(address.first).to_have_css('font-style','normal')
        expect(address.last).to_have_css('font-style','normal')
        other=directory.locator('.vpn-card').filter(has=admin.locator('[data-subscription=expired]'))
        expect(other.locator('.vpn-addresses dd').nth(1)).to_have_text('Швейцария, Берн')
    finally:
        for context in contexts:
            context.close()
