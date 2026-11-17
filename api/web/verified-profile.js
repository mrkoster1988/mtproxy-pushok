'use strict';
window.pushokVerification=(()=>{
 let profileNode=null,profileUser=null,profileSync=null,pollEpoch=0,activeAttempt=null;
 const title='Номер подтверждён в puShok';
 Object.assign(errors,{verification_request_expired:'Запрос истёк. Нажмите «Подтвердить номер» ещё раз.',own_telegram_contact_required:'Отправьте свой контакт кнопкой Telegram.',phone_invalid:'Telegram передал некорректный номер. Повторите подтверждение.'});
 function badge(){
  const wrap=el('span',undefined,'phone-verified-badge');wrap.title=title;wrap.setAttribute('role','img');wrap.setAttribute('aria-label',title);
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const circle=document.createElementNS(svg.namespaceURI,'path');circle.setAttribute('d','M10.24 2.25Q12.00 1.00 13.76 2.25Q15.52 3.50 17.65 3.86Q19.78 4.22 20.14 6.35Q20.50 8.48 21.75 10.24Q23.00 12.00 21.75 13.76Q20.50 15.52 20.14 17.65Q19.78 19.78 17.65 20.14Q15.52 20.50 13.76 21.75Q12.00 23.00 10.24 21.75Q8.48 20.50 6.35 20.14Q4.22 19.78 3.86 17.65Q3.50 15.52 2.25 13.76Q1.00 12.00 2.25 10.24Q3.50 8.48 3.86 6.35Q4.22 4.22 6.35 3.86Q8.48 3.50 10.24 2.25Z');circle.setAttribute('fill','#32a9f4');
  const check=document.createElementNS(svg.namespaceURI,'path');check.setAttribute('d','m7 12 3 3 7-7');check.setAttribute('fill','none');check.setAttribute('stroke','white');check.setAttribute('stroke-width','2.4');check.setAttribute('stroke-linecap','round');check.setAttribute('stroke-linejoin','round');svg.append(circle,check);wrap.append(svg);return wrap;
 }
 function label(text,input){const node=el('label',text);input.setAttribute('aria-label',text);node.append(input);return node;}
 function optIn(checked){const input=el('input');input.type='checkbox';input.checked=checked;const node=label('Поздравлять с днём рождения и начислять доступный бонус',input);node.className='verification-opt-in';return {input,node};}
 function botLink(){
  const username=window.cabinetBotConfig?.telegram_bot_username;
  if(!/^[A-Za-z0-9_]{5,32}$/.test(username||''))return null;
  const a=el('a','Подтвердить через бота','verification-bot-link');a.href='https://t.me/'+username+'?start=verify';a.target='_blank';a.rel='noopener noreferrer';return a;
 }
 // Ограничиваем сетевое ожидание; закрытие/отмена не оставляют запросы висеть.
 async function request(path,options={},signal,timeoutMs=8000){
  const controller=new AbortController(),abort=()=>controller.abort();
  if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(abort,timeoutMs);
  try{return await api(path,{...options,signal:controller.signal});}
  finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
 }
 function pause(ms,signal){return new Promise(resolve=>{
  const finish=()=>{clearTimeout(timer);signal.removeEventListener('abort',finish);resolve();};
  const timer=setTimeout(finish,ms);if(signal.aborted)finish();else signal.addEventListener('abort',finish,{once:true});
 });}
 function askContact(app,signal){return new Promise(resolve=>{
  let done=false;const finish=value=>{if(done)return;done=true;clearTimeout(timer);signal.removeEventListener('abort',abort);resolve(value);};
  const abort=()=>finish(false),timer=setTimeout(abort,30000);
  if(signal.aborted)return abort();signal.addEventListener('abort',abort,{once:true});
  try{app.requestContact(finish);}catch{finish(false);}
 });}
 async function refreshStatus(session=token,signal,timeoutMs=8000){
  const data=await request('/me/verification',{},signal,timeoutMs);if(session!==token||!me)return false;
  if(data.phone_verified!==Boolean(me.phone_verified)){me.phone_verified=data.phone_verified;profileNode=null;window.cabinet.sync(ownCampaigns);}
  return data.phone_verified;
 }
 function home(target){
  if(!me||me.phone_verified){target.replaceChildren();target.hidden=true;delete target.dataset.state;return;}
  target.hidden=false;
  const key=me.id+':'+Boolean(me.phone_verified);if(target.dataset.state===key)return;target.dataset.state=key;
  target.replaceChildren();const card=el('section',undefined,'verification-card');target.append(card);
  const heading=el('div',undefined,'verification-heading'),copy=el('div');
  heading.append(window.pushokViews.icon('user'),copy);card.append(heading);
  copy.append(el('h2','Подтвердите свой номер'),el('p','Номер будет виден вам и администратору.'));
  const help=el('details',undefined,'verification-consent');help.append(el('summary','Какие сведения сохранятся'),el('p','Ваш номер будет виден вам и администратору. Доступную дату рождения из Telegram используем для поздравлений и ежегодного бонуса по условиям puShok. Год рождения необязателен. Это подтверждение номера внутри puShok.'));card.append(help);
  const consent=optIn(true),status=el('p',undefined,'verification-status');status.setAttribute('role','status');card.append(consent.node);
  const actions=el('div',undefined,'verification-actions');
  const fallback=el('div');fallback.hidden=Boolean(miniAppMode&&window.Telegram?.WebApp?.requestContact);const link=botLink();fallback.append(link||el('p','Откройте бота puShok и отправьте /verify.'));
  const cancel=button('Отменить ожидание',()=>{activeAttempt?.abort();pollEpoch++;cancel.hidden=true;status.textContent='Ожидание остановлено. Можно проверить статус или подтвердить номер через бота.';fallback.hidden=false;});cancel.hidden=true;
  const confirm=button('Подтвердить номер',async()=>{
   activeAttempt?.abort();const controller=new AbortController();activeAttempt=controller;
   const signal=controller.signal,session=token,epoch=++pollEpoch;
   const valid=()=>session===token&&epoch===pollEpoch&&!signal.aborted;
   cancel.hidden=false;status.textContent='Подготавливаем запрос…';
   try{
    await request('/me/verification/request',{method:'POST',body:{birthday_opt_in:consent.input.checked}},signal);if(!valid())return;
    const app=window.Telegram?.WebApp;
    if(!miniAppMode||!app?.requestContact||!app.isVersionAtLeast?.('6.9')){
     status.textContent='Откройте бота и передайте свой контакт кнопкой Telegram.';fallback.hidden=false;return;
    }
    status.textContent='Разрешите Telegram передать ваш номер.';
    const sent=await askContact(app,signal);if(!valid())return;
    if(!sent){status.textContent='Номер не передан. Можно повторить подтверждение.';fallback.hidden=false;return;}
    status.textContent='Telegram передал контакт. Ждём подтверждения бота…';
    const deadline=Date.now()+12000;
    while(valid()&&Date.now()<deadline){
     if(await refreshStatus(session,signal,Math.min(8000,deadline-Date.now())))return;
     await pause(Math.min(2000,Math.max(0,deadline-Date.now())),signal);
    }
    if(valid()){status.textContent='Бот пока не подтвердил контакт. Проверьте статус позже или подтвердите номер через бота.';fallback.hidden=false;}
   }catch(e){if(valid()){status.textContent=e.message+' Проверьте статус или подтвердите номер через бота.';fallback.hidden=false;}}
   finally{if(activeAttempt===controller){activeAttempt=null;cancel.hidden=true;}}
  });confirm.classList.add('primary');actions.append(confirm);
  actions.append(button('Проверить статус',async()=>{
   const session=token;try{const verified=await refreshStatus(session);if(session===token){status.textContent=verified?'Номер подтверждён.':'Подтверждение ещё не получено. Проверьте ответ бота.';fallback.hidden=verified;}}
   catch(e){if(session===token){status.textContent=e.message;fallback.hidden=false;}}
  }),cancel);
  card.append(actions,fallback,status);
 }
 function readOnly(data){
  const box=el('section',undefined,'verification-card verification-private');box.append(el('h2','Личные сведения'));
  if(data.phone_number)box.append(el('p',data.phone_number),el('small','Номер подтверждён '+new Date(data.phone_verified_at).toLocaleDateString('ru-RU'),'muted'));
  if(data.birthday){const b=data.birthday;box.append(el('p','Дата рождения: '+[String(b.day).padStart(2,'0'),String(b.month).padStart(2,'0'),b.year].filter(Boolean).join('.')));}
  if(!data.phone_number&&!data.birthday)box.append(el('p','Сведения ещё не добавлены.','muted'));
  return box;
 }
 function profile(target){
  if(profileNode&&profileUser===me.id){target.append(profileNode);profileSync?.();return;}
  profileSync=null;
  const uid=me.id,session=token;profileUser=uid;const box=profileNode=el('details',undefined,'verification-card profile-personal');target.append(box);
  box.append(el('summary','Личные сведения'),el('p','Загружаем сведения…','muted'));
  const valid=()=>session===token&&me?.id===uid&&profileNode===box;
  async function load(){
   try{const data=await request('/me/verification');if(!valid())return;paint(data);if(Boolean(me.phone_verified)!==data.phone_verified){me.phone_verified=data.phone_verified;window.cabinet.sync(ownCampaigns);}}catch(e){if(valid())box.replaceChildren(el('summary','Личные сведения'),el('p',e.message,'muted'),button('Повторить',load));}
  }
  function paint(data){
   const summary=el('summary'),heading=el('span','Личные сведения');summary.append(heading);if(data.phone_verified){const state=el('span',undefined,'profile-verified-state');state.append(badge(),el('span','Верифицирован'));state.title=title;summary.append(state);}box.replaceChildren(summary);
   const phone=el('div',undefined,'profile-phone');phone.append(el('span','Номер телефона','muted'),el('strong',data.phone_number||'Не подтверждён'));box.append(phone);
   if(!data.phone_verified)box.append(button('Подтвердить номер',()=>window.cabinet.go('dashboard')));
   const privacy=el('small','Номер и дата рождения видны только вам и администратору.','profile-privacy muted');box.append(privacy);
   const form=el('form'),fields=el('div',undefined,'birthday-fields'),day=el('input'),month=el('select'),year=el('input');
   day.type='number';day.min='1';day.max='31';day.inputMode='numeric';day.placeholder='День';day.value=data.birthday?.day||'';
   [['','Месяц'],...['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'].map((m,i)=>[String(i+1),m])].forEach(([value,text])=>{const o=el('option',text);o.value=value;month.append(o);});month.value=data.birthday?.month||'';
   year.type='number';year.min=String(new Date().getFullYear()-120);year.max=String(new Date().getFullYear());year.inputMode='numeric';year.placeholder='—';year.title='Год указывать необязательно';year.value=data.birthday?.year||'';
   fields.append(label('День',day),label('Месяц',month),label('Год',year));form.append(el('h3','День рождения'),fields);
   const explanation=value=>value.birthday_source==='telegram'?'Добавлено из профиля Telegram. Можно исправить.':
    value.birthday_source==='manual'?'Дата указана вами.':value.birthday_sync_state==='manual'?'Дата рождения удалена вами.':
    value.birthday_sync_state==='unavailable'?'Telegram не передал дату рождения. Проверьте её доступность в Telegram или укажите день и месяц здесь.':
    value.birthday_sync_state==='failed'?'Не удалось получить дату из Telegram. Повторите проверку через минуту или укажите её здесь.':
    value.birthday_sync_state==='pending'?'Проверяем дату в Telegram. Повторная проверка доступна через минуту.':
    'Дату рождения можно взять из Telegram или указать здесь. Год необязателен.';
   const source=el('p',explanation(data),'muted');form.append(source);
   const consent=optIn(data.birthday_opt_in),status=el('p',undefined,'verification-status');status.setAttribute('role','status');form.append(consent.node);
   let edited=false,syncing=false,lastSyncAt=0;form.addEventListener('input',()=>{edited=true;});form.addEventListener('change',()=>{edited=true;});
   const importButton=button('Взять из Telegram',syncBirthday);importButton.classList.add('birthday-import');
   importButton.hidden=!data.phone_verified||!data.birthday_opt_in||Boolean(data.birthday)||data.birthday_sync_state==='manual';
   const save=el('button','Сохранить');save.type='submit';const actions=el('div',undefined,'verification-actions');actions.append(save,importButton);form.append(actions,status);box.append(form);
   async function syncBirthday(){
    if(importButton.hidden||syncing)return;syncing=true;lastSyncAt=Date.now();importButton.disabled=true;source.textContent='Проверяем дату рождения в Telegram…';
    try{
     const result=await request('/me/birthday/sync',{method:'POST'});if(!valid()||!box.contains(form))return;
     if(edited){source.textContent='Проверка завершена. Ваши изменения сохраните кнопкой «Сохранить».';return;}
     data=result;source.textContent=explanation(result);
     if(result.birthday){day.value=result.birthday.day;month.value=result.birthday.month;year.value=result.birthday.year||'';importButton.hidden=true;}
    }catch{if(valid()&&box.contains(form)&&!edited)source.textContent='Не удалось получить дату из Telegram. Можно повторить проверку или заполнить её вручную.';}
    finally{syncing=false;importButton.disabled=false;}
   }
   form.addEventListener('submit',async event=>{
    event.preventDefault();if(save.disabled)return;
    if((day.value||month.value||year.value)&&(!day.value||!month.value)){status.textContent='Укажите день и месяц. Год можно не заполнять.';return;}
    const birthday=day.value?{day:Number(day.value),month:Number(month.value),year:year.value?Number(year.value):null}:null;
    if(birthday){const check=new Date(Date.UTC(birthday.year||2000,birthday.month-1,birthday.day));if(check.getUTCMonth()!==birthday.month-1||check.getUTCDate()!==birthday.day){status.textContent='Проверьте дату рождения.';return;}}
    edited=true;save.disabled=true;status.textContent='Сохраняем…';
    try{await api('/me/birthday',{method:'PUT',body:{birthday,birthday_opt_in:consent.input.checked}});if(valid()){status.textContent='Сведения сохранены.';source.textContent=birthday?'Дата указана вами.':'Дата рождения удалена.';importButton.hidden=true;}}
    catch(e){if(valid())status.textContent=e.code==='request_validation_failed'||e.status===422?'Проверьте дату рождения.':e.message;}
    finally{save.disabled=false;}
   });
   profileSync=()=>{if(location.hash.startsWith('#/profile')&&!edited&&Date.now()-lastSyncAt>=60000)void syncBirthday();};
   profileSync();
  }
  void load();
 }
 async function settings(){
  if(me?.role!=='admin')return;const session=token;let box=$('birthday-settings');if(!box){box=el('section',undefined,'panel birthday-settings');box.id='birthday-settings';$('settings-system').append(box);}
  box.replaceChildren(el('h3','Поздравления и подарок ко дню рождения'),el('p','Загружаем настройки…','muted'));
  const data=await api('/admin/birthday-settings');if(session!==token||me?.role!=='admin')return;
  const form=el('form'),enabled=el('input'),amount=el('input');enabled.type='checkbox';enabled.checked=data.congratulations_enabled;
  amount.type='number';amount.min='0';amount.max='10000';amount.step='1';amount.required=true;amount.value=String(data.bonus_amount/100);
  box.replaceChildren(el('h3','Поздравления и подарок ко дню рождения'),form);
  form.append(label('Отправлять поздравление в Telegram',enabled),label('Ежегодный подарок, бонусных ₽',amount),el('p','0 — без начисления. Подарок получают пользователи с подтверждённым номером и включёнными поздравлениями. Один раз в год, по московскому времени. Для 29 февраля — 28 февраля в невисокосный год.','muted'));
  const save=el('button','Сохранить'),status=el('p',undefined,'verification-status');save.type='submit';status.setAttribute('role','status');form.append(save,status);
  form.addEventListener('submit',async event=>{event.preventDefault();if(save.disabled)return;save.disabled=true;try{await api('/admin/birthday-settings',{method:'PUT',body:{congratulations_enabled:enabled.checked,bonus_amount:Math.round(Number(amount.value)*100)}});if(session===token)status.textContent='Настройки сохранены.';}catch(e){if(session===token)status.textContent=e.message;}finally{save.disabled=false;}});
 }
 function reset(){activeAttempt?.abort();activeAttempt=null;pollEpoch++;profileNode=null;profileUser=null;profileSync=null;document.querySelectorAll('#birthday-settings,#dashboard-verification').forEach(n=>{n.replaceChildren();delete n.dataset.state;});}
 return {badge,home,profile,readOnly,settings,reset};
})();
