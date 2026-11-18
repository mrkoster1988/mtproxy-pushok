'use strict';
// Одна карточка оператора. Статусы и разрешения по-прежнему проверяет API.
window.pushokApproval=(()=>{
 let campaigns=[],selected='',currentUser='';
 const root=$('queue');
 const stages=['Канал','Оплата','Дата и узел','Показ спонсора','Завершение'];
 function stage(c){
  if(c.moderation_status!=='approved')return 0;
  if(c.payment_status!=='verified')return 1;
  return {not_scheduled:2,scheduled:3,active:4,completed:5}[c.placement_status]??5;
 }
 function field(form,title,input){input.setAttribute('aria-label',title);const label=el('label',title);label.append(input);form.append(label);return input;}
 function check(form,text){const input=el('input');input.type='checkbox';input.required=true;field(form,text,input).className='approval-check';return input;}
 function note(form,title){const input=el('textarea');input.required=true;input.minLength=12;input.maxLength=2000;input.rows=3;return field(form,title,input);}
 function details(title,parent,icon='info',cls=''){
  const d=el('details',undefined,'approval-section '+cls),summary=el('summary'),body=el('div',undefined,'approval-body');
  summary.append(window.pushokViews.icon(icon),el('span',title),window.pushokViews.icon('chevron'));
  d.append(summary,body);parent.append(d);return body;
 }
 function route(id){selected=id;if(window.cabinet)window.cabinet.go('moderation/'+id);else render();}
 function filtered(){const [key,value]=$('queue-filter').value.split('=');return campaigns.filter(c=>!c.archived_at&&(!key||String(c[key])===value));}
 const archiveReasons={placement:'Активный показ ещё не завершён.',proof:'Загруженный чек ожидает проверки.',payment:'Счёт ещё не закрыт платёжной системой.'};
 function archivedQueue(){
  const rows=campaigns.filter(c=>c.archived_at);if(!rows.length)return;
  const panel=details('Удалённые: требуют завершения ('+rows.length+')',root,'trash','archived-review');
  panel.append(el('p','Эти заявки уже удалены из рабочего списка. Здесь остаются только незавершённые платежи и показы.','block-note'));
  for(const c of rows){const row=window.pushokViews.campaignSummary(c,'moderation',{actions:false,openLabel:'Открыть для завершения'});row.classList.add('approval-summary');
   for(const reason of c.archive_pending_actions||[])if(archiveReasons[reason])row.append(el('p',archiveReasons[reason]));
   panel.append(row);
  }
 }
 function advertiser(c){
  return window.pushokProfiles.advertiser(c);
 }
 function rejectionReason(c){
  const dialog=el('dialog',undefined,'moderation-reason-dialog');
  const form=el('form'),heading=el('h2','Отклонить заявку');
  heading.id='moderation-reason-'+c.id;
  dialog.setAttribute('aria-labelledby',heading.id);
  form.append(heading,el('p',c.channel_title||c.channel_url||'#'+c.id.slice(0,8)));
  const comment=note(form,'Причина отказа для рекламодателя');
  const cancel=button('Отмена',()=>dialog.close());
  const submit=el('button','Отклонить');submit.type='submit';
  const actions=el('div',undefined,'moderation-reason-actions');actions.append(cancel,submit);
  form.append(actions);dialog.append(form);document.body.append(dialog);
  return new Promise(resolve=>{
   let reason=null;
   const leave=()=>dialog.close();
   window.addEventListener('hashchange',leave);
   dialog.addEventListener('close',()=>{
    window.removeEventListener('hashchange',leave);dialog.remove();resolve(reason);
   },{once:true});
   comment.addEventListener('input',()=>comment.setCustomValidity(''));
   form.addEventListener('submit',event=>{
    event.preventDefault();
    const text=comment.value.trim();
    if(text.length<comment.minLength){
     comment.setCustomValidity('Опишите причину отказа подробнее.');comment.reportValidity();return;
    }
    reason=text;dialog.close();
   });
   dialog.showModal();comment.focus();
  });
 }
 const decisionsInFlight=new Set();
 const needsDecision=c=>c&&!c.archived_at&&((c.moderation_status==='pending'&&c.moderation_check==='review')||(c.moderation_status==='rejected'&&c.ai_rejected));
 function saveModeration(c,body){
  return api('/operator/campaigns/'+c.id+'/moderation',{method:'POST',body});
 }
 function moderationSummary(c,options){
  const row=window.pushokViews.campaignSummary(c,'moderation',{actions:false,...options});
  return row;
 }
 function addDecisionButtons(row,c){
  if(!['admin','operator'].includes(me?.role)||!needsDecision(c))return;
  const period=row.querySelector('.tile-period'),tools=el('span',undefined,'quick-moderation-actions');
  tools.setAttribute('role','group');tools.setAttribute('aria-label','Решение по заявке');
  const session=token,user=me.id;
  const authorized=()=>session===token&&me?.id===user&&['admin','operator'].includes(me.role);
  async function decide(decision){
   if(!authorized()||decisionsInFlight.has(c.id))return;
   decisionsInFlight.add(c.id);
   try{
    const body={decision};if(c.ai_rejected)body.reconsider_ai=true;
    if(decision==='rejected'){
     body.comment=await rejectionReason(c);
     if(!body.comment)return;
    }
    if(!authorized()||!needsDecision(campaigns.find(item=>item.id===c.id)||c))return;
    let result;
    try{
     result=await saveModeration(c,body);
    }catch(error){
     if(error.code==='campaign_not_pending'&&authorized()){
      await refresh();notice('Эта заявка уже обработана. Очередь обновлена.');return;
     }
     throw error;
    }
    if(!authorized())return;
    const current=campaigns.find(item=>item.id===c.id);
    if(current){
     current.moderation_status=result.moderation_status;current.payment_status=result.payment_status;
     current.moderator_comment=result.moderator_comment;current.ai_rejected=false;
    }
    render();
    try{await refresh();}catch{if(authorized())notice('Решение сохранено. Обновите список заявок.');return;}
    if(authorized())notice(decision==='approved'?'Канал одобрен.':'Заявка отклонена. Причина сохранена.',true);
   }finally{decisionsInFlight.delete(c.id);}
  }
  for(const [decision,icon,label] of [['rejected','close','Отклонить заявку'],['approved','check','Одобрить заявку']]){
   const control=window.pushokViews.iconAction(button('',()=>decide(decision)),icon,label);
   control.classList.add('quick-'+decision);tools.append(control);
  }
  period.classList.add('quick-moderation-period');period.append(tools);
  return row;
 }
 function render(){
  if(!me||!['admin','operator'].includes(me.role))return;
  const parts=location.hash.replace(/^#\/?/,'').split('/');
  if(parts[0]==='moderation')selected=parts[1]||'';
  const list=filtered();
  if(!selected&&list.length===1)selected=list[0].id;
  const c=campaigns.find(item=>item.id===selected);
  $('queue-filter').hidden=Boolean(c);$('queue-count').hidden=Boolean(c);
  window.pushokAssistant?.reset();root.replaceChildren();$('queue-count').textContent=list.length+' заявок';
  if(!c){
   if(selected)root.append(el('p','Заявка не найдена или недоступна. Обновите список.','empty'));
   if(!list.length)root.append(el('p','Заявок в этой очереди нет.','empty'));
   for(const item of list){const row=moderationSummary(item,{openLabel:'Разобрать заявку'});row.classList.add('approval-summary');root.append(row);}
   archivedQueue();return;
  }
  const box=el('article',undefined,'approval-card');box.dataset.campaignId=c.id;root.append(box);
  const navigation=el('div',undefined,'approval-navigation'),back=el('a','Все заявки','approval-back');back.href='#/moderation';back.prepend(window.pushokViews.icon('back'));navigation.append(back);
  const choose=el('select');choose.setAttribute('aria-label','Выбрать рекламную заявку');
  const choices=campaigns.filter(item=>!item.archived_at||item.id===c.id);
  option(choose,'','Все заявки');for(const item of choices)option(choose,item.id,(item.archived_at?'Удалена · ':'')+(item.channel_title||'Канал')+' · '+item.id.slice(0,8));choose.value=c.id;choose.hidden=choices.length===1;
  choose.addEventListener('change',()=>{selected=choose.value;window.cabinet.go(selected?'moderation/'+selected:'moderation');});navigation.append(choose);box.append(navigation);
  box.append(moderationSummary(c,{route:'moderation',openLabel:'Все заявки'}),advertiser(c));

  if(c.archived_at){
   const banner=el('section',undefined,'card approval-archive-notice');banner.setAttribute('role','status');
   banner.append(el('h3','Заявка уже удалена'),el('p','Удалено: '+date(c.archived_at)+'. В рабочей очереди её нет.'));
   for(const reason of c.archive_pending_actions||[])if(archiveReasons[reason])banner.append(el('p',archiveReasons[reason]));
   const actions=el('div',undefined,'campaign-action-tools');actions.append(button('К рабочим заявкам',()=>route('')),button('Открыть «Удалённые»',()=>window.cabinet.go('archive')));banner.append(actions);box.append(banner);
  }
  const index=stage(c),progress=el('ol',undefined,'approval-progress');progress.setAttribute('aria-label','Этапы рекламной заявки');
  stages.forEach((title,i)=>{const li=el('li'),marker=el('span',undefined,'approval-step-marker');if(i<index)marker.append(window.pushokViews.icon('check'));else marker.textContent=String(i+1);li.append(marker,el('span',title));if(i===index)li.setAttribute('aria-current','step');if(i<index)li.className='done';progress.append(li);});
  const decisionNode=el('p',c.moderator_comment||'', 'moderation-decision');box.append(decisionNode);
  if(c.payment_review_comment)box.append(el('p',c.payment_review_comment));
  if(window.pushokAssistant){const assistant=window.pushokAssistant.panel(c,{expanded:true,decisionNode});assistant.classList.add('approval-section');assistant.querySelector('.assistant-body').classList.add('approval-body');box.append(assistant);}
  const workflow=el('section',undefined,'approval-current');workflow.setAttribute('aria-label','Работа с заявкой');workflow.append(progress);
  let content=el('div',undefined,'approval-body');const feedback=el('p',undefined,'approval-feedback');feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');workflow.append(content,feedback);box.append(workflow);
  const session=token,user=me.id;let busy=false;
  function validSession(){if(session!==token||me?.id!==user)throw Error('Сессия изменилась. Обновите страницу.');}
  async function perform(fn,message){
   if(busy)return;busy=true;box.querySelectorAll('button,input,select,textarea').forEach(n=>n.disabled=true);feedback.textContent='Сохраняем…';
   try{validSession();await fn(validSession);validSession();
    try{await refresh();notice(message,true);}catch{feedback.textContent='Действие выполнено. Обновите данные перед следующим шагом.';content.replaceChildren(button('Обновить состояние',refresh));}
   }catch(e){if(session===token&&me?.id===user){feedback.textContent=e.message;feedback.scrollIntoView({block:'nearest'});}}
   finally{busy=false;if(box.isConnected)box.querySelectorAll('button,input,select,textarea').forEach(n=>n.disabled=false);}
  }
  const post=(path,body)=>api('/operator/campaigns/'+c.id+'/'+path,{method:'POST',body});
  function form(title,label,fn){
   content.append(el('h3',title));const f=el('form'),submit=el('button',label);submit.type='submit';
   f.addEventListener('invalid',event=>{event.preventDefault();feedback.textContent='Заполните поле «'+(event.target.getAttribute('aria-label')||'значение')+'». Для слота нужны активный узел и дата начала.';event.target.focus();feedback.scrollIntoView({block:'nearest'});},{capture:true});
   f.addEventListener('submit',event=>{event.preventDefault();if(!f.reportValidity())return;void perform(guard=>fn(guard),'Заявка обновлена. Следующий этап — здесь же.');});
   content.append(f);return {f,submit,finish(){f.append(submit);}};
  }
  if(!c.archived_at&&['cancelled','completed'].includes(c.placement_status))content.append(el('h3',c.placement_status==='completed'?'Размещение завершено':'Размещение отменено'));
  else if(c.archived_at&&c.placement_status!=='active'&&c.payment_status!=='proof_submitted'){
   content.append(el('h3','Проверка платежа удалённой заявки'),el('p','История оплаты сохранена. Архивирование не отменяет выставленный счёт у платёжной системы. Когда счёт будет закрыт, заявка исчезнет из этого раздела и останется в «Удалённых».','block-note'),button('Проверить статус оплаты',refresh));
  }else if(c.moderation_status==='pending'){
   content.append(el('h3','1. Проверьте канал'),el('p','Откройте канал выше, проверьте содержание и выберите решение.'));
   content.append(button('Одобрить канал',()=>perform(()=>saveModeration(c,{decision:'approved'}),'Канал одобрен. Статус оплаты — в этой карточке.')));
   const extra=details('Исправления или отказ',content),f=el('form'),decision=el('select');
   option(decision,'changes_requested','Запросить исправления');option(decision,'rejected','Отклонить канал');field(f,'Решение',decision);
   const comment=note(f,'Комментарий рекламодателю'),submit=el('button','Отправить решение');submit.type='submit';f.append(submit);extra.append(f);
   f.addEventListener('submit',e=>{e.preventDefault();if(f.reportValidity())void perform(()=>saveModeration(c,{decision:decision.value,comment:comment.value.trim()}),'Решение отправлено рекламодателю.');});
  }else if(c.moderation_status!=='approved')content.append(el('h3',c.moderation_status==='rejected'?'Заявка отклонена':'Ожидаем рекламодателя'),el('p',c.moderation_status==='rejected'?'Рекламодатель увидит причину отказа и сможет создать новую заявку.':'Рекламодатель должен исправить и отправить заявку на проверку.'));
  else if(c.payment_status==='proof_submitted'){
   const block=form('2. Проверьте оплату','Подтвердить оплату',()=>post('payment-review',{decision:'verified'}));
   block.f.append(button('Посмотреть чеки',async()=>{
    const proofs=await api('/operator/campaigns/'+c.id+'/payment-proofs');if(!box.isConnected||session!==token)return;
    const target=el('div',undefined,'approval-proofs');block.f.querySelector('.approval-proofs')?.remove();
    for(const proof of proofs)target.append(button(proof.original_file_name,()=>download('/operator/campaigns/'+c.id+'/payment-proofs/'+proof.id,proof.original_file_name)));
    block.f.prepend(target);
   }));check(block.f,'Проверил поступление денег, сумму и назначение платежа');block.finish();
   const extra=details('Отклонить чек',content),f=el('form'),comment=note(f,'Причина отклонения чека'),submit=el('button','Отклонить чек');submit.type='submit';f.append(submit);extra.append(f);
   f.addEventListener('submit',e=>{e.preventDefault();if(f.reportValidity())void perform(()=>post('payment-review',{decision:'rejected',comment:comment.value.trim()}),'Чек отклонён. Причина отправлена рекламодателю.');});
  }else if(c.payment_status!=='verified'){
   content.append(el('h3','2. Ожидаем оплату'),el('p',c.payment_status==='refunded'?'Оплата возвращена. Размещение недоступно.':'Рекламодатель оплачивает свою заявку. Stars и СБП подтверждаются сервером; загруженный чек появится здесь для проверки.'),button('Проверить статус оплаты',refresh));
  }else if(c.placement_status==='not_scheduled'){
   void renderPromotionStatus(c,content);

   const resume=button('Возобновить автоподбор',()=>perform(()=>post('auto-schedule'),'Автоподбор включён. Расписание обновится автоматически.'));
   if(c.auto_schedule_paused)content.append(resume);
   content=details('Изменить расписание',content);
   if(!c.auto_schedule_paused)content.append(resume);
   const block=form('3. Назначьте размещение','Назначить слот',async guard=>{if(new Date(start.value).getTime()<=Date.now())throw Error('Начало уже прошло. Выберите будущее время или нажмите «Через 2 минуты».');const free=await api('/operator/campaigns/'+c.id+'/available-nodes?'+new URLSearchParams({start_at:new Date(start.value).toISOString()}));guard();if(!free.nodes.some(n=>n.id===select.value))throw Error('Узел занят на выбранный период или недоступен. Проверьте свободные узлы и выберите другое время.');await post('schedule',{proxy_node_id:select.value,start_at:new Date(start.value).toISOString()});});
   const select=el('select');select.required=true;option(select,'','Выберите узел');const active=nodes.filter(n=>n.status==='active');active.forEach(n=>option(select,n.id,n.name));if(active.length===1)select.value=active[0].id;field(block.f,'Прокси-узел',select);
   const start=el('input');start.type='datetime-local';start.required=true;const requested=new Date(c.requested_start_at),d=requested.getTime()>Date.now()?requested:new Date(Date.now()+5*60000);start.value=localDay(d)+'T'+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');field(block.f,'Начало размещения',start);
   block.f.append(el('p','Часовой пояс: '+calendarZone+'. Окончание рассчитает сервер по тарифу.','muted'));
   if(!active.length){block.f.append(el('p','Нет активных узлов для расписания. Подготовьте узел либо разрешите планирование на существующем узле ниже.'));if(me.role==='admin')for(const n of nodes.filter(n=>n.status==='paused'))block.f.append(button('Разрешить планирование: '+n.name,()=>perform(()=>api('/admin/proxy-nodes/'+n.id+'/status',{method:'PATCH',body:{status:'active'}}),'Узел доступен для планирования. Подключение MTProxy проверяется отдельно.')));}
   const availability=el('p',undefined,'muted');availability.setAttribute('role','status');
   block.f.append(button('Через 2 минуты',()=>{const d=new Date(Date.now()+120000);start.value=localDay(d)+'T'+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}),button('Проверить свободные узлы',async()=>{if(!start.value)throw Error('Укажите дату начала.');const data=await api('/operator/campaigns/'+c.id+'/available-nodes?'+new URLSearchParams({start_at:new Date(start.value).toISOString()}));if(!box.isConnected||token!==session)return;const old=select.value;select.replaceChildren();option(select,'','Выберите узел');data.nodes.forEach(n=>option(select,n.id,n.name));if(data.nodes.some(n=>n.id===old))select.value=old;else if(data.nodes.length===1)select.value=data.nodes[0].id;availability.textContent=data.nodes.length?'Свободных узлов: '+data.nodes.length+'. Окончание: '+date(data.ends_at):'На весь период тарифа свободных узлов нет. Измените время или снимите прежний будущий слот.';}),availability);
   block.finish();
  }else if(c.placement_status==='scheduled'){
   void renderPromotionStatus(c,content);
   content.append(el('p','Окончание: '+date(c.scheduled_end_at),'approval-period'));

   content=details('Дополнительная ручная проверка',content,'moderation');
   content.append(el('p','Назначено: '+date(c.scheduled_start_at)+' — '+date(c.scheduled_end_at)));
   let savedReport=null;
   const block=form('4. Подтвердите фактический показ','Подтвердить показ и активировать',async guard=>{
    if(!savedReport){savedReport=await post('display-observations',{result:'visible',method:'separate_unsubscribed_client',observed_at:new Date().toISOString(),note:observation.value.trim()});}
    guard();
    if(screenshot.files[0]){const body=new FormData();body.append('file',screenshot.files[0]);try{await api('/operator/campaigns/'+c.id+'/display-observations/'+savedReport.id+'/evidence',{method:'POST',body});screenshot.value='';}catch(e){throw Error('Наблюдение сохранено. Скриншот не прикреплён: '+e.message);}}
    guard();try{await post('activate');}catch(e){throw Error('Наблюдение сохранено, активация не подтверждена: '+e.message);}
   });
   block.f.append(el('p','Проверьте показ в своём Telegram: подключитесь к прокси этой кампании и убедитесь, что вы не подписаны на канал. У канала в списке чатов должна быть отметка «Спонсор прокси». Второй аккаунт не нужен. Кнопка сохраняет результат проверки, сам тег она не переключает.','block-note'));
   const observation=note(block.f,'Что увидели при проверке'),screenshot=el('input');screenshot.type='file';screenshot.accept='image/png,image/jpeg';field(block.f,'Скриншот проверки (необязательно)',screenshot);
   check(block.f,'Я подключён к этому прокси, не подписан на канал и вижу отметку «Спонсор прокси»');block.finish();
   const extra=details('Показ не обнаружен',content),f=el('form'),comment=note(f,'Что проверили'),submit=el('button','Записать: показ не обнаружен');submit.type='submit';f.append(submit);extra.append(f);
   f.addEventListener('submit',e=>{e.preventDefault();if(f.reportValidity())void perform(()=>post('display-observations',{result:'not_visible',method:'separate_unsubscribed_client',observed_at:new Date().toISOString(),note:comment.value.trim()}),'Отрицательное наблюдение сохранено. Размещение не активировано.');});
  }else if(c.placement_status==='active'){
   void renderPromotionStatus(c,content);
   content.append(el('h3','Размещение активно'),el('p','После окончания периода агент снимет или сменит тег и автоматически завершит кампанию.','block-note'));
   content=details('Ручное завершение и диагностика',content);
   const block=form('5. Завершите размещение','Подтвердить завершение',()=>post('complete',{tag_removed_confirmed:true,verification_method:'separate_unsubscribed_client'}));
   block.f.append(el('p','Период: '+date(c.scheduled_start_at)+' — '+date(c.scheduled_end_at)));
   block.f.append(el('p','При настроенном автоприменении агент вернёт исходный тег после окончания периода. При ручном размещении снимите тег самостоятельно. Затем проверьте в подключённом Telegram без подписки, что канал этой кампании больше не отмечен как спонсор. Второй аккаунт не нужен.','block-note'));
   block.f.append(el('p','Если исходный тег ведёт на тот же канал, канал может остаться спонсором. Возврат настроек сам по себе не подтверждает прекращение показа. Кнопка закрывает кампанию после вашей проверки.','muted'));
   check(block.f,'Тег кампании снят; в подключённом Telegram без подписки канал больше не показан как спонсор');block.finish();
  }
  if(me.role==='admin'&&window.pushokCampaignPricing){const pricing=window.pushokCampaignPricing.create(c);pricing.classList.add('approval-section');const heading=pricing.querySelector('summary');heading.replaceChildren(window.pushokViews.icon('price'),el('span','Тариф и скидка'),window.pushokViews.icon('chevron'));pricing.querySelector(':scope > div').classList.add('approval-body');box.append(pricing);}

  if(!c.archived_at&&window.pushokManagement)box.append(window.pushokManagement.campaignActions(c,true));
  const history=details('История и документы',box,'history'),facts=el('dl',undefined,'approval-facts'),tools=el('div',undefined,'campaign-action-tools');tools.setAttribute('role','group');tools.setAttribute('aria-label','Документы заявки');
  for(const [title,value] of [['Номер заявки',c.id],['Желаемое начало',date(c.requested_start_at)]]){const row=el('div');row.append(el('dt',title),el('dd',value));facts.append(row);}
  history.append(facts,tools);history.parentElement.addEventListener('toggle',()=>{if(history.parentElement.open&&!history.querySelector('.campaign-report'))void showCampaignReport(c,history,{embedded:true});});tools.append(window.pushokViews.iconAction(button('',()=>showCampaignReport(c,history,{refresh:true,embedded:true})),'refresh','Обновить историю и документы'));
  if(['scheduled','active','completed'].includes(c.placement_status))tools.append(window.pushokViews.iconAction(button('',async()=>{
   const rows=await api('/operator/campaigns/'+c.id+'/display-observations');if(!history.isConnected||session!==token)return;
   history.querySelector('.observation-panel')?.remove();const panel=el('section',undefined,'observation-panel observation-journal');panel.append(el('h4','Журнал проверок'));history.append(panel);
   if(!rows.length)panel.append(el('p','Проверок пока нет.','muted'));
   for(const report of rows){const entry=el('article',undefined,'observation-entry'),header=el('div',undefined,'block-heading'),copy=el('div'),at=el('time',date(report.observed_at));at.dateTime=report.observed_at;copy.append(el('strong',report.result==='visible'?'Показ виден':'Показ не найден'),at);header.append(copy);entry.append(header,el('p',report.note));panel.append(entry);
    const downloadButton=()=>window.pushokViews.iconAction(button('',()=>download('/operator/campaigns/'+c.id+'/display-observations/'+report.id+'/evidence','sponsor-evidence-'+report.id)),'download','Скачать скриншот №'+report.id);
    if(report.evidence_id)header.append(downloadButton());
    else if(report.observer_user_id===me.id){const form=el('form'),input=el('input'),feedback=el('p',undefined,'muted');feedback.setAttribute('role','status');input.type='file';input.accept='image/png,image/jpeg';input.required=true;field(form,'Скриншот проверки №'+report.id,input);const upload=window.pushokViews.iconAction(button('',async()=>{
     if(!form.reportValidity())return;const body=new FormData();body.append('file',input.files[0]);await api('/operator/campaigns/'+c.id+'/display-observations/'+report.id+'/evidence',{method:'POST',body});
     if(!entry.isConnected||session!==token)return;form.remove();header.append(downloadButton());feedback.textContent='Скриншот сохранён.';
    }),'upload','Прикрепить скриншот №'+report.id);form.append(upload);entry.append(form,feedback);window.pushokControls.fieldActions(input,[upload]);}
   }
  }),'history','Журнал проверок'));
  if(me.role==='admin'){const panel=details('Возврат оплаты',box,'wallet','campaign-refunds');panel.parentElement.addEventListener('toggle',()=>{if(panel.parentElement.open&&!panel.dataset.loaded){panel.dataset.loaded='true';window.pushokRefunds.load(c,panel);}});}
  if(!c.archived_at)archivedQueue();
 }
 async function download(path,name){const session=token;const response=await fetch('/api/v1'+path,{headers:{Authorization:'Bearer '+token}});if(!response.ok)throw Error('Не удалось скачать файл. Обновите состояние.');const blob=await response.blob();if(session!==token||!me)return;const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);}
 return {addDecisionButtons,resetSelection(){selected='';},sync(items){if(currentUser!==me?.id){selected='';currentUser=me?.id;}campaigns=items;render();},render,clear(){window.pushokAssistant?.reset();campaigns=[];selected='';currentUser='';root.replaceChildren();$('queue-filter').hidden=false;$('queue-count').hidden=false;}};
})();

if(me&&['admin','operator'].includes(me.role))window.pushokApproval.sync(operatorCampaigns);
