"""Админка ИИ с настоящим API/хранилищем; подменяется только OpenAI."""
from ui_test_helpers import open_settings
import json
from uuid import uuid4
from unittest.mock import patch
from urllib.error import HTTPError
from playwright.sync_api import expect
import psycopg
from app import channel_ai_connection as connection, channel_topic_suggestions as suggest


def check_channel_ai_ui(admin, advertiser, operator, new_page, go, output, dsn):
    key='sk-local-ui-test-only-private-key'
    reply=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'topic':'education','confident':True})}}]}).encode()
    connection._checks.clear();suggest._ai_calls.clear()
    for page in (advertiser,operator):
        statuses=page.evaluate('''async () => {
            const results=[];
            for(const [method,path] of [['GET',''],['POST','/check'],['PUT','']]) {
                const r=await fetch('/api/v1/admin/channel-ai'+path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
                    body:method==='GET'?undefined:JSON.stringify({enabled:false,model:'gpt-4.1-mini',revision:'environment'})});results.push(r.status);
            }return results;
        }''')
        assert statuses==[403,403,403],statuses
        assert page.evaluate("async()=> (await fetch('/api/v1/admin/proxy-provisioning',{headers:{Authorization:'Bearer '+token}})).status")==403
    panel=admin.locator('#channel-ai-settings')
    expect(panel.locator('#channel-ai-key')).to_be_visible()
    expect(panel.locator('.ai-state')).to_have_text('Выключен')
    admin.locator('#channel-ai-key').fill(key)
    admin.locator('#channel-ai-enabled').check()
    admin.locator('#channel-ai-cap-campaign_review').check()
    admin.locator('#channel-ai-cap-launch_diagnostics').check()
    admin.locator('#channel-ai-cap-proxy_provisioning').check()
    admin.evaluate('window.pushokAI.load()')
    expect(admin.locator('#channel-ai-key')).to_have_value(key)
    with patch.object(suggest,'read_url',side_effect=HTTPError('https://api.openai.com',401,'ignored',{},None)):
        admin.locator('#channel-ai-save').click()
        expect(admin.locator('#channel-ai-status')).to_contain_text('отклонил ключ')
        expect(admin.locator('#channel-ai-save')).to_be_enabled()
        assert not connection.current()['enabled']
    with patch.object(suggest,'read_url',return_value=reply):
        admin.locator('#channel-ai-check').click()
        expect(admin.locator('#channel-ai-status')).to_contain_text('Проверка пройдена')
        assert not connection.current()['enabled']
        admin.locator('#channel-ai-save').click()
        expect(panel.locator('.ai-state')).to_have_text('Включён')
        panel.locator('.ai-settings-card > summary').click()
        expect(admin.locator('#channel-ai-key')).to_have_value('')
        expect(admin.locator('#channel-ai-status')).to_contain_text('Подключение проверено')
        assert connection.active()==(key,'gpt-4.1-mini')
        assert connection.active('campaign_review')==(key,'gpt-4.1-mini')
        assert connection.active('launch_diagnostics')==(key,'gpt-4.1-mini')
        assert connection.active('proxy_provisioning')==(key,'gpt-4.1-mini')
        expect(admin.locator('#channel-ai-cap-campaign_review')).to_be_checked()
    assert key not in admin.evaluate('JSON.stringify({...localStorage,...sessionStorage})')
    assert key not in admin.evaluate('document.documentElement.outerHTML')
    for width in (320,390,768,1280):
        admin.set_viewport_size({'width':width,'height':844})
        panel.scroll_into_view_if_needed()
        assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),width
        for name in ('channel-ai-check','channel-ai-save'):
            assert admin.locator('#'+name).bounding_box()['height']>=44
        if width==390:panel.screenshot(path=str(output/'47-channel-ai-settings.png'))
    admin.set_viewport_size({'width':390,'height':844})
    admin.evaluate('window.pushokProvisioning.load(true)')
    provision=admin.locator('#proxy-provisioning-card')
    provision.locator('summary').click()
    admin.locator('#proxy-provisioning-enabled').check()
    admin.locator('#proxy-provisioning-max').fill('2')
    admin.locator('#proxy-provisioning-wait').fill('180')
    admin.locator('#proxy-provisioning-save').click()
    expect(provision.locator('.ai-state')).to_have_text('Включено')
    expect(admin.locator('#proxy-provisioning-wait')).to_have_value('180')
    node_id,job_id=uuid4(),uuid4()
    owner_id=admin.evaluate('me.id')
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO proxy_nodes(id,name,host,port,status) VALUES(%s,'MTProxy · Авто 9443','8.8.4.4',9443,'paused')",(node_id,))
        conn.execute("""INSERT INTO proxy_provisioning_jobs(id,proxy_node_id,owner_id,policy_revision,state,attempts,error_code,retry_at)
            SELECT %s,%s,%s,revision,'failed',5,'telegram_flood_wait',now()+interval '1 hour' FROM proxy_provisioning_settings""",(job_id,node_id,owner_id))
    admin.evaluate('window.pushokProvisioning.load(true)')
    provision.locator('summary').click()
    expect(provision.get_by_role('button',name='Пауза Telegram',exact=True)).to_be_disabled()
    expect(provision).to_contain_text('Повтор не раньше:')
    expect(provision).to_contain_text('После паузы обновите состояние')
    assert admin.evaluate("async id=>(await fetch('/api/v1/admin/proxy-provisioning/'+id+'/retry',{method:'POST',headers:{Authorization:'Bearer '+token}})).status",str(job_id))==409
    for width in (320,390,768,1280):
        admin.set_viewport_size({'width':width,'height':844})
        provision.scroll_into_view_if_needed()
        assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),width
        assert admin.locator('#proxy-provisioning-save').bounding_box()['height']>=44
        if width==390:provision.screenshot(path=str(output/'54-proxy-provisioning.png'))
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE proxy_provisioning_jobs SET retry_at=now()-interval '1 second' WHERE id=%s",(job_id,))
    admin.evaluate('window.pushokProvisioning.load(true)')
    provision.locator('summary').click()
    provision.get_by_role('button',name='Продолжить подготовку',exact=True).click()
    expect(provision).to_contain_text('Подготовка · попыток: 0')
    with psycopg.connect(dsn) as conn:
        assert conn.execute('SELECT state,attempts FROM proxy_provisioning_jobs WHERE id=%s',(job_id,)).fetchone()==('pending',0)
        conn.execute('DELETE FROM proxy_provisioning_jobs WHERE id=%s',(job_id,))
        conn.execute('DELETE FROM proxy_nodes WHERE id=%s',(node_id,))
    admin.locator('#proxy-provisioning-enabled').uncheck()
    admin.locator('#proxy-provisioning-save').click()
    expect(provision.locator('.ai-state')).to_have_text('Выключено')
    admin.set_viewport_size({'width':390,'height':844})
    # Удаление сохранённого ключа не зависит от ошибок в несохранённом черновике.
    admin.locator('#channel-ai-model').fill('')
    admin.once('dialog',lambda dialog:dialog.accept())
    admin.locator('#channel-ai-remove').click()
    expect(panel.locator('.ai-state')).to_have_text('Выключен')
    expect(admin.locator('#channel-ai-status')).to_contain_text('Ключ удалён')
    assert not connection.public()['key_saved']
    # Ответ старой сессии не возвращает настройки после выхода, даже без abort.
    isolated=new_page('admin',390)
    try:
        go(isolated,'settings');isolated.locator('#settings-system-tab').click();open_settings(isolated)
        expect(isolated.locator('#channel-ai-key')).to_be_visible()
        isolated.locator('#channel-ai-key').fill(key)
        isolated.evaluate('''() => {window.__aiFetch=window.fetch;
            window.fetch=(url,options)=>String(url).endsWith('/admin/channel-ai')
              ?new Promise(resolve=>window.__aiResolve=resolve):window.__aiFetch(url,options);
            window.pushokAI.load(true);
        }''')
        isolated.wait_for_function('Boolean(window.__aiResolve)')
        isolated.evaluate('''() => {window.cabinet.logout();window.__aiResolve(new Response(JSON.stringify({enabled:true,key_saved:true,model:'gpt-4.1-mini',revision:'old'}),{headers:{'Content-Type':'application/json'}}));window.fetch=window.__aiFetch;}''')
        expect(isolated.locator('#channel-ai-settings')).to_be_empty()
        expect(isolated.locator('#proxy-provisioning-settings')).to_be_empty()
        assert key not in isolated.evaluate('document.documentElement.outerHTML')
    finally:isolated.context.close()
