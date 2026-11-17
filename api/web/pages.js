'use strict';
// Страницы используют общие формы и действия app.js, без второго набора API.
(() => {
 Object.assign(errors,{stars_environment_changed:'Среда Telegram для этого счёта изменилась. Обратитесь к владельцу; не оплачивайте повторно.',invalid_telegram_environment:'Владелец должен проверить режим Telegram на сервере.',telegram_environment_mismatch:'Среды бота и кабинета не совпадают. Нужна проверка владельца.',stars_checkout_disabled_until_acceptance:'Оплата Stars выключена до приёмки.',stars_link_unavailable:'Telegram временно недоступен. Счёт сохранён; повтор не меняет его сумму.',stars_payment_processing:'Платёж уже обрабатывается. Обновите статус, не оплачивайте повторно.',stars_bot_changed:'Бот для оплаты изменился. Нужна проверка владельца.',stars_price_not_configured:'Цена Stars для тарифа ещё не настроена.'});
 Object.assign(errors,{sbp_test_mode_mismatch:'Режим провайдера не совпадает с настройками сервера. Счёт не открыт; нужна проверка владельца.'});
 Object.assign(errors,{sbp_checkout_disabled_until_acceptance:'СБП выключена до настройки и приёмки.',another_payment_in_progress:'У заявки уже открыт другой платёж. Сначала проверьте его результат.',sbp_provider_unavailable:'Провайдер временно недоступен. Не оплачивайте повторно; повторный выпуск использует тот же ключ.',sbp_verification_requires_review:'Платёж не прошёл сверку. Не платите повторно; обратитесь к оператору.',sbp_creation_requires_review:'Результат выпуска счёта неизвестен слишком долго. Нужна проверка оператора.',sbp_configuration_changed:'Платёжные настройки изменились. Нужна проверка оператора.',sbp_invoice_not_created:'Счёт ещё не выпущен.',campaign_not_payable:'Заявка сейчас не ожидает новую оплату.'});
 Object.assign(errors,{sponsor_engine_mismatch:'Движок тега не совпадает с планом узла.',sponsor_tag_already_registered:'Этот тег уже зарегистрирован на другом узле.',sponsor_registration_not_found:'Сначала сохраните регистрацию спонсорского тега.',proxy_node_not_found:'Узел не найден. Обновите список.'});
 const state={campaigns:[],search:'',filter:'',scope:'all',route:'',bot:null,ready:false,loaded:false,renderKey:'',selectedKey:''};
 function dataKey(){return JSON.stringify([state.campaigns,channels,tariffs,nodes,operatorCampaigns,me]);}
 function refreshLivePanels(){
  if(state.route==='dashboard'){void window.pushokGrowth.overview(dashboardReferral);}
  pages[state.route]?.querySelectorAll('[data-promotion-campaign]').forEach(panel=>void updatePromotionPanel(panel));
 }
 const routes={
  wallet:['Баланс','Пополнения, списания и история операций'],
  growth:['Бонусы и приглашения','Реферальная программа, промокоды, подарки и скидки'],
  dashboard:['Главная','Обзор ваших размещений'],
  channels:['Мои каналы','Управление рекламируемыми Telegram-каналами'],
  create:['Новая кампания','Канал, тариф и дата — в одной заявке'],
  campaigns:['Кампании','Статусы ваших рекламных заявок'],
  campaign:['Кампания','Модерация, оплата и размещение — отдельные этапы'],
  payments:['Мои оплаты','Оплата и история ваших кампаний'],
  license:['Лицензия приложения','Тариф и ограничения установки'],
  profile:['Профиль','Ваш аккаунт и разделы кабинета'],
  users:['Пользователи','Люди, роли и входы в приложение'],
  user:['Профиль пользователя','Каналы и кампании рекламодателя'],
  calendar:['Календарь размещений','Доступность на полную длительность тарифа'],
  reports:['Отчёты и история','Подтверждённые события ваших кампаний'],
  moderation:['Модерация','Одна карточка — от проверки канала до завершения'],
  sponsors:['Спонсоры и узлы','Реестр узлов и план движка'],
  settings:['Настройки','Тарифы, платежи и готовность системы'],
  archive:['Архив','Завершённые кампании, отчёты и удалённые объекты'],
  help:['Помощь и инструкции','Пошаговые сценарии и ответы на вопросы'],
  bot:['Telegram-бот','Продолжите работу в Telegram']
 };
 const hints={
  license:'Лицензия покупается владельцем установки и действует на всё приложение.',
  wallet:'Баланс оплачивает размещения puShok. Автосписание включается отдельно с пределом суммы за заявку.',
  growth:'Подарок и промокод вводятся при создании заявки. Скидки не суммируются: применяется самое выгодное предложение.',
  profile:'Ваши каналы, отчёты и настройки. Разделы управления доступны по роли аккаунта.',
  user:'Данные аккаунта, его каналы и кампании. Фото и имя берутся из Telegram.',
  dashboard:'Здесь показаны данные ваших кампаний из кабинета. Показ спонсора проверяется отдельно.',
  channels:'API принимает публичную ссылку https://t.me/channel. Добавление канала ещё не означает одобрение рекламы.',
  create:'Заполните форму и отправьте заявку. После одобрения оплатите размещение здесь же.',
  campaigns:'Одобрение канала, подтверждение оплаты и фактическое размещение — разные статусы.',
  campaign:'Если нужны исправления, прочтите комментарий, исправьте канал и отправьте заявку повторно.',
  payments:'Не переводите деньги повторно до выяснения результата. Загрузка чека не подтверждает поступление денег.',
  calendar:'Выберите желаемое начало. После оплаты система назначит свободное время; если выбранное занято — ближайший следующий период.',
  reports:'История отражает события системы и ручные наблюдения. Счётчик показов Telegram недоступен.',
  moderation:'Проверяйте канал и оплату раздельно. Агент активирует кампанию после применения тега; завершение после снятия/смены тега — автоматическое.',
  sponsors:'Запись узла и план движка не устанавливают MTProxy. Управление тегом и проверка показа выполняются отдельно.',
  settings:'Изменения цены действуют для новых заказов. RUB и XTR задаются отдельно.',
  help:'Инструкции можно открыть снова в любое время. Не отправляйте токены и полные платёжные реквизиты.',
  bot:'Ссылка берётся из настроек сервера. Доступность оплаты определяется конфигурацией бота.'
 };
 const pages={},content={},stats={};
 const workspace=$('workspace');
 function link(text,route){const a=el('a',text);a.href='#/'+route;return a;}
 function hint(text){const d=el('details',undefined,'context-help'),s=el('summary','Подсказка');d.append(s,el('p',text));return d;}
 function section(title){const s=el('section',undefined,'panel');s.append(el('h2',title));return s;}
 function empty(target,text){target.replaceChildren(el('p',text,'empty'));}
 function metrics(name,rows){
  stats[name].replaceChildren(...rows.map(([title,value,note])=>{
   const tile=el('article',undefined,'metric');tile.append(el('span',title),el('strong',String(value)),el('small',note));return tile;
  }));
 }
 function safeChannel(url,title){
  try{const u=new URL(url);if(u.protocol==='https:'&&['t.me','telegram.me'].includes(u.hostname)){
   const a=el('a',title);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';return a;
  }}catch{}
  return el('span',title);
 }
 function table(target,rows,headers){
  target.replaceChildren();
  if(!rows.length){empty(target,'Пока нет данных.');return;}
  const wrap=el('div',undefined,'table-scroll'),t=el('table'),head=el('thead'),tr=el('tr');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Таблица данных. При необходимости прокрутите по горизонтали.');
  headers.forEach(text=>{const th=el('th',text);th.scope='col';tr.append(th);});head.append(tr);t.append(head);
  const body=el('tbody');rows.forEach(row=>{const r=el('tr');row.forEach((value,index)=>{const cell=el('td');cell.dataset.label=headers[index];cell.append(typeof value==='string'?document.createTextNode(value):value);r.append(cell);});body.append(r);});
  t.append(body);wrap.append(t);target.append(wrap);
 }
 function badge(value){return el('span',labels[value]||value,'badge badge--'+value);}
 function campaignRows(list){return list.map(c=>[
  link(c.channel_title||channels.find(x=>x.id===c.channel_id)?.title||c.id.slice(0,8),'create/'+c.id),
  (()=>{const group=el('div',undefined,'status-stack');group.append(badge(c.moderation_status),(c.price_kopecks_snapshot===0?el('span','Бесплатно','badge'):badge(c.payment_status)),badge(c.placement_status));return group;})(),money(c.price_kopecks_snapshot),
  date(c.scheduled_start_at),link('Открыть','create/'+c.id)
 ]);}
 const campaignHeaders=['Канал','Статусы','Цена заявки','Начало','Действие'];
 function staffCampaigns(){return ['admin','operator'].includes(me?.role)&&state.scope==='all';}
 function campaignSource(){return staffCampaigns()?operatorCampaigns.filter(c=>!c.archived_at):state.campaigns;}
 function filtered(){return campaignSource().filter(c=>
  (!state.filter||[c.moderation_status,c.payment_status,c.placement_status].includes(state.filter))&&
  [c.channel_title,c.channel_url,c.id,c.advertiser_username,c.advertiser_first_name,c.advertiser_last_name,channels.find(x=>x.id===c.channel_id)?.title].some(v=>String(v||'').toLocaleLowerCase('ru').includes(state.search.replace(/^@/,'').toLocaleLowerCase('ru'))));}
 const shell=el('div',undefined,'cabinet-shell');
 const stage=el('div',undefined,'cabinet-stage'),bar=el('div',undefined,'cabinet-topbar');
 const searchForm=el('form'),search=el('input');search.type='search';search.placeholder='Поиск кампаний по каналу или номеру';search.setAttribute('aria-label','Поиск кампаний');
 searchForm.append(search);searchForm.addEventListener('submit',e=>{e.preventDefault();state.search=search.value.trim();go(state.scope==='mine'?'campaigns/mine':'campaigns');renderList();});
 const account=el('span',undefined,'cabinet-account');account.id='cabinet-account';
 const topHelp=el('details',undefined,'top-help'),helpSummary=el('summary','?'),helpText=el('p');helpSummary.setAttribute('aria-label','Подсказка по разделу');helpSummary.title='Подсказка по разделу';helpSummary.replaceChildren(window.pushokViews.icon('info'));topHelp.append(helpSummary);const helpPopup=el('div',undefined,'help-popup');helpPopup.append(helpText,link('Подсказки и инструкции','help'));topHelp.append(helpPopup);
 window.addEventListener('keydown',e=>{if(e.key==='Escape')topHelp.open=false;});document.addEventListener('click',e=>{if(!topHelp.contains(e.target))topHelp.open=false;});
 const backButton=button('Назад',async()=>back());backButton.className='screen-back';backButton.setAttribute('aria-label','Назад');backButton.replaceChildren(window.pushokViews.icon('back'));
 const mobileBrand=link('puShok','dashboard');mobileBrand.className='mobile-brand';
 window.pushokViews.brand(mobileBrand);
 const profileShortcut=link('','profile');profileShortcut.className='profile-shortcut';profileShortcut.setAttribute('aria-label','Открыть профиль');
 const refreshButton=$('refresh');refreshButton.setAttribute('aria-label','Обновить');window.pushokViews.iconAction(refreshButton,'refresh','Обновить');bar.append(backButton,mobileBrand,account,refreshButton,profileShortcut,topHelp);const legacyLogout=el('div');legacyLogout.hidden=true;legacyLogout.append($('logout'));bar.append(legacyLogout);stage.append(bar);shell.append(stage);workspace.prepend(shell);
 Object.entries(routes).forEach(([name,[title,subtitle]])=>{
  const page=name==='settings'?$('settings'):el('section');page.dataset.page=name;page.hidden=true;page.classList.add('cabinet-page');
  const heading=el('header',undefined,'page-heading'),titles=el('div');const h=el('h1',title);h.tabIndex=-1;titles.append(h);heading.append(titles);
  if(['campaigns','channels'].includes(name)){
   const action=link(name==='channels'?'+ Добавить канал':'+ Создать кампанию',name==='channels'?'channels/new':'create');
   window.pushokViews.iconAction(action,'plus',name==='channels'?'Добавить канал':'Создать кампанию');heading.append(action);
  }
  page.prepend(heading);const grid=el('div',undefined,'metrics');page.insertBefore(grid,heading.nextSibling);stats[name]=grid;
  const layout=el('div',undefined,'page-layout'),body=el('div',undefined,'page-content');
  layout.append(body);page.append(layout);pages[name]=page;content[name]=body;stage.append(page);
 });
 // Постоянная справка не занимает экран настроек; статусы сервера остаются на виду.
 document.querySelectorAll('#settings-payments>p.muted:not([id]),#settings-payments .settings-method>p.muted:not([id]),#settings-tariffs .tariff-builder>p.muted:not([id]),#settings-system>.core-health>p:not([id]),#loading-title-fields>p.muted').forEach(p=>{const help=window.pushokViews.disclosure('Подробнее',[]);p.before(help);help.append(p);});
 const updatePanel=$('software-updates');
 const updateNotice=el('section',undefined,'update-notice');updateNotice.id='software-update-notice';updateNotice.hidden=true;updateNotice.setAttribute('aria-label','Обновление приложения');updateNotice.setAttribute('role','status');
 const updateNoticeText=el('span');updateNoticeText.id='software-update-notice-text';const updateNoticeLink=link('Открыть обновления','profile/updates');updateNoticeLink.addEventListener('click',event=>{event.preventDefault();go('profile/updates');});updateNotice.append(updateNoticeText,updateNoticeLink);bar.after(updateNotice);
 // Перемещаем существующие формы вместе с обработчиками. Все id остаются уникальными.
 const channelAdd=$('channel-form').closest('section');channelAdd.classList.add('channel-add');channelAdd.hidden=true;channelAdd.querySelector('h2').remove();content.channels.append(channelAdd);
 const channelFormActions=el('div',undefined,'channel-form-actions'),channelCancel=button('Отмена',()=>{go('channels');});channelFormActions.append($('channel-form').querySelector('button[type=submit]'),channelCancel);$('channel-form').append(channelFormActions);
 const channelList=section('Ваши каналы'),channelRows=el('div');channelList.classList.add('flat-section');channelList.querySelector('h2').remove();channelList.append(channelRows);content.channels.prepend(channelList);
 const channelSearch=el('input',undefined,'channel-search');channelSearch.type='search';channelSearch.placeholder='Название или @username';channelSearch.setAttribute('aria-label','Поиск каналов');channelSearch.autocomplete='off';channelSearch.autocapitalize='none';channelSearch.spellcheck=false;
 const channelSearchBar=el('div',undefined,'channel-search-bar'),channelSearchClear=window.pushokViews.iconAction(button('',()=>{channelSearch.value='';renderChannels();channelSearch.focus();}),'close','Очистить поиск каналов');channelSearchClear.hidden=true;channelSearchBar.append(window.pushokViews.icon('search'),channelSearch,channelSearchClear);channelList.prepend(channelSearchBar);
 function renderChannels(){const query=channelSearch.value.trim().replace(/^@/,'').toLocaleLowerCase('ru');channelSearchClear.hidden=!channelSearch.value;const list=channels.filter(c=>!query||(c.title+' '+c.telegram_url).toLocaleLowerCase('ru').includes(query));window.pushokViews.channelCards(channelRows,list);if(query&&!list.length)channelRows.replaceChildren(el('p','Каналы не найдены. Измените запрос.','empty'));}
 channelSearch.addEventListener('input',renderChannels);

 content.create.append($('campaign-form').closest('section'));
 window.pushokViews.channelSearch($('order-channel-url'),$('order-channel-title'));
 window.pushokViews.channelSearch($('channel-form').elements.telegram_url,$('channel-form').elements.title);
 content.payments.prepend(link('Баланс · пополнить или посмотреть операции →','wallet'));
 const createNotice=el('p',undefined,'muted');content.create.prepend(createNotice);
 const calendar=$('availability-days').closest('.calendar');const calendarOrigin=$('order-calendar-home');
 const calendarPanel=section('Выбор даты'),calendarTariff=el('select');calendarTariff.setAttribute('aria-label','Тариф календаря');calendarPanel.querySelector('h2').remove();calendarPanel.append(calendarTariff);content.calendar.append(calendarPanel);
 calendarTariff.addEventListener('change',()=>run(async()=>{$('tariff-select').value=calendarTariff.value;await loadAvailability();}));
 content.calendar.append(link('Перейти к созданию кампании','create'));
 const listTools=el('div',undefined,'list-tools'),filter=el('select');filter.setAttribute('aria-label','Статус кампаний');
 [['','Все статусы'],['draft','Черновики'],['pending','На модерации'],['changes_requested','Нужны исправления'],['awaiting_payment','Ожидают оплаты'],['active','Активные']].forEach(([v,t])=>option(filter,v,t));
 const reset=button('',async()=>{state.search='';state.filter='';search.value='';filter.value='';renderList();});
 window.pushokViews.iconAction(reset,'close','Сбросить поиск и фильтр');
 search.addEventListener('input',()=>{state.search=search.value.trim();renderList();});
 filter.addEventListener('change',()=>{state.filter=filter.value;renderList();});listTools.append(searchForm,filter,reset);content.campaigns.append(listTools,$('campaigns'));
 const campaignScope=el('nav',undefined,'campaign-scope');campaignScope.setAttribute('aria-label','Чьи кампании');
 for(const [value,title] of [['all','Все кампании'],['mine','Мои кампании']]){const b=button(title,()=>go(value==='mine'?'campaigns/mine':'campaigns'));b.dataset.scope=value;campaignScope.append(b);}listTools.before(campaignScope);
 // Архив всегда виден рядом с текущими кампаниями, без поиска в настройках.
 for(const name of ['campaigns','archive','channels','calendar']){
  const tabs=el('nav',undefined,'campaign-tabs');tabs.setAttribute('aria-label','Разделы кампаний');
  for(const [route,title] of [['campaigns','Кампании'],['channels','Каналы'],['calendar','Календарь'],['archive','Архив']]){const a=link(title,route);if(route===name)a.setAttribute('aria-current','page');tabs.append(a);}
  pages[name].querySelector('.page-heading').after(tabs);
 }
 const dashboardHero=el('section',undefined,'dashboard-hero');
 const heroCopy=el('div',undefined,'dashboard-hero-copy');
 heroCopy.append(el('h2','Продвигайте свой канал'),el('p','Реклама в MTProxy'));
 const heroActions=el('div',undefined,'dashboard-hero-actions');heroActions.append(link('Создать кампанию','create'));
 const connectProxy=el('a','Подключить прокси','dashboard-connect-proxy');connectProxy.setAttribute('role','link');connectProxy.tabIndex=0;connectProxy.setAttribute('aria-disabled','true');connectProxy.title='Ссылка подключения ещё не настроена';connectProxy.addEventListener('click',event=>{if(!connectProxy.hasAttribute('href')){event.preventDefault();notice('Ссылка подключения прокси пока не настроена.');}});heroActions.append(connectProxy);
 const dashboardMascot=el('img');dashboardMascot.src='/assets/pushok-pencil.png';dashboardMascot.alt='Пушок — кот в бирюзовом худи';dashboardMascot.width=240;dashboardMascot.height=258;
 dashboardHero.append(heroCopy,dashboardMascot,heroActions);
 const dashboardIntro=el('div',undefined,'dashboard-intro'),dashboardReferral=el('div');dashboardReferral.id='dashboard-referral';dashboardIntro.append(dashboardHero);pages.dashboard.insertBefore(dashboardIntro,stats.dashboard);
 const bottomNav=el('nav',undefined,'mobile-bottom-nav');bottomNav.setAttribute('aria-label','Основные разделы');
 [['dashboard','home','Главная'],['moderation','moderation','Модерация'],['campaigns','campaign','Кампании'],['wallet','wallet','Баланс'],['profile','user','Профиль']].forEach(([route,icon,title])=>{
  const a=link(title,route);a.dataset.route=route;a.hidden=route==='moderation';
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','/assets/pushok-icons.svg#pi-'+icon);svg.append(use);a.prepend(svg);bottomNav.append(a);
 });
 shell.append(bottomNav);
 const summary=section('Ваши кампании'),summaryRows=el('div','Загрузка данных. При ошибке нажмите «Обновить».');summary.classList.add('flat-section');summary.append(summaryRows);content.dashboard.append(summary);
 const summaryHeading=el('div',undefined,'section-heading');summaryHeading.append(summary.querySelector('h2'),link('Все →','campaigns'));summary.prepend(summaryHeading);

 const dashboardHelp=link('Как работает реклама','help');dashboardHelp.className='dashboard-help';dashboardHelp.prepend(window.pushokViews.icon('support'));const helpArrow=el('span','›');helpArrow.setAttribute('aria-hidden','true');dashboardHelp.append(helpArrow);content.dashboard.append(dashboardHelp,dashboardReferral);
 const onboarding=window.pushokViews.disclosure('Как начать',[el('p','Выберите канал и тариф, отправьте заявку. После одобрения оплатите размещение — запуск и завершение выполняются по расписанию.')]);onboarding.id='onboarding';content.help.prepend(onboarding);
 const paymentOverview=el('div'),paymentRows=el('div');content.payments.append(paymentOverview,paymentRows);
 const sponsorQueue=el('section',undefined,'sponsor-queue');content.sponsors.append(sponsorQueue);const reportRows=el('div');content.reports.append(reportRows);
 const archiveRows=el('div');content.archive.append(archiveRows);
 const detail=el('div');content.campaign.append(detail);
 content.moderation.append($('queue-filter'),$('queue-count'),$('queue'));
 const operatorNodes=el('div');content.sponsors.append($('node-admin'),operatorNodes);
 const diagnosticsPanel=section('Диагностика спонсорского показа'),diagnosticsBody=el('div');
 diagnosticsPanel.id='promotion-diagnostics';diagnosticsPanel.hidden=true;
 diagnosticsBody.setAttribute('aria-live','polite');
 diagnosticsPanel.append(el('p','Готовность по данным кабинета и последний ответ агента проверяются отдельно. Применение тега не подтверждает фактический показ канала.','muted'),button('Обновить диагностику',renderDiagnostics),diagnosticsBody);
 content.sponsors.prepend(diagnosticsPanel);
 let diagnosticsRevision=0;
 async function renderDiagnostics(){
  diagnosticsPanel.hidden=me?.role!=='admin';if(me?.role!=='admin')return;
  const rev=++diagnosticsRevision,session=token;diagnosticsBody.replaceChildren(el('p','Проверяем состояние…'));
  automationRuntime.replaceChildren(el('p','Проверяем применение тега…','muted'));
  try{
   const data=await api('/admin/promotion-diagnostics');
   if(rev!==diagnosticsRevision||token!==session||me?.role!=='admin')return;
   const agent=el('div',undefined,'card');agent.id='promotion-agent-report';agent.dataset.status=data.agent.status;
   agent.append(el('h3','Последний ответ агента'),el('p',data.agent.title),el('p',data.agent.next_action,'muted'));
   if(data.agent.checked_at)agent.append(el('p','Время ответа: '+date(data.agent.checked_at),'muted'));
   if(data.agent.host)agent.append(el('p','Адрес агента: '+data.agent.host+(data.agent.port?':'+data.agent.port:''),'muted'));
   if(data.agent.last_title)agent.append(el('p','Последний результат: '+data.agent.last_title,'muted'));
   diagnosticsBody.replaceChildren(agent);
   const plans=el('div',undefined,'cards');
   for(const item of data.nodes){
    const card=el('div',undefined,'card');card.dataset.diagnosticCode=item.code;
    card.append(el('h3',item.name),el('p',item.host+':'+item.port,'muted'),el('p','Состояние: '+item.title),el('p',item.next_action));
    if(item.config_applied){card.append(el('p','По расписанию: '+item.plan_title,'muted'),el('p','Тег применён; показ не проверен.','muted'));}
    if(item.checked_at)card.append(el('p','Ответ по узлу: '+date(item.checked_at),'muted'));
    if(item.next_start_at)card.append(el('p','Начало: '+date(item.next_start_at),'muted'));
    plans.append(card);
   }
   diagnosticsBody.append(plans);
   automationRuntime.replaceChildren();
   for(const item of data.nodes.filter(n=>n.checked_at)){automationRuntime.append(el('p',item.name+': '+(!item.agent_recent?'Нет свежего подтверждения агента':item.config_applied?'Тег применён; показ не проверен':'Применение текущего плана не подтверждено')+' · '+date(item.checked_at)));}
   if(!automationRuntime.childElementCount)automationRuntime.append(el('p','Подтверждения от агента пока нет. Служба устанавливается обновлением на VPS с классическим MTProxy.','muted'));
   if(!data.nodes.length)diagnosticsBody.append(el('p','В реестре нет узлов. Добавьте узел или восстановите его из архива.'));
  }catch(e){if(rev===diagnosticsRevision&&token===session&&me?.role==='admin'){diagnosticsBody.replaceChildren(el('p','Диагностика недоступна: '+e.message));automationRuntime.replaceChildren(el('p','Состояние применения недоступно. Обновите диагностику.','muted'));}}
 }
 const sponsorForm=el('form'),sponsorPanel=section('Регистрация спонсорского тега');sponsorForm.id='sponsor-form';
 const sponsorNode=el('select');sponsorNode.required=true;sponsorNode.setAttribute('aria-label','Узел спонсорского тега');
 const sponsorEngine=el('select');sponsorEngine.setAttribute('aria-label','Движок спонсорского тега');Object.entries(engineTitles).forEach(([v,t])=>option(sponsorEngine,v,t));
 const sponsorTag=el('input');sponsorTag.type='password';sponsorTag.required=true;sponsorTag.minLength=32;sponsorTag.maxLength=32;sponsorTag.pattern='[0-9a-fA-F]{32}';sponsorTag.autocomplete='off';sponsorTag.setAttribute('aria-label','ad_tag из MTProxyBot');
 function field(title,input){const label=el('label',title);if(!input.hasAttribute('aria-label'))input.setAttribute('aria-label',title);label.append(input);return label;}
 const sponsorSave=el('button','Сохранить регистрацию');sponsorSave.type='submit';
 const sponsorResult=el('p',undefined,'muted');sponsorResult.setAttribute('role','status');
 sponsorForm.append(field('Узел',sponsorNode),field('Движок',sponsorEngine),field('ad_tag из @MTProxyBot · 32 шестнадцатеричных символа',sponsorTag),sponsorSave);
 sponsorPanel.append(el('p','Первичная регистрация связывает прокси с Telegram. Для автоматического размещения сохраните привязку канала и его тега ниже; затем агент управляет запуском и завершением по расписанию.','muted'),sponsorForm,sponsorResult);content.sponsors.append(sponsorPanel);
 sponsorForm.addEventListener('submit',e=>{e.preventDefault();run(async()=>{
  const data=await api('/admin/proxy-nodes/'+sponsorNode.value+'/sponsor-registration',{method:'PUT',body:{engine:sponsorEngine.value,ad_tag:sponsorTag.value}});
  sponsorTag.value='';sponsorResult.textContent='Сохранён тег с окончанием '+data.tag_ending+'. Изменения на прокси не применялись.';
 },sponsorSave);});
 const automationPanel=section('Автоприменение по расписанию'),automationBody=el('div'),automationRuntime=el('div');
 automationPanel.id='promotion-automation';automationRuntime.id='promotion-automation-runtime';automationRuntime.setAttribute('aria-live','polite');
 automationPanel.append(el('p','После подключения аккаунта владельца агент сам выберет свободный узел, задаст канал в @MTProxyBot и проверит привязку. Подготовка и ожидание обновления Telegram не уменьшают оплаченный период. Занятые узлы и ручные размещения не переключаются.','muted'),automationBody,automationRuntime);
 content.sponsors.append(automationPanel);
 let automationRevision=0,automationFocusRoute='';
 async function renderAutomation(){
  // Из карточки заявки сначала открываем её форму, чтобы загрузка диагностики
  // выше по странице не сдвигала поля после перехода.
  if(routeParts()[0]==='sponsors'&&routeParts()[1])content.sponsors.prepend(automationPanel);
  else content.sponsors.append(automationPanel);
  void renderDiagnostics();
  automationPanel.hidden=me?.role!=='admin';if(me?.role!=='admin')return;
  const rev=++automationRevision,session=token;automationBody.replaceChildren(el('p','Загрузка…'));
  try{
   const data=await api('/admin/promotion-bindings');if(rev!==automationRevision||token!==session)return;
   const form=el('form'),node=el('select'),channel=el('select'),tag=el('input'),confirmed=el('input');
   node.required=true;channel.required=true;tag.required=true;tag.type='password';tag.pattern='[0-9a-fA-F]{32}';tag.autocomplete='off';confirmed.type='checkbox';confirmed.required=true;
   nodes.forEach(n=>option(node,n.id,n.name+' · '+n.host+':'+n.port));data.channels.forEach(c=>option(channel,c.id,c.title+' · '+c.telegram_url));
   const selectedChannel=routeParts()[1],selectedNode=location.hash.split('/')[3];if(data.channels.some(c=>c.id===selectedChannel))channel.value=selectedChannel;if(nodes.some(n=>n.id===selectedNode))node.value=selectedNode;
   const submit=el('button','Включить для канала');submit.type='submit';submit.disabled=!nodes.length||!data.channels.length;
   form.append(field('Узел',node),field('Канал',channel),field('Тег этого канала из @MTProxyBot',tag),field('Я проверил: этот тег связан именно с выбранным каналом в Telegram',confirmed),submit);
   form.addEventListener('submit',e=>{e.preventDefault();run(async()=>{await api('/admin/promotion-bindings',{method:'PUT',body:{node_id:node.value,channel_id:channel.value,ad_tag:tag.value,telegram_binding_confirmed:confirmed.checked}});tag.value='';await renderAutomation();notice('Связь сохранена. Агент применит тег в пределах назначенного слота.',true);},submit);});
   const manual=el('details');manual.append(el('summary','Ручная привязка готового тега'),form);
   const autoStatus=el('div',undefined,'cards');autoStatus.setAttribute('aria-live','polite');
   const phaseTitles={inspect:'Проверяем регистрацию прокси',bind:'Привязываем канал в Telegram',propagating:'Ожидаем обновления Telegram',done:'Привязка проверена'};
   const errorTitles={not_connected:'Нужно подключить аккаунт владельца',revoked:'Подключение отозвано — войдите заново',automation_disabled:'Автопривязка выключена',proxy_not_found:'Прокси не найден в аккаунте владельца',proxy_ambiguous:'В @MTProxyBot несколько записей этого адреса',telegram_flood_wait:'Telegram попросил подождать',target_changed:'Заявка или канал изменились',binding_not_verified:'Бот пока не подтвердил привязку',owner_helper_unavailable:'Служба подключения пока недоступна',account_mismatch:'Подключён другой аккаунт владельца',connection_busy:'Аккаунт занят другой проверкой',public_channel_required:'Нужен доступный публичный канал',tag_changed:'Тег в Telegram изменился',telegram_unavailable:'Telegram временно недоступен',bot_response_timeout:'Бот не ответил вовремя',ambiguous_bot_response:'Ответ бота неоднозначен',proxy_identity_not_verified:'Адрес прокси не подтверждён',tag_not_verified:'Не удалось прочитать тег',set_promotion_button_missing:'Формат кнопок @MTProxyBot изменился',channel_prompt_not_verified:'Бот не подтвердил запрос канала'};
   for(const item of data.telegram_automation||[]){const row=el('div',undefined,'card binding-status');row.append(el('h3',item.channel_title||item.name),el('small',item.name,'muted'),el('p',item.error_code?(errorTitles[item.error_code]||'Формат ответа Telegram требует проверки'):(phaseTitles[item.phase]||'Проверяем состояние')));if(item.phase!=='done')row.append(el('p','Повтор не раньше: '+date(item.retry_at),'muted'));autoStatus.append(row);}
   if(!autoStatus.childElementCount)autoStatus.append(el('p','Автоматические привязки ещё не запускались. Агент берёт одобренные и оплаченные заявки, когда подходит время подготовки.','muted'));
   const commandText='sudo bash /opt/mtproxy-pushok/scripts/connect_telegram_owner.sh enable',command=el('code',commandText);
   const copyCommand=button('Скопировать команду',async()=>{await navigator.clipboard.writeText(commandText);notice('Команда скопирована. Выполните её в терминале VPS.',true);});copyCommand.prepend(window.pushokViews.icon('copy'));
   const connect=el('details',undefined,'owner-connect');connect.append(el('summary','Подключение и проверка'),el('p','Один раз выполните на VPS. Если вход уже сохранён, повторный код не потребуется.'),command,copyCommand,el('p','Код входа и пароль вводятся только в терминале VPS. Для первичного входа нужны api_id и api_hash с my.telegram.org/apps.','muted'),el('p','Проверить вход без изменения каналов:'),el('code','sudo bash /opt/mtproxy-pushok/scripts/connect_telegram_owner.sh status'));
   const owner=data.telegram_owner||{},ownerCard=el('section',undefined,'owner-connection'),ownerHead=el('div',undefined,'owner-connection-heading');ownerCard.id='telegram-owner-status';ownerCard.dataset.connectionState=owner.code||'unknown';ownerCard.dataset.tone=owner.tone||'muted';ownerCard.setAttribute('aria-live','polite');
   const refreshOwner=window.pushokViews.iconAction(button('',async()=>{await renderAutomation();}),'refresh','Обновить состояние подключения');
   ownerHead.append(window.pushokViews.icon('user'),el('h3',owner.title||'Подключение ещё не проверено'),refreshOwner);ownerCard.append(ownerHead);
   if(owner.account_username)ownerCard.append(el('p','@'+owner.account_username,'owner-account'));
   ownerCard.append(el('p',owner.next_action||'Подключите аккаунт владельца на VPS.','muted'));
   if(owner.last_verified_at)ownerCard.append(el('small','Вход проверен: '+date(owner.last_verified_at),'muted'));
   if(owner.agent_checked_at)ownerCard.append(el('small','Отчёт агента: '+date(owner.agent_checked_at),'muted'));
   automationBody.replaceChildren(ownerCard,connect,autoStatus,manual);
   if(routeParts()[0]==='sponsors'&&selectedChannel&&automationFocusRoute!==location.hash){
    automationFocusRoute=location.hash;
    window.scrollTo({top:window.scrollY+automationPanel.getBoundingClientRect().top-bar.getBoundingClientRect().height-12,behavior:'instant'});
    automationPanel.setAttribute('tabindex','-1');automationPanel.focus({preventScroll:true});
   }
   if(!nodes.length)automationBody.prepend(el('p','Нет узлов. Добавьте узел выше или восстановите его в разделе «Удалённые».'));
   for(const b of data.bindings){const row=el('div',undefined,'card');row.append(el('p',b.name+' → '+b.title+' · тег …'+b.tag_ending),button('Отключить',async()=>{await api('/admin/promotion-bindings/'+b.proxy_node_id+'/'+b.channel_id,{method:'DELETE'});await renderAutomation();notice('Связь отключена. При следующей проверке агент вернёт исходный тег.',true);}));automationBody.append(row);}
  }catch(e){if(rev===automationRevision&&token===session)automationBody.replaceChildren(el('p',e.message));}
 }

 // Настройки сохраняют отдельные вкладки и проверки роли существующего кода.
 [...pages.settings.children].filter(n=>!['page-heading','metrics','page-layout'].some(c=>n.classList.contains(c))).forEach(n=>content.settings.append(n));
 const faqs=[
  ['Как начать?','Откройте «Разместить рекламу», укажите канал, тариф и дату. Проверьте итог и отправьте на модерацию одной кнопкой.'],
  ['Как указать канал?','Используйте публичную ссылку https://t.me/channel. Исправления доступны в черновике или по запросу модератора.'],
  ['Цена и Stars','Цена фиксируется в заявке. RUB и XTR настраиваются отдельно, автоматического курса нет.'],
  ['Дата размещения','Выберите желаемое начало. После оплаты система назначит полный свободный период. Если выбранное время занято, размещение начнётся в ближайшее подходящее время после него.'],
  ['Как оплатить?',miniAppMode?'Откройте карточку оплаты заявки и выберите Stars. Успешность проверяется сервером.':'Откройте заявку. Реквизиты доступны после настройки владельцем. Чек JPEG, PNG или PDF — до 5 МБ. Поступление проверяется отдельно.'],
  [miniAppMode?'Telegram Stars':'Stars и СБП',miniAppMode?'Счёт Stars можно открыть в карточке оплаты, если приём включён владельцем.':'Stars оформляются в Telegram-боте. СБП доступна только во внешнем кабинете после настройки владельцем. Возврат из банка не подтверждает оплату: результат сверяется сервером.'],
  ['Что значит «Активно»?','Агент подтвердил применение тега и работу прокси. После окончания агент снимает или сменяет тег и завершает кампанию. Это не счётчик показов Telegram.'],
  ['Не удаётся выполнить действие','Обновите данные, проверьте комментарий и текущий статус. При завершении сессии войдите снова. Не повторяйте оплату вслепую.']
 ];
 faqs.forEach(([q,a])=>{const d=hint(a);d.querySelector('summary').textContent=q;content.help.append(d);});
 content.help.append(button('Показать стартовую инструкцию',async()=>{onboarding.open=true;onboarding.scrollIntoView({block:'center'});}),safeChannel('https://t.me/pushok_proxy','Канал проекта'));
 const botPanel=section('Работа с Telegram-ботом');content.bot.append(botPanel);
 function botLink(){
  if(!state.bot)return el('p','Имя Telegram-бота не настроено администратором.','muted');
  return safeChannel('https://t.me/'+state.bot,'Открыть @'+state.bot);
 }
 function renderList(){
  const staff=['admin','operator'].includes(me?.role);campaignScope.hidden=!staff;campaignScope.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scope===state.scope)));
  search.placeholder=staffCampaigns()?'Канал, номер заявки или @рекламодатель':'Поиск кампаний по каналу или номеру';
  const list=filtered();reset.hidden=!state.search&&!state.filter;window.pushokViews.campaigns($('campaigns'),list,staffCampaigns()?'sponsors':'campaign');
  $('campaign-count').textContent=list.length+' заявок';
  if(!list.length){empty($('campaigns'),state.search||state.filter?'Поиск не дал результатов. Измените запрос или сбросьте фильтр.':staffCampaigns()?'Текущих кампаний нет. Завершённые и удалённые доступны в архиве.':'У вас нет текущих личных кампаний. Завершённые доступны в архиве.');$('campaigns').append(link('Открыть архив','archive'));}
 }
 function renderQuote(){window.pushokOrder?.summary();}
 $('channel-select').addEventListener('change',renderQuote);$('tariff-select').addEventListener('change',renderQuote);
 function render({background=false}={}){
  if(!state.ready||!state.loaded||!me)return;
  state.renderKey=dataKey();
  profileShortcut.replaceChildren(window.pushokViews.profileAvatar());
  metrics('dashboard',[]);
  if(state.route==='dashboard'){void window.pushokGrowth.overview(dashboardReferral);}

  metrics('channels',[]);
  metrics('campaigns',[]);
  renderQuote();metrics('calendar',[]);
  metrics('reports',[['Текущих кампаний',state.campaigns.length,'Завершённые — в архиве'],['В эфире',state.campaigns.filter(c=>c.placement_status==='active').length,'По данным запуска']]);
  channelRows.classList.add('channel-manager');
  renderChannels();
  window.pushokViews.campaigns(summaryRows,[...state.campaigns].sort((a,b)=>({changes_requested:0}[a.moderation_status]??{awaiting_payment:1}[a.payment_status]??{active:2}[a.placement_status]??3)-({changes_requested:0}[b.moderation_status]??{awaiting_payment:1}[b.payment_status]??{active:2}[b.placement_status]??3)).slice(0,2));renderList();
  window.pushokViews.profile(content.profile,updatePanel);
  if(['admin','operator'].includes(me.role))window.pushokViews.sponsors(sponsorQueue,operatorCampaigns);
  onboarding.hidden=false;
  createNotice.hidden=Boolean(tariffs.length);createNotice.textContent='Нет доступных тарифов. Обратитесь к администратору.';
  sponsorPanel.hidden=me.role!=='admin';sponsorNode.replaceChildren();nodes.forEach(n=>option(sponsorNode,n.id,n.name));if(!nodes.length)option(sponsorNode,'','Нет узлов — добавьте выше или восстановите из удалённых');sponsorNode.disabled=!nodes.length;sponsorSave.disabled=!nodes.length;
  calendarTariff.replaceChildren();tariffs.forEach(t=>option(calendarTariff,t.id,tariffLabel(t)));calendarTariff.value=$('tariff-select').value;
  botPanel.replaceChildren(el('h2','Продолжить в Telegram'),botLink(),el('p','Кампании, статусы и оплата доступны в боте согласно настройкам сервера.'),el('h3','Сценарии в кабинете'));
  const shortcuts=el('nav',undefined,'bot-shortcuts');shortcuts.setAttribute('aria-label','Сценарии кабинета');[['create','Разместить рекламу'],['campaigns','Мои кампании'],['payments','Оплата'],['help','Помощь']].forEach(([route,text])=>shortcuts.append(link(text,route)));botPanel.append(shortcuts);
  const own=state.campaigns;
  window.pushokViews.campaigns(reportRows,own,'reports');
  const approved=own.filter(c=>c.moderation_status==='approved');
  window.pushokViews.paymentOverview(paymentOverview,approved);
  if(state.route!=='payments'||!routeParts()[1])window.pushokViews.campaigns(paymentRows,approved,'payments');
  operatorNodes.replaceChildren();if(me.role==='operator')table(operatorNodes,nodes.map(n=>[n.name,n.host+':'+n.port,n.status==='active'?'В реестре: активен':'На паузе']),['Узел','Адрес','Статус реестра']);
  metrics('sponsors',[['Узлов в реестре',nodes.length,'Доступность отдельно не проверялась']]);
  if(state.route)renderSelected({background});
 }
 function routeParts(){const p=location.hash.replace(/^#\/?/,'').split('/');return [Object.hasOwn(routes,p[0])?p[0]:'dashboard',p[1]||''];}
 async function renderStars(c,panel){
  const session=token,base='/campaigns/'+c.id+'/stars';
  const current=()=>panel.isConnected&&session===token&&me;
  panel.replaceChildren(el('p','Проверяем состояние Stars…'));
  try{
   const data=await api(base);if(!current())return;
   panel.replaceChildren(el('h3','Оплата Telegram Stars'));
   if(data.test_environment)panel.append(el('p','Тестовая среда Telegram · тестовые Stars'));
   if(data.payment_status==='verified'){panel.append(el('p','Оплата подтверждена сервером.'));return;}
   if(data.amount_xtr)panel.append(el('p','К оплате','eyebrow'),el('strong',data.amount_xtr+' XTR','checkout-amount'));
   if(!data.available)panel.append(el('p','Оплата Stars выключена до настройки и приёмки.'));
   if(data.available&&!data.amount_xtr)panel.append(el('p','Цена Stars ещё не настроена владельцем.'));
   if(data.processing)panel.append(el('p','Платёж обрабатывается. Не оплачивайте повторно.'));
   else if(data.available&&data.amount_xtr&&data.payment_status==='awaiting_payment'){
    const pay=button('Оплатить '+data.amount_xtr+' XTR',async()=>{
     if(!window.pushokMiniApp.canPay())throw Error('Обновите Telegram для оплаты Stars.');
     const invoice=await api(base+'/checkout',{method:'POST'});if(!current())return;
     if(invoice.amount_xtr!==data.amount_xtr){notice('Стоимость Stars обновилась. Проверьте сумму перед оплатой.');await renderStars(c,panel);return;}
     panel.append(el('p','Счёт открыт. Результат будет проверен сервером.','muted'));
     await window.pushokMiniApp.payInvoice(invoice.invoice_url);
     if(!current())return;
     // invoiceClosed("paid") не является доказательством. Только API после bot receipt.
     await refresh();if(current())await renderStars(c,panel);
    });pay.classList.add('primary');pay.prepend(window.pushokViews.icon('star'));panel.append(pay);
   }
   panel.append(button('Обновить статус оплаты',async()=>{await refresh();if(current())await renderStars(c,panel);}));
   panel.append(window.pushokViews.mascot('Оплата и подтверждение запуска — отдельные этапы.'));
   panel.append(el('p','Закрытие окна оплаты не подтверждает перевод. Если статус ещё не обновился, проверьте его позже.','muted'));
  }catch(e){if(current()){panel.replaceChildren(el('p',e.message));panel.append(button('Повторить проверку Stars',()=>renderStars(c,panel)));}}
 }
 async function renderSbp(c,panel){
  if(c.price_kopecks_snapshot>0&&!panel.walletBox){const box=el('section',undefined,'wallet-checkout');panel.before(box);panel.walletBox=box;void window.pushokWallet?.payment(c,box);}
  if(c.price_kopecks_snapshot===0){panel.replaceChildren(el('h3','Бесплатное размещение'),el('p',c.moderation_status==='approved'?'Канал одобрен. Оплата не требуется; система подберёт свободный период для подготовленного канала.':'После одобрения канала оплата не потребуется.'));return;}

  if(miniAppMode){await renderStars(c,panel);return;}
  const base='/campaigns/'+c.id+'/sbp';
  async function paint(data){
   if(!panel.isConnected)return;
   panel.replaceChildren(el('h3','СБП · внешний сайт'));
   panel.append(el('p','Сумма заявки: '+money(data.amount_kopecks)));
   if(data.test_mode)panel.append(el('p','Настроен ТЕСТОВЫЙ РЕЖИМ. Сервер проверяет режим счёта перед показом ссылки.','sbp-test-note'));
   const statuses={not_created:'Счёт не выставлен.',created:'Результат выпуска счёта ещё не подтверждён. Повторный запрос использует тот же ключ.',pending:'Ожидается результат платежа. Не переводите деньги повторно.',succeeded:'Платёж подтверждён серверной сверкой.',failed:'Платёж отменён провайдером.'};
   const status=el('p',statuses[data.status]||'Нужна проверка оператора.');status.setAttribute('role','status');panel.append(status);
   if(!data.available)panel.append(el('p','Новые счета СБП выключены или настройки не завершены.','muted'));
   if(data.can_create&&['not_created','created','failed'].includes(data.status))panel.append(button(data.status==='created'?'Повторить выпуск счёта':'Выставить счёт СБП',async()=>{
    await paint(await api(base+'/checkout',{method:'POST'}));
   }));
   if(data.confirmation_url){
    try{const u=new URL(data.confirmation_url);if(u.protocol==='https:'&&!u.username&&!u.password){const a=el('a','Перейти к оплате СБП');a.href=u.href;a.target='_self';a.rel='noreferrer';panel.append(a);}}catch{}
   }
   if(data.can_verify)panel.append(button('Проверить платёж СБП',async()=>{
    const result=await api(base+'/reconcile',{method:'POST'});
    if(result.payment_status==='verified'){await refresh();notice('Результат СБП подтверждён сервером.',true);}
    else await paint(result);
   }));
   panel.closest('.page-content').querySelectorAll('input[aria-label="Подтверждение оплаты"]').forEach(input=>input.disabled=['created','pending'].includes(data.status));
   if(['created','pending'].includes(data.status)){
    panel.append(el('p','Пока этот счёт не проверен или не отменён провайдером, не переходите к другому способу оплаты.','muted'));
   }
   if(state.returnCampaign===c.id){
    state.returnCampaign=null;
    if(data.can_verify){const result=await api(base+'/reconcile',{method:'POST'});if(result.payment_status==='verified'){await refresh();notice('Платёж после возврата подтверждён сервером.',true);}else await paint(result);}
   }
  }
  panel.append(el('p','Загрузка состояния СБП…'));
  try{await paint(await api(base));}catch(e){if(panel.isConnected){panel.replaceChildren(el('p',e.message));panel.append(button('Повторить проверку СБП',()=>renderSbp(c,panel)));}}
 }
 function permitted(name){return !(['moderation','sponsors','user','users'].includes(name)&&!['admin','operator'].includes(me?.role))&&!(['settings','license'].includes(name)&&me?.role!=='admin');}
 function renderSelected({background=false}={}){
  const [name,id]=routeParts();
  if(name==='channels'){
   const adding=id==='new';channelAdd.hidden=!adding;channelList.hidden=adding;
   pages.channels.querySelector('h1').textContent=adding?'Добавить канал':'Мои каналы';
   pages.channels.querySelector('.page-heading>.icon-action').hidden=adding;
   pages.channels.querySelector('.campaign-tabs').hidden=adding;
  }
  if(name==='users')window.pushokUsers.directory(content.users);
  if(name==='user')window.pushokProfiles.show(content.user,id);
  helpText.textContent=hints[name]||'Удалённые объекты можно восстановить. Для заявки сначала восстановите её канал и тариф.';
  const selected=state.campaigns.find(c=>c.id===id),key=JSON.stringify([name,id,selected]);
  if(background&&id&&['campaign','payments','create'].includes(name)&&state.selectedKey===key){refreshLivePanels();return;}
  const openPanels=background&&selected?[...pages[name].querySelectorAll('.campaign-report,.automation-panel')]:[];
  state.selectedKey=key;
  if(name==='license'&&!background)void window.pushokAppLicense.load(content.license);
  if(name==='wallet')void window.pushokWallet?.load(content.wallet);
  if(name==='growth')void window.pushokGrowth?.load(content.growth);
  if(name==='archive')void window.pushokManagement.renderArchive(archiveRows,id);
  if(name==='sponsors')void renderAutomation();
  if(name==='moderation')window.pushokApproval?.render();
  if(name!=='campaign'){detail.replaceChildren();metrics('campaign',[]);}
  if(name!=='create')$('order-result').replaceChildren();
  if(name==='create'){
   const result=$('order-result');$('campaign-form').hidden=Boolean(id);result.hidden=!id;result.replaceChildren();
   if(id){
    const c=state.campaigns.find(c=>c.id===id);
    if(c){
     result.append(el('h2',c.moderation_status==='pending'?'Заявка на проверке':'Ваша заявка'));
     if(c.moderation_status==='pending')result.append(el('p','Мы проверим канал. После одобрения здесь появится оплата. Бесплатный тариф не требует оплаты.'));
     result.append(card(c,false,{compact:true}));if(c.placement_status==='scheduled'||c.placement_status==='active')void renderPromotionStatus(c,result);
     if(c.moderation_status==='approved'&&c.price_kopecks_snapshot>0){const checkout=el('section',undefined,'sbp-checkout');result.append(checkout);void renderSbp(c,checkout);}
    }else result.append(el('p','Заявка не найдена или недоступна. Обновите список.'));
    result.append(link('Создать ещё одну заявку','create'),link('Мои заявки','campaigns'));
   }
  }
  if(name==='campaign'){
   detail.replaceChildren();const c=state.campaigns.find(c=>c.id===id);
   if(c){detail.append(card(c,false));void renderPromotionStatus(c,detail);metrics('campaign',[]);}
   else{empty(detail,'Кампания отсутствует в рабочем списке или недоступна вашему аккаунту. Завершённые кампании находятся в архиве.');detail.append(link('Открыть архив','archive/'+id));metrics('campaign',[]);}
  }
  paymentOverview.hidden=name==='payments'&&Boolean(id);
  if(name==='payments'&&id){
   paymentRows.replaceChildren();const c=state.campaigns.find(c=>c.id===id&&c.moderation_status==='approved');
   paymentRows.append(link('← Все оплаты','payments'));
   if(c){const panel=el('section',undefined,'panel sbp-checkout');paymentRows.append(card(c,false),panel,botLink(),el('p',miniAppMode?'Статус оплаты Stars обновляется по данным сервера.':'Stars: продолжите в Telegram-боте, если приём включён. Ручной чек — запасной способ по инструкции владельца.','muted'));void renderSbp(c,panel);}
   else paymentRows.append(el('p','Заявка недоступна или ещё не одобрена.'));
  }
  const updatedCard=pages[name]?.querySelector('.campaign-detail');
  if(updatedCard&&openPanels.length){
   updatedCard.append(...openPanels);
   if(updatedCard.querySelector('.campaign-report'))void showCampaignReport(selected,updatedCard,{refresh:true}).catch(()=>{});
   updatedCard.querySelectorAll('.automation-panel').forEach(panel=>void updatePromotionPanel(panel));
  }
 }
 function back(){
  const [name,id]=routeParts();
  if(name==='user'){go(window.pushokProfiles.backRoute());return;}
  if(name==='moderation'&&id)window.pushokApproval?.resetSelection();
  go(id?(name==='campaign'||name==='create'?'campaigns':name):['create','channels','calendar','archive'].includes(name)?'campaigns':name==='payments'?'wallet':['dashboard','campaigns','wallet','profile'].includes(name)?'dashboard':'profile');
 }
 function go(route){if(location.hash==='#/'+route){activate();return;}location.hash='/'+route;}
 function activate(){
  if(!me||!state.ready)return;
  let [name,section]=routeParts();
  if(name==='campaigns')state.scope=section==='mine'?'mine':'all';
  if(name!=='sponsors')automationFocusRoute='';
  if(!permitted(name)){history.replaceState(null,'','#/dashboard');name='dashboard';notice('Этот раздел недоступен вашей роли.');}
  if($('notice').dataset.route!==location.hash)$('notice').hidden=true;
  topHelp.open=false;state.route=name;Object.entries(pages).forEach(([key,page])=>page.hidden=key!==name);
  $('advertiser').hidden=true;$('operator').hidden=true;
  if(name==='calendar')calendarPanel.append(calendar);else calendarOrigin.append(calendar);
  const canModerate=['admin','operator'].includes(me.role);bottomNav.style.setProperty('--nav-count',canModerate?5:4);
  bottomNav.querySelectorAll('a').forEach(a=>{a.hidden=a.dataset.route==='moderation'&&!canModerate;const section=['campaign','create','archive','calendar','channels'].includes(name)?'campaigns':name==='payments'?'wallet':name==='user'?'moderation':['dashboard','moderation','campaigns','wallet'].includes(name)?name:'profile';const active=a.dataset.route===section;if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  backButton.hidden=['dashboard','campaigns','wallet','profile'].includes(name)||(name==='moderation'&&!section);profileShortcut.hidden=name!=='dashboard';
  render();window.scrollTo({top:0,left:0,behavior:'instant'});pages[name].querySelector('h1').focus({preventScroll:true});document.title=routes[name][0]+' · MTProxy Pushok';
  if(name==='channels'&&section==='new'){$('channel-form').elements.telegram_url.focus({preventScroll:true});document.title='Добавить канал · MTProxy Pushok';}
  searchForm.hidden=name!=='campaigns';
  updatePanel.hidden=me.role!=='admin';
  if(me.role==='admin'&&['profile','dashboard'].includes(name))refreshSoftware();
  if(name==='settings')run(loadAdminSettings);
  if(miniAppMode)window.pushokMiniApp.syncNavigation();
  if(name==='profile'&&section==='updates'&&me.role==='admin'){
   window.scrollTo({top:window.scrollY+updatePanel.getBoundingClientRect().top-bar.getBoundingClientRect().height-16,behavior:'instant'});
   $('software-updates-title').focus({preventScroll:true});
  }
 }
 window.cabinet={go,back,sync(own,{background=false}={}){state.campaigns=own;state.loaded=true;document.body.dataset.cabinetLoaded='true';if(background&&state.renderKey===dataKey()){refreshLivePanels();return;}render({background});},enter(){
  const url=new URL(location.href),returned=url.searchParams.get('sbp_campaign');
  if(url.searchParams.has('wallet_topup')){url.searchParams.delete('wallet_topup');url.hash='/wallet';history.replaceState(null,'',url);}
  if(!miniAppMode&&returned&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(returned)){state.returnCampaign=returned;url.searchParams.delete('sbp_campaign');url.hash='/payments/'+returned;history.replaceState(null,'',url);}
  state.ready=true;document.body.classList.add('cabinet-mode');document.documentElement.style.scrollBehavior='auto';account.textContent=(me.first_name||'Пользователь')+' · '+({admin:'Администратор',operator:'Оператор',advertiser:'Рекламодатель'}[me.role]||me.role);activate();
 },logout(){window.pushokAppLicense?.reset();window.pushokLinks?.reset();window.pushokViews.clearLogos();window.pushokWallet?.reset();window.pushokGrowth?.reset();launchReadinessEpoch++;clearLaunchCheck();$('launch-check-fields').disabled=false;tariffDraftId=null;tariffEditorDirty=false;adminTariffs=[];previousTariffId='';clearSoftware();$('settings-system').prepend(updatePanel);$('sbp-review-list').replaceChildren();window.pushokApproval?.clear();state.ready=false;state.loaded=false;state.renderKey='';state.selectedKey='';state.campaigns=[];state.search='';state.filter='';ownCampaigns=[];operatorCampaigns=[];channels=[];tariffs=[];nodes=[];me=null;window.cabinetBootReady=false;window.pushokOrder.reset();$('identity').textContent='Гость';document.body.classList.remove('cabinet-mode');delete document.body.dataset.cabinetLoaded;
  bottomNav.querySelector('[data-route=moderation]').hidden=true;bottomNav.style.setProperty('--nav-count',4);
  channelSearch.value='';
  [$('order-result'),detail,dashboardReferral,profileShortcut,paymentOverview,paymentRows,content.profile,content.user,content.users,sponsorQueue,reportRows,archiveRows,channelRows,summaryRows,$('campaigns'),$('queue'),$('nodes-list'),diagnosticsBody].forEach(n=>n.replaceChildren());Object.values(stats).forEach(n=>n.replaceChildren());diagnosticsPanel.hidden=true;diagnosticsRevision++;
  workspace.querySelectorAll('form').forEach(form=>form.reset());sponsorTag.value='';sponsorResult.textContent='';account.textContent='';
  document.documentElement.style.scrollBehavior='';
  ['settings-stars-counts','settings-stars-status','settings-sbp-status','launch-readiness','core-readiness','core-test-plan'].forEach(id=>$(id).replaceChildren());
 },config(config){if(/^https:\/\/t\.me\/proxy\?/.test(config.proxy_url||'')){connectProxy.href=config.proxy_url;connectProxy.target='_blank';connectProxy.rel='noopener noreferrer';connectProxy.removeAttribute('aria-disabled');connectProxy.title='Подключить прокси в Telegram';}state.bot=/^[A-Za-z0-9_]{5,32}$/.test(config.telegram_bot_username||'')?config.telegram_bot_username:null;}};
 const fieldHelp=[
  [$('channel-form').elements.telegram_url,'Публичная ссылка вида https://t.me/channel. Доступность и содержание проверяются при модерации.'],
  [$('tariff-select'),'Цена фиксируется при создании. Существующие заявки сохранят прежнюю стоимость.'],
  [$('calendar-time'),'Время в часовом поясе, указанном под календарём.'],
  [$('engine-mode'),'Сохраняется только план. Работающий прокси не запускается и не переключается.'],
  [$('tariff-admin-rub'),'Рубли с копейками для новых заявок. Цена Stars задаётся отдельно.'],
  [$('tariff-admin-stars'),'Целое число XTR. Пустое поле отключает цену Stars для этого тарифа.']
 ];
 fieldHelp.forEach(([input,text],i)=>{const note=el('p',text,'field-help');note.id='field-help-'+i;input.setAttribute('aria-describedby',note.id);const help=window.pushokViews.disclosure('',[note],'field-hint'),summary=help.querySelector('summary'),label=input.closest('label'),wrap=el('div',undefined,'field-with-help');summary.append(window.pushokViews.icon('info'));summary.setAttribute('aria-label','Подсказка: '+label.childNodes[0].textContent.trim());summary.title='Подробнее';label.before(wrap);wrap.append(label,help);});
 document.addEventListener('click',e=>document.querySelectorAll('.field-hint[open]').forEach(h=>{if(!h.contains(e.target))h.open=false;}));
 window.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelectorAll('.field-hint[open]').forEach(h=>h.open=false);});
 window.addEventListener('hashchange',activate);
 if(window.cabinetBotConfig)window.cabinet.config(window.cabinetBotConfig);
 if(window.cabinetBootReady&&me){window.cabinet.sync(ownCampaigns);window.cabinet.enter();}
})();
