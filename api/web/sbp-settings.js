'use strict';
window.pushokSBP=(()=>{
 let generation=0;
 async function load(){
  const host=$('sbp-connection'),session=token,uid=me?.id,version=++generation;
  if(!host||me?.role!=='admin')return;
  const valid=()=>version===generation&&session===token&&uid===me?.id;
  host.replaceChildren(el('p','Загружаем подключение СБП…'));
  try{
   const saved=await api('/admin/sbp/connection');if(!valid())return;
   const box=el('details'),summary=el('summary','Подключение СБП · ЮKassa'),form=el('form');
   box.open=!saved.secret_saved;box.append(summary);
   function field(label,type,value=''){const row=el('label',label),input=el('input');input.type=type;input.value=value;row.append(input);form.append(row);return input;}
   const shop=field('Shop ID магазина','text',saved.shop);shop.required=true;shop.inputMode='numeric';shop.pattern='[0-9]{1,32}';
   const key=field('Секретный ключ ЮKassa','password');key.autocomplete='new-password';key.required=!saved.secret_saved;key.maxLength=256;key.placeholder=saved.secret_saved?'Ключ сохранён. Оставьте пустым, чтобы сохранить его.':'Введите ключ магазина';
   const domain=field('Домен возврата после оплаты','text',saved.host);domain.required=true;domain.placeholder='ads.pushok.shop';
   const test=field('Тестовый магазин','checkbox');test.checked=saved.test_mode;
   const hint=el('p','Shop ID: ЮKassa → Настройки → Магазин. Ключ: Интеграция → Ключи API. Домен указывается без https:// и пути. Проверка не создаёт платёж.','muted');
   const result=el('p',saved.secret_saved?'Данные магазина сохранены. Приём СБП включается отдельным переключателем.':'Магазин ещё не подключён.','muted');result.setAttribute('role','status');
   const save=el('button','Проверить и сохранить');save.type='submit';save.className='primary';form.append(save);
   form.addEventListener('submit',async event=>{
    event.preventDefault();save.disabled=true;result.textContent='Проверяем магазин…';
    try{const body={shop:shop.value.trim(),host:domain.value.trim(),test_mode:test.checked};if(key.value)body.secret=key.value;
     await api('/admin/sbp/connection',{method:'PUT',body});key.value='';if(!valid())return;
     notice('Подключение СБП проверено и сохранено. Включите приём СБП переключателем.',true);await loadControl({payments:true});
    }catch(error){if(valid())result.textContent=({
     sbp_connection_invalid:'Проверьте Shop ID, домен и ключ.',
     sbp_connection_has_payments:'По этому магазину уже есть счета. Смена магазина, домена или режима требует переноса и сверки платежей.',
     sbp_connection_check_failed:'ЮKassa не подтвердила подключение. Проверьте ключ и повторите.',
     sbp_connection_shop_not_ready:'Проверьте режим магазина, доступность СБП и статус подключения в ЮKassa.'
    })[error.message]||'Не удалось сохранить подключение. Проверьте поля и повторите.';}
    finally{save.disabled=false;}
   });
   box.append(hint,result,form);host.replaceChildren(box);
  }catch(error){if(valid())host.replaceChildren(el('p','Не удалось загрузить подключение СБП.'),button('Повторить',load));}
 }
 return {load,reset(){generation++;$('sbp-connection')?.replaceChildren();}};
})();
