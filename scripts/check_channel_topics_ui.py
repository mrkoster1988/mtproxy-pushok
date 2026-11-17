"""Тематика в настоящей форме; подменяется только ответ внешнего анализа."""
from playwright.sync_api import expect


def check_channel_topics_ui(page, output):
    source=page.locator('#order-channel-url')
    select=page.locator('#order-topic')
    status=page.locator('#order-topic-status')
    detect=page.locator('#order-topic-detect')
    def choose(value):
        label=select.locator('option[value="'+value+'"]').text_content()
        page.get_by_role('button',name='Тематика канала',exact=True).click()
        dialog=page.get_by_role('dialog',name='Тематика канала',exact=True)
        dialog.get_by_role('searchbox').fill(label)
        dialog.get_by_role('option',name=label,exact=True).click()

    def suggestion(route):
        adult='onlysexstickers' in route.request.url
        route.fulfill(json={'topic':'adult' if adult else 'education','label':'18+ · Для взрослых' if adult else 'Образование',
                            'engine':'rules','basis':'metadata' if adult else 'posts','posts_count':0 if adult else 4})

    page.route('**/api/v1/channel-topics/suggest?*',suggestion)
    try:
        # Загрузка скрытой формы не должна запускать платные запросы ИИ.
        page.evaluate('''() => {
            document.getElementById('campaign-form').hidden=true;
            document.getElementById('order-channel-url').value='@hidden_topic_demo';
            window.pushokOrder.summary();
            document.getElementById('campaign-form').dispatchEvent(new CustomEvent('pushok:channel-verified',{detail:{telegram_url:'https://t.me/hidden_topic_demo'}}));
        }''')
        expect(status).not_to_have_attribute('aria-busy','true')
        page.evaluate("document.getElementById('campaign-form').hidden=false")
        page.evaluate('window.pushokOrder.summary()')
        expect(select).to_have_value('education')
        source.fill('@topic_demo')
        expect(select).to_have_value('education')
        expect(status).to_contain_text('по публичным публикациям')
        expect(page.locator('#order-summary')).to_contain_text('Образование')
        choose('health')
        page.evaluate('window.pushokOrder.summary()')
        expect(select).to_have_value('health')
        choose('')
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
        # Даже повторный выбор пустого значения — явное решение пользователя.
        choose('')
        page.evaluate('''() => {window.__topicResolve(new Response(JSON.stringify({topic:'education',engine:'ai',basis:'posts'}),{headers:{'Content-Type':'application/json'}}));}''')
        expect(select).to_have_value('')
        expect(status).to_contain_text('не указана')
        detect.click()
        choose('business')
        page.evaluate('''() => {window.__topicResolve(new Response(JSON.stringify({topic:'education',engine:'ai',basis:'posts'}),{headers:{'Content-Type':'application/json'}}));window.fetch=window.__topicFetch;}''')
        expect(select).to_have_value('business')
        expect(status).to_contain_text('Выбрано вами')
        source.fill('@onlysexstickers')
        expect(select).to_have_value('adult')
        expect(status).to_contain_text('18+ · Для взрослых')
        expect(page.locator('#order-summary')).to_contain_text('18+ · Для взрослых')
        choose('health')
        page.evaluate('window.pushokOrder.summary()')
        expect(select).to_have_value('health')
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
        choose('other')
        page.set_viewport_size({'width':320,'height':740})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.get_by_role('button',name='Тематика канала',exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(output/'46-channel-topics.png'))
        page.set_viewport_size({'width':390,'height':844})
    finally:
        page.unroute('**/api/v1/channel-topics/suggest?*')
        page.evaluate('if(window.__topicFetch)window.fetch=window.__topicFetch')
        source.fill('')

