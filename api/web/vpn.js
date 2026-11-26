'use strict';
window.pushokVpn = (() => {
  const states = {active:'Активна', expired:'Истекла', revoked:'Отозвана', pending:'Готовится'};
  const staff = () => me?.role === 'admin' || hasDeveloperAccess(me);
  const date = n => n ? new Date(n * 1000).toLocaleDateString('ru-RU',{day:'numeric',month:'short',year:'numeric'}) : '—';
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
  function download() {
    const url=new URL('/api/v1/vpn/flow.apk',location.origin).href;
    const app=window.Telegram?.WebApp;
    if(app?.isVersionAtLeast?.('8.0') && typeof app.downloadFile==='function') {
      app.downloadFile({url,file_name:'Pushok-Flow.apk'});return;
    }
    const link=el('a');link.href=url;link.download='Pushok-Flow.apk';document.body.append(link);link.click();link.remove();
  }
  Object.assign(errors,{
    vpn_offer_changed:'Условия оплаты изменились. Обновите тарифы.',vpn_order_missing:'Заказ не найден.',
    vpn_test_store:'Платные заказы доступны в рабочем боте.',vpn_shop_unavailable:'Магазин не ответил. Попробуйте ещё раз.',
    cabinet_stars_disabled:'Оплата Stars для VPN отключена в настройках.',invoice_unavailable:'Telegram не выдал счёт. Повторите открытие этого заказа.',
    telegram_bot_token_not_configured:'Бот для приёма Stars пока не настроен.',vpn_shop_integration:'Магазин не подтвердил связь с кабинетом.',
    vpn_account_conflict:'Этот аккаунт Telegram уже привязан к другому профилю. Обратитесь в поддержку.',
    vpn_changed:'Подписка изменилась. Обновите карточку.',
    vpn_profiles_in_use:'Лимит меньше числа действующих дополнительных профилей.',
    vpn_subscription_missing:'Эта подписка больше недоступна.',
    vpn_change_unconfirmed:'Магазин не подтвердил изменение. Обновите карточку и повторите.',
    vpn_change_conflict:'Операция недоступна для текущего состояния подписки. Проверьте срок и незавершённые операции.',
    vpn_policy_changed:'Настройки уже изменены. Обновите раздел.',vpn_policy_rejected:'Проверьте условия выдачи и актуальность настроек.',
    vpn_days_range:'Выберите от 1 до 365 дней.',vpn_limit_range:'Выберите от 0 до 20 дополнительных профилей.'
  });
  function controls(row,subject,reload,expanded) {
    const panel = el('details',undefined,'vpn-controls');
    panel.open=expanded;
    const title=el('summary');title.append(window.pushokViews.icon('edit'),document.createTextNode('Управление подпиской'));panel.append(title);
    const form = el('form',undefined,'vpn-edit'), op = el('select'), qty = el('input');
    const actionLabel = el('label','Действие'), valueLabel = el('label');
    const caption = el('span'), feedback = el('small',undefined,'muted');
    feedback.setAttribute('role','status');
    const options = [['limit','Изменить лимит профилей']];
    if (['active','expired'].includes(row.state)) options.unshift(['extend','Добавить дни']);
    if (row.state === 'active') options.push(['shorten','Сократить срок']);
    if (row.state === 'pending') options.push(['retry','Повторить выдачу']);
    if (row.state==='revoked') options.unshift(['restore','Выдать новый доступ']);
    else if (['active','expired'].includes(row.state)) options.push(['revoke','Отозвать доступ']);
    const pending = row.pending_change;
    for (const [key,label] of options) { const item = el('option',label); item.value = key; op.append(item); }
    actionLabel.append(op); valueLabel.append(caption,qty);
    const submit = el('button','Применить'); submit.type = 'submit';
    qty.type = 'number'; qty.step = '1'; qty.inputMode = 'numeric';
    function fields() {
      const limit = op.value === 'limit';
      valueLabel.hidden = ['retry','revoke'].includes(op.value); qty.required = !valueLabel.hidden;
      qty.min = limit?'0':'1'; qty.max = limit?'20':'365'; qty.value = limit?String(row.device_limit ?? 0):'1';
      caption.textContent = limit?'Дополнительных профилей':'Количество дней';
    }
    fields(); op.onchange = fields;
    if (pending) {
      op.value = pending.action; fields(); qty.value = pending.value;
      op.disabled = qty.disabled = true; submit.textContent = 'Повторить изменение';
    }
    let intent, sending = false;
    form.onsubmit = async event => {
      event.preventDefault();
      if (sending || !form.reportValidity()) return;
      const session = token, action = op.value, value = ['retry','revoke'].includes(action)?0:Number(qty.value);
      if(action==='revoke'&&!confirm('Отозвать подписку и отключить её основной и дополнительные профили?'))return;
      const before = action === 'limit'?row.device_limit:row.expires_at || 0;
      if (!intent || intent.action !== action || intent.value !== value) {
        intent = {request_id:crypto.randomUUID(),action,value,before,subscription_id:row.subscription_id,...pending};
      }
      sending = true; submit.disabled = op.disabled = qty.disabled = true; feedback.textContent = 'Сохраняем…';
      try {
        await api('/vpn/users/'+encodeURIComponent(subject)+'/management',{method:'POST',body:intent});
        if (session !== token) return;
        reset(); notice(action === 'retry'?'Заявка повторно поставлена в очередь.':'Изменения сохранены.',true);
        await reload();
      } catch (error) { if (session === token) feedback.textContent = error.message; }
      finally { sending = submit.disabled = false; op.disabled = qty.disabled = !!pending; }
    };
    form.append(actionLabel,valueLabel,submit,feedback); panel.append(form); return panel;
  }
  function card(row,subject,name,own,admin,reload,expanded,profileLink=false) {
    const tile=el('article',undefined,'campaign-tile vpn-card'),head=el('div',undefined,'tile-heading');
    const avatar=profileLink?window.pushokViews.userAvatar(subject,name):el('span',undefined,'ui-avatar vpn-avatar');
    if(!profileLink)avatar.append(window.pushokViews.icon('server'));
    const copy=el('div'),title=el(profileLink?'a':'h2',name,'tile-title');
    if(profileLink){title.href='#/user/'+encodeURIComponent(subject);title.onclick=()=>window.pushokProfiles.setBackRoute('vpn');}
    copy.append(title);
    if(row)copy.append(signal(row));
    if(profileLink&&row?.username)copy.append(el('small','@'+row.username,'muted'));
    head.append(avatar,copy);
    if(row) {
      const state=el('span',states[row.state]||'Уточняется','badge campaign-current');
      state.dataset.subscription=row.state; head.append(state);
    }
    tile.append(head);
    if(row) {
      tile.append(el('p',row.expires_at?'Доступ до '+date(row.expires_at):'Срок уточняется','tile-period'));
      const facts=el('dl',undefined,'tile-meta campaign-tile-facts vpn-facts');
      facts.append(metric('Получено',traffic(row.received)),metric('Отправлено',traffic(row.sent)),metric('Доп. профили',String(row.device_limit??'—')));
      tile.append(facts);
      if(admin){
        const addresses=el('dl',undefined,'vpn-addresses'),ips=Array.isArray(row.tunnel_ips)?row.tunnel_ips:[];
        let place=row.country_code;
        if(place){try{place=new Intl.DisplayNames(['ru'],{type:'region'}).of(place);}catch{}}
        addresses.append(metric('Последний IP подключения',row.endpoint_ip||'—'),
          metric('Страна по IP',place||'—'),metric('IP в туннеле',ips.join(', ')||'—'));
        tile.append(addresses);
      }
      if(row.state==='revoked')tile.append(el('p','Доступ отозван'+(row.revoked_at?' '+date(row.revoked_at):'')+
        '. Прежний ключ отключён. Для повторного подключения нужен новый профиль.','vpn-revoked-note'));
      if(row.last_seen)tile.append(el('p','Последний отклик · '+new Date(row.last_seen*1000).toLocaleString('ru-RU',{dateStyle:'short',timeStyle:'short'}),'vpn-seen'));
    } else tile.append(el('p','У аккаунта пока нет VPN-подписки.','tile-period'));
    if(own){
      const actions=el('div',undefined,'vpn-links'),get=button('Скачать Flow',download);get.className='ui-button labeled-action vpn-link';get.prepend(window.pushokViews.icon('download'));
      actions.append(renewLink(),get);tile.append(actions);
    }
    if(admin){
      if(row?.subscription_id)tile.append(controls(row,subject,reload,expanded));
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
    for(const [key,label] of [['recent','По активности'],['traffic','По расходу'],['expires','По сроку'],['name','По имени']]){
      const o=el('option',label);o.value=key;sort.append(o);
    }
    const used=r=>{const v=usage(r,'all');return v?v[0]+v[1]:-1;};
    const render=()=>{
      const q=search.value.trim().toLocaleLowerCase('ru').replace(/^@/,'');
      const found=members.filter(r=>[r.name,r.username,r.telegram_id,r.id].some(v=>String(v??'').toLocaleLowerCase('ru').includes(q)));
      const alpha=(a,b)=>a.name.localeCompare(b.name,'ru')||a.id.localeCompare(b.id);
      found.sort((a,b)=>(sort.value==='traffic'?used(b)-used(a):sort.value==='expires'?(a.expires_at||Infinity)-(b.expires_at||Infinity):sort.value==='recent'?(b.last_seen||0)-(a.last_seen||0):0)||alpha(a,b));
      results.replaceChildren(...found.map(r=>card(r,r.id,r.name,false,true,reload,false,true)));
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
      if(!admin||!directory)host.append(card(row,subject,own&&directory?'Моя подписка':'VPN · Pushok Flow',own,admin,reload,true));
      if(directory&&admin){
        const settings=el('a','Настройки VPN','ui-button');settings.href='#/management/vpn';
        host.prepend(settings,usageSummary((data.members||[]).filter(r=>r.id!==subject)));
        const credit=el('a','Страна по IP · DB-IP','muted');credit.href='https://db-ip.com';credit.target='_blank';credit.rel='noopener noreferrer';host.append(credit);
      }else if(row)host.append(usageSummary([row],true));
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
  return {load:(target,section)=>{if(section==='renew')return window.pushokVpnRenew.show(target);reset();return show(target,me?.id,true);},renewBack:()=>renewFrom,badge,profile,reset};
})();
