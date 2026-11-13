"use strict";
// Одна форма; ключ отправки переживает потерю ответа и перезагрузку Mini App.
window.pushokOrder=(()=>{
 const form=$('campaign-form'),fields=$('order-fields'),submit=$('order-submit');
 let newChannel=false,initialized=false,busy=false,attempt=null,owner=null;
 const storageKey=()=> 'pushok-order-pending:'+me.id;
 Object.assign(errors,{order_tariff_changed:'Тариф изменился. Проверьте обновлённую стоимость и длительность перед отправкой.',order_request_conflict:'Параметры сохранённой отправки не совпали. Проверьте список ваших заявок.'});
 function mode(value){
  newChannel=value;$('order-new-channel').hidden=!value;$('order-existing').hidden=value;
  $('channel-select').required=!value;$('order-channel-url').required=value;
  $('order-channel-url').disabled=!value;$('order-channel-title').disabled=!value;
  $('order-channel-mode').hidden=!channels.length;
  $('order-channel-mode').textContent=value?'Выбрать из моих каналов':'Добавить другой канал';summary();
 }
 function summary(){
  const tariff=tariffs.find(t=>t.id===$('tariff-select').value);
  const channel=newChannel?($('order-channel-title').value.trim()||$('order-channel-url').value.trim()||'Укажите канал'):channels.find(c=>c.id===$('channel-select').value)?.title||'Выберите канал';
  const start=$('requested-start').value;
  $('order-date').querySelector('summary').textContent='3. Дата · '+(start?date(start):'согласовать с оператором');
  const rows=[['Канал',channel],['Тариф',tariff?tariff.name+' · '+tariff.duration_seconds/3600+' ч':'Не выбран'],['Стоимость',tariff?(tariff.price_kopecks===0?'Бесплатно':money(tariff.price_kopecks)):'—'],['Начало',start?date(start):'По согласованию']];
  $('order-summary').replaceChildren(...rows.flatMap(([key,value])=>[el('dt',key),el('dd',value)]));
 }
 function feedback(text){$('order-feedback').textContent=text;$('order-feedback').hidden=!text;}
 function sync(){
  if(!me)return;
  if(owner!==me.id){owner=me.id;initialized=false;attempt=null;try{attempt=JSON.parse(sessionStorage.getItem(storageKey())||'null');}catch{}}
  if(!initialized){initialized=true;mode(!channels.length);if(channels.length===1)$('channel-select').value=channels[0].id;}
  if(!attempt&&newChannel&&channels.length&&!$('order-channel-url').value.trim())mode(false);
  fields.disabled=Boolean(attempt);submit.textContent=attempt?'Проверить отправку':'Отправить на модерацию';
  submit.disabled=busy||(!attempt&&!tariffs.length);
  if(attempt){mode(Boolean(attempt.channel));$('channel-select').value=attempt.channel_id||'';$('order-channel-url').value=attempt.channel?.telegram_url||'';$('order-channel-title').value=attempt.channel?.title||'';$('tariff-select').value=attempt.tariff_id;$('requested-start').value=attempt.requested_start_at||'';}
  if(attempt)feedback('Ответ на предыдущую отправку ещё не получен. Нажмите «Проверить отправку»: повторная заявка не создастся.');
  summary();
 }
 function payload(){
  const t=tariffs.find(t=>t.id===$('tariff-select').value);
  if(!t)throw Error('Выберите доступный тариф.');
  const data={request_id:crypto.randomUUID(),tariff_id:t.id,expected_price_kopecks:t.price_kopecks,expected_duration_seconds:t.duration_seconds,requested_start_at:$('requested-start').value||null};
  if(newChannel){
   const value=$('order-channel-url').value.trim();
   const match=value.match(/^(?:@|https:\/\/(?:t\.me|telegram\.me)\/)([a-z][a-z0-9_]{3,31})\/?$/i);
   if(!match)throw Error('Укажите публичный @username или ссылку https://t.me/channel.');
   data.channel={telegram_url:'https://t.me/'+match[1].toLowerCase(),title:$('order-channel-title').value.trim()||'@'+match[1]};
  }else data.channel_id=$('channel-select').value;
  return data;
 }
 form.addEventListener('input',summary);form.addEventListener('change',summary);
 $('order-channel-mode').addEventListener('click',()=>mode(!newChannel));
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;
  const session=token,user=me?.id;if(!user)return;
  busy=true;submit.disabled=true;
  try{
   if(!attempt){attempt=payload();sessionStorage.setItem(storageKey(),JSON.stringify(attempt));}
   fields.disabled=true;feedback('Отправляем заявку…');
   const campaign=await api('/orders',{method:'POST',body:attempt});
   if(token!==session||me?.id!==user)return;
   sessionStorage.removeItem(storageKey());attempt=null;feedback('');
   // Результат отображаем сразу, даже если последующее обновление списка прервётся.
   ownCampaigns=[campaign,...ownCampaigns.filter(c=>c.id!==campaign.id)];
   window.cabinet.sync(ownCampaigns);window.cabinet.go('create/'+campaign.id);
   const calendarFrom=$('calendar-from').value,calendarTime=$('calendar-time').value;form.reset();$('calendar-from').value=calendarFrom;$('calendar-time').value=calendarTime;clearRequestedDate();initialized=false;
   try{await refresh();}catch{notice('Заявка отправлена. Не удалось обновить список; нажмите «Обновить».');}
  }catch(error){
   if(token!==session||me?.id!==user)return;
   // Ошибка проверки означает откат транзакции. При потере связи сохраняем исходный ключ.
   if([400,403,404,409,422].includes(error.status)){attempt=null;sessionStorage.removeItem(storageKey());try{await refresh();}catch{}}
   feedback(error.message+(attempt?' Повторная отправка с тем же ключом безопасна.':''));
  }finally{busy=false;if(me?.id===user){sync();if(!$('order-feedback').hidden)$('order-feedback').scrollIntoView({block:'nearest'});}}
 });
 return {sync,summary,selectChannel(){mode(false);summary();},reset(){owner=null;initialized=false;attempt=null;busy=false;fields.disabled=false;feedback('');}};
})();
