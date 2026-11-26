'use strict';
window.pushokVpn = (() => {
  const states = {active:'Активна', expired:'Истекла', revoked:'Отозвана', pending:'Готовится'};
  const staff = () => me?.role === 'admin' || hasDeveloperAccess(me);
  const date = n => n ? new Date(n * 1000).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow',day:'numeric',month:'short',year:'numeric'}) : '—';
  const traffic = n => {
    if (n == null) return '—';
    const units = ['Б','КБ','МБ','ГБ','ТБ'];
    let index = 0;
    while (n >= 1024 && index < units.length - 1) { n /= 1024; index++; }
    return n.toLocaleString('ru-RU',{maximumFractionDigits:1}) + ' ' + units[index];
  };
  let cache = null;
  function reset() { cache = null; }
  function snapshot() {
    const session = token, admin = staff();
    if (!session) return Promise.reject(Error('Войдите в кабинет'));
    if (!cache || cache.session !== session || cache.admin !== admin || Date.now() >= cache.until) {
      const entry = {session,admin,until:Infinity};
      entry.read = api('/vpn/dashboard').then(data => {
        entry.until = Date.now() + 20000;
        return data;
      }).catch(error => { if (cache === entry) reset(); throw error; });
      cache = entry;
    }
    return cache.read;
  }
  function signal(row) {
    if(!row)return null;
    const offline=['expired','revoked'].includes(row.state) || row.online===false;
    const state=offline?'offline':row.online===true?'online':'unknown';
    const description={online:'VPN подключён',offline:'VPN отключён',unknown:'Состояние VPN неизвестно'}[state];
    const mark=el('small',state==='unknown'?'нет данных':'VPN','vpn-signal');
    mark.dataset.signal=state;
    mark.title=description;
    mark.setAttribute('aria-label',description);
    return mark;
  }
  function badge(node,id) {
    if (!node || !id || !token || !staff()) return;
    const session=token;
    snapshot().then(data=>{
      if(session!==token||!staff()||!data.can_manage||node.querySelector('.vpn-signal'))return;
      const mark=signal(data.members?.find(r=>r.id===id));
      if(mark && mark.dataset.signal!=='unknown')node.append(mark);
    }).catch(()=>{});
  }
  function metric(label,value) {
    const cell=el('div');cell.append(el('dt',label),el('dd',value));return cell;
  }
  let renewFrom='vpn';
  function renewLink() {
    const link=el('a',undefined,'ui-button labeled-action vpn-link');link.href='#/vpn/renew';
    link.append(window.pushokViews.icon('wallet'),document.createTextNode('Продлить'));
    link.onclick=()=>{renewFrom=location.hash==='#/profile'?'profile':'vpn';};
    return link;
  }
  const systems={android:'Android',ios:'iPhone / iPad',windows:'Windows',macos:'macOS',linux:'Linux'};
  function localSystem(){
    const tg=window.Telegram?.WebApp?.platform, ua=navigator.userAgent;
    if(tg==='android'||/Android/i.test(ua))return 'android';
    if(tg==='ios'||/iPhone|iPad|iPod/i.test(ua)||(/Mac/i.test(ua)&&navigator.maxTouchPoints>1))return 'ios';
    if(tg==='macos'||/Macintosh|Mac OS/i.test(ua))return 'macos';
    if(/Windows/i.test(ua))return 'windows';
    return /Linux/i.test(ua)?'linux':'';
  }
  const installers = {
    ios:'https://apps.apple.com/app/wireguard/id1441195209',
    macos:'https://apps.apple.com/app/wireguard/id1451685025',
    windows:'https://www.wireguard.com/install/#windows-10-11-2016-2019-2022-2025',
    linux:'https://www.wireguard.com/install/'
  };
  function platformIcon(platform){
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    const shapes={
      android:'M4 15a8 8 0 0 1 16 0ZM7 5l2 3m8-3-2 3M8 17v2m8-2v2',
      ios:'M8 2h8q2 0 2 2v16q0 2-2 2H8q-2 0-2-2V4q0-2 2-2ZM10 5h4m-3 14h2',
      windows:'M3 5l8-1v7H3Zm10-1 8-1v8h-8ZM3 13h8v7l-8-1Zm10 0h8v8l-8-1Z',
      macos:'M5 4h14v12H5ZM2 19h20l-2 1H4Z',
      linux:'M3 4h18v15H3ZM6 8l3 3-3 3m6 0h4',
      unknown:'M2 4h16v12H2Zm6 15h5m3-7h6v9h-6Z'
    };
    svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('class','vpn-peer-os');
    svg.setAttribute('role','img');const title=systems[platform]?'Платформа по загрузке профиля и адресу подключения · '+systems[platform]:'Устройство подключения неизвестно';
    svg.setAttribute('aria-label',title);const tip=document.createElementNS(svg.namespaceURI,'title');tip.textContent=title;
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',shapes[platform]||shapes.unknown);svg.append(tip,path);return svg;
  }
  function installer(platform){
    if(platform==='android'){
      const get=button('Скачать Flow',download);get.className='ui-button labeled-action vpn-link';get.prepend(window.pushokViews.icon('download'));return get;
    }
    const link=el('a',platform?'Скачать WireGuard':'Выбрать приложение','ui-button labeled-action vpn-link');
    link.href=installers[platform]||'https://www.wireguard.com/install/';link.target='_blank';link.rel='noopener noreferrer';
    link.prepend(window.pushokViews.icon('download'));return link;
  }
  function download() {
    const url=new URL('/api/v1/vpn/flow.apk',location.origin).href;
    const app=window.Telegram?.WebApp;
    if(typeof app?.openLink==='function'){
      const android=/^android(?:_x)?$/.test(app.platform)||/Android/i.test(navigator.userAgent);
      try{
        app.openLink(url,android?{try_browser:'chrome'}:{});
      }catch{
        // Older clients can still use a regular download link.
        const fallback=el('a','Скачать APK','ui-button');fallback.href=url;fallback.download='';
        notice('Откройте ссылку для загрузки Pushok Flow.',false,{actions:[fallback]});return;
      }
      if(android)notice('После загрузки откройте APK в браузере или в «Мои файлы → Загрузки».',true);
      return;
    }
    const link=el('a');link.href=url;link.download='';document.body.append(link);link.click();link.remove();
  }
  Object.assign(errors,{
    vpn_order_in_progress:'Магазин предыдущей версии заблокировал изменение из-за заказа. Обновите Shop и повторите.',
    vpn_key_rotation:'Идёт перевыпуск ключа или дополнительного профиля. Дождитесь завершения и обновите карточку.',
    vpn_node_unavailable:'Исходный сервер WireGuard недоступен. Проверьте подключение WGDashboard в Shop.',
    vpn_expiry_schedule_required:'В Shop откройте настройки узла WGDashboard и проверьте управление сроком пиров и часовой пояс.',
    vpn_pending_operation:'Предыдущее изменение ещё в работе. Обновите карточку и нажмите «Повторить изменение».',
    vpn_node_unconfirmed:'Узел WireGuard не подтвердил изменение. Проверьте связь с ним и повторите тот же запрос.',
    vpn_key_missing:'У Shop нет сохранённого ключа для продления. Проверьте основной пир подписки в диагностике WGDashboard.',
    vpn_shorten_past:'Такой срок уже прошёл. Чтобы отключить доступ сейчас, выберите статус «Истекла».',
    vpn_offer_changed:'Условия оплаты изменились. Обновите тарифы.',vpn_order_missing:'Заказ не найден.',
    vpn_test_store:'Платные заказы доступны в рабочем боте.',vpn_shop_unavailable:'Магазин не ответил. Попробуйте ещё раз.',
    cabinet_stars_disabled:'Оплата Stars для VPN отключена в настройках.',invoice_unavailable:'Telegram не выдал счёт. Повторите открытие этого заказа.',
    telegram_bot_token_not_configured:'Бот для приёма Stars пока не настроен.',vpn_shop_integration:'Магазин не подтвердил связь с кабинетом.',
    vpn_account_conflict:'Этот аккаунт Telegram уже привязан к другому профилю. Обратитесь в поддержку.',
    vpn_restore_elapsed:'Срок прежней попытки восстановления истёк. Обновите карточку и выберите новый срок.',
    vpn_changed:'Подписка изменилась. Обновите карточку.',
    vpn_profiles_in_use:'Лимит меньше числа действующих дополнительных профилей.',
    vpn_subscription_missing:'Эта подписка больше недоступна.',
    vpn_change_unconfirmed:'Магазин не подтвердил изменение. Обновите карточку и повторите.',
    vpn_change_conflict:'Операция недоступна для текущего состояния подписки. Проверьте срок и незавершённые операции.',
    vpn_policy_changed:'Настройки уже изменены. Обновите раздел.',vpn_policy_rejected:'Проверьте условия выдачи и актуальность настроек.',
    vpn_days_range:'Выберите от 1 до 365 дней.',vpn_limit_range:'Выберите от 0 до 20 дополнительных профилей.'
  });
  let disclosureId=0;
  function controls(row,subject,reload) {
    const panel=el('div',undefined,'vpn-controls');
    panel.id='vpn-edit-'+(++disclosureId);
    const toggle=el('button',undefined,'vpn-pencil');
    toggle.type='button';toggle.append(window.pushokViews.icon('edit'));
    toggle.title='Редактировать подписку';toggle.setAttribute('aria-label',toggle.title);
    toggle.setAttribute('aria-controls',panel.id);

    const form = el('form',undefined,'vpn-edit'), op = el('select'), qty = el('input');
    const actionLabel = el('label','Действие'), valueLabel = el('label');
    const caption = el('span'), feedback = el('small',undefined,'muted');
    feedback.setAttribute('role','status');
    const options = [['limit','Изменить лимит профилей']];
    if (!row.is_premium&&['active','expired'].includes(row.state)) options.unshift(['extend','Добавить дни']);
    if (!row.is_premium&&row.state === 'active') options.push(['shorten','Сократить срок']);
    if (row.state === 'pending') options.push(['retry','Повторить выдачу']);
    if (row.state==='revoked') options.unshift(['restore','Выдать новый доступ']);
    else if (['active','expired'].includes(row.state)) options.push(['revoke','Отозвать доступ']);
    if(row.state==='active')options.push(['expire','Завершить срок сейчас']);
    const statusLabel=el('label','Новый статус'),statusSelect=el('select');
    const prompt=el('option','Выберите статус');prompt.value='';prompt.disabled=true;statusSelect.append(prompt);
    const transitions={
      active:[['expire','Истекла'],['revoke','Отозвана']],
      expired:[['extend','Активна'],['revoke','Отозвана']],
      revoked:[['restore','Активна']],
      pending:[['retry','Активна · повторить выдачу']]
    };
    for(const [key,label] of transitions[row.state]||[]){const item=el('option',label);item.value=key;statusSelect.append(item);}
    statusLabel.append(statusSelect);
    const consentLabel=el('label',undefined,'vpn-status-consent'),consent=el('input'),consentText=el('span');
    consent.type='checkbox';consentLabel.append(consent,consentText);
    const refresh=button('Обновить карточку',reload);refresh.hidden=true;
    let mode='',sending=false;
    const assignment=window.pushokVpnPlans.assignment(row,subject,reload);
    const cancel=el('button','Отмена','labeled-action');cancel.type='button';
    const statusButton=el('button',states[row.state]||'Уточняется','badge campaign-current');
    statusButton.type='button';statusButton.dataset.subscription=row.state;statusButton.title='Изменить статус подписки';
    statusButton.setAttribute('aria-label','Изменить статус подписки: '+statusButton.textContent);statusButton.setAttribute('aria-controls',panel.id);
    const pending = row.pending_change;
    for (const [key,label] of options) { const item = el('option',label); item.value = key; op.append(item); }
    actionLabel.append(op); valueLabel.append(caption,qty);
    const submit = el('button','Применить','labeled-action'); submit.type = 'submit';
    qty.type = 'number'; qty.step = '1'; qty.inputMode = 'numeric';
    function fields() {
      const limit = op.value === 'limit';
      const ending=['expire','revoke'].includes(op.value);
      consentLabel.hidden=!ending;consent.checked=false;consent.required=ending;
      consentText.textContent=op.value==='revoke'?'Отозвать ключи основного и дополнительных профилей':'Завершить доступ и отключить пиры сейчас';
      valueLabel.hidden = ending||op.value==='retry'||!op.value; qty.required = !valueLabel.hidden;
      qty.min = limit?'0':'1'; qty.max = limit?'20':'365'; qty.value = limit?String(row.device_limit ?? 0):'1';
      caption.textContent = limit?'Дополнительных профилей':'Количество дней';
    }
    function open(next){
      if(sending)return;
      mode=next===mode?'':next;
      if(pending&&mode&&mode!=='tariff')mode='pending';
      panel.hidden=!mode;panel.dataset.mode=mode;
      form.hidden=mode==='tariff';assignment.hidden=mode!=='tariff';assignment.open=mode==='tariff';
      const isStatus=mode==='status';
      statusLabel.hidden=!isStatus;actionLabel.hidden=mode!=='settings';
      statusSelect.required=isStatus;op.disabled=!!pending;
      statusSelect.value='';op.selectedIndex=isStatus?-1:0;
      if(mode==='limit'||mode==='revoke')op.value=mode;
      if(pending)op.value=pending.action;
      fields();if(pending){qty.value=pending.value;qty.disabled=true;}
      submit.textContent=pending?'Повторить изменение':mode==='revoke'?'Подтвердить отзыв':'Сохранить';
      cancel.hidden=mode!=='revoke';feedback.textContent='';
      toggle.setAttribute('aria-expanded',String(mode==='settings'||mode==='pending'));
      statusButton.setAttribute('aria-expanded',String(isStatus));
    }
    open('');
    toggle.onclick=()=>open(pending?'pending':'settings');
    statusButton.onclick=()=>open(pending?'pending':'status');
    cancel.onclick=()=>open('');
    op.onchange=fields;statusSelect.onchange=()=>{op.value=statusSelect.value;fields();};
    if (pending) {
      op.value = pending.action; fields(); qty.value = pending.value;
      op.disabled = qty.disabled = true; submit.textContent = 'Повторить изменение';
    }
    let intent;
    form.onsubmit = async event => {
      event.preventDefault();
      if (sending || !op.value || !form.reportValidity()) return;
      const session = token, action = op.value, value = ['retry','revoke','expire'].includes(action)?0:Number(qty.value);
      const before = action === 'limit'?row.device_limit:row.expires_at || 0;
      if (!intent || intent.action !== action || intent.value !== value) {
        intent = {request_id:crypto.randomUUID(),action,value,before,subscription_id:row.subscription_id,...pending};
      }
      sending = true; refresh.hidden=true; toggle.disabled=statusButton.disabled=statusSelect.disabled=submit.disabled=op.disabled=qty.disabled=true; feedback.textContent = 'Сохраняем…';
      try {
        await api('/vpn/users/'+encodeURIComponent(subject)+'/management',{method:'POST',body:intent});
        if (session !== token) return;
        reset(); notice(action === 'retry'?'Заявка повторно поставлена в очередь.':'Изменения сохранены.',true);
        await reload();
      } catch (error) { if (session === token){feedback.textContent=error.orderId
        ? 'Магазин выдаёт доступ по заказу № '+error.orderId+'. Когда выдача завершится, обновите карточку.'
        : error.message;refresh.hidden=false;} }
      finally { sending=submit.disabled=toggle.disabled=statusButton.disabled=statusSelect.disabled=false; op.disabled = qty.disabled = !!pending; }
    };
    form.append(statusLabel,actionLabel,valueLabel,consentLabel,submit,cancel,feedback,refresh);panel.append(form,assignment);return {toggle,panel,statusButton,open};
  }
  let historyFrom='vpn';
  function card(row,subject,name,own,admin,reload,profileLink=false) {
    const tile=el('article',undefined,'campaign-tile vpn-card'),head=el('div',undefined,'tile-heading');
    const avatar=profileLink?window.pushokViews.userAvatar(subject,name):el('span',undefined,'ui-avatar vpn-avatar');
    if(!profileLink)avatar.append(window.pushokViews.icon('server'));
    const copy=el('div'),title=el(profileLink?'a':'h2',name,'tile-title');
    if(profileLink){title.href='#/user/'+encodeURIComponent(subject);title.onclick=()=>window.pushokProfiles.setBackRoute('vpn');}
    const identity=el('div',undefined,'vpn-identity');
    identity.append(title);
    if(row){
      const marks=el('span',undefined,'vpn-indicators');
      if(row.phone_verified)marks.append(window.pushokVerification.badge());
      const dot=el('span','·','vpn-separator');dot.setAttribute('aria-hidden','true');
      marks.append(dot,signal(row));identity.append(marks);
    }
    copy.append(identity);
    if(profileLink&&row?.username)copy.append(el('small','@'+row.username,'muted'));
    head.append(avatar,copy);
    const edit=admin&&row?.subscription_id?controls(row,subject,reload):null;
    if(row){
      const stack=el('span',undefined,'vpn-status-stack');
      const state=edit?.statusButton||el('span',states[row.state]||'Уточняется','badge campaign-current');
      state.dataset.subscription=row.state;
      if(row.is_premium)tile.classList.add('vpn-premium');
      stack.append(state);
      if(edit){
        stack.append(edit.toggle);
        if(['active','expired'].includes(row.state)&&!row.pending_change){
          const revoke=el('button',undefined,'vpn-revoke labeled-action');
          revoke.type='button';revoke.title='Отозвать подписку';revoke.setAttribute('aria-label',revoke.title);
          revoke.append(window.pushokViews.icon('close'));revoke.onclick=()=>edit.open('revoke');stack.append(revoke);
        }
      }
      head.append(stack);
    }
    tile.append(head);
    const details=el('details',undefined,'vpn-details'),summary=el('summary','Подробности');
    details.append(summary);
    const body=row?details:tile;
    if(row) {
      const dates=el('div',undefined,'vpn-dates tile-period');
      const terms=el('div',undefined,'vpn-terms-line');
      const tariff=el(edit?'button':'span',row.tariff_name||'Тариф не указан','vpn-tariff labeled-action');
      if(row.is_premium)tariff.classList.add('vpn-premium-label');
      if(edit){tariff.type='button';tariff.title='Изменить тариф';tariff.onclick=()=>edit.open('tariff');}
      terms.append(tariff,el('span','·','vpn-separator'),el('span',row.is_premium?'Бессрочно':row.expires_at?'До '+date(row.expires_at):'Срок уточняется'));
      dates.append(terms);
      if(row.last_seen){
        const stamp=new Date(row.last_seen*1000);
        const seen=el('time',stamp.toLocaleString('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'})+' МСК','vpn-seen');
        seen.dateTime=stamp.toISOString();seen.title='Последний отклик';seen.setAttribute('aria-label','Последний отклик '+seen.textContent);
        dates.append(seen);
      }
      const envelope=el('a',undefined,'vpn-mail');
      const delivery=row.plan_notice?.delivery;
      envelope.dataset.delivery=delivery||'none';
      envelope.title='История сообщений'+(delivery?' · '+window.pushokVpnHistory.status(delivery):'');
      envelope.setAttribute('aria-label',envelope.title);
      envelope.href='#/vpn/messages/'+encodeURIComponent(subject);
      envelope.onclick=()=>{historyFrom=location.hash.slice(2)||'vpn';};
      envelope.append(window.pushokViews.icon('mail'));dates.append(envelope);tile.append(dates);
      const tools=el('div',undefined,'vpn-summary-tools');
      const limit=el(edit?'button':'span','Доп. профили · '+(row.device_limit??'—'),'vpn-limit labeled-action');
      if(edit){limit.type='button';limit.title='Изменить лимит профилей';limit.onclick=()=>edit.open(row.pending_change?'pending':'limit');}
      tile.append(details,tools);tools.append(limit);
      const facts=el('dl',undefined,'tile-meta campaign-tile-facts vpn-facts');
      facts.append(metric('Получено',traffic(row.received)),metric('Отправлено',traffic(row.sent)));
      body.append(facts);
      if(admin){
        const addresses=el('dl',undefined,'vpn-addresses'),ips=Array.isArray(row.tunnel_ips)?row.tunnel_ips:[];
        let place=row.country_code;
        if(place){try{place=new Intl.DisplayNames(['ru'],{type:'region'}).of(place);}catch{}}
        const endpoint=metric('Последний IP подключения',row.endpoint_ip||'—');
        if(row.endpoint_ip)endpoint.querySelector('dd').prepend(platformIcon(row.endpoint_platform));
        addresses.append(endpoint,
          metric('Геолокация по IP',row.country_code==='RU'?(row.city||'—'):([place,row.city].filter(Boolean).join(', ')||'—')),metric('IP в туннеле',ips.join(', ')||'—'));
        body.append(addresses);
      }
      if(row.state==='revoked')body.append(el('p','Доступ отозван'+(row.revoked_at?' '+date(row.revoked_at):'')+
        '. Прежний ключ отключён. Для повторного подключения нужен новый профиль.','vpn-revoked-note'));
    } else tile.append(el('p','У аккаунта пока нет VPN-подписки.','tile-period'));
    if(own){
      const actions=el('div',undefined,'vpn-links'),get=installer(localSystem());
      if(!row?.is_premium)actions.append(renewLink());actions.append(get);body.append(actions);
      const widget=el('a','Моя подписка в Telegram','ui-button labeled-action');
      widget.href='https://t.me/mtproxy_pushok_bot?start=subscription';widget.target='_blank';widget.rel='noopener noreferrer';body.append(widget);
    }
    if(admin){
      if(edit)tile.append(edit.panel);
      else tile.append(el('small',row?'Параметры управления пока недоступны. Обновите сведения.':'Изменение срока и лимита станет доступно после выдачи подписки.','muted'));
    }
    return tile;
  }
  function usage(row,period){
    const values=period==='all'?[row.received,row.sent]:row.periods?.[period];
    return values?.length===2&&values.every(Number.isFinite)?values:null;
  }
  function usageSummary(rows,personal=false){
    const box=el('section',undefined,'vpn-usage'),head=el('div',undefined,'vpn-usage-head'),period=el('select');
    period.setAttribute('aria-label','Период расхода');
    for(const [value,label] of [['all','За всё время'],['today','Сегодня'],['week','7 дней'],['month','30 дней']]){
      const option=el('option',label);option.value=value;period.append(option);
    }
    head.append(el('h2',personal?'Расход трафика':'Трафик всех подписок'),period);
    const facts=el('dl',undefined,'vpn-usage-facts'),note=el('small',undefined,'muted');
    const render=()=>{
      const valid=rows.map(r=>usage(r,period.value)).filter(Boolean);
      const rx=valid.reduce((sum,p)=>sum+p[0],0),tx=valid.reduce((sum,p)=>sum+p[1],0);
      facts.replaceChildren(metric('Всего',traffic(valid.length?rx+tx:null)),metric('Получено',traffic(valid.length?rx:null)),metric('Отправлено',traffic(valid.length?tx:null)));
      note.textContent=period.value==='all'?'По счётчикам WireGuard':'Прирост счётчиков с начала наблюдения · МСК';
      if(valid.length<rows.length)note.textContent+=' · Есть профили без данных';
    };
    period.onchange=render;box.append(head,facts,note);render();return box;
  }
  function renderDirectory(list,members,reload){
    const search=el('input'),sort=el('select'),bar=el('div',undefined,'vpn-directory-tools'),results=el('div',undefined,'vpn-results');
    search.type='search';search.placeholder='Имя, ник или ID';search.setAttribute('aria-label','Поиск подписки');
    sort.setAttribute('aria-label','Сортировка подписок');
    for(const [key,label] of [['newest','Сначала новые'],['recent','По активности'],['traffic','По расходу'],['expires','По сроку'],['name','По имени']]){
      const o=el('option',label);o.value=key;sort.append(o);
    }
    const used=r=>{const v=usage(r,'all');return v?v[0]+v[1]:-1;};
    const render=()=>{
      const q=search.value.trim().toLocaleLowerCase('ru').replace(/^@/,'');
      const found=members.filter(r=>[r.name,r.username,r.telegram_id,r.id].some(v=>String(v??'').toLocaleLowerCase('ru').includes(q)));
      const alpha=(a,b)=>a.name.localeCompare(b.name,'ru')||a.id.localeCompare(b.id);
      found.sort((a,b)=>(sort.value==='newest'?(b.registered_at||0)-(a.registered_at||0):sort.value==='traffic'?used(b)-used(a):sort.value==='expires'?(a.expires_at||Infinity)-(b.expires_at||Infinity):sort.value==='recent'?(b.last_seen||0)-(a.last_seen||0):0)||alpha(a,b));
      results.replaceChildren(...found.map(r=>card(r,r.id,r.name,false,true,reload,true)));
      if(!found.length)results.append(el('p',q?'Подписки не найдены.':'Других подписок пока нет.','muted'));
    };
    search.oninput=sort.onchange=render;bar.append(search,sort);list.append(bar,results);render();
  }
  async function show(target,subject,directory) {
    const own=subject===me?.id,session=token;
    if(!own&&!staff())return;
    const host=el('div',undefined,'vpn-stack'),busy=el('p','Загрузка подписки…','muted');
    host.append(busy);target.replaceChildren(host);
    const reload=()=>{reset();return show(target,subject,directory);};
    try{
      const data=await snapshot();
      if(!host.isConnected||session!==token)return;
      const admin=staff()&&data.can_manage===true, selected=admin?data.members?.find(r=>r.id===subject):null;
      const row=selected||(own?data.personal:null);
      host.replaceChildren();
      if(!admin||!directory)host.append(card(row,subject,own&&directory?'Моя подписка':'VPN · Pushok Flow',own,admin,reload));
      if(directory&&admin){
        const settings=el('a','Настройки VPN','ui-button');settings.href='#/management/vpn';
        host.prepend(settings,usageSummary((data.members||[]).filter(r=>r.id!==subject)));
      }else if(row)host.append(usageSummary([row],true));
      if(admin&&(directory||row)){
        const credit=el('a','Геолокация по IP · DB-IP','muted');credit.href='https://db-ip.com';credit.target='_blank';credit.rel='noopener noreferrer';host.append(credit);
      }
      if(admin&&directory){
        const list=el('section',undefined,'vpn-directory'),heading=el('h2','Управление VPN');
        const members=(data.members||[]).filter(r=>r.id!==subject);
        heading.append(el('small',String(members.length),'vpn-count'));list.append(heading);
        renderDirectory(list,members,reload);
        host.append(list);
      }
    }catch(e){
      if(host.isConnected&&session===token){
        const retry=button('Обновить сведения',reload);
        host.replaceChildren(el('p','Не удалось загрузить сведения о подписке.','muted'),retry);
        if(own&&(!directory||!staff()))host.append(renewLink());
      }
    }
  }
  function profile(target,id) {
    if (id !== me?.id && !staff()) return;
    const block = el('div',undefined,'vpn-profile'); target.append(block); void show(block,id,false);
  }
  return {download,load:(target,section)=>{if(section==='messages')return window.pushokVpnHistory.show(target,location.hash.split('/')[3]);if(section==='renew')return window.pushokVpnRenew.show(target);reset();return show(target,me?.id,true);},historyBack:()=>historyFrom,renewBack:()=>renewFrom,badge,profile,reset};
})();
