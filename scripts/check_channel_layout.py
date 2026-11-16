"""Карточки каналов и отдельный экран добавления: реальные API и Chromium."""
import io
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from PIL import Image
from playwright.sync_api import expect


def check_channel_layout(browser,base,output,failures,dsn):
    from app import main as api, channel_logos, channel_directory
    uid,sid,cid=uuid4(),uuid4(),uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO users(id,telegram_user_id,first_name) VALUES(%s,99883901,'Проверка карточек')",(uid,))
        conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid))
        conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'puShok Новости','https://t.me/push_canal')",(cid,uid))
    token=api._create_session_token(uid,sid)
    ctx=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
    ctx.add_init_script("sessionStorage.setItem('pushok-session',"+repr(token)+")")
    page=ctx.new_page();page.on('pageerror',lambda error:failures.append(str(error)))
    page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort())
    # Иллюстрация используется только как тестовый логотип; API остаётся настоящим.
    cat=Image.open(Path(__file__).resolve().parents[1]/'api/web/pushok-pencil.png').convert('RGBA')
    cat.thumbnail((140,140));logo=Image.new('RGB',(160,160),'#153b48');logo.paste(cat,((160-cat.width)//2,(160-cat.height)//2),cat)
    data=io.BytesIO();logo.save(data,format='JPEG')
    try:
        with patch.object(channel_logos,'logo',return_value=data.getvalue()):
            page.goto(base+'/#/channels');expect(page.locator('body')).to_have_attribute('data-cabinet-loaded','true')
            card=page.locator('.channel-tile');expect(card).to_have_count(1)
            expect(card.locator('.channel-avatar img')).to_be_visible()
            expect(page.locator('.channel-add')).to_be_hidden()
            page.screenshot(path=str(output/'68-channels-redesign-390.png'),full_page=True)
            search=page.get_by_role('searchbox',name='Поиск каналов')
            search.fill('@PUSH_CANAL');expect(card).to_have_count(1)
            search.fill('нет такого');expect(card).to_have_count(0)
            page.get_by_role('button',name='Очистить поиск каналов').click();expect(card).to_have_count(1);expect(search).to_be_focused()
            expect(page.get_by_role('button',name='Очистить поиск каналов')).to_be_hidden()
            # Добавление не смешивается со списком; назад и отмена возвращают к нему.
            add=page.locator('[data-page=channels] .page-heading a')
            add.click();expect(page).to_have_url(base+'/#/channels/new')
            url=page.locator('#channel-form [name=telegram_url]');expect(url).to_be_focused();expect(card).to_be_hidden()
            expect(page.locator('[data-page=channels] .campaign-tabs')).to_be_hidden()
            url.fill('@layout_channel');page.get_by_role('button',name='Отмена',exact=True).click()
            expect(page).to_have_url(base+'/#/channels');expect(card).to_be_visible()
            add.click();expect(url).to_have_value('@layout_channel')
            page.locator('.screen-back').click();expect(page).to_have_url(base+'/#/channels');expect(card).to_be_visible()
            add.click()
            with patch.object(channel_directory,'lookup',return_value=dict(title='Новый канал',username='layout_channel',telegram_url='https://t.me/layout_channel')):
                page.locator('#channel-form').get_by_role('button',name='Найти канал',exact=True).click()
                expect(page.locator('#channel-form [name=title]')).to_have_value('Новый канал')
                page.screenshot(path=str(output/'69-channel-add-390.png'),full_page=True)
                page.locator('#channel-form button[type=submit]').click()
                expect(page).to_have_url(base+'/#/channels');expect(card).to_have_count(2)
                expect(page.locator('#notice')).to_contain_text('Канал добавлен.')
            # Кнопка на карточке должна выбрать именно этот канал в заказе.
            card.filter(has_text='Новый канал').get_by_role('link',name='Создать кампанию',exact=True).click()
            expect(page.locator('#order-selected-preview')).to_contain_text('Новый канал')
            page.evaluate("window.cabinet.go('channels')")
            with psycopg.connect(dsn) as conn:conn.execute('UPDATE sponsor_channels SET title=%s WHERE id=%s',('ОченьДлинноеНазваниеКаналаБезПробелов · новости технологий и путешествий',cid))
            page.locator('#refresh').click();expect(card.filter(has_text='ОченьДлинноеНазвание')).to_have_count(1)
            for width in (320,390,768):
                page.set_viewport_size({'width':width,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                for row in card.all():
                    identity=row.locator('.channel-identity').bounding_box();actions=row.locator('.card-tools').bounding_box()
                    assert actions['x']>=identity['x']+identity['width']+5
                    assert row.bounding_box()['height']<180
                    for action in row.locator('.card-tools>button').all():
                        rect=action.bounding_box();assert rect['width']>=30 and rect['height']>=32
                    count=row.locator('.channel-count').bounding_box();create=row.locator('.channel-footer a').bounding_box()
                    assert create['x']>=count['x']+count['width']+5
                page.screenshot(path=str(output/f'70-channels-long-{width}.png'),full_page=True)
            assert not failures,failures
    finally:
        ctx.close()
