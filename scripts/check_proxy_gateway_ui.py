"""Реальные API/SQL: согласие на тему, удаление, ссылка и настройки владельца."""
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch


def check_proxy_gateway_ui(new_page, go, output, dsn):
    import psycopg
    from playwright.sync_api import expect
    from app import proxy_link
    page=new_page('advertiser',390)
    admin=new_page('admin',390)
    try:
        with tempfile.TemporaryDirectory() as tmp, patch.object(proxy_link,'LINK_FILE',Path(tmp)/'proxy-link.json'):
            direct=proxy_link.connection_url('proxy.example.org',8443,'a'*32)
            Path(tmp,'proxy-link.json').write_text(json.dumps({'url':direct}))
            Path(tmp,'proxy-gateway.json').write_text(json.dumps(dict(url=direct.replace('8443','10443'),expires_at=time.time()+120,topic_ports={'technology':10444})))
            go(page,'profile')
            page.locator('#proxy-interest > summary').click()
            expect(page.locator('#proxy-interest-topic')).to_have_value('')
            page.locator('#proxy-interest-topic').select_option('technology')
            page.get_by_role('button',name='Сохранить тему',exact=True).click()
            expect(page.locator('#proxy-interest a')).to_have_attribute('href',direct.replace('8443','10444'))
            expect(page.locator('#proxy-interest a')).to_have_text('Подключиться по интересам')
            go(page,'dashboard')
            expect(page.locator('.dashboard-connect-proxy')).to_have_attribute('href',direct.replace('8443','10444'))
            go(page,'profile')
            page.locator('#proxy-interest > summary').click()
            expect(page.locator('#proxy-interest-topic')).to_have_value('technology')
            page.locator('#proxy-interest').screenshot(path=str(output/'gateway-interests-mobile.png'))
            page.locator('#proxy-interest-topic').select_option('')
            page.get_by_role('button',name='Сохранить тему',exact=True).click()
            expect(page.locator('#proxy-interest a')).to_have_attribute('href',direct.replace('8443','10443'))
            with psycopg.connect(dsn) as conn:
                assert conn.execute('SELECT count(*) FROM user_proxy_interests').fetchone()[0]==0
            go(admin,'management/automation','management')
            host=admin.locator('#proxy-gateway-settings')
            assert host.evaluate("node=>!node.closest('details')")
            admin.locator('#proxy-gateway-card > summary').click()
            expect(admin.locator('#proxy-gateway-enabled')).not_to_be_checked()
            admin.locator('#proxy-gateway-enabled').check()
            admin.locator('#proxy-gateway-interests').check()
            with admin.expect_response(lambda r: r.url.endswith('/admin/proxy-gateway') and r.request.method=='GET'):
                admin.get_by_role('button',name='Сохранить единый вход',exact=True).click()
            expect(admin.locator('#proxy-gateway-status')).to_be_visible()
            expect(admin.locator('#proxy-gateway-enabled')).to_be_checked()
            expect(admin.locator('#proxy-gateway-interests')).to_be_checked()
            expect(admin.locator('#proxy-gateway-card')).to_contain_text('10443–10468')
            admin.locator('#proxy-gateway-card').screenshot(path=str(output/'gateway-settings-mobile.png'))
            assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    finally:
        page.context.close();admin.context.close()
        with psycopg.connect(dsn) as conn:
            conn.execute('UPDATE proxy_gateway_settings SET enabled=false,interests_enabled=false,port=10443')
