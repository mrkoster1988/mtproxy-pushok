"use strict";
// Одна форма; ключ отправки переживает потерю ответа и перезагрузку Mini App.
window.pushokOrder=(()=>{
 const form=$('campaign-form'),fields=$('order-fields'),submit=$('order-submit');
 let priceQuote=null,quoteKey=null,quoteEpoch=0;
 let newChannel=false,initialized=false,busy=false,attempt=null,owner=null;
 let selectedChecker=null,selectedValue='';
 const topic=window.pushokTopics.create({locked:()=>busy||Boolean(attempt),onChange:()=>summary()});
 form.addEventListener('pushok:channel-verified',event=>topic.verified(event.detail.telegram_url));
 const storageKey=()=> 'pushok-order-pending:'+me.id;
 Object.assign(errors,{order_tariff_changed:'Тариф изменился. Проверьте обновлённую стоимость и длительность перед отправкой.',order_request_conflict:'Параметры сохранённой отправки не совпали. Проверьте список ваших заявок.'});
 function currentKey(){const t=tariffs.find(t=>t.id===$('tariff-select').value);return [me?.id,t?.id,t?.price_kopecks,t?.stars_price_xtr,t?.duration_seconds,$('order-promo').value.trim().toUpperCase()].join(':');}
 async function calculate(){
  const tid=$('tariff-select').value;if(!tid||!me)return null;
  const key=currentKey(),version=++quoteEpoch,session=token;
  $('order-quote-status').textContent='Рассчитываем стоимость…';
  try{const data=await api('/growth/quote',{method:'POST',body:{tariff_id:tid,code:$('order-promo').value.trim()||null}});
   if(version!==quoteEpoch||key!==currentKey()||session!==token)return null;
   priceQuote=data;quoteKey=key;$('order-quote-status').textContent=data.offer_name?data.offer_name+' · '+data.discount_percent+'%. Скидки не суммируются.':'';summary();return data;
  }catch(e){if(version===quoteEpoch&&session===token){priceQuote=null;quoteKey=null;$('order-quote-status').textContent=e.message;summary();}throw e;}
 }
 function invalidate(){priceQuote=null;quoteKey=null;quoteEpoch++;$('order-quote-status').textContent='Нажмите «Рассчитать скидку», чтобы увидеть итог.';summary();}
 $('order-quote').addEventListener('click',()=>calculate().catch(()=>{}));
 $('order-promo').addEventListener('input',invalidate);
 $('tariff-select').addEventListener('change',()=>{invalidate();void calculate().catch(()=>{});});
 function mode(value){
  if(value!==newChannel){$('order-channel-url').pushokChannelCheck?.reset();selectedChecker?.reset();selectedValue='';}
  newChannel=value;$('order-new-channel').hidden=!value;$('order-existing').hidden=value;
  $('channel-select').required=!value;$('order-channel-url').required=value;
  $('order-channel-url').disabled=!value;$('order-channel-title').disabled=!value;
  $('order-channel-mode').hidden=!channels.length;
  $('order-channel-mode').textContent=value?'Выбрать из моих каналов':'Добавить другой канал';summary();
 }
 function summary(){
  const tariff=tariffs.find(t=>t.id===$('tariff-select').value);
  const channel=newChannel?($('order-channel-title').value.trim()||$('order-channel-url').value.trim()||'Укажите канал'):channels.find(c=>c.id===$('channel-select').value)?.title||'Выберите канал';
  topic.context(newChannel?$('order-channel-url').value:channels.find(c=>c.id===$('channel-select').value)?.telegram_url||'');
  if(attempt)topic.restore(attempt.topic);
  topic.resume();
  const preview=$('order-selected-preview');if(preview&&window.pushokViews){
   selectedChecker??=window.pushokViews.channelVerification(preview,()=>newChannel?'':channels.find(c=>c.id===$('channel-select').value)?.telegram_url||'',data=>{
    const selected=channels.find(c=>c.id===$('channel-select').value),heading=preview.querySelector('strong');if(selected&&heading)heading.textContent=selected.title;topic.verified(data.telegram_url);
   });
   const value=newChannel?'':channels.find(c=>c.id===$('channel-select').value)?.telegram_url||'';
   if(value!==selectedValue){selectedValue=value;if(value)selectedChecker.schedule();else selectedChecker.reset();}
  }
  const start=$('requested-start').value;
  const q=quoteKey===currentKey()?priceQuote:null;
  const total=attempt?attempt.expected_price_kopecks:q?q.price_kopecks:tariff?.price_kopecks;
  $('order-date').querySelector('summary').textContent='Дата · '+(start?date(start):'согласовать с оператором');
  const stars=q?q.stars_price_xtr:tariff?.stars_price_xtr;
  const cost=total===0?'Бесплатно':miniAppMode?(attempt?'Уточняется после отправки':stars>0?stars+' XTR':'Цена Stars не задана'):money(total);
  const rows=[['Канал',channel],['Тариф',tariff?tariff.name+' · '+tariff.duration_seconds/3600+' ч':'Не выбран'],['Стоимость',tariff?cost:'—'],['Начало',start?date(start):'После оплаты, ближайшее свободное время']];
  if(topic.value())rows.splice(1,0,['Тематика',topic.label()]);
  if(q?.offer_name)rows.push(['Скидка',q.offer_name+' · '+q.discount_percent+'%']);
  if(!miniAppMode&&q?.stars_price_xtr>0)rows.push(['В Telegram Stars',q.stars_price_xtr+' XTR']);
  $('order-summary').replaceChildren(...rows.flatMap(([key,value])=>[el('dt',key),el('dd',value)]));
 }
 function feedback(text){$('order-feedback').textContent=text;$('order-feedback').hidden=!text;}
 function sync(){
  if(!me)return;
  $('order-promo-section').hidden=false;
  if(owner!==me.id){topic.reset();owner=me.id;initialized=false;attempt=null;priceQuote=null;quoteKey=null;quoteEpoch++;try{attempt=JSON.parse(sessionStorage.getItem(storageKey())||'null');}catch{}}
  if(!initialized){initialized=true;mode(!channels.length);if(channels.length===1)$('channel-select').value=channels[0].id;}
  if(!attempt&&newChannel&&channels.length&&!$('order-channel-url').value.trim())mode(false);
  mode(newChannel);
  fields.disabled=Boolean(attempt);submit.textContent=attempt?'Проверить отправку':'Отправить на модерацию';
  submit.disabled=busy||(!attempt&&!tariffs.length);
  if(attempt){$('order-promo').value=attempt.promo_code||'';mode(Boolean(attempt.channel));$('channel-select').value=attempt.channel_id||'';$('order-channel-url').value=attempt.channel?.telegram_url||'';$('order-channel-title').value=attempt.channel?.title||'';$('tariff-select').value=attempt.tariff_id;$('requested-start').value=attempt.requested_start_at||'';}
  if(attempt)feedback('Ответ на предыдущую отправку ещё не получен. Нажмите «Проверить отправку»: повторная заявка не создастся.');
  summary();
  if(!attempt&&!$('order-promo').value.trim()&&quoteKey!==currentKey())void calculate().catch(()=>{});
 }
 function payload(){
  const t=tariffs.find(t=>t.id===$('tariff-select').value);
  if(!t)throw Error('Выберите доступный тариф.');
  const data={request_id:crypto.randomUUID(),tariff_id:t.id,expected_price_kopecks:priceQuote.price_kopecks,promo_code:$('order-promo').value.trim()||null,quote_revision:priceQuote.revision,expected_duration_seconds:t.duration_seconds,requested_start_at:$('requested-start').value||null};
  data.topic=topic.value();
  if(newChannel){
   const value=$('order-channel-url').value.trim();
   const match=value.match(/^(?:@|https:\/\/(?:t\.me|telegram\.me)\/)?([a-z][a-z0-9_]{3,31})\/?$/i);
   if(!match)throw Error('Укажите публичный @username или ссылку https://t.me/channel.');
   data.channel={telegram_url:'https://t.me/'+match[1].toLowerCase(),title:$('order-channel-title').value.trim()||'@'+match[1]};
  }else data.channel_id=$('channel-select').value;
  return data;
 }
 function edited(){if(!busy&&!attempt)feedback('');summary();}
 form.addEventListener('input',edited);form.addEventListener('change',edited);
 $('order-channel-mode').addEventListener('click',()=>mode(!newChannel));
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;
  const session=token,user=me?.id;if(!user)return;
  busy=true;submit.disabled=true;
  try{
   if(!attempt){
    feedback('Проверяем канал…');
    const selectedAtStart=$('channel-select').value,modeAtStart=newChannel,urlAtStart=$('order-channel-url').value;
    if(newChannel)await $('order-channel-url').pushokChannelCheck?.check();else await selectedChecker?.check();
    if(token!==session||me?.id!==user)return;
    if(modeAtStart!==newChannel||selectedAtStart!==$('channel-select').value||urlAtStart!==$('order-channel-url').value)throw Error('Канал изменился. Проверьте новую ссылку.');
    if(!priceQuote||quoteKey!==currentKey()){await calculate();if(!priceQuote||quoteKey!==currentKey())return;feedback('Стоимость рассчитана. Проверьте итог и нажмите «Отправить на модерацию».');return;}
    attempt=payload();sessionStorage.setItem(storageKey(),JSON.stringify(attempt));
   }
   fields.disabled=true;feedback('Отправляем заявку…');
   const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),12000);
   let campaign;try{campaign=await api('/orders',{method:'POST',body:attempt,signal:controller.signal});}finally{clearTimeout(timeout);}
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
   if([400,403,404,409,422].includes(error.status)||['channel_lookup_unavailable','channel_lookup_rate_limit'].includes(error.code)){attempt=null;priceQuote=null;quoteKey=null;sessionStorage.removeItem(storageKey());try{await refresh();}catch{}}
   feedback(error.message+(attempt?' Повторная отправка с тем же ключом безопасна.':''));
  }finally{busy=false;if(me?.id===user){sync();if(!$('order-feedback').hidden)$('order-feedback').scrollIntoView({block:'nearest'});}}
 });
 // API мог завершить загрузку раньше этого отложенного скрипта.
 if(me)sync();
 async function applyOffer(offer){
  sync();if(attempt)throw Error('Сначала проверьте предыдущую отправку заявки. Затем откройте ссылку на бонус ещё раз.');
  if(!tariffs.some(t=>t.id===offer.tariff_id))throw Error('Тариф предложения больше недоступен.');
  $('tariff-select').value=offer.tariff_id;$('order-promo').value=offer.code||'';$('order-promo-section').open=true;
  invalidate();await calculate();
 }
 return {sync,summary,applyOffer,selectChannel(){mode(false);summary();},reset(){topic.reset();selectedChecker?.reset();selectedValue='';$('order-channel-url').pushokChannelCheck?.reset();priceQuote=null;quoteKey=null;quoteEpoch++;owner=null;initialized=false;attempt=null;busy=false;fields.disabled=false;feedback('');}};
})();
