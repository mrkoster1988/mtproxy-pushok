"""Прямые пункты управления: реальные формы, роли, ссылки и сохранение черновика."""
from uuid import uuid4

import psycopg
from playwright.sync_api import expect
from ui_test_helpers import open_management, open_settings


def check_management_navigation(browser, base, output, failures, dsn):
    from app import main
    tokens, contexts = {}, []
    with psycopg.connect(dsn) as conn:
        for role in ('admin', 'operator', 'advertiser'):
            uid, sid = uuid4(), uuid4()
            if role == 'admin':
                uid = conn.execute('SELECT id FROM users WHERE is_owner').fetchone()[0]
            else:
                conn.execute('INSERT INTO users(id,telegram_user_id,role) VALUES(%s,%s,%s)',
                             (uid, int(uid.hex[:12], 16), role))
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
        'payments': ('Платежи', ['#payment-switches', '#sbp-connection', '#wallet-settings', '#sbp-review-list']),
        'tariffs': ('Тарифы', ['#tariff-admin-form']),
        'design': ('Оформление', ['#home-banner-form', '#loading-title-form', '#icons-form']),
        'people': ('Пользователи и награды', ['#team-form', '#birthday-settings', '#referral-settings']),
        'automation': ('ИИ и прокси', ['#channel-ai-settings', '#proxy-provisioning-settings', '#default-sponsor-form']),
        'technical': ('Диагностика', ['#launch-readiness', '[aria-label="Готовность Pushok Core"]']),
    }
    try:
        admin = launch('admin')
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
            for key in sections:
                page.evaluate('key => window.cabinet.go("management/"+key)', key)
                expect(page.locator('[data-page=dashboard]')).to_be_visible()
                expect(page.locator('[data-page=management]')).to_be_hidden()
        admin.evaluate("window.cabinet.go('profile/management')")
        admin.set_viewport_size({'width': 390, 'height': 844})
        admin.locator('#profile-management').screenshot(path=str(output/'management-menu.png'))
    finally:
        for context in contexts:
            context.close()
