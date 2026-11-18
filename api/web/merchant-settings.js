'use strict';
window.pushokSBP=(()=>{
 let generation=0,formSession=null;
 async function load(force=false){
  const host=$('sbp-connection'),session=token,uid=me?.id;
  if(!host)return;
  host.hidden=!me?.is_owner;
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
   const domain=field('Домен возврата после оплаты','text',saved.host);domain.required=true;domain.placeholder='ads.pushok.shop';
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
