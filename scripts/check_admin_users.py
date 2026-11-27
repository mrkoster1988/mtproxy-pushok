"""Мобильный каталог, роли по никнейму и управление бонусами с настоящим API/SQL."""
from ui_test_helpers import open_settings, open_management
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import json
import psycopg
from psycopg.rows import dict_row
from playwright.sync_api import expect


def check_admin_users(browser,base,output,failures,dsn):
    from app import main as api, wallet_ledger as ledger
    ids={role:uuid4() for role in ('admin','advertiser','operator','new_staff')}
    tokens={};tariff,channel,campaign=uuid4(),uuid4(),uuid4()
    with psycopg.connect(dsn,row_factory=dict_row) as conn:
        for i,(role,uid) in enumerate(ids.items()):
            name='Мария-Александра Константинопольская' if role=='new_staff' else 'Каталог '+role
            conn.execute('INSERT INTO users(id,telegram_user_id,role,first_name,username) VALUES(%s,%s,%s,%s,%s)',(uid,998834000000000+i,'advertiser' if role=='new_staff' else role,name,'directory_'+role))
            sid=uuid4();conn.execute("INSERT INTO user_sessions(id,user_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",(sid,uid));tokens[role]=api._create_session_token(uid,sid)
        # Opposite registration and login orders, restricted to this fixture's search below.
        conn.execute("""UPDATE users SET created_at=now()-interval '2 days',last_login_at=clock_timestamp()
                        WHERE id=%s""",(ids['admin'],))
        conn.execute("""UPDATE users SET created_at=clock_timestamp(),last_login_at=now()-interval '1 day'
                        WHERE id=%s""",(ids['new_staff'],))
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
    def check_card_layout(card):
        layout=None
        try:
            expect(card).to_be_visible()
            # The directory can repaint asynchronously: measure one DOM generation in one call.
            layout=card.evaluate("""e=>{
                const measure=node=>{
                    if(!node)return null;
                    const box=node.getBoundingClientRect(),style=getComputedStyle(node);
                    return {...box.toJSON(),text:node.textContent,
                        visible:node.isConnected&&node.getClientRects().length>0&&box.width>0&&box.height>0&&
                            style.display!=='none'&&style.visibility==='visible',
                        textAlign:style.textAlign,scrollWidth:node.scrollWidth,clientWidth:node.clientWidth,
                        lineHeight:parseFloat(style.lineHeight)||parseFloat(style.fontSize)*1.5};
                };
                return {card:measure(e),avatar:measure(e.querySelector('.user-avatar')),
                    name:measure(e.querySelector('.person-name')),badge:measure(e.querySelector(':scope > .person-role')),
                    metadata:measure(e.querySelector('.directory-meta')),id:measure(e.querySelector('.directory-id')),
                    activity:measure(e.querySelector('.directory-activity'))};
            }""")
            for name,node in layout.items():
                assert node is not None,f'User card is missing {name}'
                assert node['visible'],f'User card {name} is not visible: {node}'
            face=layout['avatar']
            assert abs(face['width']-48)<1 and abs(face['height']-48)<1,'User avatar is not 48 by 48 pixels'
            badge,name_box,card_box=layout['badge'],layout['name'],layout['card']
            assert abs(badge['top']-name_box['top'])<=4,'User status is not aligned with the name'
            assert badge['textAlign']=='left','User status text is not left aligned'
            assert badge['left']>=card_box['left'] and badge['right']<=card_box['right'],'User status overflows the card'
            metadata,identity,activity=layout['metadata'],layout['id'],layout['activity']
            assert 'ID ' in identity['text'],'User ID is missing its label'
            assert abs(identity['top']-activity['top'])<=2,'User ID and activity are not on the same row'
            assert abs(activity['right']-metadata['right'])<=1,'User activity is not aligned to the right edge'
            assert identity['right']+4<=activity['left'],'User ID and activity overlap'
            assert identity['height']<=activity['lineHeight']+1,'User ID wraps to multiple lines'
            assert activity['height']<=activity['lineHeight']+1,'User activity wraps to multiple lines'
            for name,node in (('metadata',metadata),('ID',identity),('activity',activity)):
                assert node['scrollWidth']<=node['clientWidth']+1,f'User {name} overflows horizontally'
            return layout
        except Exception:
            # Capture here: the scenario closes its context before the suite's failure screenshots.
            try:
                snapshot=card.evaluate("""e=>{
                    const inspect=node=>{
                        const style=getComputedStyle(node),box=node.getBoundingClientRect();
                        return {tag:node.tagName,className:node.className,hidden:node.hidden,
                            text:node.textContent.trim().slice(0,160),box:box.toJSON(),
                            display:style.display,visibility:style.visibility,minWidth:style.minWidth,
                            flex:style.flex,columns:style.gridTemplateColumns};
                    };
                    const selectors=[':scope > .user-summary','.user-avatar','.person-copy','.person-name',
                        ':scope > .person-role','.directory-meta','.directory-id','.directory-activity'];
                    const nodes=Object.fromEntries(selectors.map(selector=>[selector,
                        [...e.querySelectorAll(selector)].map(inspect)]));
                    const ancestors=[];for(let node=e.parentElement;node;node=node.parentElement){
                        ancestors.push({tag:node.tagName,className:node.className,hidden:node.hidden,
                            display:getComputedStyle(node).display,visibility:getComputedStyle(node).visibility});
                    }
                    return {route:location.hash,viewport:{width:innerWidth,height:innerHeight},card:inspect(e),nodes,ancestors};
                }""")
                snapshot['measuredLayout']=layout
                diagnostic=json.dumps(snapshot,ensure_ascii=False,indent=2)
                (output/'54-user-directory-layout-failure.json').write_text(diagnostic,encoding='utf-8')
                print('User directory layout failure: '+diagnostic)
            except Exception as diagnostic_error:
                print('Could not capture user directory geometry: '+str(diagnostic_error))
            try:
                card.page.screenshot(path=str(output/'54-user-directory-layout-failure.png'),full_page=True)
            except Exception as screenshot_error:
                print('Could not capture user directory screenshot: '+str(screenshot_error))
            raise
    try:
        admin=launch('admin');go(admin,'campaigns')
        expect(admin.locator('#campaigns')).to_contain_text('Кампания из каталога')
        expect(admin.get_by_role('button',name='Мои кампании',exact=True)).to_have_count(0)
        expect(admin.get_by_role('button',name='Все кампании',exact=True)).to_have_count(0)
        card=admin.locator('.campaign-tile').filter(has_text='Кампания из каталога');card.get_by_role('link',name='Открыть кампанию Кампания из каталога').click();expect(admin.locator('#queue')).to_contain_text('Кампания из каталога')
        go(admin,'profile');admin.get_by_role('link',name='Пользователи',exact=True).click()
        sort=admin.get_by_label('Сортировка пользователей',exact=True)
        expect(sort).to_have_value('newest')
        search=admin.get_by_label('Поиск пользователей',exact=True)
        search.fill('directory_');admin.locator('.directory-search button[type=submit]').click()
        first=admin.locator('.user-directory .directory-user').first
        expect(first).to_contain_text('@directory_new_staff')
        expect(first).to_contain_text('Регистрация ·')
        expect(first.locator('.directory-id')).to_have_text('ID 998834000000003')
        expect(first.locator('.directory-activity')).to_contain_text('мск')
        expect(first.locator('.directory-activity')).not_to_contain_text('МСК')
        for width in (320,390,768):
            admin.set_viewport_size({'width':width,'height':844})
            check_card_layout(first)
            assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            admin.screenshot(path=str(output/f'54-user-directory-meta-{width}.png'),full_page=True)
        admin.set_viewport_size({'width':390,'height':844})
        sort.select_option('recent')
        expect(first).to_contain_text('@directory_admin')
        expect(first).to_contain_text('Последний вход:')
        go(admin,'profile');admin.get_by_role('link',name='Пользователи',exact=True).click()
        expect(sort).to_have_value('newest')
        expect(first).to_contain_text('@directory_new_staff')
        expect(first).to_contain_text('Регистрация ·')
        sort.select_option('name')
        admin.get_by_label('Поиск пользователей',exact=True).fill('@directory_advertiser');admin.locator('.directory-search button[type=submit]').click()
        row=admin.locator('.user-directory .directory-user');expect(row).to_have_count(1);expect(row).to_contain_text('Последний вход:')
        row.get_by_role('link',name='Открыть профиль пользователя').click();expect(admin.locator('.profile-wallet')).to_contain_text('123,45')
        admin.get_by_role('button',name='Назад',exact=True).click();expect(admin.locator('[data-page=users]')).to_be_visible()
        expect(sort).to_have_value('name')
        search=admin.get_by_label('Поиск пользователей',exact=True)
        expect(search).to_have_value('@directory_advertiser')
        category=admin.locator('.directory-search').get_by_label('Категория пользователя',exact=True)
        category.select_option('user')
        expect(row).to_have_count(1)
        expect(row.locator(':scope > .person-role')).to_have_text('Пользователь')
        category.select_option('advertiser')
        expect(row).to_have_count(0)
        category.select_option('user')
        search.fill('')
        admin.locator('.directory-search button[type=submit]').click()
        for width in (320,390,768):
            admin.set_viewport_size({'width':width,'height':844})
            for card in admin.locator('.user-directory .directory-user').all():
                layout=check_card_layout(card)
                assert layout['card']['height']<160
            assert admin.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        for role,color in (('admin','rgb(255, 140, 152)'),('operator','rgb(114, 229, 165)')):
            admin.locator('.directory-search').get_by_label('Категория пользователя',exact=True).select_option(role)
            badge=admin.locator('.directory-user>.person-role[data-role='+role+']').first
            expect(badge).to_be_visible()
            assert badge.evaluate('e=>getComputedStyle(e).color')==color
        category.select_option('user')
        expect(admin.locator('.directory-user>.person-role[data-role=user]').first).to_be_visible()
        admin.screenshot(path=str(output/'54-user-directory.png'))
        operator=launch('operator');go(operator,'user/'+str(ids['advertiser']));expect(operator.locator('[data-page=user] .profile-identity .person-handle')).to_have_count(0);expect(operator.locator('.profile-wallet')).to_have_count(0)
        go(admin,'management/payments');open_management(admin,'people');expect(admin.locator('#team-fields')).to_be_enabled()
        admin.get_by_label('Пользователь для назначения роли',exact=True).fill('@directory_new_staff');admin.locator('#team-role').select_option('operator');admin.once('dialog',lambda d:d.accept());admin.locator('#team-form button[type=submit]').click();expect(admin.locator('#team-status')).to_contain_text('Роль сохранена')
        with psycopg.connect(dsn) as conn:assert conn.execute('SELECT role FROM users WHERE id=%s',(ids['new_staff'],)).fetchone()[0]=='operator'
        go(admin,'wallet');admin.locator('[data-wallet-section=gifts]>summary').click()
        admin.get_by_label('Название подарка',exact=True).fill('Подарок по никнейму');admin.get_by_label('Подарок, бонусных ₽',exact=True).fill('50');admin.get_by_label('Только одному пользователю Telegram',exact=True).check();admin.get_by_label('Получатель подарка: @никнейм или ID',exact=True).fill('@directory_advertiser');admin.get_by_label('Действует до (мск)',exact=True).fill((datetime.now(timezone(timedelta(hours=3)))+timedelta(days=10)).strftime('%Y-%m-%dT%H:%M'));admin.get_by_role('button',name='Создать подарочный код',exact=True).click()
        gift=admin.locator('.wallet-gift').filter(has=admin.get_by_role('heading',name='Подарок по никнейму',exact=True));expect(gift).to_contain_text('@directory_advertiser');code=gift.locator('.growth-code').inner_text()
        gift.get_by_role('button',name='Изменить',exact=True).click();edit=gift.locator('.gift-editor');edit.get_by_label('Название подарка',exact=True).fill('Изменённый подарок');edit.get_by_label('Подарок, бонусных ₽',exact=True).fill('75');edit.get_by_role('button',name='Сохранить подарок',exact=True).click()
        gift=admin.locator('.wallet-gift').filter(has=admin.get_by_role('heading',name='Изменённый подарок',exact=True));expect(gift).to_contain_text('75')
        call(admin,'advertiser','POST','/wallet/gifts/redeem',{'code':code})
        go(admin,'wallet');expect(admin.locator('.wallet-gift').filter(has_text='Изменённый подарок')).to_have_count(0);admin.get_by_role('button',name='Архив',exact=True).click();expect(admin.locator('.wallet-gift').filter(has_text='Изменённый подарок')).to_contain_text('активаций 1 из 1')
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
