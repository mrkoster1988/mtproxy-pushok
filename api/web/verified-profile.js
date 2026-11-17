'use strict';
window.pushokVerification=(()=>{
 let profileNode=null,profileUser=null,pollEpoch=0;
 const title='Номер подтверждён в puShok';
 Object.assign(errors,{verification_request_expired:'Запрос истёк. Нажмите «Подтвердить номер» ещё раз.',own_telegram_contact_required:'Отправьте свой контакт кнопкой Telegram.',phone_invalid:'Telegram передал некорректный номер. Повторите подтверждение.'});
 function badge(){
  const wrap=el('span',undefined,'phone-verified-badge');wrap.title=title;wrap.setAttribute('role','img');wrap.setAttribute('aria-label',title);
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const circle=document.createElementNS(svg.namespaceURI,'circle');circle.setAttribute('cx','12');circle.setAttribute('cy','12');circle.setAttribute('r','11');circle.setAttribute('fill','#258bfa');
  const check=document.createElementNS(svg.namespaceURI,'path');check.setAttribute('d','m7 12 3 3 7-7');check.setAttribute('fill','none');check.setAttribute('stroke','white');check.setAttribute('stroke-width','2.4');check.setAttribute('stroke-linecap','round');check.setAttribute('stroke-linejoin','round');svg.append(circle,check);wrap.append(svg);return wrap;
 }
 function label(text,input){const node=el('label',text);input.setAttribute('aria-label',text);node.append(input);return node;}
 function optIn(checked){const input=el('input');input.type='checkbox';input.checked=checked;const node=label('Поздравлять с днём рождения и начислять доступный бонус',input);node.className='verification-opt-in';return {input,node};}
 function botLink(){
  const username=window.cabinetBotConfig?.telegram_bot_username;
  if(!/^[A-Za-z0-9_]{5,32}$/.test(username||''))return null;
  const a=el('a','Подтвердить через бота','verification-bot-link');a.href='https://t.me/'+username+'?start=verify';a.target='_blank';a.rel='noopener noreferrer';return a;
 }
 async function refreshStatus(session=token){
  const data=await api('/me/verification');if(session!==token||!me)return false;
  if(data.phone_verified!==Boolean(me.phone_verified)){me.phone_verified=data.phone_verified;profileNode=null;window.cabinet.sync(ownCampaigns);}
  return data.phone_verified;
 }
 function home(target){
  if(!me){target.replaceChildren();delete target.dataset.state;return;}
  const key=me.id+':'+Boolean(me.phone_verified);if(target.dataset.state===key)return;target.dataset.state=key;
  target.replaceChildren();const card=el('section',undefined,'verification-card');target.append(card);
  const heading=el('div',undefined,'verification-heading'),copy=el('div');
  heading.append(me.phone_verified?badge():window.pushokViews.icon('user'),copy);card.append(heading);
  if(me.phone_verified){
   card.classList.add('verification-complete');copy.append(el('strong',title));const link=el('a','Мой профиль');link.href='#/profile';card.append(link);return;
  }
  copy.append(el('h2','Подтвердите свой номер'),el('p','Номер будет виден вам и администратору.'));
  const help=el('details',undefined,'verification-consent');help.append(el('summary','Какие сведения сохранятся'),el('p','Ваш номер будет виден вам и администратору. Доступную дату рождения из Telegram используем для поздравлений и ежегодного бонуса по условиям puShok. Год рождения необязателен. Это подтверждение номера внутри puShok.'));card.append(help);
  const consent=optIn(true),status=el('p',undefined,'verification-status');status.setAttribute('role','status');card.append(consent.node);
  const actions=el('div',undefined,'verification-actions');
  const confirm=button('Подтвердить номер',async()=>{
   const session=token;await api('/me/verification/request',{method:'POST',body:{birthday_opt_in:consent.input.checked}});if(session!==token)return;
   const app=window.Telegram?.WebApp;
   if(!miniAppMode||!app?.requestContact||!app.isVersionAtLeast?.('6.9')){
    status.textContent='Откройте бота и передайте свой контакт кнопкой Telegram.';fallback.hidden=false;return;
   }
   status.textContent='Разрешите Telegram передать ваш номер.';
   const sent=await new Promise(resolve=>{let done=false;const finish=value=>{if(done)return;done=true;clearTimeout(timeout);resolve(value);};const timeout=setTimeout(()=>finish(false),60000);try{app.requestContact(finish);}catch{finish(false);}});
   if(session!==token)return;
   if(!sent){status.textContent='Номер не передан. Можно повторить подтверждение.';return;}
   status.textContent='Telegram передал контакт. Ждём подтверждения бота…';
   const epoch=++pollEpoch;
   for(let attempt=0;attempt<30&&epoch===pollEpoch&&session===token;attempt++){
    try{if(await refreshStatus(session)){notice('Номер подтверждён. Данные сохранены в профиле.',true);return;}}catch{status.textContent='Проверяем подтверждение. Если бот недоступен, повторите проверку позже.';}
    await new Promise(resolve=>setTimeout(resolve,2000));
   }
   if(session===token&&epoch===pollEpoch)status.textContent='Бот ещё не подтвердил контакт. Нажмите «Проверить статус» позже; галочка появится после проверки.';
  });confirm.classList.add('primary');actions.append(confirm);
  actions.append(button('Проверить статус',async()=>{status.textContent=await refreshStatus()?'Номер подтверждён.':'Подтверждение ещё не получено. Передайте свой номер кнопкой Telegram.';}));
  const fallback=el('div');fallback.hidden=Boolean(miniAppMode&&window.Telegram?.WebApp?.requestContact);const link=botLink();fallback.append(link||el('p','Откройте бота puShok и отправьте /verify.'));
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
  if(profileNode&&profileUser===me.id){target.append(profileNode);return;}
  const uid=me.id,session=token;profileUser=uid;const box=profileNode=el('section',undefined,'verification-card profile-personal');target.append(box);
  box.append(el('h2','Личные сведения'),el('p','Загружаем сведения…','muted'));
  const valid=()=>session===token&&me?.id===uid&&profileNode===box;
  async function load(){
   try{const data=await api('/me/verification');if(!valid())return;paint(data);if(Boolean(me.phone_verified)!==data.phone_verified){me.phone_verified=data.phone_verified;window.cabinet.sync(ownCampaigns);}}catch(e){if(valid())box.replaceChildren(el('h2','Личные сведения'),el('p',e.message,'muted'),button('Повторить',load));}
  }
  function paint(data){
   box.replaceChildren(el('h2','Личные сведения'));
   const phone=el('div',undefined,'profile-phone');phone.append(el('span','Номер телефона','muted'),el('strong',data.phone_number||'Не подтверждён'));if(data.phone_verified)phone.append(badge());box.append(phone);
   if(!data.phone_verified)box.append(button('Подтвердить номер',()=>window.cabinet.go('dashboard')));
   box.append(el('p','Номер и дата рождения видны только вам и администратору.','muted'));
   const form=el('form'),fields=el('div',undefined,'birthday-fields'),day=el('input'),month=el('select'),year=el('input');
   day.type='number';day.min='1';day.max='31';day.inputMode='numeric';day.placeholder='День';day.value=data.birthday?.day||'';
   [['','Месяц'],...['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'].map((m,i)=>[String(i+1),m])].forEach(([value,text])=>{const o=el('option',text);o.value=value;month.append(o);});month.value=data.birthday?.month||'';
   year.type='number';year.min=String(new Date().getFullYear()-120);year.max=String(new Date().getFullYear());year.inputMode='numeric';year.placeholder='—';year.title='Год указывать необязательно';year.value=data.birthday?.year||'';
   fields.append(label('День',day),label('Месяц',month),label('Год',year));form.append(el('h3','День рождения'),fields);
   const source=el('p',data.birthday_source==='telegram'?'Добавлено из профиля Telegram. Можно исправить.':'Если Telegram не передал дату, укажите день и месяц здесь.','muted');form.append(source);
   const consent=optIn(data.birthday_opt_in),status=el('p',undefined,'verification-status');status.setAttribute('role','status');form.append(consent.node);
   const save=el('button','Сохранить');save.type='submit';form.append(save,status);box.append(form);
   form.addEventListener('submit',async event=>{
    event.preventDefault();if(save.disabled)return;
    if((day.value||month.value||year.value)&&(!day.value||!month.value)){status.textContent='Укажите день и месяц. Год можно не заполнять.';return;}
    const birthday=day.value?{day:Number(day.value),month:Number(month.value),year:year.value?Number(year.value):null}:null;
    if(birthday){const check=new Date(Date.UTC(birthday.year||2000,birthday.month-1,birthday.day));if(check.getUTCMonth()!==birthday.month-1||check.getUTCDate()!==birthday.day){status.textContent='Проверьте дату рождения.';return;}}
    save.disabled=true;status.textContent='Сохраняем…';
    try{await api('/me/birthday',{method:'PUT',body:{birthday,birthday_opt_in:consent.input.checked}});if(valid()){status.textContent='Сведения сохранены.';source.textContent=birthday?'Дата указана вами.':'Дата рождения удалена.';}}
    catch(e){if(valid())status.textContent=e.code==='request_validation_failed'||e.status===422?'Проверьте дату рождения.':e.message;}
    finally{save.disabled=false;}
   });
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
 function reset(){pollEpoch++;profileNode=null;profileUser=null;document.querySelectorAll('#birthday-settings,#dashboard-verification').forEach(n=>{n.replaceChildren();delete n.dataset.state;});}
 return {badge,home,profile,readOnly,settings,reset};
})();
