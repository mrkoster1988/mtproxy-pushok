"""Real bundled Telegram SDK + signed login; only the native client bridge is fake."""
import json
from urllib.parse import urlencode

from playwright.sync_api import expect

from test_miniapp_auth import signed_data


def check_miniapp_sdk(browser, base, output, failures):
    def launch(raw, failure=None):
        context = browser.new_context(viewport={'width': 390, 'height': 844},
                                      reduced_motion='reduce')
        # No Telegram.WebApp mock: the asset must parse the real launch fragment.
        context.add_init_script("""window.__nativeEvents=[];
            window.TelegramWebviewProxy={postEvent(name, data){
                window.__nativeEvents.push({name, data:JSON.parse(data||'{}')});
            }};""")
        page = context.new_page()
        requests, blocked, held = [], [], []
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.on('request', lambda request: requests.append((request.method, request.url)))
        attempts = []

        def network(route):
            url = route.request.url
            if not url.startswith(base + '/'):
                blocked.append(url)
                route.abort()
            elif '/assets/vendor/telegram-web-app.' in url:
                attempts.append(url)
                if failure and len(attempts) == 1:
                    if failure == 'timeout':
                        held.append(route)
                    else:
                        route.fulfill(status=404, body='missing asset')
                else:
                    route.continue_()
            else:
                route.continue_()

        page.route('**/*', network)
        fragment = urlencode({'tgWebAppData': raw, 'tgWebAppVersion': '8.0',
                              'tgWebAppPlatform': 'android',
                              'tgWebAppThemeParams': json.dumps({'bg_color': '#0b1728'})})
        page.goto(base + '/miniapp#' + fragment, wait_until='domcontentloaded')
        return page, requests, blocked, attempts, held

    raw = signed_data(user=json.dumps({'id': 99881001, 'first_name': 'Новый клиент'}),
                      query_id='bundled-sdk-new-client')
    page, requests, blocked, attempts, _ = launch(raw)
    try:
        expect(page.locator('body')).to_have_class('miniapp-mode cabinet-mode')
        expect(page.locator('[data-page=dashboard]')).to_be_visible()
        expect(page.locator('.cabinet-account')).to_contain_text('Новый клиент')
        assert len(attempts) == 1 and not blocked, (attempts, blocked)
        assert page.evaluate('Telegram.WebApp.initData') == raw
        assert page.evaluate("__nativeEvents.some(e=>e.name==='web_app_ready')")
        assert page.evaluate("__nativeEvents.some(e=>e.name==='web_app_expand')")
        page.locator('.mobile-bottom-nav a[data-route=profile]').click()
        expect(page.locator('[data-page=profile]')).to_be_visible()
        assert page.evaluate('Telegram.WebApp.BackButton.isVisible')
        page.evaluate("Telegram.WebView.receiveEvent('back_button_pressed')")
        expect(page.locator('[data-page=dashboard]')).to_be_visible()
        assert not page.evaluate('Telegram.WebApp.BackButton.isVisible')
        page.evaluate("""() => {
            Telegram.WebView.receiveEvent('safe_area_changed',{top:24,bottom:16});
            Telegram.WebView.receiveEvent('content_safe_area_changed',{top:12});
        }""")
        assert page.evaluate("document.documentElement.style.getPropertyValue('--mini-safe-top')") == '36px'
        # A synthetic invoice is sent only to the fake native bridge, never to Telegram.
        page.evaluate("""() => {
            window.__invoiceResult=null;
            pushokMiniApp.payInvoice('https://t.me/$pushok-sdk-test')
                .then(value=>window.__invoiceResult=value);
        }""")
        assert page.evaluate("__nativeEvents.some(e=>e.name==='web_app_open_invoice'&&e.data.slug==='pushok-sdk-test')")
        page.evaluate("Telegram.WebView.receiveEvent('invoice_closed',{slug:'pushok-sdk-test',status:'cancelled'})")
        page.wait_for_function("window.__invoiceResult==='cancelled'")
        page.screenshot(path=str(output / '47-miniapp-local-sdk.png'), full_page=True)
        page.reload()
        expect(page.locator('body')).to_have_class('miniapp-mode cabinet-mode')
        assert page.evaluate('Telegram.WebApp.initData') == raw
        assert len([url for method, url in requests if method == 'POST' and
                    url.endswith('/auth/telegram/miniapp')]) == 1
        assert not blocked, blocked
    finally:
        page.context.close()

    for index, failure in enumerate(('404', 'timeout')):
        raw = signed_data(user=json.dumps({'id': 99881002 + index, 'first_name': 'Повторный вход'}),
                          query_id='bundled-sdk-retry-' + failure)
        page, requests, blocked, attempts, held = launch(raw, failure)
        try:
            expect(page.locator('#miniapp-login')).to_contain_text(
                'Не удалось загрузить компонент приложения', timeout=12000)
            expect(page.get_by_role('button', name='Повторить вход')).to_be_enabled()
            expect(page.locator('#workspace')).to_be_hidden()
            assert not any(url.endswith('/auth/telegram/miniapp') for _, url in requests)
            for route in held:
                route.abort()
            page.get_by_role('button', name='Повторить вход').click()
            expect(page.locator('body')).to_have_class('miniapp-mode cabinet-mode')
            expect(page.locator('.cabinet-account')).to_contain_text('Повторный вход')
            assert len(attempts) == 2 and not blocked, (attempts, blocked)
            assert len([url for method, url in requests if method == 'POST' and
                        url.endswith('/auth/telegram/miniapp')]) == 1
        finally:
            page.context.close()

    page, _, blocked, _, _ = launch('')
    try:
        expect(page.locator('#miniapp-login')).to_contain_text('Откройте Mini App из меню')
        expect(page.locator('#workspace')).to_be_hidden()
        assert not blocked, blocked
    finally:
        page.context.close()
