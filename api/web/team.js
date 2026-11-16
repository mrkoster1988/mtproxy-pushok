'use strict';
window.pushokTeam=(()=>{
 const form=$('team-form'),fields=$('team-fields'),status=$('team-status'),list=$('team-list');
 let picker=null;
 const roles={admin:'Администратор',operator:'Оператор',advertiser:'Рекламодатель'};
 async function load(){
  if(me?.role!=='admin')return;
  if(!picker||picker.session!==token){$('team-user-picker').replaceChildren();picker=window.pushokUsers.picker($('team-user-picker'),{label:'Пользователь для назначения роли'});picker.session=token;}
  fields.disabled=true;
  try{
   const session=token;const rows=await api('/admin/team');if(session!==token||me?.role!=='admin')return;list.replaceChildren();
   for(const user of rows){
    const row=el('section',undefined,'team-member');
    row.append(window.pushokProfiles.compact(user,'settings'),el('p',roles[user.role]));
    const edit=button('Изменить роль',()=>{picker.set(user);$('team-role').value=user.role;form.scrollIntoView({block:'center'});});row.append(edit);list.append(row);
   }
   fields.disabled=false;
  }catch(e){status.textContent='Не удалось загрузить администраторов. Откройте настройки повторно.';throw e;}
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(fields.disabled||!form.reportValidity())return;
  const role=$('team-role').value;let user;try{user=await picker.resolve();}catch(e){status.textContent=e.message;return;}if(!user)return;const id=user.telegram_user_id;
  if(!confirm('Назначить роль «'+roles[role]+'» пользователю '+window.pushokUsers.caption(user)+'?'))return;
  fields.disabled=true;status.textContent='Сохраняем…';
  try{
   await api('/admin/team',{method:'PUT',body:{telegram_user_id:id,role}});
   status.textContent='Роль сохранена. Пользователю нужно заново открыть кабинет. Для уведомлений он должен запустить бота.';
   await load();
  }catch(e){status.textContent=e.message||'Не удалось изменить роль. Проверьте ID и свои права.';fields.disabled=false;}
 });
 return {load};
})();
