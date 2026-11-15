'use strict';
// Представления используют только полученные от API данные и существующие действия.
window.pushokViews=(()=>{
 function icon(name){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.classList.add('ui-icon');
  const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','/assets/pushok-icons.svg#pi-'+name);svg.append(use);return svg;
 }
 function link(text,route,primary=false){const a=el('a',text,primary?'ui-button primary':'ui-button');a.href='#/'+route;return a;}
 function mascot(text){const row=el('aside',undefined,'mascot-tip'),img=el('img');img.src='/assets/pushok-pencil.png';img.alt='';img.width=88;img.height=96;img.loading='lazy';row.append(img,el('p',text));return row;}
 function badge(value,text){return el('span',text||labels[value]||value,'badge badge--'+value);}
 function title(c){return c.channel_title||channels.find(x=>x.id===c.channel_id)?.title||'Кампания';}
 function handle(value){try{return '@'+new URL(value).pathname.replace(/^\//,'').replace(/\/$/,'');}catch{return '';}}
 function avatar(kind='channel'){const a=el('span',undefined,'ui-avatar');a.append(icon(kind));return a;}
 function brand(target){
  const img=el('img');img.src='/assets/pushok-pencil.png';img.alt='';img.width=44;img.height=44;
  const copy=el('span',undefined,'brand-copy');copy.append(el('strong','puShok'),el('small','MTProxy'));
  target.replaceChildren(img,copy);target.setAttribute('aria-label','puShok MTProxy — главная');
 }
 function statusSteps(c){
  const list=el('dl',undefined,'campaign-steps');
  [['Модерация',c.moderation_status],['Оплата',c.payment_status],['Размещение',c.placement_status]].forEach(([label,value])=>{
   const row=el('div'),done=['approved','verified','active','completed'].includes(value);row.dataset.stage=label;
   const term=el('dt',label);term.prepend(icon(done?'check':'status'));
   const description=el('dd');description.append(badge(value,label==='Оплата'&&c.price_kopecks_snapshot===0?'Без оплаты':null));row.append(term,description);list.append(row);
  });return list;
 }
 function facts(c){const list=el('dl',undefined,'campaign-facts');
  [['Тариф',c.tariff_name_snapshot],['Стоимость заявки',money(c.price_kopecks_snapshot)],['Желаемое начало',date(c.requested_start_at)],['Назначенное начало',date(c.scheduled_start_at)],['Окончание',date(c.scheduled_end_at)]].forEach(([k,v])=>{list.append(el('dt',k),el('dd',v||'—'));});return list;
 }
 function campaignSummary(c,mode='campaign'){
  const item=el('article',undefined,'campaign-tile'),head=el('div',undefined,'tile-heading'),copy=el('div');
  const a=el('a',title(c),'tile-title');a.href='#/campaign/'+c.id;copy.append(a,el('small','#'+c.id.slice(0,8)+(c.channel_url?' · '+handle(c.channel_url):'')));
  head.append(avatar('megaphone'),copy);item.append(head);
  item.append(el('p',c.scheduled_start_at?'Назначено: '+date(c.scheduled_start_at):c.requested_start_at?'Желаемое начало: '+date(c.requested_start_at):'Дата — по согласованию','tile-period'));
  item.append(statusSteps(c));
  const meta=el('div',undefined,'tile-meta');meta.append(el('span',c.tariff_name_snapshot||'Тариф'),el('strong',c.price_kopecks_snapshot===0?'Без оплаты':money(c.price_kopecks_snapshot)));item.append(meta);
  if(mode==='reports'){
   const actions=el('div',undefined,'tile-actions');actions.append(button('История кампании',()=>showCampaignReport(c,item)));if(!miniAppMode)actions.append(button('Скачать CSV',()=>downloadCampaignCsv(c)));item.append(actions);
  }else if(mode==='sponsors')item.append(link('Управлять размещением','moderation/'+c.id));
  else if(mode==='payments')item.append(link(c.payment_status==='verified'?'Посмотреть оплату':c.price_kopecks_snapshot===0?'Открыть размещение':'Открыть оплату','payments/'+c.id,c.payment_status==='awaiting_payment'));
  else if(c.moderation_status==='approved'&&c.payment_status==='awaiting_payment'&&c.price_kopecks_snapshot>0)item.append(link('Перейти к оплате','payments/'+c.id,true));
  else item.append(link(c.moderation_status==='changes_requested'?'Посмотреть замечания':'Открыть кампанию','campaign/'+c.id));
  return item;
 }
 function campaigns(target,list,mode='campaign'){
  target.replaceChildren();target.classList.add('mobile-card-list');
  if(!list.length){const empty=el('div',undefined,'empty-state');empty.append(avatar('megaphone'),el('h3',mode==='payments'?'Пока нет оплат':'Здесь появятся кампании'),el('p',mode==='payments'?'Оплата станет доступна после одобрения канала.':'Создайте первую заявку — её статус появится здесь.'),link('Создать кампанию','create',true));target.append(empty);return;}
  list.forEach(c=>target.append(campaignSummary(c,mode)));
 }
 function channelCards(target,list){
  target.replaceChildren();target.classList.add('mobile-card-list');
  if(!list.length){target.append(mascot('Добавьте публичный канал, который хотите продвигать.'));return;}
  list.forEach(c=>{
   const item=el('article',undefined,'channel-tile'),head=el('div',undefined,'tile-heading'),copy=el('div');copy.append(el('h3',c.title),el('p',handle(c.telegram_url),'muted'));head.append(avatar(),copy);item.append(head);
   const count=ownCampaigns.filter(x=>x.channel_id===c.id).length;item.append(el('p','Кампаний: '+count,'muted'));
   const create=link('Создать кампанию','create',true);create.addEventListener('click',()=>{$('channel-select').value=c.id;window.pushokOrder.selectChannel();});item.append(create);
   const more=el('details',undefined,'channel-tools');more.append(el('summary','Управление каналом'),window.pushokManagement.channelActions(c));item.append(more);target.append(item);
  });
 }
 function paymentOverview(target,list,{compact=false}={}){
  target.replaceChildren();
  const banner=el('section',undefined,'payment-overview');banner.append(icon('wallet'),el('h2','Оплата кампаний'),el('p','Оплачивайте одобренные заявки и следите за статусом.'));
  const counts=el('div',undefined,'payment-counts');
  [['Ожидают оплаты',list.filter(c=>c.price_kopecks_snapshot>0&&c.payment_status==='awaiting_payment').length],['Подтверждены',list.filter(c=>c.price_kopecks_snapshot>0&&c.payment_status==='verified').length]].forEach(([label,value])=>{const tile=el('div');tile.append(el('strong',String(value)),el('span',label));counts.append(tile);});banner.append(counts);
  if(compact){banner.classList.add('payment-overview-compact');banner.append(link('Мои оплаты','payments'));}
  target.append(banner);
 }
 function profile(target){
  target.replaceChildren();const identity=el('section',undefined,'profile-identity');
  const info=el('div');info.append(el('h2',[me.first_name,me.last_name].filter(Boolean).join(' ')||'Пользователь'),el('p',me.username?'@'+me.username:'Вход через Telegram','muted'),badge('approved',({admin:'Администратор',operator:'Оператор',advertiser:'Рекламодатель'})[me.role]||'Пользователь'));identity.append(avatar('user'),info);target.append(identity);
  const menu=el('nav',undefined,'profile-menu');menu.setAttribute('aria-label','Разделы профиля');
  const items=[['channels','channel','Мои каналы'],['calendar','calendar','Календарь размещений'],['reports','chart','Отчёты'],['help','support','Помощь'],['archive','campaign','Удалённые']];
  if(['admin','operator'].includes(me.role))items.push(['moderation','moderation','Модерация'],['sponsors','sponsor','Спонсоры']);
  if(me.role==='admin')items.push(['settings','settings','Настройки']);
  items.forEach(([route,name,text])=>{const a=link(text,route);a.prepend(icon(name));const arrow=el('span','›','menu-chevron');arrow.setAttribute('aria-hidden','true');a.append(arrow);menu.append(a);});target.append(menu,mascot('Все ваши каналы и размещения — под рукой.'));
  const logout=button('Выйти из аккаунта',()=>$('logout').click());logout.className='profile-logout';target.append(logout);
 }
 function sponsors(target,list){
  target.replaceChildren();
  const active=list.filter(c=>c.placement_status==='active'),queued=list.filter(c=>c.moderation_status==='approved'&&c.payment_status==='verified'&&['scheduled','not_scheduled'].includes(c.placement_status));
  target.append(el('h2','Сейчас в эфире'));if(!active.length)target.append(el('p','Активных размещений пока нет.','empty'));active.forEach(c=>target.append(campaignSummary(c,'sponsors')));
  target.append(el('h2','Очередь размещений'));if(!queued.length)target.append(el('p','Нет оплаченных кампаний в очереди.','empty'));
  queued.sort((a,b)=>(a.scheduled_start_at||'9999').localeCompare(b.scheduled_start_at||'9999')).forEach(c=>target.append(campaignSummary(c,'sponsors')));
 }
 return {icon,brand,mascot,statusSteps,facts,campaigns,channelCards,paymentOverview,profile,sponsors};
})();
