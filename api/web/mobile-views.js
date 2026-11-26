'use strict';
// Представления используют только полученные от API данные и существующие действия.
window.pushokViews=(()=>{
 function icon(name){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.classList.add('ui-icon');
  const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href','/assets/pushok-icons.svg?v=223#pi-'+name);svg.append(use);return svg;
 }
 // Иконка всегда имеет доступное имя и подпись при наведении.
 function iconAction(node,name,label){if(/^Сохранить/.test(label))name='save';node.replaceChildren(icon(name));node.dataset.actionIcon=name;node.classList.add('icon-action');node.setAttribute('aria-label',label);node.title=label;return node;}
 function disclosure(title,nodes,cls='inline-help'){const d=el('details',undefined,cls),summary=el('summary',title);d.append(summary,...nodes);return d;}
 function info(panel,text,label='О балансе'){
  const heading=panel.querySelector(':scope > h2'),row=el('div',undefined,'panel-heading'),body=el('p',text,'inline-info muted');body.id='info-'+crypto.randomUUID();body.hidden=true;
  const toggle=iconAction(button('',()=>{body.hidden=!body.hidden;toggle.setAttribute('aria-expanded',String(!body.hidden));}),'info',label);toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls',body.id);
  if(heading)row.append(heading);row.append(toggle);panel.prepend(row,body);return body;
 }
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
  const initial=Array.from((label||'').replace(/^@/,'').trim())[0];if(initial)fallback.textContent=initial.toUpperCase();
  return loadAvatar(fallback,id?'channels/'+encodeURIComponent(id)+'/logo':url?'channel-preview/logo?query='+encodeURIComponent(url):'','channel:'+id+':'+url,'Логотип канала '+(label||''));
 }
 function channelPreview(channel){
  const row=el('div',undefined,'channel-preview'),copy=el('span');copy.append(el('strong',channel.title),el('small',handle(channel.telegram_url),'muted'));row.append(channelAvatar(channel.id,channel.title,channel.telegram_url),copy);return row;
 }
 Object.assign(errors,{
  invalid_telegram_channel_url:'Укажите публичный @username или ссылку https://t.me/channel. Пригласительная ссылка не подходит.',
  channel_lookup_unavailable:'Не удалось проверить канал: Telegram временно недоступен. Повторите проверку.',
  channel_lookup_rate_limit:'Слишком много проверок. Повторите через минуту.',
  channel_not_broadcast:'По этой ссылке группа или личный аккаунт. Укажите публичный канал.',
  channel_public_unavailable:'Канал не найден или недоступен. Проверьте его публичный @username.',
  channel_identity_changed:'Этот @username теперь принадлежит другому каналу. Обновите канал в списке.',
  channel_changed_during_check:'Канал изменился во время проверки. Проверьте его ещё раз.'
 });
 function channelVerification(result,getValue,onSuccess=()=>{}){
  let epoch=0,timer=null,controller=null,pending=null,lastValue='',lastSession='',verified=null,verifiedAt=0;
  result.setAttribute('role','status');result.setAttribute('aria-live','polite');result.hidden=true;
  function reset(){epoch++;clearTimeout(timer);timer=null;controller?.abort();controller=null;pending=null;verified=null;verifiedAt=0;lastValue='';lastSession='';result.hidden=true;result.replaceChildren();result.removeAttribute('aria-busy');delete result.dataset.state;}
  function check({force=false}={}){
   clearTimeout(timer);timer=null;
   const value=getValue().trim(),session=token;
   if(value===lastValue&&session===lastSession&&pending)return pending;
   if(!force&&value===lastValue&&session===lastSession&&verified&&Date.now()-verifiedAt<30000)return Promise.resolve(verified);
   reset();lastValue=value;lastSession=session;const version=epoch;
   result.hidden=false;result.dataset.state='checking';result.setAttribute('aria-busy','true');result.replaceChildren(el('p','Проверяем публичность канала…','muted'));
   const active=new AbortController();controller=active;let timedOut=false;
   const timeout=setTimeout(()=>{timedOut=true;active.abort();},7000);
   const current=()=>version===epoch&&session===token&&getValue().trim()===value;
   pending=(async()=>{
    try{
     await Promise.resolve();
     if(!current())throw Error('Канал изменился. Дождитесь новой проверки.');
     if(!/^(?:@|https:\/\/(?:t\.me|telegram\.me)\/)?[a-z][a-z0-9_]{3,31}\/?$/i.test(value))throw Error(errors.invalid_telegram_channel_url);
     const data=await api('/channel-preview?query='+encodeURIComponent(value),{signal:active.signal});
     if(!current())throw Error('Канал изменился. Дождитесь новой проверки.');
     verified=data;verifiedAt=Date.now();result.dataset.state='verified';
     const status=el('p','Публичный канал','channel-check-success');status.prepend(icon('check'));
     result.replaceChildren(channelPreview(data),status);
     onSuccess(data);return data;
    }catch(error){
     if(current()){verified=null;result.dataset.state='error';result.replaceChildren(el('p',timedOut?errors.channel_lookup_unavailable:error.message,'channel-check-error'));}
     throw error;
    }finally{clearTimeout(timeout);if(current()){pending=null;controller=null;result.removeAttribute('aria-busy');}}
   })();return pending;
  }
  function schedule(){
   const value=getValue().trim();if(value===lastValue&&token===lastSession&&(pending||verified&&Date.now()-verifiedAt<30000))return;
   reset();if(!value)return;
   result.hidden=false;result.replaceChildren(el('p','Канал проверится автоматически','muted'));
   timer=setTimeout(()=>{void check().catch(()=>{});},600);
  }
  return {check,schedule,reset};
 }
 function channelSearch(input,titleInput){
  input.type='text';input.autocapitalize='none';input.spellcheck=false;
  const line=el('span',undefined,'channel-lookup-line'),result=el('div',undefined,'channel-search-result'),label=input.closest('label');result.setAttribute('role','status');result.hidden=true;
  input.before(line);line.append(input);label.after(result);
  let autoTitle='';
  const checker=channelVerification(result,()=>input.disabled?'':input.value,data=>{
   if(!titleInput.value.trim()||titleInput.value===autoTitle){autoTitle=data.title;titleInput.value=data.title;}
   input.dispatchEvent(new Event('change',{bubbles:true}));
   input.dispatchEvent(new CustomEvent('pushok:channel-verified',{bubbles:true,detail:data}));
  });input.pushokChannelCheck=checker;
  const search=iconAction(button('',()=>checker.check({force:true}).catch(()=>{})),'search','Проверить канал');line.append(search);
  input.addEventListener('input',()=>{if(titleInput.value===autoTitle)titleInput.value='';autoTitle='';checker.schedule();});
  input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();search.click();}});
  input.form?.addEventListener('reset',()=>{checker.reset();autoTitle='';});return checker;
 }
 function userAvatar(id,name='',hall=false){
  const fallback=avatar('user');fallback.classList.add('user-avatar');
  const initials=name.trim().split(/\s+/).filter(Boolean).slice(0,2).map(s=>Array.from(s)[0]).join('').toUpperCase();
  if(initials)fallback.textContent=initials;fallback.setAttribute('aria-hidden','true');
  const node=loadAvatar(fallback,id?'community/users/'+encodeURIComponent(id)+'/avatar':'','user:'+id,'');
  window.pushokCommunity.decorate(node,id);return node;
 }
 function loadAvatar(fallback,path,key,alt){
  if(!path||!token)return fallback;const scope=token+'|'+me?.role;if(logoSession!==scope){clearLogos();logoSession=scope;}
  const epoch=logoEpoch,session=token;
  if(!logos.has(key)){
   if(logos.size>=128)return fallback;
   const entry={url:null};logos.set(key,entry);
   entry.promise=fetchLogo('/api/v1/'+path,{headers:{Authorization:'Bearer '+session},cache:'no-store'})
    .then(async r=>{if(!r||r.status!==200||r.headers.get('Content-Type')?.split(';')[0]!=='image/jpeg')return null;const blob=await r.blob();if(blob.size>1048576||epoch!==logoEpoch||session!==token||scope!==token+'|'+me?.role)return null;entry.url=URL.createObjectURL(blob);return entry.url;}).catch(()=>null).then(src=>{if(!src&&logos.get(key)===entry)logos.delete(key);return src;});
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
 function pendingLabel(c){
  return {processing:'ИИ проверяет',review:'У модератора',retry:'Ожидает повтора'}[c.moderation_check]||'На проверке';
 }
 function statusSteps(c,compact=false){
  const list=el('dl',undefined,'campaign-steps'+(compact?' campaign-steps-compact':''));
  [['Модерация',c.moderation_status],['Оплата',c.payment_status],['Размещение',c.placement_status]].forEach(([label,value])=>{
   const row=el('div'),done=['approved','verified','active','completed'].includes(value);row.dataset.stage=label;
   const term=el('dt',label);term.prepend(icon(done?'check':'status'));
   const description=el('dd');description.append(badge(value,label==='Модерация'&&value==='pending'?pendingLabel(c):label==='Оплата'&&c.price_kopecks_snapshot===0?'Без оплаты':compact?({active:'Активна',scheduled:'В очереди',completed:'Завершена',not_scheduled:'Не начата',awaiting_payment:'К оплате',proof_submitted:'Проверка',changes_requested:'Исправить'})[value]:null));row.append(term,description);list.append(row);
  });return list;
 }
 function duration(c){const seconds=Number(c.duration_seconds_snapshot)||0;if(!seconds)return '—';if(seconds%86400===0)return seconds/86400+' дн.';if(seconds%3600===0)return seconds/3600+' ч';return Math.ceil(seconds/60)+' мин';}
 function campaignState(c){
  if(c.placement_status==='completed')return ['completed','Завершена',5];
  if(c.placement_status==='active')return ['active','Активна',4];
  if(c.placement_status==='cancelled')return ['cancelled','Отменена',0];
  if(c.moderation_status==='draft')return ['draft','Черновик',0];
  if(['pending','rejected','changes_requested'].includes(c.moderation_status))return [c.moderation_status,({pending:pendingLabel(c),rejected:'Отклонена',changes_requested:'Исправить'})[c.moderation_status],1];
  if(c.payment_status!=='verified'&&c.price_kopecks_snapshot!==0)return [c.payment_status,c.payment_status==='proof_submitted'?'Проверка оплаты':'К оплате',2];
  return [c.placement_status,c.placement_status==='scheduled'?'В очереди':'Ожидает запуска',3];
 }
 function progress(c){
  const rail=el('ol',undefined,'campaign-progress'),state=campaignState(c);rail.setAttribute('aria-label','Этапы кампании');
  ['Черновик','Модерация','Оплата','Расписание','Активна','Завершена'].forEach((name,i)=>{const step=el('li',undefined,i<state[2]?'is-done':i===state[2]?'is-current':''),dot=el('span',undefined,'progress-dot');dot.append(i<state[2]?icon('check'):document.createTextNode(String(i+1)));step.append(dot,el('span',name));if(i===state[2])step.setAttribute('aria-current','step');rail.append(step);});return rail;
 }
 const launchClock=new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
 function launchTime(value){return launchClock.format(new Date(value))+' МСК';}
 function launchPeriod(c){
  const start=c.scheduled_start_at,end=c.scheduled_end_at;
  if(c.placement_status==='completed')return end?'Размещение завершено · окончание по плану '+launchTime(end):'Размещение завершено';
  if(c.placement_status==='active')return end?'Размещение идёт до '+launchTime(end):'Размещение идёт';
  if(c.placement_status==='cancelled'||c.archived_at)return 'Запуск отменён';
  if(c.moderation_status==='rejected'||c.payment_status==='refunded')return 'Запуск не планируется';
  if(start)return (c.auto_schedule_tag_pending?'Предварительный запуск · ':'Запуск по расписанию · ')+launchTime(start);
  if(c.auto_schedule_paused)return 'Запуск приостановлен · ожидает нового расписания';
  if(c.requested_start_at&&(c.moderation_status!=='approved'||(c.price_kopecks_snapshot>0&&c.payment_status!=='verified')))return 'Желаемое начало: '+launchTime(c.requested_start_at);
  if(c.moderation_status==='draft')return 'Запуск после подачи и одобрения заявки';
  if(c.moderation_status==='changes_requested')return 'Запуск после исправлений и повторной модерации';
  if(c.moderation_status==='pending')return 'Срок запуска уточнится после модерации';
  if(c.price_kopecks_snapshot>0&&c.payment_status!=='verified')return 'Срок запуска уточнится после подтверждения оплаты';
  if(c.auto_start_not_before){
   const earliest=Math.max(Date.parse(c.auto_start_not_before),c.requested_start_at?Date.parse(c.requested_start_at):0);
   return earliest>Date.now()?'Подбираем свободный период · запуск не раньше '+launchTime(earliest):'Подбираем ближайший свободный период для запуска';
  }
  return 'Запуск в свободный период, не раньше чем через 1 час после подтверждения канала в Telegram';
 }
 function campaignWindow(c){
  if(!['active','scheduled','completed'].includes(c.placement_status))return null;
  const start=Date.parse(c.scheduled_start_at),end=Date.parse(c.scheduled_end_at);
  if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)return null;
  const period=el('div',undefined,'campaign-window');
  for(const [label,value] of [['Начало',c.scheduled_start_at],['Окончание',c.scheduled_end_at]]){
   const stamp=new Date(value),group=el('div'),time=el('time');
   time.dateTime=value;time.title=launchTime(value);
   time.append(el('strong',stamp.toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow',day:'numeric',month:'short',year:'numeric'})),
    el('small',stamp.toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'})+' МСК'));
   group.append(el('span',label),time);period.append(group);
  }
  const fraction=Math.max(0,Math.min(100,100*(Date.now()-start)/(end-start)));
  const track=el('progress');track.max=100;track.value=fraction;track.setAttribute('aria-label','Прошедшая часть запланированного периода');period.append(track);
  return period;
 }
 function facts(c,datesOnly=false,datesInOverview=false){
  const list=el('dl',undefined,'campaign-facts');
  const rows=datesOnly?[]:[['Размещение','Прокси-спонсорство'],['Тариф',c.tariff_name_snapshot],['Срок',duration(c)],['Стоимость',price(c)]];
  if(c.requested_start_at)rows.push(['Желаемое начало',launchTime(c.requested_start_at)]);
  if(!datesInOverview)rows.push(['Начало',c.scheduled_start_at?launchTime(c.scheduled_start_at):'Не назначено'],['Окончание',c.scheduled_end_at?launchTime(c.scheduled_end_at):'Не назначено']);
  for(const [key,value] of rows)list.append(el('dt',key),el('dd',value||'—'));
  return list;
 }
 function price(c){return c.price_kopecks_snapshot===0?'Без оплаты':miniAppMode?(c.stars_price_snapshot>0?c.stars_price_snapshot+' XTR':'Цена Stars не задана'):money(c.price_kopecks_snapshot);}
 // Единая карточка во всех разделах; контекст меняет только действия и переходы.
 function campaignTools(item){
  let actions=item.querySelector(':scope > .campaign-tools');
  if(!actions){actions=el('div',undefined,'tile-actions campaign-tools');actions.setAttribute('role','group');actions.setAttribute('aria-label','Действия кампании');item.append(actions);}
  return actions;
 }
 function campaignSummary(c,mode='campaign',{actions=true,route,openLabel,openIcon='chevron',onOpen,overview=false}={}){
  const item=el('article',undefined,'campaign-tile'),head=el('div',undefined,'tile-heading'),copy=el('div');
  item.dataset.placement=c.placement_status;item.dataset.campaignId=c.id;
  const destination=route||(['sponsors','moderation'].includes(mode)?'moderation/':mode==='archive'?'archive/':'campaign/')+c.id;
  const a=el(overview?'h2':'a',title(c),'tile-title');if(!overview)a.href='#/'+destination;const details=el('small',overview?'':'#'+c.id.slice(0,8)),channelUrl=c.channel_url||channels.find(x=>x.id===c.channel_id)?.telegram_url;
  if(/^https:\/\/(?:t\.me|telegram\.me)\/[A-Za-z][A-Za-z0-9_]{3,31}\/?$/.test(channelUrl||'')){const channel=el('a',handle(channelUrl),'campaign-channel-link');channel.href=channelUrl;channel.target='_blank';channel.rel='noopener noreferrer';channel.title='Открыть канал в Telegram';if(!overview)details.append(document.createTextNode(' · '));details.append(channel);}
  copy.append(a);if(details.childNodes.length&&details.textContent)copy.append(details);
  if(c.topic_label)copy.append(el('small',c.topic_label,'campaign-topic'));
  const open=onOpen?button('',()=>onOpen(item)):link('',destination);iconAction(open,openIcon,openLabel||'Открыть кампанию '+title(c));const current=campaignState(c),stateBadge=badge(current[0],current[1]);stateBadge.classList.add('campaign-current');head.append(channelAvatar(c.channel_id,title(c),c.channel_url),copy,stateBadge);if(!overview)head.append(open);else item.classList.add('campaign-overview');item.append(head);
  const period=el('p',undefined,'tile-period'),periodText=el('span',launchPeriod(c));
  if(c.requested_start_at&&!c.scheduled_start_at&&!c.archived_at&&c.placement_status==='not_scheduled'&&c.moderation_status==='approved'&&c.payment_status==='verified'&&!c.auto_start_not_before)periodText.append(el('small','Желаемое начало: '+launchTime(c.requested_start_at),'tile-requested'));
  if(overview)period.append(icon('calendar'));period.append(periodText);
  const windowDates=overview&&campaignWindow(c);item.append(windowDates||period);
  if(mode==='sponsors')item.classList.add('sponsor-placement');
  if(!overview)item.append(statusSteps(c,true));
  if(c.moderation_status==='pending'&&!c.archived_at&&c.moderation_check){
   const section=el('section',undefined,'campaign-review-note');
   section.append(el('h3','Проверка заявки'));
   const fallback={processing:'ИИ проверяет публичные сведения и публикации канала.',review:'Для решения нужна проверка модератора.',retry:'Система повторит проверку.'};
   section.append(el('p',c.moderation_reason||fallback[c.moderation_check]));
   if(c.moderation_attempted_at){
    const at=el('time','Последняя попытка · '+date(c.moderation_attempted_at));
    at.dateTime=c.moderation_attempted_at;section.append(at);
   }
   if(c.moderation_check==='retry'&&c.moderation_retry_at){
    const retry=el('time','Следующая попытка · '+date(c.moderation_retry_at));
    retry.dateTime=c.moderation_retry_at;section.append(retry);
   }
   item.append(section);
  }
  if(c.ai_checked_at&&!overview){
   const description=el('p',c.ai_description||'Описание канала пока отсутствует.','campaign-ai-description');
   const stamp=el('time','Проверено: '+date(c.ai_checked_at),'muted');stamp.dateTime=c.ai_checked_at;
   item.append(el('strong','Pushok ИИ:'),description,stamp);
  }
  if(mode==='moderation'||c.ai_rejected)window.pushokApproval?.addDecisionButtons(item,c);
  const meta=el('dl',undefined,'tile-meta campaign-tile-facts');for(const [label,value] of [['Тариф',c.tariff_name_snapshot||'—'],['Срок',duration(c)],['Стоимость',price(c)]]){const cell=el('div');cell.append(el('dt',label),el('dd',value));meta.append(cell);}item.append(meta);
  if(!actions)return item;
  const payment=()=>iconAction(link('','payments/'+c.id,c.payment_status==='awaiting_payment'&&c.price_kopecks_snapshot>0),'wallet',c.payment_status==='verified'?'Посмотреть оплату':c.price_kopecks_snapshot===0?'Посмотреть размещение':'Оплатить');
  const payable=!c.archived_at && c.moderation_status==='approved' && c.payment_status==='awaiting_payment' && c.price_kopecks_snapshot>0;
  if(payable && ['campaign','payments','sponsors'].includes(mode)){
   const own=mode!=='sponsors' || c.advertiser_user_id===me?.id;
   const action=own?link('Оплатить','payments/'+c.id):window.pushokLinks.share('pay_'+c.id.replaceAll('-',''),'Оплата размещения в puShok');
   if(!own)action.replaceChildren(document.createTextNode('Поделиться ссылкой на оплату'));
   action.prepend(icon(own?'wallet':'share'));action.classList.add('campaign-pay-action');
   const bar=el('div',undefined,'campaign-buy-actions');
   const remove=window.pushokManagement.archiveCampaign(bar,c,mode==='payments'?'payments':'campaigns');
   remove.classList.add('campaign-delete-action');remove.append(el('span','Удалить'));
   bar.append(action);item.append(bar);
   return item;
  }
  if(mode==='reports'){
   const tools=campaignTools(item);tools.append(iconAction(button('',()=>showCampaignReport(c,item)),'chart','История кампании'));if(!miniAppMode)tools.append(iconAction(button('',()=>downloadCampaignCsv(c)),'download','Скачать CSV'));
  }else if(mode==='payments')campaignTools(item).append(payment());
  else if(mode==='campaign'&&c.moderation_status==='changes_requested')campaignTools(item).append(iconAction(link('','campaign/'+c.id),'edit','Исправить кампанию'));
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
   const item=el('article',undefined,'channel-tile'),head=el('div',undefined,'channel-heading'),copy=el('div',undefined,'channel-identity');
   const name=el('h3',c.title),username=el('p',handle(c.telegram_url),'muted');name.title=c.title;username.title=handle(c.telegram_url);copy.append(name,username);
   head.append(channelAvatar(c.id,c.title,c.telegram_url),copy,window.pushokManagement.channelActions(c,item));item.append(head);
   const count=ownCampaigns.filter(x=>x.channel_id===c.id).length,footer=el('div',undefined,'channel-footer'),counter=el('span',undefined,'channel-count');
   const word=count%100>=11&&count%100<=14?'кампаний':count%10===1?'кампания':[2,3,4].includes(count%10)?'кампании':'кампаний';counter.append(el('strong',String(count)),document.createTextNode(' '+word));counter.title='Текущие кампании';
   const create=iconAction(link('','create',true),'campaign','Продвигать канал');create.addEventListener('click',()=>{$('channel-select').value=c.id;window.pushokOrder.selectChannel();});footer.append(counter,create);item.append(footer);
   target.append(item);
  });
 }
 function profile(target,updatePanel){
  target.replaceChildren(window.pushokProfiles.identity(me));
  const overview=el('div',undefined,'profile-overview');target.append(overview);
  if(location.hash.startsWith('#/profile'))void window.pushokProfiles.ownOverview(overview);
  window.pushokVerification.profile(target);
  window.pushokGateway?.profile(target);
  const groups=[['Мой кабинет',[['channels','channel','Мои каналы'],['reports','chart','Отчёты'],...(me.role==='admin'?[]:[['growth','gift','Бонусы и подарки']]),['hall','star','Доска почёта']]]];
  if(['admin','operator'].includes(me.role)){
   const admin=[['moderation','moderation','Модерация'],['sponsors','server','Узлы и спонсоры']];
   admin.unshift(['users','users','Пользователи']);
   if(me.role==='admin')admin.push(['license','moderation','Лицензия приложения'],...window.pushokInterface.managementSections.filter(item=>!item.developerOnly).map(item=>['management/'+item.key,item.icon,item.title]),['growth','gift','Бонусы и акции'],['profile/updates','refresh','Версия и обновления']);
   groups.push(['Управление',admin]);
  }
  if(hasDeveloperAccess(me)){
   const bot=window.pushokInterface.managementSection('ai-bot');
   const links=[['management/'+bot.key,bot.icon,bot.title]];
   if(me.role!=='admin')links.push(['management/vpn','server','Настройки VPN']);
   groups.unshift(['Настройки',links]);
  }
  if(me.role==='moderator')groups.push(['Управление',[['moderation','moderation','Модерация']]]);
  groups.push(['Поддержка',[['help','support','Помощь'],['bot','channel','Telegram-бот']]]);
  if(hasDeveloperAccess(me)||['operator','moderator','admin'].includes(me.role))groups[groups.length-1][1].unshift(['help/team','support','Переписка с пользователями']);
  const sections=el('div',undefined,'profile-groups');
  groups.forEach(([title,items])=>{const group=el('section',undefined,'profile-group'),menu=el('nav',undefined,'profile-menu');if(title==='Управление')group.id='profile-management';if(title==='Настройки')group.id='profile-settings';menu.setAttribute('aria-label',title);group.append(el('h2',title));
   items.forEach(([route,name,text])=>{const a=link(text,route);a.prepend(icon(name));const arrow=icon('chevron');arrow.classList.add('menu-chevron');a.append(arrow);menu.append(a);});group.append(menu);sections.append(group);});target.append(sections);
  if(updatePanel)target.append(updatePanel);
 }
 function emptyLine(text,name='info'){const row=el('p',undefined,'sponsor-empty');row.append(icon(name),el('span',text));return row;}
 function sponsors(target,list){
  target.replaceChildren();
  const active=list.filter(c=>!c.archived_at&&c.placement_status==='active'),queued=list.filter(c=>!c.archived_at&&c.moderation_status==='approved'&&c.payment_status==='verified'&&['scheduled','not_scheduled'].includes(c.placement_status));
  queued.sort((a,b)=>(a.scheduled_start_at||'9999').localeCompare(b.scheduled_start_at||'9999'));
  for(const [title,items,emptyText] of [['Сейчас в эфире',active,'Активных размещений пока нет'],['Очередь размещений',queued,'Готовых к запуску кампаний нет']]){
   const group=el('section',undefined,'sponsor-placement-group'),heading=el('div',undefined,'section-heading');heading.append(el('h2',title),el('span',String(items.length),'count-pill'));group.append(heading);
   if(!items.length)group.append(emptyLine(emptyText,'campaign'));else{const rows=el('div',undefined,'sponsor-placement-list');items.forEach(c=>rows.append(campaignSummary(c,'sponsors')));group.append(rows);}target.append(group);
  }
 }
 return {emptyLine,clearLogos,channelAvatar,channelPreview,channelSearch,channelVerification,userAvatar,icon,iconAction,disclosure,info,brand,mascot,profileAvatar,statusSteps,progress,facts,campaignSummary,campaignTools,campaigns,channelCards,title,duration,price,profile,sponsors};
})();
