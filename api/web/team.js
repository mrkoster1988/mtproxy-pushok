'use strict';
window.pushokTeam=(()=>{
 const form=$('team-form'),fields=$('team-fields'),status=$('team-status'),list=$('team-list');
 let picker=null;
 const roles={developer:'Разработчик',admin:'Администратор',operator:'Оператор',moderator:'Модератор',friends:'Друзья',tamada:'Тамада',advertiser:'Рекламодатель',user:'Пользователь'};
 function category(user){
  if(hasDeveloperAccess(user))return 'developer';
  if(user.role==='advertiser'&&!user.has_launched_ads)return 'user';
  return user.role;
 }
 function displayRole(user){return (roles[category(user)]||roles.user)+(user.is_tamada?' · 🎩 Тамада':'')+(user.is_friend?' · Друзья':'');}
 Object.assign(errors,{team_developer_protected:'Управлять другим разработчиком может только разработчик.',team_developer_grant_forbidden:'Назначить разработчика может только разработчик.',user_blocked:'Доступ к кабинету заблокирован. Обратитесь к администратору.',team_admin_protected:'Оператор не может менять права или блокировать администратора.',team_admin_grant_forbidden:'Назначить администратора может только администратор.',team_moderator_protected:'Управлять доступом модератора может администратор.',team_moderator_grant_forbidden:'Назначить модератора может администратор.',team_cannot_block_self:'Нельзя заблокировать собственный аккаунт.'});
 function roleControl(user){
  const editable=!user.is_owner&&(!hasDeveloperAccess(user)||hasDeveloperAccess(me))&&['admin','operator'].includes(me?.role)&&user.id!==me.id&&(me.role==='admin'||!['admin','moderator'].includes(user.role));
  const label=()=>displayRole(user);
  const assignedRole=()=>hasDeveloperAccess(user)?'developer':user.role;
  const badge=el(editable?'button':'span',undefined,'person-role'+(editable?' role-button':'')),editor=el('form',undefined,'role-editor');editor.hidden=true;
  function paint(){badge.dataset.role=hasDeveloperAccess(user)?'owner':category(user);badge.replaceChildren(document.createTextNode(roles[category(user)]||roles.user));if(user.is_tamada)badge.append(document.createTextNode(' · '),el('span','🎩 Тамада','tamada-status'));if(user.is_friend)badge.append(document.createTextNode(' · Друзья'));if(user.blocked_at)badge.append(document.createTextNode(' · Заблокирован'));badge.classList.toggle('is-blocked',Boolean(user.blocked_at));if(editable){badge.setAttribute('aria-label','Изменить права: '+label());}}
  paint();if(!editable)return {badge,editor};
  badge.type='button';badge.setAttribute('aria-expanded','false');editor.id='role-'+crypto.randomUUID();badge.setAttribute('aria-controls',editor.id);
  const session=token,viewer=me.id,current=()=>session===token&&viewer===me?.id&&badge.isConnected;
  const fields=el('fieldset'),title=el('legend','Права пользователя'),select=el('select'),selectLabel=el('label','Роль пользователя'),hint=el('p',undefined,'muted'),feedback=el('p',undefined,'muted'),actions=el('div',undefined,'role-actions');
  select.setAttribute('aria-label','Роль пользователя');for(const role of ['advertiser','operator',...(me.role==='admin'?['moderator','admin']:[]),...(hasDeveloperAccess(me)?['developer']:[])])option(select,role,role==='advertiser'?'Базовый доступ':roles[role]);select.value=assignedRole();selectLabel.append(select);
  const descriptions={developer:'Управляет настройками системы и может назначать других разработчиков.',moderator:'Разбирает спорные заявки и пересматривает решения операторов до оплаты и планирования.',advertiser:'Собственные каналы, заявки и оплаты.',operator:'Модерация, назначение операторов и блокировка пользователей. Снимает каналы с чёрного списка.',admin:'Полный доступ к настройкам, ролям и балансам пользователей.'};
  const save=window.pushokViews.iconAction(el('button'),'check','Сохранить права');save.type='submit';save.classList.add('primary');
  const cancel=button('Отмена',()=>close()),block=button('',async()=>{
   if(!current())return;const blocked=!user.blocked_at;
   if(!confirm((blocked?'Заблокировать':'Разблокировать')+' доступ пользователю '+window.pushokUsers.caption(user)+'?'+(blocked?' Вход в кабинет и действия через бота будут закрыты. Баланс и оплаченные кампании сохранятся.':'')))return;
   fields.disabled=true;
   try{await api('/admin/team/access',{method:'PUT',body:{telegram_user_id:user.telegram_user_id,blocked}});if(!current())return;user.blocked_at=blocked?new Date().toISOString():null;paint();close();notice(blocked?'Пользователь заблокирован.':'Доступ восстановлен. Пользователю нужно войти заново.',true);}
   catch(e){if(current())feedback.textContent=e.message;}finally{fields.disabled=false;}
  });
  const friend=button('',async()=>{
   if(!current())return;
   fields.disabled=true;
   try{
    const result=await api('/admin/team/friend',{method:'PUT',body:{telegram_user_id:user.telegram_user_id,is_friend:!user.is_friend}});
    if(!current())return;
    user.is_friend=result.is_friend;paint();update();close();
    notice(result.is_friend?'Отметка «Друзья» добавлена.':'Отметка «Друзья» снята.',true);
    editor.dispatchEvent(new CustomEvent('friend-mark-changed',{bubbles:true}));
   }catch(error){if(current())feedback.textContent=error.message;}finally{fields.disabled=false;}
  });
  const tamada=button('',async()=>{
   if(!current()||me.role!=='admin')return;
   const grant=!user.is_tamada;
   if(!confirm(grant?'Назначить статус «Тамада»? Пользователь получит полный доступ к мероприятиям и выделению призов.':'Снять статус «Тамада»? Доступ к чужим мероприятиям будет отозван.'))return;
   fields.disabled=true;
   try{
    const result=await api('/admin/team/tamada',{method:'PUT',body:{telegram_user_id:user.telegram_user_id,is_tamada:grant}});
    if(!current())return;
    user.is_tamada=result.is_tamada;paint();update();close();
    notice(result.is_tamada?'Статус «Тамада» назначен.':'Статус «Тамада» снят.',true);
    editor.dispatchEvent(new CustomEvent('tamada-status-changed',{bubbles:true}));
   }catch(error){if(current())feedback.textContent=error.message;}finally{fields.disabled=false;}
  });
  tamada.classList.add('tamada-status');
  window.pushokViews.iconAction(cancel,'back','Отмена');
  block.className='role-block';feedback.setAttribute('role','status');
  function update(){tamada.textContent=user.is_tamada?'Снять статус «Тамада»':'Назначить статус «Тамада»';friend.textContent=user.is_friend?'Снять отметку «Друзья»':'Добавить в «Друзья»';hint.textContent=descriptions[select.value];save.disabled=select.value===assignedRole();window.pushokViews.iconAction(block,'power',user.blocked_at?'Разблокировать':'Заблокировать');}
  function close(){editor.hidden=true;badge.setAttribute('aria-expanded','false');badge.focus({preventScroll:true});}
  badge.addEventListener('click',()=>{if(!current())return;if(!editor.hidden){close();return;}select.value=assignedRole();feedback.textContent='';update();editor.hidden=false;badge.setAttribute('aria-expanded','true');select.focus({preventScroll:true});editor.scrollIntoView({block:'nearest'});});
  select.addEventListener('change',update);editor.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();close();}});
  editor.addEventListener('submit',async e=>{
   e.preventDefault();if(fields.disabled||!current()||select.value===assignedRole())return;const role=select.value;
   if(!confirm('Назначить роль «'+(role==='advertiser'?'Базовый доступ':roles[role])+'» пользователю '+window.pushokUsers.caption(user)+'?'))return;
   fields.disabled=true;feedback.textContent='Сохраняем…';
   try{await api('/admin/team',{method:'PUT',body:{telegram_user_id:user.telegram_user_id,role}});if(!current())return;user.is_developer=role==='developer';user.role=user.is_developer?'admin':role;document.querySelectorAll('[data-person-id="'+user.id+'"]').forEach(n=>n.dataset.userRole=user.is_developer?'owner':role);paint();close();notice('Права сохранены. Пользователю нужно войти заново.',true);}
   catch(e){if(current())feedback.textContent=e.message;}finally{fields.disabled=false;}
  });
  actions.append(save,cancel,friend,...(me.role==='admin'?[tamada]:[]),block);fields.append(title,selectLabel,hint,actions,feedback);editor.append(fields);update();return {badge,editor};
 }
 async function load(){
  if(me?.role!=='admin')return;
  const levels=$('team-role');
  levels.querySelector('[value="developer"]')?.remove();
  if(hasDeveloperAccess(me))option(levels,'developer',roles.developer);
  if(!picker||picker.session!==token){$('team-user-picker').replaceChildren();picker=window.pushokUsers.picker($('team-user-picker'),{label:'Пользователь для назначения роли'});picker.session=token;}
  fields.disabled=true;
  try{
   const session=token;const rows=await api('/admin/team');if(session!==token||me?.role!=='admin')return;list.replaceChildren();
   for(const user of rows){
    const row=el('section',undefined,'team-member');
    const access=roleControl(user);row.append(window.pushokProfiles.compact(user,'management/people'),access.badge,access.editor);list.append(row);
   }
   fields.disabled=false;
  }catch(e){status.textContent='Не удалось загрузить администраторов. Откройте настройки повторно.';throw e;}
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(fields.disabled||!form.reportValidity())return;
  const role=$('team-role').value;let user;try{user=await picker.resolve();}catch(e){status.textContent=e.message;return;}if(!user)return;const id=user.telegram_user_id;
  if(!confirm('Назначить роль «'+(role==='advertiser'?'Базовый доступ':roles[role])+'» пользователю '+window.pushokUsers.caption(user)+'?'))return;
  fields.disabled=true;status.textContent='Сохраняем…';
  try{
   await api('/admin/team',{method:'PUT',body:{telegram_user_id:id,role}});
   status.textContent='Роль сохранена. Пользователю нужно заново открыть кабинет.';
   await load();
  }catch(e){status.textContent=e.message||'Не удалось изменить роль. Проверьте ID и свои права.';fields.disabled=false;}
 });
 return {load,roleControl,roles,displayRole};
})();
