"""Тематика в настоящей форме; подменяется только ответ внешнего анализа."""
from playwright.sync_api import expect


def check_channel_topics_ui(page, output):
    source=page.locator('#order-channel-url')
    select=page.locator('#order-topic')
    status=page.locator('#order-topic-status')
    detect=page.locator('#order-topic-detect')

    def suggestion(route):
        route.fulfill(json={'topic':'education','label':'Образование','engine':'rules','basis':'posts','posts_count':4})

    page.route('**/api/v1/channel-topics/suggest?*',suggestion)
    try:
        source.fill('@topic_demo')
        expect(select).to_have_value('education')
        expect(status).to_contain_text('по публичным публикациям')
        expect(page.locator('#order-summary')).to_contain_text('Образование')
        select.select_option('health')
        page.evaluate('window.pushokOrder.summary()')
        expect(select).to_have_value('health')
        select.select_option('')
        page.evaluate('window.pushokOrder.summary()')
        expect(select).to_have_value('')
        expect(status).to_contain_text('не указана')
        # Поздний ответ не перезаписывает ручной выбор, даже если fetch игнорирует abort.
        page.evaluate('''() => {window.__topicFetch=window.fetch;
            window.fetch=(url,options)=>String(url).includes('channel-topics/suggest')
              ?new Promise(resolve=>{window.__topicResolve=resolve;}) :window.__topicFetch(url,options);
        }''')
        detect.click()
        page.wait_for_function('Boolean(window.__topicResolve)')
        expect(page.locator('#order-submit')).to_be_enabled()
        select.select_option('business')
        page.evaluate('''() => {window.__topicResolve(new Response(JSON.stringify({topic:'education',engine:'ai',basis:'posts'}),{headers:{'Content-Type':'application/json'}}));window.fetch=window.__topicFetch;}''')
        expect(select).to_have_value('business')
        expect(status).to_contain_text('Выбрано вами')
        # Смена канала очищает выбор и начинает новый независимый анализ.
        source.fill('@next_topic_demo')
        expect(select).to_have_value('education')
        # Недоступность анализа не блокирует отправку и ручной выбор.
        page.unroute('**/api/v1/channel-topics/suggest?*',suggestion)
        page.route('**/api/v1/channel-topics/suggest?*',lambda route:route.fulfill(status=503,json={'detail':'unavailable'}))
        source.fill('@offline_topic_demo')
        expect(status).to_contain_text('Не удалось определить')
        expect(select).to_have_value('')
        expect(page.locator('#order-submit')).to_be_enabled()
        select.select_option('other')
        page.set_viewport_size({'width':320,'height':740})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        select.scroll_into_view_if_needed()
        page.screenshot(path=str(output/'46-channel-topics.png'))
        page.set_viewport_size({'width':390,'height':844})
    finally:
        page.unroute('**/api/v1/channel-topics/suggest?*')
        page.evaluate('if(window.__topicFetch)window.fetch=window.__topicFetch')
        source.fill('')

