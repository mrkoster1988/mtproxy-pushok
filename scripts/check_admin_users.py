"""Мобильный каталог, роли по никнейму и управление бонусами с настоящим API/SQL."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from playwright.sync_api import expect


def check_admin_users(browser,base,output,failures,dsn):
    from app import main as api, wallet_ledger as ledger
    ids={role:uuid4() for role in ('admin','advertiser','operator','new_staff')}
    tokens={};tariff,channel,campaign=uuid4(),uuid4(),uuid4()
    with psycopg.connect(dsn,row_factory=dict_row) as conn:
        for i,(role,uid) in enumerate(ids.items()):
            conn.execute('INSERT INTO users(id,telegram_user_id,role,first_name,username) VALUES(%s,%s,%s,%s,%s)',(uid,99883400+i,'advertiser' if role=='new_staff' else role,'Каталог '+role,'directory_'+role))
            sid=uuid4();conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid));tokens[role]=api._create_session_token(uid,sid)
        conn.execute("INSERT INTO tariffs(id,name,duration_seconds,price_kopecks,stars_price_xtr) VALUES(%s,'Каталог тариф',3600,10000,100)",(tariff,))
        conn.execute("INSERT INTO sponsor_channels(id,owner_user_id,title,telegram_url) VALUES(%s,%s,'Кампания из каталога','https://t.me/directory_campaign')",(channel,ids['advertiser']))
        conn.execute("INSERT INTO campaigns(id,advertiser_user_id,channel_id,tariff_id,tariff_name_snapshot,duration_seconds_snapshot,price_kopecks_snapshot,currency_snapshot) VALUES(%s,%s,%s,%s,'Каталог тариф',3600,10000,'RUB')",(campaign,ids['advertiser'],channel,tariff))
        ledger.post(conn,ids['advertiser'],'BONUS',12345,'manual_credit','directory:'+str(uuid4()),'Тест каталога',actor=ids['admin'])
    contexts=[]
    def launch(role):
        ctx=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce');contexts.append(ctx)
        ctx.add_init_script("sessionStorage.setItem('pushok-session',"+repr(tokens[role])+")")
        p=ctx.new_page();p.on('pageerror',lambda e:failures.append(str(e)));p.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort());p.goto(base)
        expect(p.locator('body')).to_have_attribute('data-cabinet-loaded','true');return p
    def go(p,route):
        p.evaluate('r=>window.cabinet.go(r)',route);expect(p.locator('[data-page='+route.split('/')[0]+']')).to_be_visible()
    def call(p,role,method,path,data=None):
        r=p.request.fetch(base+'/api/v1'+path,method=method,headers={'Authorization':'Bearer '+tokens[role]},data=data);assert r.ok,(r.status,r.text());return r.json()
    try:
        admin=launch('admin');go(admin,'campaigns')
        expect(admin.locator('#campaigns')).to_contain_text('Кампания из каталога')
        admin.get_by_role('button',name='Мои кампании',exact=True).click();expect(admin.locator('#campaigns')).to_contain_text('У вас нет текущих личных кампаний')
        admin.get_by_role('button',name='Все кампании',exact=True).click();expect(admin.locator('#campaigns')).to_contain_text('Кампания из каталога')
        card=admin.locator('.campaign-tile').filter(has_text='Кампания из каталога');card.get_by_role('link',name='Открыть кампанию Кампания из каталога').click();expect(admin.locator('#queue')).to_contain_text('Кампания из каталога')
        go(admin,'profile');admin.get_by_role('link',name='Пользователи',exact=True).click()
        admin.get_by_label('Поиск пользователей',exact=True).fill('@directory_advertiser');admin.locator('.directory-search button[type=submit]').click()
        row=admin.locator('.directory-user');expect(row).to_have_count(1);expect(row).to_contain_text('Последний вход:')
        row.get_by_role('link',name='Открыть профиль пользователя').click();expect(admin.locator('.profile-wallet')).to_contain_text('123,45')
        admin.get_by_role('button',name='Назад',exact=True).click();expect(admin.locator('[data-page=users]')).to_be_visible()
        for width in (320,390):
            admin.set_viewport_size({'width':width,'height':844});assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        for width in (320,390):
            admin.set_viewport_size({'width':width,'height':844})
            for card in admin.locator('.directory-user').all():
                identity=card.locator('.user-summary').bounding_box();badge=card.locator(':scope > .person-role').bounding_box()
                assert badge['x']>=identity['x']+identity['width']+6
                assert abs(badge['y']-identity['y'])<2
                assert card.bounding_box()['height']<160
            assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        for role,color in (('admin','rgb(255, 140, 152)'),('operator','rgb(114, 229, 165)')):
            badge=admin.locator('.directory-user>.person-role[data-role='+role+']').first
            expect(badge).to_be_visible()
            assert badge.evaluate('e=>getComputedStyle(e).color')==color
        admin.screenshot(path=str(output/'54-user-directory.png'),full_page=True)
        operator=launch('operator');go(operator,'user/'+str(ids['advertiser']));expect(operator.locator('[data-page=user] .profile-identity')).to_contain_text('@directory_advertiser');expect(operator.locator('.profile-wallet')).to_have_count(0)
        go(admin,'settings');admin.locator('#settings-system-tab').click();expect(admin.locator('#team-fields')).to_be_enabled()
        admin.get_by_label('Пользователь для назначения роли',exact=True).fill('@directory_new_staff');admin.locator('#team-role').select_option('operator');admin.once('dialog',lambda d:d.accept());admin.locator('#team-form button[type=submit]').click();expect(admin.locator('#team-status')).to_contain_text('Роль сохранена')
        with psycopg.connect(dsn) as conn:assert conn.execute('SELECT role FROM users WHERE id=%s',(ids['new_staff'],)).fetchone()[0]=='operator'
        go(admin,'wallet');admin.locator('[data-wallet-section=gifts]>summary').click()
        admin.get_by_label('Название подарка',exact=True).fill('Подарок по никнейму');admin.get_by_label('Подарок, бонусных ₽',exact=True).fill('50');admin.get_by_label('Только одному пользователю Telegram',exact=True).check();admin.get_by_label('Получатель подарка: @никнейм или ID',exact=True).fill('@directory_advertiser');admin.get_by_label('Действует до (ваше время)',exact=True).fill((datetime.now(timezone.utc)+timedelta(days=10)).strftime('%Y-%m-%dT%H:%M'));admin.get_by_role('button',name='Создать подарочный код',exact=True).click()
        gift=admin.locator('.wallet-gift').filter(has=admin.get_by_role('heading',name='Подарок по никнейму',exact=True));expect(gift).to_contain_text('@directory_advertiser');code=gift.locator('.growth-code').inner_text()
        gift.get_by_role('button',name='Изменить',exact=True).click();edit=gift.locator('.gift-editor');edit.get_by_label('Название подарка',exact=True).fill('Изменённый подарок');edit.get_by_label('Подарок, бонусных ₽',exact=True).fill('75');edit.get_by_role('button',name='Сохранить подарок',exact=True).click()
        gift=admin.locator('.wallet-gift').filter(has=admin.get_by_role('heading',name='Изменённый подарок',exact=True));expect(gift).to_contain_text('75')
        call(admin,'advertiser','POST','/wallet/gifts/redeem',{'code':code})
        go(admin,'wallet');expect(admin.locator('.wallet-gift').filter(has_text='Изменённый подарок')).to_contain_text('активаций 1 из 1')
        for width in (320,390):
            admin.set_viewport_size({'width':width,'height':844});assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        gift=admin.locator('.wallet-gift').filter(has=admin.get_by_role('heading',name='Изменённый подарок',exact=True));gift.evaluate("n=>n.scrollIntoView({block:'center',behavior:'instant'})");gift.screenshot(path=str(output/'55-gift-actions.png'))
        gift.get_by_role('button',name='Изменить',exact=True).click();expect(gift.locator('.gift-editor').get_by_label('Подарок, бонусных ₽',exact=True)).to_be_disabled();gift.get_by_role('button',name='Отмена',exact=True).click()
        admin.once('dialog',lambda d:d.accept());gift.get_by_role('button',name='Удалить',exact=True).click();expect(admin.locator('.wallet-gift').filter(has_text='Изменённый подарок')).to_have_count(0)
        admin.get_by_role('button',name='Удалённые коды',exact=True).click();deleted=admin.locator('.wallet-gift').filter(has_text='Изменённый подарок');deleted.get_by_role('button',name='История активаций',exact=True).click();expect(deleted.locator('.wallet-gift-report')).to_contain_text('начислено 75')
        go(admin,'growth');create=admin.locator('.offer-editor');create.get_by_label('Название',exact=True).fill('Скидка по никнейму');create.get_by_label('Промокод',exact=True).fill('DIRECTORY30');create.get_by_label('Скидка, %',exact=True).fill('30');create.get_by_label('Тариф',exact=True).select_option(str(tariff));create.get_by_label('Персональное предложение',exact=True).check();create.get_by_label('Получатель предложения: @никнейм или ID',exact=True).fill('@directory_advertiser');create.get_by_role('button',name='Создать предложение',exact=True).click()
        offer=admin.locator('.bonus-offer').filter(has_text='Скидка по никнейму');expect(offer).to_contain_text('@directory_advertiser');offer.get_by_role('button',name='Изменить',exact=True).click();editor=offer.locator('.offer-editor');editor.get_by_label('Скидка, %',exact=True).fill('40');editor.get_by_role('button',name='Сохранить предложение',exact=True).click();expect(admin.locator('.bonus-offer').filter(has_text='Скидка по никнейму')).to_contain_text('40%')
        offer=admin.locator('.bonus-offer').filter(has_text='Скидка по никнейму');offer.evaluate("n=>n.scrollIntoView({block:'center',behavior:'instant'})");offer.screenshot(path=str(output/'56-offer-actions.png'))
        admin.once('dialog',lambda d:d.accept());offer.get_by_role('button',name='Удалить',exact=True).click();expect(admin.locator('.bonus-offer').filter(has_text='Скидка по никнейму')).to_have_count(0)
        admin.get_by_role('button',name='Удалённые предложения',exact=True).click();expect(admin.locator('.bonus-offer').filter(has_text='Скидка по никнейму')).to_contain_text('Удалено')
        assert not failures,failures
    finally:
        for ctx in contexts:ctx.close()
