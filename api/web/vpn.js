'use strict';
window.pushokVpn = (() => {
  let cache = null, pending = null, owner = null, expires = 0;
  function snapshot() {
    const session = token;
    if (!session) return Promise.reject(new Error('Войдите в кабинет'));
    if (session !== owner) { owner = session; pending = null; cache = null; expires = 0; }
    if (cache && Date.now() < expires) return Promise.resolve(cache);
    if (!pending) pending = api('/vpn/dashboard').then(data => {
      if (token !== session) throw new Error('Войдите в кабинет');
      cache = data; expires = Date.now() + 20000;
      return data;
    }).finally(() => { if (owner === session) pending = null; });
    return pending;
  }
  function badge(node, id) {
    if (!node || !id || !token) return;
    const session = token;
    snapshot().then(data => {
      const seen = (me?.id === id && data.personal?.last_seen) || data.leaders.some(x => x.id === id)
        || data.members?.some(x => x.id === id && x.last_seen);
      if (seen && token === session && !node.querySelector('.vpn-badge')) {
        const mark = el('small','VPN','vpn-badge');
        mark.title = 'Подключение подтверждено VPN-узлом';
        node.append(mark);
      }
    }).catch(() => {});
  }
  function detail(stats) {
    if (!stats) return el('p','Личного подключения пока нет.');
    const labels = {active:'Активна', pending:'Готовится', revoked:'Отозвана', expired:'Истекла'};
    const date = n => new Date(n * 1000).toLocaleString('ru-RU');
    const bytes = n => n == null ? '—' : new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1}).format(n/1048576)+' МБ';
    const box = el('div',undefined,'vpn-details');
    box.append(el('strong',stats.online === true ? 'Онлайн' : stats.online === false ? 'Офлайн' : 'Нет данных о связи'),
      el('span','Подписка · '+(labels[stats.state] || 'Статус уточняется')+(stats.expires_at?' · до '+date(stats.expires_at):'')),
      el('span','Получено '+bytes(stats.received)+' · отправлено '+bytes(stats.sent)));
    if (stats.last_seen) box.append(el('small','Последний отклик '+date(stats.last_seen),'muted'));
    return box;
  }
  async function load(target) {
    const session = token, box = el('section',undefined,'panel');
    const actions = el('div',undefined,'vpn-actions');
    for (const [caption,url] of [['Скачать Flow для Android','https://github.com/mrkoster1988/Pushok-Flow/releases/latest'],['Подключить или продлить','https://pushok.shop/shop/']]) {
      const a = el('a',caption); a.href=url; actions.append(a);
    }
    const content=el('div'), refresh=button('Обновить',()=>{expires=0;return load(target);});
    box.append(content,actions,refresh); target.replaceChildren(box);
    content.setAttribute('role','status');content.textContent='Проверяем подключение…';
    try {
      const data=await snapshot();
      if(token!==session || !box.isConnected) return;
      content.replaceChildren(detail(data.personal));
      if(data.members?.length) {
        const list=el('details'), header=el('summary','Подключения пользователей · '+data.members.length);list.append(header);
        for(const row of data.members){const item=el('section',undefined,'card');item.append(el('strong',row.name),detail(row));list.append(item);}
        box.append(list);
      }
    } catch (_) {if(token===session && box.isConnected) content.textContent='Магазин временно не передал статус подключения.';}
  }
  function leaderboard(target) {
    const section=el('section',undefined,'panel'), session=token;
    target.append(section);
    snapshot().then(data=>{
      if(!section.isConnected || token!==session) return;
      section.append(el('h3','Недавно подключались к VPN'));
      if(!data.leaders.length){section.append(el('p','Подтверждённых подключений пока нет.'));return;}
      const list=el('ol');
      for(const user of data.leaders){const item=el('li',user.name);badge(item,user.id);list.append(item);}
      section.append(list);
    }).catch(()=>{section.remove();});
  }
  return {load,badge,leaderboard};
})();
