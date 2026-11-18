"""Помощник в настоящей карточке модерации: API/SQL, черновик, роли, mobile."""
import json
from uuid import uuid4
from unittest.mock import patch
from urllib.error import URLError
import psycopg
from playwright.sync_api import expect
from app import channel_ai_connection as settings, campaign_assistant as assistant, channel_topic_suggestions as topics, control


def check_campaign_assistant_ui(new_page, go, output, dsn):
    old=control.read(settings.FILE)
    config=dict(enabled=True,key='sk-assistant-local-only',model='gpt-4.1-mini',revision='assistant-ui',capabilities=['campaign_review','launch_diagnostics'])
    control.write(settings.FILE,config)
    page=new_page('operator',390)
    assistant._requests.clear();topics._ai_calls.clear()
    try:
        uid=page.evaluate('me.id');tariff,channel,cid=[uuid4() for _ in range(3)]
        with psycopg.connect(dsn) as conn:
            conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,currency) VALUES(%s,'ИИ тест',3600,30000,'RUB')",(tariff,))
            conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,telegram_url,title) VALUES(%s,%s,%s,'Уроки Excel')",(channel,uid,'https://t.me/assistant_demo_'+uuid4().hex[:6]))
            conn.execute("""INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot,moderation_status)
                VALUES(%s,%s,%s,%s,'ИИ тест',3600,30000,'RUB','pending')""",(cid,uid,channel,tariff))
        page.evaluate('refresh()');go(page,'moderation/'+str(cid))
        panel=page.locator('#queue .campaign-assistant');expect(panel).to_have_attribute('open','')
        expect(panel.get_by_role('button',name='Разобрать заявку',exact=True)).to_be_visible()
        # Открытие панели само не вызывает платный анализ.
        response={'summary':'<img src=x onerror="window.aiInjected=true"> Нужна проверка публикаций.',
                  'checks':['Проверьте описание канала.'],'next_steps':['Выберите решение в форме.'],
                  'advertiser_message':'Уточните, пожалуйста, описание канала перед проверкой.',
                  'content_review':{'assessment':'flagged','evidence':[{'source':'post_1','quote':'18+'}]}}
        post='Описание: <img src=x onerror="window.aiInjected=true"> Материалы 18+, проверьте контекст.'
        raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(response)}}]}).encode()
        with patch.object(topics,'read_url',return_value=raw) as transport,patch.object(topics,'public_posts',return_value=[post]):
            transport.assert_not_called()
            panel.get_by_role('button',name='Разобрать заявку',exact=True).click()
            expect(panel.locator('.assistant-output')).to_contain_text('Рекомендации ИИ')
            assert transport.call_count==1
            assert page.evaluate('window.aiInjected') is None
            expect(panel.locator('img')).to_have_count(0)
            expect(panel.locator('.assistant-content-review')).to_contain_text('Нужна проверка 18+')
            expect(panel.locator('.assistant-content-review')).to_contain_text('Фото и видео не анализировались')
            panel.get_by_text('На чём основан вывод',exact=True).click()
            expect(panel.locator('blockquote')).to_contain_text('<img src=x')
            expect(panel.locator('img')).to_have_count(0)
            panel.get_by_role('button',name='Вставить в комментарий',exact=True).click()
            comment=page.get_by_label('Комментарий рекламодателю',exact=True)
            expect(comment).to_have_value(response['advertiser_message'])
            expect(panel).to_contain_text('Он ещё не отправлен.')
            with psycopg.connect(dsn) as conn:
                assert conn.execute('SELECT moderation_status,moderator_comment FROM campaigns WHERE id=%s',(cid,)).fetchone()==('pending',None)
                assert conn.execute("SELECT count(*) FROM audit_events WHERE campaign_id=%s AND event_type='campaign_ai_analyzed'",(cid,)).fetchone()[0]==1
            for width in (320,390,768,1280):
                page.set_viewport_size({'width':width,'height':1000});panel.scroll_into_view_if_needed()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),width
                draft=panel.get_by_label('Черновик для рекламодателя',exact=True).bounding_box()
                copy=panel.get_by_role('button',name='Скопировать черновик',exact=True).bounding_box()
                paste=panel.get_by_role('button',name='Вставить в комментарий',exact=True).bounding_box()
                assert copy['x']>=draft['x']+draft['width']+7
                assert paste['x']>=copy['x']+copy['width']+7
                assert abs(draft['y']+draft['height']-copy['y']-copy['height'])<2
                for button in panel.locator('button').all():
                    box=button.bounding_box()
                    assert box['height']>=44 and box['width']>=44,(width,button.get_attribute('aria-label'),box)
                if width==390:panel.screenshot(path=str(output/'48-campaign-assistant.png'))
        # Без публикаций не показываем «признаки не выявлены», даже если модель так ответила.
        clean=response|{'content_review':{'assessment':'not_detected','evidence':[]}}
        raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(clean)}}]}).encode()
        with patch.object(topics,'read_url',return_value=raw),patch.object(topics,'public_posts',return_value=[]):
            panel.get_by_role('button',name='Разобрать заявку',exact=True).click()
            expect(panel.locator('.assistant-content-review')).to_contain_text('Недостаточно данных для вывода')
            expect(panel.locator('.assistant-content-review')).not_to_contain_text('Признаки 18+ не выявлены')
        with patch.object(topics,'read_url',return_value=raw),patch.object(topics,'public_posts',return_value=['Обучение: сводные таблицы Excel и формулы.']):
            panel.get_by_role('button',name='Разобрать заявку',exact=True).click()
            expect(panel.locator('.assistant-content-review')).to_contain_text('Признаки 18+ не выявлены')
            expect(panel.locator('.assistant-content-review')).to_contain_text('не распространяется на остальные публикации')
        with patch.object(topics,'read_url',side_effect=URLError('private-secret')),patch.object(topics,'public_posts',return_value=['Новости о маркировке 18+, без взрослых материалов.']):
            panel.get_by_role('button',name='Разобрать заявку',exact=True).click()
            expect(panel.locator('.assistant-content-review')).to_contain_text('Проверка маркеров')
            expect(panel.locator('.assistant-content-review')).to_contain_text('проверьте контекст')
        with patch.object(topics,'read_url',side_effect=URLError('private-secret')):
            panel.get_by_role('button',name='Проверить запуск',exact=True).click()
            expect(panel.locator('.assistant-output')).to_contain_text('Проверки без ИИ')
            expect(panel.locator('.assistant-output')).not_to_contain_text('private-secret')
            expect(panel.locator('.assistant-content-review')).to_have_count(0)
        # Поздний ответ после выхода игнорируется, даже если fetch не поддержал abort.
        page.evaluate('''() => {window.__assistantFetch=window.fetch;
            window.fetch=(url,options)=>String(url).endsWith('/ai-analysis')
              ?new Promise(resolve=>window.__assistantResolve=resolve):window.__assistantFetch(url,options);
        }''')
        panel.get_by_role('button',name='Разобрать заявку',exact=True).click()
        page.wait_for_function('Boolean(window.__assistantResolve)')
        # Таймер обновления не должен удалять карточку и отменять незавершённый анализ.
        assert page.evaluate('''async () => {const card=document.querySelector('#queue .campaign-assistant');
            document.querySelectorAll('details[open]:not(.assistant-open)').forEach(d=>d.open=false);
            await refreshStatuses();return window.pushokAssistant.isBusy() && card.isConnected
              && card===document.querySelector('#queue .campaign-assistant');}''')
        page.evaluate('''() => {window.cabinet.logout();window.__assistantResolve(new Response(JSON.stringify({engine:'ai',advice:{summary:'LATE-PRIVATE-RESULT',checks:[],next_steps:[],advertiser_message:''},facts:[]} ),{headers:{'Content-Type':'application/json'}}));window.fetch=window.__assistantFetch;}''')
        expect(page.locator('#queue')).to_be_empty()
        assert page.evaluate('me') is None
        assert 'LATE-PRIVATE-RESULT' not in page.locator('body').inner_text()
        with psycopg.connect(dsn) as conn:
            assert conn.execute('SELECT moderation_status FROM campaigns WHERE id=%s',(cid,)).fetchone()[0]=='pending'
        print('PASS: campaign assistant — real API, grounded 18+ review, missing texts, drafts, no mutations, fallback, logout, responsive layout')
    finally:
        page.context.close()
        if old is None:(control.root()/settings.FILE).unlink(missing_ok=True)
        else:control.write(settings.FILE,old)
