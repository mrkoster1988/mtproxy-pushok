'use strict';
// Действия рядом с объектом; сервер проверяет владение, состояние и связи.
window.pushokManagement=(()=>{
 Object.assign(errors,{
  management_object_already_exists:'Такой объект уже существует. Проверьте рабочий список и раздел «Удалённые».',
  management_payment_in_use:'У заявки есть оплата, чек или незавершённый счёт. Сначала закончите проверку платежа или возврат.',
  management_placement_in_use:'Сначала снимите будущий слот либо завершите активное размещение.',
  management_slot_already_started:'Слот уже начался либо больше не запланирован. Обновите данные; активное размещение завершается после выключения тега.',
  management_slot_has_observations:'По этому слоту уже записана проверка показа. Сначала завершите проверку и размещение.',
  management_restore_dependencies:'Сначала восстановите канал и тариф этой заявки в разделе «Удалённые».',
  management_restore_channel:'Этот канал удалён из списка. Восстановите его в разделе «Удалённые».',
  management_object_not_found:'Объект не найден или недоступен вашей роли.',
  active_proxy_node_not_found:'Выбранный узел недоступен или на паузе. Обновите список узлов.',
  schedule_start_requires_timezone:'Укажите дату и время начала.',
  channel_not_found:'Канал не найден или уже удалён из списка.'
 });
 function label(f,text,input){input.setAttribute('aria-label',text);const l=el('label',text);l.append(input);f.append(l);return input;}
 function editor(parent,title,fields,save){
  parent.querySelector('.management-form')?.remove();const form=el('form',undefined,'management-form'),heading=el('h4',title),hint=el('p');hint.setAttribute('role','status');form.append(heading);
  const inputs={};for(const spec of fields){const input=el(spec.type==='textarea'?'textarea':'input');if(spec.type!=='textarea')input.type=spec.type||'text';input.value=spec.value??'';input.required=spec.required!==false;if(spec.min)input.min=spec.min;if(spec.max)input.max=spec.max;inputs[spec.name]=label(form,spec.label,input);}
  const submit=el('button','Сохранить изменения');submit.type='submit';form.append(submit,button('Отмена',()=>form.remove()),hint);
  form.addEventListener('invalid',e=>{e.preventDefault();hint.textContent='Заполните корректно поле «'+(e.target.getAttribute('aria-label')||'значение')+'».';e.target.focus();},{capture:true});
  form.addEventListener('submit',e=>{e.preventDefault();if(!form.reportValidity()||submit.disabled)return;void run(async()=>{try{await save(inputs);form.remove();await refresh();notice('Изменения сохранены.',true);}catch(error){hint.textContent=error.message;}},submit);});
  parent.append(form);form.scrollIntoView({block:'nearest'});return form;
 }
 function archive(parent,path,text,route){
  const b=button('🗑️ Удалить',async()=>{
   if(!confirm(text+' Объект попадёт в «Удалённые»; историю можно будет восстановить.'))return;
   const from=location.hash;await api(path,{method:'DELETE'});window.pushokApproval?.resetSelection();await refresh();if(route&&location.hash===from){window.pushokApproval?.resetSelection();window.cabinet.go(route);}notice('Объект перемещён в «Удалённые». История сохранена.',true);
  });parent.append(b);
 }
 function channelActions(c){
  const box=el('div',undefined,'management-actions');box.append(button('✏️ Изменить',()=>editor(box,'Изменить канал',[
   {name:'title',label:'Название канала',value:c.title},{name:'url',label:'Ссылка на канал',value:c.telegram_url,type:'url'}
  ],v=>api('/channels/'+c.id,{method:'PATCH',body:{title:v.title.value.trim(),telegram_url:v.url.value.trim()}}))));
  archive(box,'/channels/'+c.id,'Удалить канал из рабочего списка?','channels');return box;
 }
 function campaignActions(c,operator=false){
  const box=el('details',undefined,'management-actions');box.append(el('summary','Изменить или удалить заявку'));
  if(!operator&&c.moderation_status==='pending')box.append(button('Отозвать для изменения',async()=>{await api('/campaigns/'+c.id+'/recall',{method:'POST'});await refresh();notice('Заявка отозвана. Измените её и отправьте на проверку повторно.',true);}));
  if(!operator&&['draft','changes_requested'].includes(c.moderation_status))box.append(button('Изменить тариф и дату',()=>{
   const f=editor(box,'Тариф и желаемая дата',[{name:'start',label:'Желаемое начало',value:local(c.requested_start_at),type:'datetime-local',required:false}],v=>api('/campaigns/'+c.id,{method:'PATCH',body:{...(c.growth_offer_name?{}:{tariff_id:select.value}),requested_start_at:v.start.value?new Date(v.start.value).toISOString():null}}));
   const select=el('select');select.required=true;tariffs.forEach(t=>option(select,t.id,tariffLabel(t)));select.value=c.tariff_id;if(c.growth_offer_name){select.disabled=true;if(!select.value){option(select,c.tariff_id,c.tariff_name_snapshot);select.value=c.tariff_id;}f.append(el('p','Тариф и скидка этой заявки зафиксированы. Можно исправить желаемую дату.','muted'));}label(f,'Новый тариф заявки',select);f.prepend(select.closest('label'));
  }));
  if(operator&&c.placement_status==='scheduled'){
   box.append(button('Изменить слот',()=>{
    const f=editor(box,'Перенести будущий слот',[{name:'start',label:'Новое начало слота',value:local(c.scheduled_start_at),type:'datetime-local'}],v=>api('/operator/campaigns/'+c.id+'/slot',{method:'PATCH',body:{proxy_node_id:select.value,start_at:new Date(v.start.value).toISOString()}}));
    const select=el('select');select.required=true;option(select,'','Выберите узел');const active=nodes.filter(n=>n.status==='active');active.forEach(n=>option(select,n.id,n.name));if(active.length===1)select.value=active[0].id;label(f,'Новый узел слота',select);f.prepend(select.closest('label'));
    f.append(el('p','Часовой пояс: '+calendarZone+'. Перенос доступен до начала слота и до проверки показа.','muted'));
   }));
   box.append(button('Снять будущий слот',async()=>{
    if(!confirm('Снять будущий слот? Оплата останется у заявки, затем можно назначить новое время.'))return;
    await api('/operator/campaigns/'+c.id+'/slot',{method:'DELETE'});await refresh();notice('Слот снят. Оплата сохранена, можно выбрать новое время.',true);
   }));
  }
  archive(box,'/campaigns/'+c.id,'Убрать заявку из списка? Будущий слот отменится. Активный показ и платежи сохранятся у оператора.',operator?'moderation':'campaigns');return box;
 }
 function nodeActions(n){
  const box=el('details',undefined,'management-actions');box.append(el('summary','Изменить или удалить узел'));
  box.append(button('Изменить узел',()=>{
   const f=editor(box,'Запись узла',[
    {name:'name',label:'Название узла',value:n.name},{name:'host',label:'Адрес узла',value:n.host},{name:'port',label:'Порт узла',value:n.port,type:'number',min:1,max:65535}
   ],v=>api('/admin/proxy-nodes/'+n.id,{method:'PATCH',body:{name:v.name.value.trim(),host:v.host.value.trim(),port:Number(v.port.value)}}));
   f.append(el('p','Изменяется запись в кабинете. Настройка работающего прокси на VPS выполняется отдельно.','muted'));
  }));
  archive(box,'/admin/proxy-nodes/'+n.id,'Удалить узел из реестра? Будущие размещения вернутся на подбор. Активные показы сохранятся у оператора; прокси на VPS продолжит работать.','sponsors');return box;
 }
 function local(value){if(!value)return '';const d=new Date(value);return localDay(d)+'T'+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
 async function renderArchive(target,selectedId=''){
  const session=token;target.replaceChildren(el('p','Загрузка архива…'));
  try{const data=await api('/management/archive');if(session!==token||!me||!target.isConnected)return;target.replaceChildren();
   const completed=data.items.filter(x=>x.kind==='campaigns'&&x.placement_status==='completed');
   const deleted=data.items.filter(x=>!completed.includes(x));
   const kinds={channels:'Канал',campaigns:'Заявка',nodes:'Узел',tariffs:'Тариф'};
   for(const [title,items] of [['Завершённые кампании',completed],['Удалённые',deleted]]){
    target.append(el('h2',title));
    if(!items.length)target.append(el('p',title==='Удалённые'?'Удалённых объектов нет.':'Завершённых кампаний пока нет.','empty'));
    for(const item of items){
     const done=completed.includes(item),row=el('article',undefined,'card');row.dataset.archiveKind=item.kind;row.dataset.archiveId=item.id;
     row.append(el('h3',kinds[item.kind]+' · '+item.title),el('p',(done?'В архиве: ':'Удалено: ')+date(item.archived_at)));
     if(item.kind==='campaigns'){
      if(done)row.append(el('p','Завершено · '+(labels[item.payment_status]||item.payment_status)),el('p','Период: '+date(item.scheduled_start_at)+' — '+date(item.scheduled_end_at)));
      row.append(button('Отчёт по кампании',()=>showCampaignReport({id:item.id},row)));
      if(!miniAppMode)row.append(button('Скачать CSV',()=>downloadCampaignCsv({id:item.id})));
     }
     if(!done)row.append(button('Восстановить',async()=>{await api('/management/archive/'+item.kind+'/'+item.id+'/restore',{method:'POST'});await refresh();await renderArchive(target);notice('Объект восстановлен. Проверьте настройки перед использованием.',true);}));
     if(item.kind==='campaigns'&&['admin','operator'].includes(me.role)&&operatorCampaigns.some(c=>c.id===item.id&&c.archived_at))row.append(button('Незавершённые действия',()=>window.cabinet.go('moderation/'+item.id)));
     target.append(row);
     if(item.id===selectedId)void showCampaignReport({id:item.id},row)?.catch(e=>row.append(el('p',e.message)));
    }
   }
   if(selectedId&&!data.items.some(x=>x.id===selectedId)){
    const row=el('article',undefined,'card');target.prepend(row);void showCampaignReport({id:selectedId},row).catch(e=>row.append(el('p',e.message)));
   }
  }catch(e){if(session===token&&target.isConnected)target.replaceChildren(el('p',e.message),button('Повторить загрузку',()=>renderArchive(target,selectedId)));}
 }
 if($('tariff-admin-actions')){
  const removeTariff=button('Удалить',async()=>{const id=$('tariff-admin-select').value;if(!id)throw Error('Выберите тариф.');if(!confirm('Убрать тариф из списка новых заявок? История заказов сохранится.'))return;await api('/admin/tariffs/'+id,{method:'DELETE'});await renderTariffAdmin();await refresh();notice('Тариф перемещён в «Удалённые».',true);});
  removeTariff.id='tariff-admin-delete';removeTariff.hidden=true;
  removeTariff.setAttribute('aria-label','Удалить тариф из списка');
  $('tariff-admin-actions').append(removeTariff);
 }
 return {channelActions,campaignActions,nodeActions,renderArchive};
})();

if(me&&['admin','operator'].includes(me.role)&&nodes.length)renderNodes();
