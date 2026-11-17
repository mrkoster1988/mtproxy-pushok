'use strict';
window.pushokCampaignAnalytics=(()=>{
 Object.assign(errors,{analytics_bot_rights_required:'Добавьте бота puShok администратором канала с правом приглашать пользователей и повторите подключение.',analytics_archived:'Для архивной кампании доступен только сохранённый отчёт.',analytics_source_changed:'Канал или среда Telegram изменились. Обратитесь к администратору.',analytics_unavailable:'Статистика временно недоступна.',analytics_channel_unavailable:'Проверьте публичную ссылку канала.'});
 function panel(c){
  const root=el('section',undefined,'campaign-analytics'),session=token,uid=me?.id;let seq=0,timer;
  root.setAttribute('aria-label','Статистика кампании');root.append(el('h4','Статистика кампании'),el('p','Загружаем статистику…','muted'));
  const current=n=>n===seq&&token===session&&me?.id===uid&&root.isConnected;
  async function load(){clearTimeout(timer);if(!root.isConnected||session!==token||uid!==me?.id)return;if(document.hidden){timer=setTimeout(load,30000);return;}const n=++seq;
   try{const data=await api('/campaigns/'+c.id+'/analytics');if(!current(n))return;
    root.replaceChildren(el('h4','Статистика кампании'));
    const metrics=el('dl',undefined,'campaign-analytics-metrics');
    for(const [label,value] of [['Охват MTProxy','—'],['Охват ссылки',data.link_views],['Переходы',data.clicks],['Подписки',data.joins],['Конверсия',data.conversion_percent===null?null:data.conversion_percent+'%']]){const cell=el('div');cell.append(el('dt',label),el('dd',value===null?'—':String(value)));metrics.append(cell);}root.append(metrics);
    root.append(el('p','Показы спонсора MTProxy недоступны. Остальные показатели относятся только к отдельной ссылке кампании.','analytics-source muted'));
    if(data.configured&&!data.enabled)root.append(el('p','Канал, бот или среда Telegram изменились. Сохранена история прежнего источника; ссылка отключена.','muted'));
    if(data.test_environment)root.append(el('p','Тестовая среда Telegram','muted'));
    if(!data.configured&&!c.archived_at){const connect=button('Подключить подсчёт',async()=>{await api('/campaigns/'+c.id+'/analytics/enable',{method:'POST'});await load();});connect.prepend(window.pushokViews.icon('chart'));root.append(connect);}
    if(data.url){const line=el('div',undefined,'analytics-link'),a=el('a','Ссылка кампании'),copy=window.pushokViews.iconAction(button('',async()=>{await navigator.clipboard.writeText(data.url);notice('Ссылка кампании скопирована.',true); }),'copy','Скопировать ссылку кампании');a.href=data.url;a.target='_blank';a.rel='noopener noreferrer';line.append(a,copy);root.append(line);}
    const help=window.pushokViews.disclosure('Как считаем',[
     el('p','Подключение создаёт отдельное приглашение канала. Боту нужны права администратора и приглашения пользователей. Размещайте ссылку кампании в рекламе или публикациях. Прямые открытия спонсорского канала в Telegram в эти счётчики не входят.'),
     el('p','Охват ссылки — браузеры, открывшие страницу ссылки с JavaScript или нажавшие «Открыть канал». Повторные открытия в том же браузере учитываются один раз. Переходы — браузеры, нажавшие эту кнопку. Подписки — новые участники по приглашению, подтверждённые Telegram.'),
     el('p','Конверсия — подписки / переходы × 100%. Это отношение двух агрегатов, а не сопоставление личности подписчика и браузера. Если приглашение переслали напрямую и подписок больше переходов, процент не рассчитывается. Удаление cookie, разные устройства и блокировка скриптов влияют на подсчёт.'),
     el('p','Сбор начинается после подключения. Предыдущие события не восстанавливаются. История хранится за всё время ссылки; в таблице — последние 30 дней с событиями, время московское. IP и Telegram ID в отчёте не сохраняются. Автообновление — каждые 30 секунд, пока открыт экран.')
    ]);root.append(help);
    if(data.days?.length){const table=el('table',undefined,'analytics-days'),caption=el('caption','Динамика по дням · МСК'),head=el('tr');for(const label of ['Дата','Охват ссылки','Переходы','Подписки'])head.append(el('th',label));const thead=el('thead');thead.append(head);table.append(caption,thead);const body=el('tbody');for(const d of data.days){const row=el('tr');for(const v of [d.day,d.views,d.clicks,d.joins])row.append(el('td',String(v)));body.append(row);}table.append(body);const more=window.pushokViews.disclosure('Динамика по дням',[table],'analytics-history');root.append(more);}
    root.append(window.pushokViews.iconAction(button('',load),'refresh','Обновить статистику кампании'));
   }catch(e){if(current(n)){root.replaceChildren(el('h4','Статистика кампании'),el('p',e.message,'muted'),button('Повторить',load));}}
   finally{if(current(n))timer=setTimeout(load,30000);}
  }
  // Карточка ещё не прикреплена к документу во время создания.
  setTimeout(()=>{if(root.isConnected&&session===token)void load();},0);return root;
 }
 return {panel};
})();
