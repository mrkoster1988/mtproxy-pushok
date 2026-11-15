'use strict';
window.pushokTeam=(()=>{
 const form=$('team-form'),fields=$('team-fields'),status=$('team-status'),list=$('team-list');
 const roles={admin:'Администратор',operator:'Оператор',advertiser:'Рекламодатель'};
 async function load(){
  if(me?.role!=='admin')return;
  fields.disabled=true;
  try{
   const session=token;const rows=await api('/admin/team');if(session!==token||me?.role!=='admin')return;list.replaceChildren();
   for(const user of rows){
    const row=el('section',undefined,'team-member');
    row.append(el('strong',[user.first_name,user.last_name].filter(Boolean).join(' ')||'Пользователь'),el('p',(user.username?'@'+user.username+' · ':'')+'Telegram ID: '+user.telegram_user_id+' · '+roles[user.role]));
    const edit=button('Изменить роль',()=>{$('team-id').value=user.telegram_user_id;$('team-role').value=user.role;form.scrollIntoView({block:'center'});});row.append(edit);list.append(row);
   }
   fields.disabled=false;
  }catch(e){status.textContent='Не удалось загрузить администраторов. Откройте настройки повторно.';throw e;}
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(fields.disabled||!form.reportValidity())return;
  const id=Number($('team-id').value),role=$('team-role').value;
  if(!Number.isSafeInteger(id)||id<=0){status.textContent='Укажите числовой Telegram ID.';return;}
  if(!confirm('Назначить роль «'+roles[role]+'» пользователю Telegram ID '+id+'?'))return;
  fields.disabled=true;status.textContent='Сохраняем…';
  try{
   await api('/admin/team',{method:'PUT',body:{telegram_user_id:id,role}});
   status.textContent='Роль сохранена. Пользователю нужно заново открыть кабинет. Для уведомлений он должен запустить бота.';
   await load();
  }catch(e){status.textContent=e.message||'Не удалось изменить роль. Проверьте ID и свои права.';fields.disabled=false;}
 });
 return {load};
})();
