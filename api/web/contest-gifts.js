'use strict';
window.pushokContests=(()=>{
 const root='/contests';
 const manager=user=>user?.role==='admin'||hasDeveloperAccess(user)||Boolean(user?.is_tamada);
 let renderRevision=0;
 const term=days=>days===90?'3 месяца':'1 месяц';
 const link=id=>'https://t.me/mtproxy_pushok_bot?startapp=prize_'+id.replaceAll('-','');
 const end=stamp=>new Date(stamp*1000).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'});
 function field(form,title,type,value=''){
  const label=el('label',title),input=el('input');input.type=type;input.value=value??'';
  input.required=true;if(type==='number'){input.step='1';input.min='0';}
  label.append(input);form.append(label);return input;
 }
 function action(host,title,fn){
  const status=el('small'),btn=button(title,async()=>{
   btn.disabled=true;status.textContent='';
   try{await fn();}catch(error){status.textContent=/^[А-ЯЁ]/.test(error.code||'')?error.code:error.message;}
   finally{btn.disabled=false;}
  });
  btn.type='button';status.setAttribute('role','status');host.append(btn,status);return btn;
 }
 function textForm(host,event,allocate,done,current){
  const form=el('form'),title=field(form,'Название мероприятия','text',event.title),sender=field(form,'От кого подарок','text',event.sender);
  title.maxLength=90;sender.maxLength=64;
  const greeting=field(form,'Поздравление','text',event.greeting);greeting.required=false;greeting.maxLength=400;
  let owner,month,quarter;
  const requests=new Map();
  if(allocate){
   owner=field(form,'Telegram ID организатора','number',me.telegram_user_id||'');owner.min='1';owner.max=String(2**52-1);
   month=field(form,'Количество призов на месяц','number',0);quarter=field(form,'Количество призов на три месяца','number',0);
   month.max=quarter.max='10000';
  }
  const submit=async()=>{
   if(!current()||!form.reportValidity())return;
   const body={title:title.value,sender:sender.value,greeting:greeting.value};
   if(allocate){
    Object.assign(body,{organizer:Number(owner.value),month_limit:Number(month.value),quarter_limit:Number(quarter.value)});
    const key=JSON.stringify(body);
    if(!requests.has(key))requests.set(key,crypto.randomUUID());
    body.request_id=requests.get(key);
   }else body.expected_revision=event.revision;
   const result=await api(allocate?root:root+'/events/'+event.id,{method:allocate?'POST':'PUT',body});
   if(current())done(result);
  };
  form.onsubmit=e=>{e.preventDefault();form.querySelector('button').click();};
  action(form,allocate?'Создать мероприятие':'Сохранить',submit);host.append(form);
 }
 async function render(host,id){
  const session=token,route=location.hash,revision=++renderRevision;
  const current=()=>session===token&&location.hash===route&&revision===renderRevision&&host.isConnected;
  host.replaceChildren(el('p','Загружаем…'));
  try{
   if(!id){
    const [events,profile]=await Promise.all([api(root),api('/me')]);if(!current())return;host.replaceChildren();
    if(manager(profile)){
     const create=el('details',undefined,'panel');create.append(el('summary','Создать мероприятие и выделить призы'));
     textForm(create,{},true,r=>window.cabinet.go('events/'+r.id),current);host.append(create);
    }
    for(const [archived,title] of [[false,'Активные мероприятия'],[true,'Архив']]){const rows=events.filter(event=>Boolean(event.archived_at)===archived);if(rows.length)host.append(el('h3',title));for(const event of rows){const a=el('a',event.title,'panel');a.href='#/events/'+event.id;host.append(a);}}
    if(!events.length)host.append(el('p','Здесь появятся мероприятия, для которых вам выделили призы.'));
    return;
   }
   if(route.startsWith('#/prize/')){
    const prize=await api(root+'/prizes/'+id);if(!current())return;
    const card=el('article',undefined,'panel'),art=el('img');art.src='/assets/flow-gift-'+prize.days+'.jpg';art.alt='';art.style.cssText='width:min(100%,280px);border-radius:16px';
    card.append(art,el('h2',term(prize.days)+' Pushok Flow'),el('strong',prize.title),el('p',prize.greeting),el('small','От '+prize.sender));
    if(prize.expires)card.append(el('p','Подарок получен · доступ до '+end(prize.expires)));
    else action(card,'Забрать подарок',async()=>{
     if(!current())return;
     const result=await api(root+'/prizes/'+id+'/claim',{method:'POST'});
     if(!current())return;
     if(result.expires)return render(host,id);
     throw Error('Готовим подключение. Проверьте подарок ещё раз через минуту.');
    });
    host.replaceChildren(card);return;
   }
   const path=root+'/events/'+id,[event,profile]=await Promise.all([api(path),api('/me')]);if(!current())return;
   host.replaceChildren(el('h2',event.title));
   if(event.archived_at)host.append(el('p','В архиве. Создание новых подарков приостановлено. Ранее выданные ссылки действуют, пока не отозваны.','muted'));
   if(manager(profile)){
    const section=el('details',undefined,'panel');section.append(el('summary','Управление мероприятием'));
    const controls=el('form'),owner=field(controls,'Telegram ID организатора','number',event.organizer),month=field(controls,'Количество призов на месяц','number',event.month_limit),quarter=field(controls,'Количество призов на три месяца','number',event.quarter_limit);
    owner.min='1';owner.max=String(2**52-1);month.max=quarter.max='10000';
    const archive=el('input');archive.type='checkbox';archive.checked=Boolean(event.archived_at);const archiveLabel=el('label','В архиве');archiveLabel.prepend(archive);controls.append(archiveLabel);
    action(controls,'Сохранить управление',async()=>{
     if(!current()||!controls.reportValidity())return;
     if(archive.checked!==Boolean(event.archived_at)&&!confirm(archive.checked?'Перенести мероприятие в архив? Выданные ссылки сохранятся.':'Восстановить мероприятие?'))return;
     await api(path+'/management',{method:'PUT',body:{organizer:Number(owner.value),month_limit:Number(month.value),quarter_limit:Number(quarter.value),archived:archive.checked,expected_revision:event.revision}});if(current())await render(host,id);
    });controls.onsubmit=e=>{e.preventDefault();controls.querySelector('button').click();};section.append(controls);host.append(section);
   }
   const used=event.prizes.filter(p=>!p.revoked),claimed=used.filter(p=>p.expires).length;
   host.append(el('p','Выдано: '+used.length+' · Получено: '+claimed+' · Осталось: '+(event.month_limit+event.quarter_limit-used.length),'muted'));
   if(!event.archived_at){
   const settings=el('details');settings.append(el('summary','Оформление карточки'));textForm(settings,event,false,()=>render(host,id),current);host.append(settings);
   const form=el('form',undefined,'panel'),duration=el('select'),label=el('label','Срок подписки');
   for(const days of [30,90]){
    const limit=days===30?event.month_limit:event.quarter_limit;
    const left=limit-event.prizes.filter(p=>p.days===days&&!p.revoked).length;
    const o=el('option',term(days)+' · осталось '+left);o.value=days;o.disabled=left<=0;duration.append(o);
   }
   label.append(duration);form.append(label);
   const recipient=field(form,'Telegram ID победителя (для именного подарка)','number');recipient.required=false;recipient.min='1';recipient.max=String(2**52-1);
   form.append(el('small','Если оставить поле пустым, подарок сможет забрать первый получатель ссылки.'));
   let request='',previous='';
   const createPrize=action(form,'Создать подарок',async()=>{
    if(!current()||!form.reportValidity()||!duration.value||duration.selectedOptions[0]?.disabled)return;
    const body={days:Number(duration.value),recipient:recipient.value?Number(recipient.value):null};
    if(JSON.stringify(body)!==previous){request=crypto.randomUUID();previous=JSON.stringify(body);}
    await api(path+'/prizes',{method:'POST',body:{...body,request_id:request}});if(current())await render(host,id);
   });createPrize.disabled=[...duration.options].every(o=>o.disabled);
   form.onsubmit=e=>{e.preventDefault();if(!createPrize.disabled)createPrize.click();};host.append(form);
   }
   for(const p of event.prizes){
    const card=el('article',undefined,'panel');
    card.append(el('strong',term(p.days)+' Pushok Flow'),el('p',p.recipient?'Для Telegram ID '+p.recipient:'Одноразовая ссылка'),
     el('small',p.revoked?'Отозван':p.expires?'Получен · доступ до '+end(p.expires):p.winner?'Оформляется':'Готов к отправке'));
    if(p.winner_telegram_id)card.append(el('small','Победитель: Telegram ID '+p.winner_telegram_id));
    if(!p.revoked&&!p.winner){
     if(!event.archived_at)action(card,'Отправить карточку',async()=>{
      if(!current())return;
      const app=window.Telegram?.WebApp;
      if(!app?.shareMessage||!app.isVersionAtLeast('8.0'))throw Error('Скопируйте ссылку или откройте этот экран в обновлённом Telegram.');
      const prepared=await api(path+'/prizes/'+p.id+'/share',{method:'POST'});if(current())app.shareMessage(prepared.id);
     });
     if(!event.archived_at){
      const label=el('label','Ссылка на подарок'),url=el('input');url.type='url';url.readOnly=true;url.value=link(p.id);label.append(url);card.append(label);
      action(card,'Скопировать ссылку',async()=>{
       if(!current())return;
       try{await navigator.clipboard.writeText(url.value);if(current())notice('Ссылка скопирована');}
       catch{if(current()){url.focus();url.select();throw Error('Выделили ссылку. Скопируйте её вручную.');}}
      });
     }
     action(card,'Отозвать',async()=>{if(current()&&confirm('Отозвать этот подарок и вернуть его в лимит?')){await api(path+'/prizes/'+p.id+'/revoke',{method:'POST'});if(current())await render(host,id);}});
    }
    host.append(card);
   }
  }catch(error){if(current())host.replaceChildren(el('p',/^[А-ЯЁ]/.test(error.code||'')?error.code:error.message),button('Повторить',()=>render(host,id)));}
 }
 return {render,term};
})();
