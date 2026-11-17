"""Проверка автопоиска в Chromium. Telegram подменяется только на границе API."""
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import expect


def check_public_channel_ui(page, output):
    source = page.locator('#order-channel-url')
    title = page.locator('#order-channel-title')
    result = page.locator('#order-new-channel .channel-search-result')
    calls = []

    def preview(route):
        value = parse_qs(urlsplit(route.request.url).query)['query'][0]
        calls.append(value)
        if value == '@group_channel':
            route.fulfill(status=422,json={'detail':'channel_not_broadcast'})
        elif value == '@offline_channel':
            route.fulfill(status=503,json={'detail':'channel_lookup_unavailable'})
        else:
            name=value.lstrip('@')
            route.fulfill(json=dict(title='Канал '+name,username=name,telegram_url='https://t.me/'+name,telegram_channel_id=-100123))

    page.route('**/api/v1/channel-preview?*', preview)
    try:
        source.fill('@auto_channel')
        expect(result).to_have_attribute('data-state','verified')
        expect(title).to_have_value('Канал auto_channel')
        assert calls.count('@auto_channel') == 1, calls
        title.fill('Моё название')
        source.fill('@next_channel')
        expect(result).to_contain_text('Канал next_channel')
        expect(title).to_have_value('Моё название')
        source.fill('https://t.me/+invite')
        expect(result).to_have_attribute('data-state','error')
        expect(result).to_contain_text('Пригласительная ссылка не подходит')
        assert 'https://t.me/+invite' not in calls
        source.fill('@group_channel')
        expect(result).to_contain_text('группа или личный аккаунт')
        page.locator('#order-submit').click()
        expect(page.locator('#order-feedback')).to_contain_text('группа или личный аккаунт')
        expect(page.locator('#order-submit')).to_be_enabled()
        source.fill('@offline_channel')
        expect(result).to_contain_text('Telegram временно недоступен')
        expect(result).not_to_contain_text('закрыт')
        # Транспорт намеренно игнорирует AbortSignal: поздний ответ всё равно отбрасывается.
        page.evaluate("""() => {
          window.__channelFetch=window.fetch;
          window.fetch=(url,options)=>String(url).includes('channel-preview?query=%40old_channel')
            ?new Promise(resolve=>{window.__oldChannelResolve=resolve;}) :window.__channelFetch(url,options);
        }""")
        source.fill('@old_channel')
        page.wait_for_function('Boolean(window.__oldChannelResolve)')
        source.fill('@fresh_channel')
        expect(result).to_contain_text('Канал fresh_channel')
        page.evaluate("""() => window.__oldChannelResolve(new Response(JSON.stringify({title:'Устаревший канал',username:'old_channel',telegram_url:'https://t.me/old_channel'}),{headers:{'Content-Type':'application/json'}}))""")
        expect(result).to_contain_text('Канал fresh_channel')
        expect(result).not_to_contain_text('Устаревший')
        page.evaluate('window.fetch=window.__channelFetch')
        page.evaluate("""() => {window.fetch=(url,options)=>String(url).includes('channel-preview?query=%40timeout_channel')
          ?new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}))
          :window.__channelFetch(url,options);} """)
        source.fill('@timeout_channel')
        expect(result).to_contain_text('Telegram временно недоступен',timeout=10000)
        expect(page.locator('#order-submit')).to_be_enabled()
        page.evaluate('window.fetch=window.__channelFetch')
        source.fill('@fresh_channel')
        expect(result).to_have_attribute('data-state','verified')
        expect(page.locator('#order-feedback')).to_be_hidden()
        page.set_viewport_size({'width':320,'height':740})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.screenshot(path=str(output/'45-channel-auto-check.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
    finally:
        page.unroute('**/api/v1/channel-preview?*', preview)
        page.evaluate('if(window.__channelFetch)window.fetch=window.__channelFetch')
        source.fill('');title.fill('')
