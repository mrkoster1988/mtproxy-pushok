'use strict';
window.pushokTeam=(()=>{
 const form=$('team-form'),fields=$('team-fields'),status=$('team-status'),list=$('team-list');
 let picker=null;
 const roles={admin:'Администратор',operator:'Оператор',advertiser:'Рекламодатель'};
 Object.assign(errors,{user_blocked:'Доступ к кабинету заблокирован. Обратитесь к администратору.',team_admin_protected:'Оператор не может менять права или блокировать администратора.',team_admin_grant_forbidden:'Назначить администратора может только администратор.',team_cannot_block_self:'Нельзя заблокировать собственный аккаунт.'});
 function roleControl(user){
  const editable=['admin','operator'].includes(me?.role)&&user.id!==me.id&&(me.role==='admin'||user.role!=='admin');
  const label=()=>roles[user.role]||'Пользователь';
  const badge=el(editable?'button':'span',undefined,'person-role'+(editable?' role-button':'')),editor=el('form',undefined,'role-editor');editor.hidden=true;
  function paint(){badge.replaceChildren(document.createTextNode(label()+(user.blocked_at?' · Заблокирован':'')));badge.classList.toggle('is-blocked',Boolean(user.blocked_at));if(editable){badge.append(window.pushokViews.icon('edit'));badge.setAttribute('aria-label','Изменить права: '+label());}}
  paint();if(!editable)return {badge,editor};
  badge.type='button';badge.setAttribute('aria-expanded','false');editor.id='role-'+crypto.randomUUID();badge.setAttribute('aria-controls',editor.id);
  const session=token,viewer=me.id,current=()=>session===token&&viewer===me?.id&&badge.isConnected;
  const fields=el('fieldset'),title=el('legend','Права пользователя'),select=el('select'),selectLabel=el('label','Роль пользователя'),hint=el('p',undefined,'muted'),feedback=el('p',undefined,'muted'),actions=el('div',undefined,'role-actions');
  select.setAttribute('aria-label','Роль пользователя');for(const role of ['advertiser','operator',...(me.role==='admin'?['admin']:[])])option(select,role,roles[role]);select.value=user.role;selectLabel.append(select);
  const descriptions={advertiser:'Собственные каналы, заявки и оплаты.',operator:'Модерация, назначение операторов и блокировка пользователей. Без управления администраторами и чужими балансами.',admin:'Полный доступ к настройкам, ролям и балансам пользователей.'};
  const save=el('button','Сохранить права');save.type='submit';save.className='primary';
  const cancel=button('Отмена',()=>close()),block=button('',async()=>{
   if(!current())return;const blocked=!user.blocked_at;
   if(!confirm((blocked?'Заблокировать':'Разблокировать')+' доступ пользователю '+window.pushokUsers.caption(user)+'?'+(blocked?' Вход в кабинет и действия через бота будут закрыты. Баланс и оплаченные кампании сохранятся.':'')))return;
   fields.disabled=true;
   try{await api('/admin/team/access',{method:'PUT',body:{telegram_user_id:user.telegram_user_id,blocked}});if(!current())return;user.blocked_at=blocked?new Date().toISOString():null;paint();close();notice(blocked?'Пользователь заблокирован.':'Доступ восстановлен. Пользователю нужно войти заново.',true);}
   catch(e){if(current())feedback.textContent=e.message;}finally{fields.disabled=false;}
  });
  block.className='role-block';feedback.setAttribute('role','status');
  function update(){hint.textContent=descriptions[select.value];save.disabled=select.value===user.role;block.textContent=user.blocked_at?'Разблокировать':'Заблокировать';}
  function close(){editor.hidden=true;badge.setAttribute('aria-expanded','false');badge.focus({preventScroll:true});}
  badge.addEventListener('click',()=>{if(!current())return;if(!editor.hidden){close();return;}select.value=user.role;feedback.textContent='';update();editor.hidden=false;badge.setAttribute('aria-expanded','true');select.focus({preventScroll:true});editor.scrollIntoView({block:'nearest'});});
  select.addEventListener('change',update);editor.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();close();}});
  editor.addEventListener('submit',async e=>{
   e.preventDefault();if(fields.disabled||!current()||select.value===user.role)return;const role=select.value;
   if(!confirm('Назначить роль «'+roles[role]+'» пользователю '+window.pushokUsers.caption(user)+'?'))return;
   fields.disabled=true;feedback.textContent='Сохраняем…';
   try{await api('/admin/team',{method:'PUT',body:{telegram_user_id:user.telegram_user_id,role}});if(!current())return;user.role=role;paint();close();notice('Права сохранены. Пользователю нужно войти заново.',true);}
   catch(e){if(current())feedback.textContent=e.message;}finally{fields.disabled=false;}
  });
  actions.append(save,cancel,block);fields.append(title,selectLabel,hint,actions,feedback);editor.append(fields);update();return {badge,editor};
 }
 async function load(){
  if(me?.role!=='admin')return;
  if(!picker||picker.session!==token){$('team-user-picker').replaceChildren();picker=window.pushokUsers.picker($('team-user-picker'),{label:'Пользователь для назначения роли'});picker.session=token;}
  fields.disabled=true;
  try{
   const session=token;const rows=await api('/admin/team');if(session!==token||me?.role!=='admin')return;list.replaceChildren();
   for(const user of rows){
    const row=el('section',undefined,'team-member');
    const access=roleControl(user);row.append(window.pushokProfiles.compact(user,'settings'),access.badge,access.editor);list.append(row);
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
 return {load,roleControl};
})();
