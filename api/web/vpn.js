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
  function badge(node,id) {
    if (!node || !id || !token || !staff()) return;
    const session = token;
    snapshot().then(data => {
      if (session !== token || !staff() || !data.can_manage || node.querySelector('.vpn-badge')) return;
      if (data.members?.some(member => member.id === id && member.online === true)) {
        const mark = el('small','VPN','vpn-badge'); mark.title = 'Сейчас онлайн в VPN'; node.append(mark);
      }
    }).catch(() => {});
  }
  function checkout(active) {
    const link = el('a',undefined,'ui-button primary');
    link.href = 'https://pushok.shop/shop/?renew=wg#plans';
    link.append(window.pushokViews.icon('wallet'),document.createTextNode(active?'Продлить за деньги':'Выбрать платный тариф'));
    return link;
  }
  function metric(label,value) {
    const cell = el('div'); cell.append(el('dt',label),el('dd',value)); return cell;
  }
  function details(row) {
    const group = el('div',undefined,'vpn-summary');
    if (!row) { group.append(el('p','Подключений пока нет.','muted')); return group; }
    const line = el('div',undefined,'vpn-state-line'), status = el('strong',states[row.state] || 'Статус уточняется','vpn-state');
    status.dataset.state = row.state;
    line.append(status);
    if (row.state === 'active') line.append(el('span',row.online === true?'● Онлайн':row.online === false?'Офлайн':'Нет данных о связи','vpn-signal'));
    const values = el('dl',undefined,'vpn-metrics');
    values.append(metric('Срок подписки',date(row.expires_at)));
    if (Number.isInteger(row.device_limit)) values.append(metric('Доп. профили',String(row.device_limit)));
    if (row.received != null || row.sent != null) values.append(metric('Получено',traffic(row.received)),metric('Отправлено',traffic(row.sent)));
    group.append(line,values);
    if (row.last_seen) group.append(el('small','Последний отклик · '+new Date(row.last_seen*1000).toLocaleString('ru-RU',{dateStyle:'short',timeStyle:'short'}),'muted'));
    return group;
  }
  Object.assign(errors,{
    vpn_changed:'Подписка изменилась. Обновите карточку.',
    vpn_profiles_in_use:'Лимит меньше числа действующих дополнительных профилей.',
    vpn_subscription_missing:'Эта подписка больше недоступна.',
    vpn_change_unconfirmed:'Магазин не подтвердил изменение. Обновите карточку и повторите.',
    vpn_change_conflict:'Операция недоступна для текущего состояния подписки. Проверьте срок и незавершённые операции.',
    vpn_days_range:'Выберите от 1 до 365 дней.',vpn_limit_range:'Выберите от 0 до 20 дополнительных профилей.'
  });
  function controls(row,subject,reload) {
    const panel = el('details',undefined,'vpn-controls');
    panel.append(el('summary','Управление подпиской'));
    const form = el('form',undefined,'vpn-edit'), op = el('select'), qty = el('input');
    const actionLabel = el('label','Действие'), valueLabel = el('label');
    const caption = el('span'), feedback = el('small',undefined,'muted');
    feedback.setAttribute('role','status');
    const options = [['limit','Изменить лимит профилей']];
    if (['active','expired'].includes(row.state)) options.unshift(['extend','Добавить дни']);
    if (row.state === 'active') options.push(['shorten','Сократить срок']);
    if (row.state === 'pending') options.push(['retry','Повторить выдачу']);
    const pending = row.pending_change;
    for (const [key,label] of options) { const item = el('option',label); item.value = key; op.append(item); }
    actionLabel.append(op); valueLabel.append(caption,qty);
    const submit = el('button','Применить'); submit.type = 'submit';
    qty.type = 'number'; qty.step = '1'; qty.inputMode = 'numeric';
    function fields() {
      const limit = op.value === 'limit';
      valueLabel.hidden = op.value === 'retry'; qty.required = !valueLabel.hidden;
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
      const session = token, action = op.value, value = action === 'retry'?0:Number(qty.value);
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
  function heading(text) {
    const h = el('h2',undefined,'vpn-heading'); h.append(window.pushokViews.icon('server'),document.createTextNode(text)); return h;
  }
  function member(row) {
    const link = el('a',undefined,'vpn-member');
    link.href = '#/user/'+encodeURIComponent(row.id);
    link.addEventListener('click',()=>window.pushokProfiles.setBackRoute('vpn'));
    const copy = el('span'); copy.append(el('strong',row.name),el('small',(states[row.state] || 'Статус уточняется')+' · '+date(row.expires_at)));
    link.append(copy);
    if (row.online === true) link.append(el('span','Онлайн','vpn-live'));
    link.append(window.pushokViews.icon('chevron')); return link;
  }
  async function show(target,subject,directory) {
    const own = subject === me?.id, session = token;
    if (!own && !staff()) return;
    const box = el('section',undefined,'panel vpn-card');
    const content = el('div'), actions = el('div',undefined,'vpn-links');
    const reload = () => { reset(); return show(target,subject,directory); };
    box.append(heading(own && directory?'Моя подписка':'VPN'),content,actions);
    if (own) {
      const download = el('a','Скачать Flow','ui-button'); download.href = 'https://github.com/mrkoster1988/Pushok-Flow/releases/latest';
      actions.append(checkout(true),download);
    }
    content.textContent = 'Загрузка подписки…'; target.replaceChildren(box);
    try {
      const data = await snapshot();
      if (!box.isConnected || session !== token || (!own && !staff())) return;
      const admin = staff() && data.can_manage === true;
      const selected = admin ? data.members?.find(r=>r.id === subject) : null;
      const row = selected || (own ? data.personal : null);
      content.replaceChildren(details(row));
      if (own && !row) actions.firstChild.replaceWith(checkout(false));
      if (admin && selected?.subscription_id) box.append(controls(selected,subject,reload));
      if (directory && admin) {
        const list = el('section',undefined,'panel vpn-directory');
        list.append(heading('Подключения пользователей'));
        for (const row of data.members || []) list.append(member(row));
        if (!data.members?.length) list.append(el('p','Подключений пока нет.','muted'));
        target.append(list);
      }
    } catch (error) {
      if (session === token && box.isConnected) content.replaceChildren(el('p','Не удалось загрузить сведения о подписке.'),button('Повторить',reload));
    }
  }
  function profile(target,id) {
    if (id !== me?.id && !staff()) return;
    const block = el('div',undefined,'vpn-profile'); target.append(block); void show(block,id,false);
  }
  function leaderboard(target) {
    if (!staff()) return;
    const session = token, list = el('section',undefined,'panel vpn-directory'); target.append(list);
    snapshot().then(data=>{
      if (!list.isConnected || token !== session || !staff() || !data.can_manage || !data.leaders?.length) { list.remove(); return; }
      list.append(heading('Недавно подключались к VPN'));
      for (const user of data.leaders) {
        const link = el('a',user.name,'vpn-member');link.href='#/user/'+encodeURIComponent(user.id);list.append(link);
      }
    }).catch(()=>list.remove());
  }
  return {load:target=>{reset();return show(target,me?.id,true);},badge,profile,leaderboard,reset};
})();
