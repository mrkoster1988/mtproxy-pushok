'use strict';
// Страницы используют общие формы и действия app.js, без второго набора API.
(() => {
 const state={campaigns:[],search:'',filter:'',route:'',bot:null,ready:false,loaded:false};
 const routes={
  dashboard:['Главная','Обзор ваших размещений'],
  channels:['Мои каналы','Управление рекламируемыми Telegram-каналами'],
  create:['Создать кампанию','Новая заявка на спонсорское размещение'],
  campaigns:['Кампании','Статусы ваших рекламных заявок'],
  campaign:['Карточка кампании','Модерация, оплата и размещение — отдельные этапы'],
  payments:['Оплата рекламы','Подтверждение оплаты и доступные способы'],
  calendar:['Календарь размещений','Доступность на полную длительность тарифа'],
  reports:['Отчёты и история','Подтверждённые события ваших кампаний'],
  moderation:['Модерация','Очередь заявок оператора'],
  sponsors:['Спонсоры и узлы','Реестр узлов и план движка'],
  settings:['Настройки','Тарифы, платежи и готовность системы'],
  help:['Помощь и инструкции','Пошаговые сценарии и ответы на вопросы'],
  bot:['Telegram-бот','Продолжите работу в Telegram']
 };
 const hints={
  dashboard:'Здесь показаны данные ваших кампаний из кабинета. Показ спонсора проверяется отдельно.',
  channels:'API принимает публичную ссылку https://t.me/channel. Добавление канала ещё не означает одобрение рекламы.',
  create:'Цена фиксируется в черновике. После создания проверьте заявку и отправьте её на модерацию.',
  campaigns:'Одобрение канала, подтверждение оплаты и фактическое размещение — разные статусы.',
  campaign:'Если нужны исправления, прочтите комментарий, исправьте канал и отправьте заявку повторно.',
  payments:'Не переводите деньги повторно до выяснения результата. Загрузка чека не подтверждает поступление денег.',
  calendar:'Выбор даты не бронирует слот. Временная бронь на 15 минут доступна в одобренной заявке до проверки оплаты.',
  reports:'История отражает события системы и ручные наблюдения. Счётчик показов Telegram недоступен.',
  moderation:'Проверяйте канал и оплату раздельно. Активация требует положительного наблюдения оператора.',
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
  const wrap=el('div',undefined,'table-scroll'),t=el('table'),head=el('thead'),tr=el('tr');
  headers.forEach(text=>{const th=el('th',text);th.scope='col';tr.append(th);});head.append(tr);t.append(head);
  const body=el('tbody');rows.forEach(row=>{const r=el('tr');row.forEach(value=>{const cell=el('td');cell.append(typeof value==='string'?document.createTextNode(value):value);r.append(cell);});body.append(r);});
  t.append(body);wrap.append(t);target.append(wrap);
 }
 function badge(value){return el('span',labels[value]||value,'badge badge--'+value);}
 function campaignRows(list){return list.map(c=>[
  link(c.channel_title||channels.find(x=>x.id===c.channel_id)?.title||c.id.slice(0,8),'campaign/'+c.id),
  badge(c.moderation_status),badge(c.payment_status),badge(c.placement_status),money(c.price_kopecks_snapshot),
  date(c.scheduled_start_at),link('Открыть','campaign/'+c.id)
 ]);}
 const campaignHeaders=['Канал','Модерация','Оплата','Размещение','Цена заявки','Начало','Действие'];
 function filtered(){return state.campaigns.filter(c=>
  (!state.filter||[c.moderation_status,c.payment_status,c.placement_status].includes(state.filter))&&
  [c.channel_title,c.channel_url,c.id,channels.find(x=>x.id===c.channel_id)?.title].some(v=>String(v||'').toLocaleLowerCase('ru').includes(state.search.toLocaleLowerCase('ru'))));}
 const shell=el('div',undefined,'cabinet-shell'),sidebar=el('aside',undefined,'cabinet-sidebar');sidebar.id='cabinet-sidebar';
 const brand=link('✳ puShok · MTProxy','dashboard');brand.className='cabinet-brand';sidebar.append(brand);
 const nav=el('nav');nav.setAttribute('aria-label','Разделы страниц кабинета');
 const menu=[['dashboard','⌂','Главная'],['channels','▣','Мои каналы'],['campaigns','▤','Кампании'],['payments','◇','Оплата'],['calendar','▦','Календарь'],['reports','▥','Отчёты'],['moderation','✓','Модерация'],['sponsors','⬡','Узлы'],['settings','⚙','Настройки'],['help','?','Помощь'],['bot','↗','Telegram-бот']];
 menu.forEach(([name,icon,title])=>{const a=link(icon+'  '+title,name);a.dataset.route=name;nav.append(a);});sidebar.append(nav);
 sidebar.append(el('p','Подсказки и инструкции доступны в каждом разделе.','sidebar-note'));
 const stage=el('div',undefined,'cabinet-stage'),bar=el('div',undefined,'cabinet-topbar');
 const toggle=button('☰ Меню',async()=>{const open=sidebar.classList.toggle('is-open');toggle.setAttribute('aria-expanded',String(open));});
 toggle.className='menu-toggle';toggle.setAttribute('aria-controls','cabinet-sidebar');toggle.setAttribute('aria-expanded','false');
 const searchForm=el('form'),search=el('input');search.type='search';search.placeholder='Поиск кампаний по каналу или номеру';search.setAttribute('aria-label','Поиск кампаний');
 searchForm.append(search);searchForm.addEventListener('submit',e=>{e.preventDefault();state.search=search.value.trim();go('campaigns');renderList();});
 const account=el('span',undefined,'cabinet-account');account.id='cabinet-account';
 const refreshButton=$('refresh');bar.append(toggle,searchForm,account,refreshButton,$('logout'));stage.append(bar);shell.append(sidebar,stage);workspace.prepend(shell);
 Object.entries(routes).forEach(([name,[title,subtitle]])=>{
  const page=name==='settings'?$('settings'):el('section');page.dataset.page=name;page.hidden=true;page.classList.add('cabinet-page');
  const heading=el('header',undefined,'page-heading'),titles=el('div');const h=el('h1',title);h.tabIndex=-1;titles.append(h,el('p',subtitle));heading.append(titles);
  if(['dashboard','campaigns','channels'].includes(name))heading.append(link(name==='channels'?'+ Добавить канал':'+ Создать кампанию',name==='channels'?'channels':'create'));
  page.prepend(heading);const grid=el('div',undefined,'metrics');page.insertBefore(grid,heading.nextSibling);stats[name]=grid;
  const layout=el('div',undefined,'page-layout'),body=el('div',undefined,'page-content'),aside=el('aside',undefined,'page-help');
  aside.append(hint(hints[name]),el('h2','Как это работает'));
  const steps=el('ol');[['channels','Добавьте канал'],['create','Выберите тариф и дату'],['campaigns','Дождитесь модерации'],['payments','Оплатите и следите за запуском']].forEach(([route,text])=>{const li=el('li');li.append(link(text,route));steps.append(li);});aside.append(steps,link('Подробнее в справке','help'));
  layout.append(body,aside);page.append(layout);pages[name]=page;content[name]=body;stage.append(page);
 });
 // Перемещаем существующие формы вместе с обработчиками. Все id остаются уникальными.
 const channelPanel=$('channel-form').closest('section');content.channels.append(channelPanel);
 const channelList=section('Ваши каналы'),channelRows=el('div');channelList.append(channelRows);content.channels.prepend(channelList);
 content.create.append($('campaign-form').closest('section'));
 const createNotice=el('p',undefined,'muted');content.create.prepend(createNotice);
 const calendar=$('availability-days').closest('.calendar');const calendarOrigin=calendar.parentNode;
 const calendarPanel=section('Выбор даты'),calendarTariff=el('select');calendarTariff.setAttribute('aria-label','Тариф календаря');calendarPanel.append(calendarTariff);content.calendar.append(calendarPanel);
 calendarTariff.addEventListener('change',()=>run(async()=>{$('tariff-select').value=calendarTariff.value;await loadAvailability();}));
 content.calendar.append(link('Перейти к созданию кампании','create'));
 const listTools=el('div',undefined,'list-tools'),filter=el('select');filter.setAttribute('aria-label','Статус кампаний');
 [['','Все статусы'],['draft','Черновики'],['pending','На модерации'],['changes_requested','Нужны исправления'],['awaiting_payment','Ожидают оплаты'],['active','Активные'],['completed','Завершённые']].forEach(([v,t])=>option(filter,v,t));
 const reset=button('Сбросить поиск и фильтр',async()=>{state.search='';state.filter='';search.value='';filter.value='';renderList();});
 filter.addEventListener('change',()=>{state.filter=filter.value;renderList();});listTools.append(filter,reset);content.campaigns.append(listTools,$('campaigns'));
 const summary=section('Ваши кампании'),summaryRows=el('div','Загрузка данных. При ошибке нажмите «Обновить».');summary.append(summaryRows);content.dashboard.append(summary);
 const onboarding=section('С чего начать');onboarding.id='onboarding';onboarding.append(el('p','Добавьте публичный канал, создайте черновик и отправьте его на проверку. После одобрения откройте оплату.'));
 onboarding.append(button('Скрыть инструкцию',async()=>{sessionStorage.setItem('pushok-onboarding-hidden','1');onboarding.hidden=true;}),link('Начать с канала','channels'));content.dashboard.prepend(onboarding);
 const paymentRows=el('div');content.payments.append(paymentRows);const reportRows=el('div');content.reports.append(reportRows);
 const detail=el('div');content.campaign.append(link('← Все кампании','campaigns'),detail);
 content.moderation.append($('queue-filter'),$('queue-count'),$('queue'));
 const operatorNodes=el('div');content.sponsors.append($('node-admin'),operatorNodes);
 const sponsorForm=el('form'),sponsorPanel=section('Регистрация спонсорского тега');sponsorForm.id='sponsor-form';
 const sponsorNode=el('select');sponsorNode.required=true;sponsorNode.setAttribute('aria-label','Узел спонсорского тега');
 const sponsorEngine=el('select');sponsorEngine.setAttribute('aria-label','Движок спонсорского тега');Object.entries(engineTitles).forEach(([v,t])=>option(sponsorEngine,v,t));
 const sponsorTag=el('input');sponsorTag.type='password';sponsorTag.required=true;sponsorTag.minLength=32;sponsorTag.maxLength=32;sponsorTag.pattern='[0-9a-fA-F]{32}';sponsorTag.autocomplete='off';sponsorTag.setAttribute('aria-label','ad_tag из MTProxyBot');
 function field(title,input){const label=el('label',title);label.append(input);return label;}
 const sponsorSave=el('button','Сохранить регистрацию');sponsorSave.type='submit';
 const sponsorResult=el('p',undefined,'muted');sponsorResult.setAttribute('role','status');
 sponsorForm.append(field('Узел',sponsorNode),field('Движок',sponsorEngine),field('ad_tag из @MTProxyBot · 32 шестнадцатеричных символа',sponsorTag),sponsorSave);
 sponsorPanel.append(el('p','Регистрация сохраняется в базе. Тег не применяется к прокси; Set promotion и проверка показа выполняются вручную.','muted'),sponsorForm,sponsorResult);content.sponsors.append(sponsorPanel);
 sponsorForm.addEventListener('submit',e=>{e.preventDefault();run(async()=>{
  const data=await api('/admin/proxy-nodes/'+sponsorNode.value+'/sponsor-registration',{method:'PUT',body:{engine:sponsorEngine.value,ad_tag:sponsorTag.value}});
  sponsorTag.value='';sponsorResult.textContent='Сохранён тег с окончанием '+data.tag_ending+'. Изменения на прокси не применялись.';
 },sponsorSave);});
 // Настройки сохраняют отдельные вкладки и проверки роли существующего кода.
 [...pages.settings.children].filter(n=>!['page-heading','metrics','page-layout'].some(c=>n.classList.contains(c))).forEach(n=>content.settings.append(n));
 const faqs=[
  ['Как начать?','Добавьте канал и создайте черновик. Отправка на модерацию — отдельное действие в карточке.'],
  ['Как указать канал?','Используйте публичную ссылку https://t.me/channel. Исправления доступны в черновике или по запросу модератора.'],
  ['Цена и Stars','Цена фиксируется в заявке. RUB и XTR настраиваются отдельно, автоматического курса нет.'],
  ['Дата и бронь','Календарь справочный. В одобренной заявке можно получить бронь на 15 минут, если API найдёт свободный узел.'],
  ['Как оплатить?','Откройте заявку. Реквизиты доступны после настройки владельцем. Чек JPEG, PNG или PDF — до 5 МБ. Поступление проверяется отдельно.'],
  ['Stars и СБП','Stars оформляются в Telegram-боте, если разрешены сервером. Веб-эквайринг СБП сейчас не подключён. Не оплачивайте по неподтверждённым реквизитам.'],
  ['Что значит «Активно»?','Оператор отметил запуск после ручного включения тега и отдельной проверки показа. Это не автоматический счётчик показов.'],
  ['Не удаётся выполнить действие','Обновите данные, проверьте комментарий и текущий статус. При завершении сессии войдите снова. Не повторяйте оплату вслепую.']
 ];
 faqs.forEach(([q,a])=>{const d=hint(a);d.querySelector('summary').textContent=q;content.help.append(d);});
 content.help.append(button('Показать стартовую инструкцию',async()=>{sessionStorage.removeItem('pushok-onboarding-hidden');go('dashboard');}),safeChannel('https://t.me/pushok_proxy','Канал проекта'));
 const botPanel=section('Работа с Telegram-ботом');content.bot.append(botPanel);
 function botLink(){
  if(!state.bot)return el('p','Имя Telegram-бота не настроено администратором.','muted');
  return safeChannel('https://t.me/'+state.bot,'Открыть @'+state.bot);
 }
 function renderList(){
  const list=filtered();table($('campaigns'),campaignRows(list),campaignHeaders);
  $('campaign-count').textContent=list.length+' заявок';
  if(!list.length)empty($('campaigns'),state.search||state.filter?'Поиск не дал результатов. Измените запрос или сбросьте фильтр.':'Заявок пока нет. Создайте первую кампанию.');
 }
 function render(){
  if(!state.ready||!state.loaded||!me)return;
  metrics('dashboard',[
   ['Активные кампании',state.campaigns.filter(c=>c.placement_status==='active').length,'По статусу размещения'],
   ['На модерации',state.campaigns.filter(c=>c.moderation_status==='pending').length,'Ожидают решения'],
   ['Оплата подтверждена',state.campaigns.filter(c=>c.payment_status==='verified').length,'Количество заявок'],
   ['Каналы',channels.length,'В вашем аккаунте']
  ]);
  metrics('channels',[['Каналов',channels.length,'Добавлены в кабинет']]);
  metrics('campaigns',[['Всего',state.campaigns.length,'Ваши заявки'],['Ожидают оплаты',state.campaigns.filter(c=>c.payment_status==='awaiting_payment').length,'После модерации'],['Завершены',state.campaigns.filter(c=>c.placement_status==='completed').length,'По данным системы']]);
  table(channelRows,channels.map(c=>[c.title,safeChannel(c.telegram_url,c.telegram_url),link('Создать кампанию','create')]),['Название','Ссылка','Действие']);
  table(summaryRows,campaignRows(state.campaigns.slice(0,5)),campaignHeaders);renderList();
  onboarding.hidden=sessionStorage.getItem('pushok-onboarding-hidden')==='1';
  createNotice.textContent=!channels.length?'Сначала добавьте канал в разделе «Мои каналы».':!tariffs.length?'Нет доступных тарифов. Обратитесь к администратору.':'Проверьте канал, тариф и желаемую дату. Отправка на модерацию доступна после создания.';
  sponsorPanel.hidden=me.role!=='admin';sponsorNode.replaceChildren();nodes.forEach(n=>option(sponsorNode,n.id,n.name));
  calendarTariff.replaceChildren();tariffs.forEach(t=>option(calendarTariff,t.id,t.name+' · '+money(t.price_kopecks)));calendarTariff.value=$('tariff-select').value;
  botPanel.replaceChildren(el('h2','Главное меню бота'),botLink(),el('p','Кампании, статусы и оплата доступны в боте согласно настройкам сервера.'));
  const own=state.campaigns;
  table(reportRows,own.map(c=>[link(c.channel_title||c.id.slice(0,8),'campaign/'+c.id),badge(c.placement_status),button('Открыть отчёт',async()=>{reportRows.querySelectorAll('.campaign-report').forEach(x=>x.remove());await showCampaignReport(c,reportRows);}),button('Скачать CSV',()=>downloadCampaignCsv(c))]),['Кампания','Размещение','История','Экспорт']);
  const approved=own.filter(c=>c.moderation_status==='approved');
  table(paymentRows,approved.map(c=>[link(c.channel_title||c.id.slice(0,8),'payments/'+c.id),money(c.price_kopecks_snapshot),badge(c.payment_status),link('Открыть оплату','payments/'+c.id)]),['Кампания','Цена заявки','Статус','Действие']);
  paymentRows.prepend(el('p','Для оплаты сначала требуется одобрение канала. СБП в веб-кабинете не подключена. Stars — через бота при включении на сервере.','muted'));
  operatorNodes.replaceChildren();if(me.role==='operator')table(operatorNodes,nodes.map(n=>[n.name,n.host+':'+n.port,n.status==='active'?'В реестре: активен':'На паузе']),['Узел','Адрес','Статус реестра']);
  metrics('sponsors',[['Узлов в реестре',nodes.length,'Доступность отдельно не проверялась']]);
  if(state.route)renderSelected();
 }
 function routeParts(){const p=location.hash.replace(/^#\/?/,'').split('/');return [Object.hasOwn(routes,p[0])?p[0]:'dashboard',p[1]||''];}
 function permitted(name){return !(['moderation','sponsors'].includes(name)&&!['admin','operator'].includes(me?.role))&&!(name==='settings'&&me?.role!=='admin');}
 function renderSelected(){
  const [name,id]=routeParts();
  if(name==='campaign'){
   detail.replaceChildren();const c=state.campaigns.find(c=>c.id===id);
   if(c){detail.append(card(c,false));metrics('campaign',[['Модерация',labels[c.moderation_status],'По данным API'],['Оплата',labels[c.payment_status],'По данным API'],['Размещение',labels[c.placement_status],'По данным API']]);}
   else{empty(detail,'Кампания не найдена или недоступна вашему аккаунту.');metrics('campaign',[]);}
  }
  if(name==='payments'&&id){
   paymentRows.replaceChildren();const c=state.campaigns.find(c=>c.id===id&&c.moderation_status==='approved');
   paymentRows.append(link('← Все оплаты','payments'));
   if(c){paymentRows.append(card(c,false),botLink(),el('p','Stars: продолжите в Telegram-боте, если приём включён. СБП: веб-оплата недоступна. Проверка статуса — кнопкой «Обновить».','muted'));}
   else paymentRows.append(el('p','Заявка недоступна или ещё не одобрена.'));
  }
 }
 function go(route){if(location.hash==='#/'+route){activate();return;}location.hash='/'+route;}
 function activate(){
  if(!me||!state.ready)return;
  let [name]=routeParts();
  if(!permitted(name)){notice('Этот раздел недоступен вашей роли.');history.replaceState(null,'','#/dashboard');name='dashboard';}
  state.route=name;Object.entries(pages).forEach(([key,page])=>page.hidden=key!==name);
  $('advertiser').hidden=true;$('operator').hidden=true;
  if(name==='calendar')calendarPanel.append(calendar);else calendarOrigin.insertBefore(calendar,calendarOrigin.querySelector('button[type="submit"]'));
  nav.querySelectorAll('a').forEach(a=>{const active=a.dataset.route===name||(name==='create'||name==='campaign')&&a.dataset.route==='campaigns';a.classList.toggle('selected',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  sidebar.classList.remove('is-open');toggle.setAttribute('aria-expanded','false');
  render();pages[name].querySelector('h1').focus({preventScroll:true});document.title=routes[name][0]+' · MTProxy Pushok';
  if(name==='settings')run(loadAdminSettings);
 }
 window.cabinet={go,sync(own){state.campaigns=own;state.loaded=true;document.body.dataset.cabinetLoaded='true';render();},enter(){
  state.ready=true;document.body.classList.add('cabinet-mode');account.textContent=(me.first_name||'Пользователь')+' · '+({admin:'Администратор',operator:'Оператор',advertiser:'Рекламодатель'}[me.role]||me.role);
  nav.querySelectorAll('a').forEach(a=>a.hidden=!permitted(a.dataset.route));activate();
 },logout(){state.ready=false;state.loaded=false;state.campaigns=[];ownCampaigns=[];me=null;window.cabinetBootReady=false;$('identity').textContent='Гость';document.body.classList.remove('cabinet-mode');delete document.body.dataset.cabinetLoaded;
  [detail,paymentRows,reportRows,channelRows,summaryRows,$('campaigns'),$('queue'),$('nodes-list')].forEach(n=>n.replaceChildren());Object.values(stats).forEach(n=>n.replaceChildren());
 },config(config){state.bot=/^[A-Za-z0-9_]{5,32}$/.test(config.telegram_bot_username||'')?config.telegram_bot_username:null;}};
 const fieldHelp=[
  [$('channel-form').elements.telegram_url,'Публичная ссылка вида https://t.me/channel. Доступность и содержание проверяются при модерации.'],
  [$('tariff-select'),'Цена фиксируется при создании. Существующие заявки сохранят прежнюю стоимость.'],
  [$('calendar-time'),'Время в часовом поясе, указанном под календарём.'],
  [$('engine-mode'),'Сохраняется только план. Работающий прокси не запускается и не переключается.'],
  [$('tariff-admin-rub'),'Рубли с копейками для новых заявок. Цена Stars задаётся отдельно.'],
  [$('tariff-admin-stars'),'Целое число XTR. Пустое поле отключает цену Stars для этого тарифа.']
 ];
 fieldHelp.forEach(([input,text],i)=>{const note=el('small',text,'field-help');note.id='field-help-'+i;input.setAttribute('aria-describedby',note.id);input.after(note);});
 window.addEventListener('hashchange',activate);
 if(window.cabinetBotConfig)window.cabinet.config(window.cabinetBotConfig);
 if(window.cabinetBootReady&&me){window.cabinet.sync(ownCampaigns);window.cabinet.enter();}
})();
