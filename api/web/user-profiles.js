'use strict';
window.pushokProfiles=(()=>{
 let returnRoute='moderation';
 Object.assign(errors,{user_profile_not_found:'Профиль не найден или недоступен вашему аккаунту.'});
 const views=()=>window.pushokViews;
 function person(user){
  const username=typeof user.username==='string'&&/^[A-Za-z0-9_]{1,32}$/.test(user.username)?user.username:'';
  const name=[user.first_name,user.last_name].filter(Boolean).join(' ').trim();
  return {username:['admin','moderator'].includes(me?.role)?username:'',
   name:username&&name.replace(/^@/,'').toLowerCase()===username.toLowerCase()?'':name};
 }
 function telegramHandle(user,linked=true){
  const {username}=person(user);
  if(!username)return null;
  const clickable=linked&&['admin','moderator'].includes(me?.role);
  const handle=el(clickable?'a':'span','@'+username,'person-handle');
  if(clickable){
   handle.href='https://t.me/'+username;
   handle.target='_blank';
   handle.rel='noopener noreferrer';
  }
  return handle;
 }
 function names(user,linked=false){
  const {name,username}=person(user),copy=el('div',undefined,'person-copy');copy.dataset.userRole=hasDeveloperAccess(user)?'owner':user.role||'advertiser';if(user.id)copy.dataset.personId=user.id;
  if(name)copy.append(el('strong',name,'person-name'));
  if(username){
   const handle=telegramHandle(user,linked);
   if(!name)handle.classList.add('person-name');
   copy.append(handle);
  }else if(!name)copy.append(el('strong','Пользователь','person-name'));
  if(user.phone_verified)copy.querySelector('.person-name')?.append(window.pushokVerification.badge());
  window.pushokVpn.badge(copy.querySelector('.person-name'),user.id);
  return copy;
 }
 function advertiser(c){
  const user={id:c.advertiser_user_id,telegram_user_id:c.advertiser_telegram_user_id,first_name:c.advertiser_first_name,last_name:c.advertiser_last_name,username:c.advertiser_username};
  const section=el('article',undefined,'card directory-user approval-advertiser'),row=compact(user,'moderation/'+c.id);
  row.classList.add('advertiser-identity');row.setAttribute('aria-label','Открыть профиль рекламодателя');
  section.append(row,el('span','Автор заявки','person-role'));return section;
 }
 function compact(user,back='wallet'){
  const id=user.user_id||user.id,row=el(id?'a':'div',undefined,'user-summary person-line'),copy=names(user);
  if(id){row.href=(me?.role==='moderator'?'#/member/':'#/user/')+id;row.setAttribute('aria-label','Открыть профиль пользователя');row.addEventListener('click',()=>{returnRoute=back;});}
  if(user.telegram_user_id)copy.append(el('small','ID '+user.telegram_user_id,'muted'));
  row.append(views().userAvatar(id,person(user).name||person(user).username),copy);return row;
 }
 function accountDetails(user){
  const details=el('div',undefined,'profile-details'),id=el('div',undefined,'profile-id');id.append(el('span','Telegram ID: '+user.telegram_user_id));
  id.append(views().iconAction(button('',async()=>{try{await navigator.clipboard.writeText(String(user.telegram_user_id));notice('ID скопирован.',true);}catch{notice('Не удалось скопировать.');}}),'copy','Скопировать ID'));details.append(id);
  if(user.created_at)details.append(el('small','В кабинете с '+new Date(user.created_at).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'}),'muted'));return details;
 }
 function identity(user){
  const section=el('section',undefined,'profile-identity user-identity'),row=el('div',undefined,'person-line'),copy=names(user,true);
  const access=window.pushokTeam.roleControl(user);access.badge.classList.add('profile-role');
  row.append(views().userAvatar(user.id,person(user).name||person(user).username),copy);section.append(access.badge,row,access.editor);return section;
 }
 function balance(data,user){
  const box=el('section',undefined,'panel profile-section profile-wallet');box.append(el('h2','Баланс пользователя'),el('p','Виден только администратору','muted'));
  const balances=el('dl',undefined,'profile-balance-values');for(const [key,label] of [['RUB','Рубли'],['BONUS','Бонусы'],['XTR','Stars в puShok']])balances.append(el('dt',label),el('dd',key==='XTR'?data[key]+' XTR':money(data[key])));box.append(balances);
  box.append(views().iconAction(button('',()=>{window.pushokWallet.selectUser(user.telegram_user_id);window.cabinet.go('wallet');}),'wallet','Управлять балансом'));return box;
 }
 function stats(counts,onSelect){
  const row=el('nav',undefined,'profile-stats');row.setAttribute('aria-label','Обзор пользователя');
  for(const [key,title] of [['channels','Каналы'],['current','Кампании'],['archived','Архив']]){
   const item=button('',()=>onSelect(key));item.append(el('strong',String(counts[key])),el('span',title));item.setAttribute('aria-label',title+': '+counts[key]);row.append(item);
  }
  return row;
 }
 async function ownOverview(target){
  const session=token,uid=me?.id;target.replaceChildren(el('p','Загрузка обзора…','muted'));
  try{const data=await api('/users/'+uid+'/profile');if(session!==token||uid!==me?.id||!target.isConnected)return;
   target.replaceChildren(stats(data.counts,key=>window.cabinet.go({current:'campaigns/mine',channels:'channels',archived:'archive'}[key])),window.pushokCommunity.invited(uid,data.counts.invited));
   window.pushokHall.medals(target,data);
   window.pushokVpn.profile(target,uid);

   const personal=target.parentElement.querySelector('.profile-personal');if(personal){const details=personal.querySelector('.profile-details');if(details)details.replaceWith(accountDetails(data.user));else personal.append(accountDetails(data.user));}
  }catch(e){if(session===token&&target.isConnected)target.replaceChildren(el('p','Обзор не обновлён. '+e.message,'muted'),button('Повторить',()=>ownOverview(target)));}
 }
 function agentCard(back='users',linked=true){
  const card=el('article',undefined,'card directory-user ai-agent-card'),row=el(linked?'a':'div',undefined,'user-summary person-line'),avatar=el('span',undefined,'user-avatar agent-avatar'),copy=el('div',undefined,'person-copy');
  avatar.append(views().icon('moderation'));copy.append(el('strong','Pushok ИИ','person-name'),el('small','Решения и статистика','muted'));row.append(avatar,copy);if(linked){row.href='#/user/ai-agent';row.addEventListener('click',()=>{returnRoute=back;});}card.append(row,el('span','Служебный аккаунт','person-role'));return card;
 }
 async function showAgent(target){
  const session=token,valid=()=>session===token&&target.isConnected&&location.hash==='#/user/ai-agent'&&['admin','operator'].includes(me?.role);target.replaceChildren(el('p','Загрузка статистики…','muted'));target.classList.add('ai-agent-profile');
  try{const data=await api('/users/ai-agent/profile');if(!valid())return;
   const card=agentCard('users',false),stats=el('dl',undefined,'ai-agent-stats');
   for(const [key,title] of [['approved','Одобрено'],['rejected','Отклонено'],['review','Передано сотруднику'],['campaigns','Проверено кампаний']]){const cell=el('div');cell.append(el('dt',title),el('dd',String(data.counts[key])));stats.append(cell);}
   const section=el('section',undefined,'panel');section.append(el('h2','Решения по темам'));const table=el('table',undefined,'ai-topic-table'),head=el('tr');['Тематика','Одобрено','Отклонено','На проверку'].forEach(t=>head.append(el('th',t)));const thead=el('thead');thead.append(head);table.append(thead);const body=el('tbody');
   for(const t of data.topics){const row=el('tr');[t.label,t.approved,t.rejected,t.review].forEach(v=>row.append(el('td',String(v))));body.append(row);}table.append(body);section.append(data.topics.length?table:el('p','Решений пока нет.','empty'));
   target.replaceChildren(card,stats,section);
   window.pushokControls.help(section,[el('p','Считаются сохранённые решения за всё время, включая повторные проверки. Передача сотруднику не является отказом. '+(data.automatic_rejection_enabled?'Автоматическое отклонение нерекомендуемых каналов включено. ':'Автоматическая модерация сейчас выключена. ')+'Темы фиксируются на момент решения; у старых записей тема может отсутствовать.'),el('p','Повторов из-за временной недоступности: '+data.counts.retry+'. Последнее решение: '+date(data.counts.last_decision_at))],'Как считается статистика агента');
  }catch(e){if(valid())target.replaceChildren(el('p',e.message,'empty'),button('Повторить',()=>showAgent(target)));}
 }
 function show(target,uid){
  if(!['admin','operator'].includes(me?.role)){target.replaceChildren();return;}
  target.classList.remove('ai-agent-profile');if(uid==='ai-agent'){void showAgent(target);return;}
  const session=token,viewer=me?.id;let archived=false,campaignPage=0,channelPage=0,serial=0;
  target.replaceChildren(el('p','Загрузка профиля…','muted'));
  const valid=()=>session===token&&viewer===me?.id&&['admin','operator'].includes(me?.role)&&target.isConnected&&location.hash==='#/user/'+uid;
  function external(url,text){
   try{const parsed=new URL(url);if(parsed.protocol!=='https:'||parsed.hostname!=='t.me'||parsed.username||parsed.password)throw Error();
    const a=el('a',text);a.href=parsed.href;a.target='_blank';a.rel='noopener noreferrer';return a;
   }catch{return el('span',text);}
  }
  async function load(){
   const request=++serial;
   try{
    const data=await api('/users/'+encodeURIComponent(uid)+'/profile?archived='+archived+'&campaign_page='+campaignPage+'&channel_page='+channelPage);
    if(!valid()||request!==serial)return;
    paint(data);
   }catch(e){if(valid()&&request===serial)target.replaceChildren(el('p',e.message,'empty'),button('Повторить',load));}
  }
  function pager(parent,page,more,change){
   if(!page&&!more)return;const nav=el('nav',undefined,'profile-pagination');
   const previous=views().iconAction(button('',()=>change(page-1)),'back','Предыдущая страница');previous.disabled=!page;
   const next=views().iconAction(button('',()=>change(page+1)),'chevron','Следующая страница');next.disabled=!more;
   nav.append(previous,el('span','Страница '+(page+1)),next);parent.append(nav);
  }
  function paint(data){
   const channelSection=el('section',undefined,'profile-section profile-channels'),campaignSection=el('section',undefined,'profile-section profile-campaigns');
   const overview=stats(data.counts,key=>{if(key==='channels')channelSection.scrollIntoView({block:'start'});else{archived=key==='archived';campaignPage=0;void load();}});
   const personal=window.pushokVerification.readOnly(data.verification||{});personal.querySelector('.personal-body').append(accountDetails(data.user));window.pushokCommunity.ownerTools(personal.querySelector('.personal-body'),uid,data.verification||{},load);
   target.replaceChildren(identity(data.user),window.pushokCommunity.invited(uid,data.counts.invited),personal,...(me.role==='admin'&&data.wallet?[balance(data.wallet,data.user)]:[]),overview,channelSection,campaignSection);

   const vpnSlot=el('div'); overview.before(vpnSlot); window.pushokVpn.profile(vpnSlot,uid);
   window.pushokHall.medals(target.querySelector('.profile-identity'),data);
   channelSection.append(el('h2','Каналы'));
   const channelList=el('div',undefined,'profile-list');channelSection.append(channelList);
   if(!data.channels.length)channelList.append(el('p','Каналов пока нет.','empty'));
   for(const channel of data.channels){
    const row=el('div',undefined,'profile-channel-row'),copy=el('div');
    copy.append(external(channel.telegram_url,channel.title));
    row.append(views().channelAvatar(channel.id,channel.title,channel.telegram_url),copy);channelList.append(row);
   }
   pager(channelSection,data.channel_page,data.more_channels,page=>{channelPage=page;return load();});
   campaignSection.append(el('h2','Кампании'));
   const tabs=el('nav',undefined,'profile-tabs');tabs.setAttribute('aria-label','Кампании рекламодателя');
   for(const [flag,label,count] of [[false,'Текущие',data.counts.current],[true,'Архив',data.counts.archived]]){
    const tab=button(label+' · '+count,()=>{archived=flag;campaignPage=0;return load();});tab.setAttribute('aria-pressed',String(flag===data.archived));tabs.append(tab);
   }
   campaignSection.append(tabs);const list=el('div',undefined,'mobile-card-list');campaignSection.append(list);
   if(!data.campaigns.length)list.append(el('p',data.archived?'Архив пуст.':'Текущих кампаний нет.','empty'));
   for(const c of data.campaigns){
    const row=views().campaignSummary(c,data.archived?'archive':uid===me.id?'campaign':'moderation',{
     actions:false,
     openIcon:data.archived?'chart':'chevron',
     openLabel:data.archived?'Отчёт кампании '+c.id.slice(0,8):'Открыть кампанию '+c.id.slice(0,8),
     onOpen:data.archived?item=>showCampaignReport(c,item):undefined
    });
    row.classList.add('profile-campaign-row');list.append(row);
   }
   pager(campaignSection,data.campaign_page,data.more_campaigns,page=>{campaignPage=page;return load();});
  }
  void load();
 }
 return {agentCard,names,telegramHandle,compact,identity,accountDetails,advertiser,ownOverview,show,setBackRoute:route=>{returnRoute=route;},backRoute:()=>returnRoute};
})();
