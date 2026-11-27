'use strict';
window.pushokCampaignAnalytics=(()=>{
 Object.assign(errors,{analytics_bot_rights_required:'Добавьте бота puShok администратором канала с правом приглашать пользователей и повторите подключение.',analytics_archived:'Для архивной кампании доступен только сохранённый отчёт.',analytics_source_changed:'Канал или среда Telegram изменились. Обратитесь к администратору.',analytics_unavailable:'Статистика временно недоступна.',analytics_channel_unavailable:'Проверьте публичную ссылку канала.'});
 function panel(c,reportButton=null){
  const root=el('section',undefined,'campaign-analytics'),session=token,uid=me?.id;let seq=0,timer;
  root.setAttribute('aria-label','Статистика кампании');root.append(el('h4','Статистика ссылки'),el('p','Загружаем статистику…','muted'));
  const footer=el('div',undefined,'analytics-footer');
  if(reportButton){footer.append(reportButton);root.append(footer);}
  const current=n=>n===seq&&token===session&&me?.id===uid&&root.isConnected;
  async function load(){clearTimeout(timer);if(!root.isConnected||session!==token||uid!==me?.id)return;if(document.hidden){timer=setTimeout(load,30000);return;}const n=++seq;
   try{const data=await api('/campaigns/'+c.id+'/analytics');if(!current(n))return;
    const signature=JSON.stringify(data);if(root.dataset.snapshot===signature)return;const historyOpen=root.querySelector('.analytics-history')?.open,helpOpen=root.querySelector('.analytics-help')?.open;
    const header=el('div',undefined,'analytics-header'),actions=el('div',undefined,'analytics-actions');header.append(el('h4','Статистика ссылки'),actions);actions.append(window.pushokViews.iconAction(button('',load),'refresh','Обновить статистику кампании'));root.replaceChildren(header);
    if(!data.configured){
     const empty=el('div',undefined,'analytics-empty'),mark=el('span',undefined,'analytics-symbol'),copy=el('div');
     mark.append(window.pushokViews.icon('chart'));
     copy.append(el('strong',c.archived_at?'Статистика не собиралась':'Подсчёт не подключён'),el('p',c.archived_at?'Кампания завершилась без ссылки для подсчёта.':'Подключите ссылку кампании, чтобы видеть переходы и подписки.'));
     empty.append(mark,copy);root.append(empty);
    }else{
    const metrics=el('dl',undefined,'campaign-analytics-metrics');
    for(const [label,value] of [['Охват ссылки',data.link_views],['Переходы',data.clicks],['Подписки',data.joins],['Конверсия',data.conversion_percent===null?null:data.conversion_percent+'%']]){const cell=el('div');cell.append(el('dt',label),el('dd',value===null?'—':String(value)));metrics.append(cell);}root.append(metrics);
    }

    if(data.configured&&!data.enabled)root.append(el('p','Канал, бот или среда Telegram изменились. Сохранена история прежнего источника; ссылка отключена.','muted'));
    if(data.test_environment)root.append(el('p','Тестовая среда Telegram','muted'));
    footer.replaceChildren();
    if(!data.configured&&!c.archived_at){const connect=button('Подключить подсчёт',async()=>{await api('/campaigns/'+c.id+'/analytics/enable',{method:'POST'});await load();});connect.className='campaign-stat-action analytics-connect';connect.prepend(window.pushokViews.icon('plus'));footer.append(connect);}
    if(data.url){const line=el('div',undefined,'analytics-link'),a=el('a','Ссылка кампании'),copy=window.pushokViews.iconAction(button('',async()=>{await navigator.clipboard.writeText(data.url);notice('Ссылка кампании скопирована.',true); }),'copy','Скопировать ссылку кампании');a.href=data.url;a.target='_blank';a.rel='noopener noreferrer';const share=window.pushokViews.iconAction(button('',()=>{const url='https://t.me/share/url?url='+encodeURIComponent(data.url)+'&text='+encodeURIComponent('Приглашаю в канал '+(c.channel_username||'')+' — переходите по ссылке.');if(window.Telegram?.WebApp?.openTelegramLink)window.Telegram.WebApp.openTelegramLink(url);else window.open(url,'_blank','noopener,noreferrer');}),'share','Поделиться ссылкой кампании');line.append(a,copy,share);root.append(line);}
    const help=window.pushokViews.disclosure('Как считаем',[
     el('p','Это статистика отдельной ссылки. Показы спонсора MTProxy недоступны и сюда не входят.'),
     el('h5','Как проверить'),
     el('p','1. Добавьте бота администратором канала с правом приглашать пользователей. Нажмите «Подключить подсчёт».'),
     el('p','2. Откройте ссылку кампании в другом браузере или инкогнито, затем нажмите «Открыть канал».'),
     el('p','3. Вступите с аккаунта, который ещё не состоит в канале. Обновите статистику или подождите 30 секунд.'),
     el('p','Для новой ссылки: охват 1, переход 1, подписка 1, конверсия 100%. Для используемой ссылки проверяйте прирост. Повторные действия того же посетителя не увеличивают уникальные счётчики. Если подписка не появилась, проверьте права бота и вступление именно через эту ссылку.'),
     el('h5','Как считаем'),
     el('p','Подключение создаёт отдельное приглашение канала. Боту нужны права администратора и приглашения пользователей. Размещайте ссылку кампании в рекламе или публикациях. Прямые открытия спонсорского канала в Telegram в эти счётчики не входят.'),
     el('p','Охват ссылки — браузеры, открывшие страницу ссылки с JavaScript или нажавшие «Открыть канал». Повторные открытия в том же браузере учитываются один раз. Переходы — браузеры, нажавшие эту кнопку. Подписки — новые участники по приглашению, подтверждённые Telegram.'),
     el('p','Конверсия — подписки / переходы × 100%. Это отношение двух агрегатов, а не сопоставление личности подписчика и браузера. Если приглашение переслали напрямую и подписок больше переходов, процент не рассчитывается. Удаление cookie, разные устройства и блокировка скриптов влияют на подсчёт.'),
     el('p','Сбор начинается после подключения. Предыдущие события не восстанавливаются. История хранится за всё время ссылки; в таблице — последние 30 дней с событиями, время московское. IP и Telegram ID в отчёте не сохраняются. Автообновление — каждые 30 секунд, пока открыт экран.')
    ]);help.classList.add('analytics-help');help.open=Boolean(helpOpen);help.querySelector('summary').hidden=true;const info=window.pushokViews.iconAction(button('',()=>{help.open=!help.open;info.setAttribute('aria-expanded',String(help.open));}),'info','Как проверить статистику');info.setAttribute('aria-expanded',String(help.open));actions.append(info);root.append(help);
    if(data.days?.length){const table=el('table',undefined,'analytics-days'),caption=el('caption','Динамика по дням · мск'),head=el('tr');for(const label of ['Дата','Охват ссылки','Переходы','Подписки'])head.append(el('th',label));const thead=el('thead');thead.append(head);table.append(caption,thead);const body=el('tbody');for(const d of data.days){const row=el('tr');for(const v of [d.day,d.views,d.clicks,d.joins])row.append(el('td',String(v)));body.append(row);}table.append(body);const more=window.pushokViews.disclosure('Динамика по дням',[table],'analytics-history');more.open=Boolean(historyOpen);root.append(more);}
    if(reportButton)footer.append(reportButton);
    if(footer.childElementCount)root.append(footer);
    root.dataset.snapshot=signature;
   }catch(e){if(current(n)){delete root.dataset.snapshot;root.replaceChildren(el('h4','Статистика ссылки'),el('p',e.message,'muted'),button('Повторить',load));if(reportButton){footer.replaceChildren(reportButton);root.append(footer);}}}
   finally{if(current(n))timer=setTimeout(load,30000);}
  }
  // Карточка ещё не прикреплена к документу во время создания.
  setTimeout(()=>{if(root.isConnected&&session===token)void load();},0);return root;
 }
 return {panel};
})();
