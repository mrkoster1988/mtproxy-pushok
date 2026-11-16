'use strict';
// Представления используют только полученные от API данные и существующие действия.
window.pushokViews=(()=>{
 function icon(name){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.classList.add('ui-icon');
  const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','/assets/pushok-icons.svg?v=135#pi-'+name);svg.append(use);return svg;
 }
 // Иконка всегда имеет доступное имя и подпись при наведении.
 function iconAction(node,name,label){node.replaceChildren(icon(name));node.classList.add('icon-action');node.setAttribute('aria-label',label);node.title=label;return node;}
 function disclosure(title,nodes,cls='inline-help'){const d=el('details',undefined,cls),summary=el('summary',title);d.append(summary,...nodes);return d;}
 function link(text,route,primary=false){const a=el('a',text,primary?'ui-button primary':'ui-button');a.href='#/'+route;return a;}
 function mascot(text){const row=el('aside',undefined,'mascot-tip'),img=el('img');img.src='/assets/pushok-pencil.png';img.alt='';img.width=88;img.height=96;img.loading='lazy';row.append(img,el('p',text));return row;}
 function badge(value,text){return el('span',text||labels[value]||value,'badge badge--'+value);}
 function title(c){return c.channel_title||channels.find(x=>x.id===c.channel_id)?.title||'Кампания';}
 function handle(value){try{return '@'+new URL(value).pathname.replace(/^\//,'').replace(/\/$/,'');}catch{return '';}}
 const logos=new Map(),logoQueue=[];let logoSession='',logoEpoch=0,logoActive=0;
 function fetchLogo(url,options){return new Promise(resolve=>{logoQueue.push({url,options,resolve});pumpLogos();});}
 function pumpLogos(){while(logoActive<4&&logoQueue.length){const task=logoQueue.shift();logoActive++;fetch(task.url,task.options).then(task.resolve,()=>task.resolve(null)).finally(()=>{logoActive--;pumpLogos();});}}
 function clearLogos(){logoEpoch++;for(const task of logoQueue.splice(0))task.resolve(null);for(const entry of logos.values())if(entry.url)URL.revokeObjectURL(entry.url);logos.clear();logoSession='';}
 function channelAvatar(id,label,url=''){
  const fallback=avatar();fallback.classList.add('channel-avatar');fallback.title=label||'Канал';
  return loadAvatar(fallback,id?'channels/'+encodeURIComponent(id)+'/logo':'','channel:'+id+':'+url,'Логотип канала '+(label||''));
 }
 function userAvatar(id,name=''){
  const fallback=avatar('user');fallback.classList.add('user-avatar');
  const initials=name.trim().split(/\s+/).filter(Boolean).slice(0,2).map(s=>Array.from(s)[0]).join('').toUpperCase();
  if(initials)fallback.textContent=initials;fallback.setAttribute('aria-hidden','true');
  return loadAvatar(fallback,id?'users/'+encodeURIComponent(id)+'/avatar':'','user:'+id,'');
 }
 function loadAvatar(fallback,path,key,alt){
  if(!path||!token)return fallback;const scope=token+'|'+me?.role;if(logoSession!==scope){clearLogos();logoSession=scope;}
  const epoch=logoEpoch,session=token;
  if(!logos.has(key)){
   if(logos.size>=128)return fallback;
   const entry={url:null};logos.set(key,entry);
   entry.promise=fetchLogo('/api/v1/'+path,{headers:{Authorization:'Bearer '+session},cache:'no-store'})
    .then(async r=>{if(!r||r.status!==200||r.headers.get('Content-Type')?.split(';')[0]!=='image/jpeg')return null;const blob=await r.blob();if(blob.size>1048576||epoch!==logoEpoch||session!==token||scope!==token+'|'+me?.role)return null;entry.url=URL.createObjectURL(blob);return entry.url;}).catch(()=>null);
  }
  const initial=[...fallback.childNodes].map(n=>n.cloneNode(true));
  logos.get(key).promise.then(src=>{if(!src||epoch!==logoEpoch||session!==token||scope!==token+'|'+me?.role)return;const img=el('img');img.alt=alt;img.width=48;img.height=48;img.decoding='async';img.addEventListener('error',()=>fallback.replaceChildren(...initial),{once:true});img.src=src;fallback.replaceChildren(img);});return fallback;
 }
 function avatar(kind='channel'){const a=el('span',undefined,'ui-avatar');a.append(icon(kind));return a;}
 function profileAvatar(){
  return userAvatar(me?.id,[me?.first_name,me?.last_name].filter(Boolean).join(' '));
 }
 function brand(target){
  const img=el('img');img.src='/assets/pushok-pencil.png';img.alt='';img.width=44;img.height=44;
  const copy=el('span',undefined,'brand-copy');copy.append(el('strong','puShok'),el('small','MTProxy'));
  target.replaceChildren(img,copy);target.setAttribute('aria-label','puShok MTProxy — главная');
 }
 function statusSteps(c,compact=false){
  const list=el('dl',undefined,'campaign-steps'+(compact?' campaign-steps-compact':''));
  [['Модерация',c.moderation_status],['Оплата',c.payment_status],['Размещение',c.placement_status]].forEach(([label,value])=>{
   const row=el('div'),done=['approved','verified','active','completed'].includes(value);row.dataset.stage=label;
   const term=el('dt',label);term.prepend(icon(done?'check':'status'));
   const description=el('dd');description.append(badge(value,label==='Оплата'&&c.price_kopecks_snapshot===0?'Без оплаты':compact?({active:'Активна',scheduled:'В очереди',completed:'Завершена',not_scheduled:'Не начата',pending:'На проверке',awaiting_payment:'К оплате',proof_submitted:'Проверка',changes_requested:'Исправить'})[value]:null));row.append(term,description);list.append(row);
  });return list;
 }
 function facts(c){const list=el('dl',undefined,'campaign-facts');
  [['Тариф',c.tariff_name_snapshot],['Стоимость',price(c)],...(c.requested_start_at?[['Желаемое начало',date(c.requested_start_at)]]:[]),['Начало',date(c.scheduled_start_at)],['Окончание',date(c.scheduled_end_at)]].forEach(([k,v])=>{list.append(el('dt',k),el('dd',v||'—'));});return list;
 }
 function price(c){return c.price_kopecks_snapshot===0?'Без оплаты':miniAppMode?(c.stars_price_snapshot>0?c.stars_price_snapshot+' XTR':'Цена Stars не задана'):money(c.price_kopecks_snapshot);}
 function campaignSummary(c,mode='campaign'){
  const item=el('article',undefined,'campaign-tile'),head=el('div',undefined,'tile-heading'),copy=el('div');
  item.dataset.placement=c.placement_status;
  const a=el('a',title(c),'tile-title');a.href='#/'+(mode==='sponsors'?'moderation/':'campaign/')+c.id;copy.append(a,el('small','#'+c.id.slice(0,8)+(c.channel_url?' · '+handle(c.channel_url):'')));
  const open=link('',(mode==='sponsors'?'moderation/':'campaign/')+c.id);iconAction(open,'chevron','Открыть кампанию '+title(c));head.append(channelAvatar(c.channel_id,title(c),c.channel_url),copy,open);item.append(head);
  item.append(el('p',c.placement_status==='active'&&c.scheduled_end_at?'До '+date(c.scheduled_end_at):c.scheduled_start_at?'Начало: '+date(c.scheduled_start_at):c.requested_start_at?'Желаемое начало: '+date(c.requested_start_at):'Дата — по согласованию','tile-period'));
  item.append(statusSteps(c,true));
  const meta=el('div',undefined,'tile-meta');meta.append(el('span',c.tariff_name_snapshot||'Тариф'),el('strong',price(c)));item.append(meta);
  if(mode==='reports'){
   const actions=el('div',undefined,'tile-actions');actions.append(button('История кампании',()=>showCampaignReport(c,item)));if(!miniAppMode)actions.append(button('Скачать CSV',()=>downloadCampaignCsv(c)));item.append(actions);
  }else if(mode==='sponsors')item.append(link('Управлять','moderation/'+c.id));
  else if(mode==='payments')item.append(link(c.payment_status==='verified'?'Оплата':c.price_kopecks_snapshot===0?'Размещение':'Оплатить','payments/'+c.id,c.payment_status==='awaiting_payment'));
  else if(c.moderation_status==='approved'&&c.payment_status==='awaiting_payment'&&c.price_kopecks_snapshot>0)item.append(link('Оплатить','payments/'+c.id,true));
  else if(c.moderation_status==='changes_requested')item.append(link('Исправить','campaign/'+c.id));
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
   const item=el('article',undefined,'channel-tile'),head=el('div',undefined,'tile-heading'),copy=el('div');copy.append(el('h3',c.title),el('p',handle(c.telegram_url),'muted'));head.append(channelAvatar(c.id,c.title,c.telegram_url),copy);item.append(head);
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
 function profile(target,updatePanel){
  target.replaceChildren(window.pushokProfiles.identity(me));
  const overview=el('div',undefined,'profile-overview');target.append(overview);
  if(location.hash.startsWith('#/profile'))void window.pushokProfiles.ownOverview(overview);
  const groups=[['Мой кабинет',[['channels','channel','Мои каналы'],['reports','chart','Отчёты'],['growth','gift','Бонусы и подарки']]]];
  if(['admin','operator'].includes(me.role)){
   const admin=[['moderation','moderation','Модерация'],['sponsors','server','Узлы и спонсоры']];
   if(me.role==='admin')admin.unshift(['users','users','Пользователи']);
   if(me.role==='admin')admin.push(['settings','settings','Настройки']);
   groups.push(['Управление',admin]);
  }
  groups.push(['Поддержка',[['help','support','Помощь'],['bot','channel','Telegram-бот']]]);
  const sections=el('div',undefined,'profile-groups');
  groups.forEach(([title,items])=>{const group=el('section',undefined,'profile-group'),menu=el('nav',undefined,'profile-menu');menu.setAttribute('aria-label',title);group.append(el('h2',title));
   items.forEach(([route,name,text])=>{const a=link(text,route);a.prepend(icon(name));const arrow=icon('chevron');arrow.classList.add('menu-chevron');a.append(arrow);menu.append(a);});group.append(menu);sections.append(group);});target.append(sections);
  if(updatePanel)target.append(updatePanel);
  const logout=button('Выйти',()=>$('logout').click());logout.setAttribute('aria-label','Выйти из аккаунта');logout.className='profile-logout';target.append(logout);
 }
 function sponsors(target,list){
  target.replaceChildren();
  const active=list.filter(c=>c.placement_status==='active'),queued=list.filter(c=>!c.archived_at&&c.moderation_status==='approved'&&c.payment_status==='verified'&&['scheduled','not_scheduled'].includes(c.placement_status));
  target.append(el('h2','Сейчас в эфире'));if(!active.length)target.append(el('p','Активных размещений пока нет.','empty'));active.forEach(c=>target.append(campaignSummary(c,'sponsors')));
  target.append(el('h2','Очередь размещений'));if(!queued.length)target.append(el('p','Нет оплаченных кампаний в очереди.','empty'));
  queued.sort((a,b)=>(a.scheduled_start_at||'9999').localeCompare(b.scheduled_start_at||'9999')).forEach(c=>target.append(campaignSummary(c,'sponsors')));
 }
 return {clearLogos,channelAvatar,userAvatar,icon,iconAction,disclosure,brand,mascot,profileAvatar,statusSteps,facts,campaigns,channelCards,paymentOverview,profile,sponsors};
})();
