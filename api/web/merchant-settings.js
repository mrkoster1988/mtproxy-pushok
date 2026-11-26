'use strict';
window.pushokSBP=(()=>{
 let generation=0,formSession=null;
 async function load(force=false){
  const host=$('sbp-connection'),session=token,uid=me?.id;
  if(!host)return;
  host.hidden=!hasDeveloperAccess(me);
  if(host.hidden){reset();return;}
  if(!force&&formSession===session&&host.querySelector('details[open]'))return;
  const version=++generation,wasOpen=Boolean(host.querySelector('details')?.open);
  const valid=()=>version===generation&&session===token&&uid===me?.id;
  if(formSession!==session)host.replaceChildren(el('p','Загружаем подключение СБП…'));
  try{
   const saved=await api('/admin/sbp/connection');if(!valid()||(!force&&formSession===session&&host.querySelector('details[open]')))return;
   const box=el('details'),summary=el('summary','Подключение СБП · ЮKassa'),form=el('form');
   box.className='payment-fold merchant-fold';box.open=wasOpen;box.append(summary);form.className='merchant-fields';
   function field(label,type,value=''){const row=el('label',label),input=el('input');input.type=type;input.value=value;row.append(input);form.append(row);return input;}
   const shop=field('Shop ID магазина','text',saved.shop);shop.required=true;shop.inputMode='numeric';shop.pattern='[0-9]{1,32}';
   const key=field('Секретный ключ ЮKassa','password');key.autocomplete='new-password';key.required=!saved.secret_saved;key.maxLength=256;key.placeholder=saved.secret_saved?'Оставьте пустым, чтобы сохранить ключ':'Введите ключ магазина';
   const domain=field('Домен возврата после оплаты','text',saved.host);domain.required=true;domain.placeholder=location.hostname;
   const test=field('Тестовый магазин','checkbox');test.checked=saved.test_mode;
   shop.placeholder='ID из кабинета ЮKassa';
   key.parentElement.className='merchant-secret';
   domain.title='Домен без https:// и пути';
   test.parentElement.className='merchant-test';
   const hint=el('p','Ключ находится в ЮKassa → Интеграция → Ключи API.','merchant-hint');
   const result=el('p',saved.secret_saved?'Реквизиты магазина сохранены.':'Магазин ещё не подключён.','muted');result.setAttribute('role','status');
   const save=el('button','Проверить и сохранить');save.type='submit';save.className='payment-save secondary-button';form.append(hint,save);
   form.addEventListener('submit',async event=>{
    event.preventDefault();save.disabled=true;result.textContent='Проверяем магазин…';
    try{const body={shop:shop.value.trim(),host:domain.value.trim(),test_mode:test.checked};if(key.value)body.secret=key.value;
     await api('/admin/sbp/connection',{method:'PUT',body});key.value='';if(!valid())return;
     notice('Магазин СБП проверен и сохранён.',true);
     await load(true);if(session===token)await Promise.all([loadControl({payments:true}),renderPaymentSettings()]);
    }catch(error){if(valid())result.textContent=({
     sbp_connection_invalid:'Проверьте Shop ID, домен и ключ.',
     sbp_connection_has_payments:'По этому магазину уже есть счета. Смена магазина, домена или режима требует переноса и сверки платежей.',
     sbp_connection_check_failed:'ЮKassa не подтвердила подключение. Проверьте ключ и повторите.',
     sbp_connection_shop_not_ready:'Проверьте режим магазина, доступность СБП и статус подключения в ЮKassa.'
    })[error.message]||error.message||'Не удалось сохранить подключение. Проверьте поля и повторите.';}
    finally{save.disabled=false;}
   });
   box.append(result,form);host.replaceChildren(box);formSession=session;
  }catch(error){if(valid())host.replaceChildren(el('p','Не удалось загрузить подключение СБП.'),button('Повторить',load));}
 }
 function reset(){generation++;formSession=null;const host=$('sbp-connection');if(host){host.replaceChildren();host.hidden=true;}}
 return {load,reset};
})();

