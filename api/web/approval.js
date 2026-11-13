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
 function details(title,parent){const d=el('details');d.append(el('summary',title));parent.append(d);return d;}
 function route(id){selected=id;if(window.cabinet)window.cabinet.go('moderation/'+id);else render();}
 function filtered(){const [key,value]=$('queue-filter').value.split('=');return campaigns.filter(c=>!key||String(c[key])===value);}
 function render(){
  if(!me||!['admin','operator'].includes(me.role))return;
  const parts=location.hash.replace(/^#\/?/,'').split('/');
  if(parts[0]==='moderation')selected=parts[1]||selected;
  const list=filtered();
  if(!selected&&list.length===1)selected=list[0].id;
  const c=campaigns.find(item=>item.id===selected);
  $('queue-filter').hidden=Boolean(c);$('queue-count').hidden=Boolean(c);
  root.replaceChildren();$('queue-count').textContent=list.length+' заявок';
  if(!c){
   if(selected)root.append(el('p','Заявка не найдена или недоступна. Обновите список.','empty'));
   if(!list.length)root.append(el('p','Заявок в этой очереди нет.','empty'));
   for(const item of list){const row=el('article',undefined,'approval-summary');row.append(el('h3',item.channel_title||'Канал'),el('p',(labels[item.moderation_status]||item.moderation_status)+' · '+(item.price_kopecks_snapshot===0?'Бесплатно':labels[item.payment_status])+' · '+(labels[item.placement_status]||item.placement_status)),button('Разобрать заявку',()=>route(item.id)));root.append(row);}
   return;
  }
  const box=el('article',undefined,'approval-card');box.dataset.campaignId=c.id;root.append(box);
  const choose=el('select');choose.setAttribute('aria-label','Выбрать рекламную заявку');
  option(choose,'','Все заявки');for(const item of campaigns)option(choose,item.id,(item.channel_title||'Канал')+' · '+item.id.slice(0,8));choose.value=c.id;choose.hidden=campaigns.length===1;
  choose.addEventListener('change',()=>{selected=choose.value;window.cabinet.go(selected?'moderation/'+selected:'moderation');});box.append(choose);
  box.append(el('h2',c.channel_title||'Рекламная заявка'));
  if(c.channel_url){try{const url=new URL(c.channel_url);if(url.protocol==='https:'&&['t.me','telegram.me'].includes(url.hostname)){const a=el('a','Открыть канал в Telegram');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';box.append(a);}}catch{}}
  box.append(el('p',c.tariff_name_snapshot+' · '+(c.price_kopecks_snapshot===0?'Бесплатно':money(c.price_kopecks_snapshot)),'approval-price'));
  const index=stage(c),progress=el('ol',undefined,'approval-progress');progress.setAttribute('aria-label','Этапы рекламной заявки');
  stages.forEach((title,i)=>{const li=el('li',(i<index?'✓ ':String(i+1)+'. ')+title);if(i===index)li.setAttribute('aria-current','step');if(i<index)li.className='done';progress.append(li);});box.append(progress);
  const status=el('p',(labels[c.moderation_status]||c.moderation_status)+' · '+(c.price_kopecks_snapshot===0?'Бесплатно':labels[c.payment_status])+' · '+(labels[c.placement_status]||c.placement_status),'approval-status');box.append(status);
  for(const comment of [c.moderator_comment,c.payment_review_comment])if(comment)box.append(el('p',comment));
  const content=el('section',undefined,'approval-current'),feedback=el('p',undefined,'approval-feedback');feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');box.append(content,feedback);
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
  if(['cancelled','completed'].includes(c.placement_status))content.append(el('h3',c.placement_status==='completed'?'Размещение завершено':'Размещение отменено'));
  else if(c.moderation_status==='pending'){
   content.append(el('h3','1. Проверьте канал'),el('p','Откройте канал выше, проверьте содержание и выберите решение.'));
   content.append(button('Одобрить канал',()=>perform(()=>post('moderation',{decision:'approved'}),'Канал одобрен. Статус оплаты — в этой карточке.')));
   const extra=details('Исправления или отказ',content),f=el('form'),decision=el('select');
   option(decision,'changes_requested','Запросить исправления');option(decision,'rejected','Отклонить канал');field(f,'Решение',decision);
   const comment=note(f,'Комментарий рекламодателю'),submit=el('button','Отправить решение');submit.type='submit';f.append(submit);extra.append(f);
   f.addEventListener('submit',e=>{e.preventDefault();if(f.reportValidity())void perform(()=>post('moderation',{decision:decision.value,comment:comment.value.trim()}),'Решение отправлено рекламодателю.');});
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
   const block=form('3. Назначьте размещение','Назначить слот',async guard=>{if(new Date(start.value).getTime()<=Date.now())throw Error('Начало уже прошло. Выберите будущее время или нажмите «Через 2 минуты».');const free=await api('/operator/campaigns/'+c.id+'/available-nodes?'+new URLSearchParams({start_at:new Date(start.value).toISOString()}));guard();if(!free.nodes.some(n=>n.id===select.value))throw Error('Узел занят на выбранный период или недоступен. Проверьте свободные узлы и выберите другое время.');await post('schedule',{proxy_node_id:select.value,start_at:new Date(start.value).toISOString()});});
   const select=el('select');select.required=true;option(select,'','Выберите узел');const active=nodes.filter(n=>n.status==='active');active.forEach(n=>option(select,n.id,n.name));if(active.length===1)select.value=active[0].id;field(block.f,'Прокси-узел',select);
   const start=el('input');start.type='datetime-local';start.required=true;const requested=new Date(c.requested_start_at),d=requested.getTime()>Date.now()?requested:new Date(Date.now()+5*60000);start.value=localDay(d)+'T'+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');field(block.f,'Начало размещения',start);
   block.f.append(el('p','Часовой пояс: '+calendarZone+'. Окончание рассчитает сервер по тарифу.','muted'));
   if(!active.length){block.f.append(el('p','Нет активных узлов для расписания. Подготовьте узел либо разрешите планирование на существующем узле ниже.'));if(me.role==='admin')for(const n of nodes.filter(n=>n.status==='paused'))block.f.append(button('Разрешить планирование: '+n.name,()=>perform(()=>api('/admin/proxy-nodes/'+n.id+'/status',{method:'PATCH',body:{status:'active'}}),'Узел доступен для планирования. Подключение MTProxy проверяется отдельно.')));}
   const availability=el('p',undefined,'muted');availability.setAttribute('role','status');
   block.f.append(button('Через 2 минуты',()=>{const d=new Date(Date.now()+120000);start.value=localDay(d)+'T'+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}),button('Проверить свободные узлы',async()=>{if(!start.value)throw Error('Укажите дату начала.');const data=await api('/operator/campaigns/'+c.id+'/available-nodes?'+new URLSearchParams({start_at:new Date(start.value).toISOString()}));if(!box.isConnected||token!==session)return;const old=select.value;select.replaceChildren();option(select,'','Выберите узел');data.nodes.forEach(n=>option(select,n.id,n.name));if(data.nodes.some(n=>n.id===old))select.value=old;else if(data.nodes.length===1)select.value=data.nodes[0].id;availability.textContent=data.nodes.length?'Свободных узлов: '+data.nodes.length+'. Окончание: '+date(data.ends_at):'На весь период тарифа свободных узлов нет. Измените время или снимите прежний будущий слот.';}),availability);
   block.finish();
  }else if(c.placement_status==='scheduled'){
   content.append(el('p','Назначено: '+date(c.scheduled_start_at)+' — '+date(c.scheduled_end_at)));
   let savedReport=null;
   const block=form('4. Подтвердите фактический показ','Подтвердить показ и активировать',async guard=>{
    if(!savedReport){savedReport=await post('display-observations',{result:'visible',method:'separate_unsubscribed_client',observed_at:new Date().toISOString(),note:observation.value.trim()});}
    guard();
    if(screenshot.files[0]){const body=new FormData();body.append('file',screenshot.files[0]);try{await api('/operator/campaigns/'+c.id+'/display-observations/'+savedReport.id+'/evidence',{method:'POST',body});screenshot.value='';}catch(e){throw Error('Наблюдение сохранено. Скриншот не прикреплён: '+e.message);}}
    guard();try{await post('activate');}catch(e){throw Error('Наблюдение сохранено, активация не подтверждена: '+e.message);}
   });
   block.f.append(el('p','Включите спонсорский тег вручную. Проверьте показ в отдельном Telegram-аккаунте, не подписанном на канал. Эта кнопка сама тег не переключает.'));
   const observation=note(block.f,'Что увидели при проверке'),screenshot=el('input');screenshot.type='file';screenshot.accept='image/png,image/jpeg';field(block.f,'Скриншот проверки (необязательно)',screenshot);
   check(block.f,'Тег включён, спонсорский канал действительно виден в отдельном аккаунте');block.finish();
   const extra=details('Показ не обнаружен',content),f=el('form'),comment=note(f,'Что проверили'),submit=el('button','Записать: показ не обнаружен');submit.type='submit';f.append(submit);extra.append(f);
   f.addEventListener('submit',e=>{e.preventDefault();if(f.reportValidity())void perform(()=>post('display-observations',{result:'not_visible',method:'separate_unsubscribed_client',observed_at:new Date().toISOString(),note:comment.value.trim()}),'Отрицательное наблюдение сохранено. Размещение не активировано.');});
  }else if(c.placement_status==='active'){
   const block=form('5. Завершите размещение','Тег выключен — завершить',()=>post('complete',{tag_removed_confirmed:true,verification_method:'separate_unsubscribed_client'}));
   block.f.append(el('p','Период: '+date(c.scheduled_start_at)+' — '+date(c.scheduled_end_at)));
   check(block.f,'Выключил тег и проверил отсутствие спонсора в отдельном аккаунте');block.finish();
  }
  if(window.pushokManagement)box.append(window.pushokManagement.campaignActions(c,true));
  const history=details('История и документы',box);
  history.append(el('p','Номер заявки: '+c.id),el('p','Желаемое начало: '+date(c.requested_start_at)),button('Отчёт по кампании',()=>showCampaignReport(c,history)),button('Выгрузить CSV',()=>downloadCampaignCsv(c)));
  if(['scheduled','active','completed'].includes(c.placement_status))history.append(button('Журнал проверок',async()=>{
   const rows=await api('/operator/campaigns/'+c.id+'/display-observations');if(!history.isConnected||session!==token)return;
   history.querySelector('.observation-panel')?.remove();const panel=el('section',undefined,'observation-panel');history.append(panel);
   if(!rows.length)panel.append(el('p','Проверок пока нет.'));
   for(const report of rows){panel.append(el('p',(report.result==='visible'?'Показ виден':'Показ не найден')+' · '+date(report.observed_at)+' · '+report.note));
    if(report.evidence_id)panel.append(button('Скачать скриншот №'+report.id,()=>download('/operator/campaigns/'+c.id+'/display-observations/'+report.id+'/evidence','sponsor-evidence-'+report.id)));
    else if(report.observer_user_id===me.id){const input=el('input');input.type='file';input.accept='image/png,image/jpeg';field(panel,'Добавить скриншот к отчёту '+report.id,input);panel.append(button('Прикрепить скриншот №'+report.id,async()=>{if(!input.files[0])throw Error('Выберите PNG или JPEG.');const body=new FormData();body.append('file',input.files[0]);await api('/operator/campaigns/'+c.id+'/display-observations/'+report.id+'/evidence',{method:'POST',body});notice('Скриншот сохранён.',true);}));}
   }
  }));
 }
 async function download(path,name){const session=token;const response=await fetch('/api/v1'+path,{headers:{Authorization:'Bearer '+token}});if(!response.ok)throw Error('Не удалось скачать файл. Обновите состояние.');const blob=await response.blob();if(session!==token||!me)return;const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);}
 return {resetSelection(){selected='';},sync(items){if(currentUser!==me?.id){selected='';currentUser=me?.id;}campaigns=items;render();},render,clear(){campaigns=[];selected='';currentUser='';root.replaceChildren();$('queue-filter').hidden=false;$('queue-count').hidden=false;}};
})();

if(me&&['admin','operator'].includes(me.role))window.pushokApproval.sync(operatorCampaigns);
