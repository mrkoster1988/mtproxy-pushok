'use strict';
window.pushokProfiles=(()=>{
 let returnRoute='moderation';
 const roles={admin:'Администратор',operator:'Оператор',advertiser:'Рекламодатель'};
 Object.assign(errors,{user_profile_not_found:'Профиль не найден или недоступен вашему аккаунту.'});
 const views=()=>window.pushokViews;
 function person(user){
  const username=typeof user.username==='string'&&/^[A-Za-z0-9_]{1,32}$/.test(user.username)?user.username:'';
  const name=[user.first_name,user.last_name].filter(Boolean).join(' ').trim();
  return {username,name:username&&name.replace(/^@/,'').toLowerCase()===username.toLowerCase()?'':name};
 }
 function names(user,linked=false){
  const {name,username}=person(user),copy=el('div',undefined,'person-copy');copy.dataset.userRole=user.is_owner?'owner':user.role||'advertiser';if(user.id)copy.dataset.personId=user.id;
  if(name)copy.append(el('strong',name,'person-name'));
  if(username){
   const handle=el(linked?'a':'span','@'+username,'person-handle'+(!name?' person-name':''));
   if(linked){handle.href='https://t.me/'+username;handle.target='_blank';handle.rel='noopener noreferrer';}
   copy.append(handle);
  }else if(!name)copy.append(el('strong','Пользователь','person-name'));
  if(user.phone_verified)copy.querySelector('.person-name')?.append(window.pushokVerification.badge());
  return copy;
 }
 function advertiser(c){
  const user={id:c.advertiser_user_id,first_name:c.advertiser_first_name,last_name:c.advertiser_last_name,username:c.advertiser_username};
  const section=el('section',undefined,'approval-advertiser'),row=el(user.id?'a':'div',undefined,'advertiser-identity person-line');
  section.append(el('span','Рекламодатель','person-caption'));
  if(user.id){row.href='#/user/'+user.id;row.setAttribute('aria-label','Открыть профиль рекламодателя');row.addEventListener('click',()=>{returnRoute='moderation/'+c.id;});}
  row.append(views().userAvatar(user.id,person(user).name||person(user).username),names(user));
  if(user.id)row.append(views().icon('chevron'));section.append(row);return section;
 }
 function compact(user,back='wallet'){
  const id=user.user_id||user.id,row=el(id?'a':'div',undefined,'user-summary person-line'),copy=names(user);
  if(id){row.href='#/user/'+id;row.setAttribute('aria-label','Открыть профиль пользователя');row.addEventListener('click',()=>{returnRoute=back;});}
  copy.append(el('small','ID '+user.telegram_user_id,'muted'));
  row.append(views().userAvatar(id,person(user).name||person(user).username),copy);return row;
 }
 function accountDetails(user){
  const details=el('div',undefined,'profile-details'),id=el('div',undefined,'profile-id');id.append(el('span','Telegram ID: '+user.telegram_user_id));
  id.append(views().iconAction(button('',async()=>{try{await navigator.clipboard.writeText(String(user.telegram_user_id));notice('ID скопирован.',true);}catch{notice('Не удалось скопировать.');}}),'copy','Скопировать ID'));details.append(id);
  if(user.created_at)details.append(el('small','В кабинете с '+new Date(user.created_at).toLocaleDateString('ru-RU'),'muted'));return details;
 }
 function identity(user){
  const section=el('section',undefined,'profile-identity user-identity'),row=el('div',undefined,'person-line'),copy=names(user,true);
  const access=window.pushokTeam.roleControl(user);access.badge.classList.add('profile-role');access.badge.dataset.role=user.is_owner?'owner':user.role;
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

   const personal=target.parentElement.querySelector('.profile-personal');if(personal){const details=personal.querySelector('.profile-details');if(details)details.replaceWith(accountDetails(data.user));else personal.append(accountDetails(data.user));}
  }catch(e){if(session===token&&target.isConnected)target.replaceChildren(el('p','Обзор не обновлён. '+e.message,'muted'),button('Повторить',()=>ownOverview(target)));}
 }
 function show(target,uid){
  if(!['admin','operator'].includes(me?.role)){target.replaceChildren();return;}
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
 return {names,compact,identity,accountDetails,advertiser,ownOverview,show,setBackRoute:route=>{returnRoute=route;},backRoute:()=>returnRoute};
})();