window.pushokWalletConnections=(()=>{
 let generation=0,loadedSession='';
 const path='/admin/wallet/connections',names={platega:'Platega',antilopay:'СБП через Antilopay'};
 function reset(){
  generation++;loadedSession='';
  const host=$('wallet-connections');if(host){host.hidden=true;host.replaceChildren();}
 }
 function render(saved,valid){
  const isPlatega=saved.id==='platega',box=el('details',undefined,'payment-fold merchant-fold');
  const summary=el('summary'),state=el('small'),form=el('form',undefined,'merchant-fields');
  function paint(current){
   state.textContent=current.enabled?(current.test_mode?'Тестовый режим':'Включён'):current.configured?'Выключен':'Нужны реквизиты';
  }
  paint(saved);summary.append(el('strong',names[saved.id]),state);box.append(summary,form);
  function field(label,type,value){
   const row=el('label',label),input=el('input');input.type=type;
   if(type==='checkbox'){input.checked=value;row.className='merchant-test';}else input.value=value||'';
   row.append(input);form.append(row);return input;
  }
  const account=field(isPlatega?'Merchant ID':'Secret ID','text',saved.account);
  account.required=true;account.maxLength=160;
  const project=isPlatega?null:field('Project ID','text',saved.project);
  if(project){project.required=true;project.maxLength=160;}
  const secret=field(isPlatega?'Секретный ключ':'Закрытый ключ в Base64','password');
  secret.autocomplete='new-password';secret.maxLength=16384;
  function stored(exists){secret.required=!exists;secret.placeholder=exists?'Ключ сохранён. Введите новый для замены.':'';}
  stored(saved.secret_saved);
  let vat;
  if(!isPlatega){
   const row=el('label','НДС для чека');vat=el('select');
   for(const [v,label] of [['','Не передавать ставку'],['10','10 %'],['22','22 %']])vat.append(new Option(label,v));
   vat.value=saved.vat;row.append(vat);form.append(row);
  }
  const test=field('Тестовый режим','checkbox',saved.test_mode);
  const enabled=field('Принимать пополнения в веб-кабинете','checkbox',saved.enabled);
  const feedback=el('p',undefined,'muted');feedback.setAttribute('role','status');
  const save=el('button','Сохранить','payment-save secondary-button');save.type='submit';form.append(save,feedback);
  form.addEventListener('submit',async event=>{
   event.preventDefault();if(save.disabled)return;
   const body={account:account.value.trim(),project:project?.value.trim()||'',vat:vat?.value||'',test_mode:test.checked,enabled:enabled.checked};
   if(secret.value.trim())body.secret=secret.value.trim();
   const inputs=[...form.elements];inputs.forEach(i=>i.disabled=true);feedback.textContent='Сохраняем…';
   try{
    const result=await api(path+'/'+saved.id,{method:'PUT',body});
    if(!valid()||!box.isConnected)return;
    secret.value='';stored(result.secret_saved);paint(result);feedback.textContent='Настройки сохранены.';
   }catch(e){
    if(valid()&&box.isConnected)feedback.textContent=({
     wallet_connection_fields:'Проверьте идентификаторы магазина.',
     wallet_connection_secret:'Введите действующий ключ для указанного магазина.',
     wallet_connection_has_payments:'Смена магазина или режима затронет счета на сверке или возврате. Можно выключить приём новых платежей.'
    })[e.code||e.message]||'Не удалось сохранить настройки. Попробуйте ещё раз.';
   }finally{inputs.forEach(i=>i.disabled=false);}
  });
  return box;
 }
 async function load(){
  const host=$('wallet-connections'),session=token;
  if(!host||!hasDeveloperAccess(me)){reset();return;}
  host.hidden=false;
  if(loadedSession===session&&host.querySelector('details[open]'))return;
  const seq=++generation,valid=()=>seq===generation&&session===token&&hasDeveloperAccess(me);
  try{
   const rows=await api(path);
   if(!valid()||(loadedSession===session&&host.querySelector('details[open]')))return;
   // A later background fetch must not invalidate the save callback of an open form.
   host.replaceChildren(...rows.map(row=>render(row,()=>session===token&&hasDeveloperAccess(me))));
   loadedSession=session;
  }catch{if(valid()&&!host.querySelector('details'))host.replaceChildren(button('Загрузить настройки Platega и Antilopay',load));}
 }
 return {load,reset};
})();
