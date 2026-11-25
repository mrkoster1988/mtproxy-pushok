'use strict';
window.pushokCommunity=(()=>{
 const cache=new Map();let session='',activeAt=Date.now(),returnRoute='hall';
 const names=u=>[u.first_name,u.last_name].filter(Boolean).join(' ')||u.username||'Участник';
 const v=()=>window.pushokViews;
 async function person(id){if(session!==token){cache.clear();session=token;}const old=cache.get(id);if(old&&Date.now()-old.at<60000)return old.promise;const promise=api('/community/users/'+id);cache.set(id,{at:Date.now(),promise});try{return await promise;}catch(e){cache.delete(id);throw e;}}
 function decorate(node,id){
  if(!id)return;node.removeAttribute('aria-hidden');node.setAttribute('role','link');node.tabIndex=0;node.setAttribute('aria-label','Открыть профиль');
  const go=e=>{e.preventDefault();e.stopPropagation();const from=location.hash.replace(/^#\/?/,'')||'dashboard';
   // Вложенный профиль сохраняет исходный раздел, в том числе после перехода по имени.
   if(!/^(user|member)\//.test(from)){returnRoute=from;window.pushokProfiles.setBackRoute(from);}
   window.cabinet.go(id===me?.id?'profile':['admin','operator'].includes(me?.role)?'user/'+id:'member/'+id);};
  node.addEventListener('click',go);node.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' ')go(e);});
  node.dataset.userId=id;updateAvatar(node,id);
 }
 function updateAvatar(node,id){
  const s=token;void person(id).then(data=>{if(s!==token)return;
   const copy=node.parentElement?.querySelector('.person-copy,.hall-person');if(copy)copy.dataset.userRole=hasDeveloperAccess(data)?'owner':data.role;
   if(!node.dataset.hallRank){node.classList.remove('hall-frame','hall-gold','hall-silver','hall-bronze','hall-teal');window.pushokHall.frame(node,data.frame_rank);}
   node.classList.toggle('user-online',data.online===true);
   if(data.online!==null)node.title=data.online?'В приложении сейчас':'Не в сети';
   node.classList.toggle('birthday-avatar',data.birthday_today);if(data.birthday_today)node.title='Сегодня день рождения';
  }).catch(()=>{});
 }

 async function show(target,id){const s=token;target.replaceChildren(el('p','Загрузка…','muted'));try{const u=await person(id);if(s!==token||!target.isConnected)return;
   const card=el('section',undefined,'profile-identity user-identity public-person'),row=el('div',undefined,'person-line'),copy=window.pushokProfiles.names(u,true);
   if(u.online!==null)copy.append(el('small',u.online?'Онлайн в приложении':'Не в сети','muted'));
   row.append(v().userAvatar(id,names(u)),copy);card.append(row);const role=el('span',window.pushokTeam.displayRole(u),'profile-role');role.dataset.role=hasDeveloperAccess(u)?'owner':u.role;card.append(role);target.replaceChildren(card);window.pushokVpn.profile(target,id);
  }catch(e){if(s===token)target.replaceChildren(el('p',e.message,'empty'));}}
 function invited(id,count){
  const box=el('details',undefined,'panel profile-invited-block'),head=el('summary'),list=el('div',undefined,'invitees'),feedback=el('p',undefined,'invite-feedback'),actions=el('div',undefined,'invite-actions');
  head.append(v().icon('users'),el('span','Приглашено'),el('strong',String(count)),v().icon('chevron'));feedback.setAttribute('role','status');
  const body=el('div',undefined,'invite-body');body.append(list,feedback,actions);box.append(head,body);
  let loaded=false,nextPage=0,busy=false;const s=token,seen=new Set();
  const more=v().iconAction(button('',load),'plus','Ещё приглашённые');actions.append(more);actions.hidden=true;
  async function load(){if(busy||s!==token)return;busy=true;more.disabled=true;feedback.textContent='Загрузка…';box.setAttribute('aria-busy','true');
   try{const data=await api('/community/users/'+id+'/invited?page='+nextPage);if(s!==token||!box.isConnected)return;
    for(const u of data.items){if(seen.has(u.id))continue;seen.add(u.id);const row=el('div',undefined,'person-line invitee user-identity');row.append(v().userAvatar(u.id,names(u)),window.pushokProfiles.names(u,true));if(me?.role==='admin')row.append(v().iconAction(button('',async()=>{
      const result=await api('/admin/growth/referrals/'+u.id+'/from/'+id,{method:'DELETE'});
      if(s!==token||!box.isConnected)return;
      row.remove();seen.delete(u.id);
      head.querySelector('strong').textContent=String((await api('/users/'+id+'/profile')).counts.invited);
      feedback.textContent=result.removed?'Приглашение снято.':'Список изменился.';
      nextPage=0;seen.clear();list.replaceChildren();await load();
     }),'close','Убрать из приглашённых'));
     list.append(row);}
    loaded=true;nextPage++;feedback.textContent=seen.size?'':'Пока никто не приглашён';actions.hidden=!data.more;v().iconAction(more,'plus','Ещё приглашённые');
   }catch(e){if(s===token&&box.isConnected){feedback.textContent=e.message;actions.hidden=false;v().iconAction(more,'refresh','Повторить загрузку приглашённых');}}
   finally{busy=false;more.disabled=false;box.setAttribute('aria-busy','false');}
  }
  if(me?.role==='admin'){
   const form=el('form',undefined,'invite-form');
   const submit=el('button','Привязать пользователя','primary invite-submit');
   let assigning=false;
   function syncSubmit(){submit.disabled=assigning||!picker.input.value.trim();}
   const picker=window.pushokUsers.picker(form,{label:'Добавить приглашённого',onSelect:syncSubmit});
   submit.type='submit';submit.prepend(v().icon('plus'));form.append(submit);body.append(form);syncSubmit();
   form.addEventListener('submit',async event=>{
    event.preventDefault();if(submit.disabled)return;assigning=true;syncSubmit();
    try{
     const friend=await picker.resolve();if(!friend||!box.isConnected||s!==token)return;
     const chosenText=picker.input.value;
     await api('/admin/growth/referrals/'+friend.id,{method:'PUT',body:{inviter_user_id:id}});
     if(!box.isConnected||s!==token)return;
     const profile=await api('/users/'+id+'/profile');head.querySelector('strong').textContent=String(profile.counts.invited);
     if(picker.input.value===chosenText){
      picker.input.value='';picker.input.dispatchEvent(new Event('input',{bubbles:true}));
     }
     nextPage=0;seen.clear();list.replaceChildren();await load();notice('Приглашённый назначен.',true);
    }catch(e){feedback.textContent=e.message;}finally{assigning=false;syncSubmit();}
   });
  }
  box.addEventListener('toggle',()=>{if(box.open&&!loaded)void load();});return box;
 }
 async function leaders(target){const s=token;target.setAttribute('aria-busy','true');try{const data=await Promise.all(['invited','paid_referrals','spend','paid_campaigns'].map(metric=>api('/hall?kind=month&metric='+metric)));if(s!==token||!target.isConnected)return;
  const key=JSON.stringify(data.map(d=>d.rows.find(r=>r.rank===1)?.user_id));if(target.dataset.leaders===key)return;target.dataset.leaders=key;const ids=new Set(),rowNode=el('div',undefined,'home-leader-avatars');target.replaceChildren(el('h2','Лидеры месяца'),rowNode);for(const d of data){const row=d.rows.find(r=>r.rank===1);if(!row||ids.has(row.user_id))continue;ids.add(row.user_id);const avatar=v().userAvatar(row.user_id,row.first_name||'Участник');window.pushokHall.frame(avatar,1);rowNode.append(avatar);}if(!ids.size)rowNode.append(el('span','Первые места пока свободны','muted'));target.hidden=false;
 }catch{target.hidden=true;}finally{target.setAttribute('aria-busy','false');}}
 function ownerTools(target,uid,data,reload){if(!hasDeveloperAccess(me))return;
  const box=el('details',undefined,'owner-personal-tools'),head=el('summary');head.append(v().icon('settings'),el('span','Изменить сведения'));head.title='Изменить или перенести сведения';head.setAttribute('aria-label',head.title);box.append(head);target.append(box);
  const form=el('form'),phone=el('input'),birthday=el('input'),year=el('input');phone.value=data.phone_number||'';phone.placeholder='+79991234567';birthday.placeholder='ДД.ММ';birthday.value=data.birthday?[data.birthday.day,data.birthday.month].map(x=>String(x).padStart(2,'0')).join('.'):'';year.type='number';year.value=data.birthday?.year||'';year.placeholder='Необязательно';
  for(const [label,input] of [['Телефон',phone],['День и месяц',birthday],['Год',year]]){const l=el('label',label);l.append(input);form.append(l);}
  const save=v().iconAction(el('button'),'check','Сохранить сведения');save.type='submit';const status=el('p',undefined,'muted');status.setAttribute('role','status');form.append(save,status);box.append(form);
  form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{let b=null;if(birthday.value){const parts=birthday.value.split('.');b={day:Number(parts[0]),month:Number(parts[1]),year:year.value?Number(year.value):null};}await api('/community/users/'+uid+'/personal',{method:'PUT',body:{phone_number:phone.value.trim()||null,birthday:b}});await reload();}catch(err){status.textContent=err.message;}finally{save.disabled=false;}};
  const transfer=el('form'),dest=el('input');dest.inputMode='numeric';dest.required=true;dest.pattern='[0-9]+';const l=el('label','Telegram ID нового аккаунта');l.append(dest);transfer.append(l,el('small','Только личные сведения. Новый аккаунт должен уже войти в приложение и не иметь заполненных сведений. Баланс и кампании остаются на старом аккаунте.','muted'));
  const move=v().iconAction(el('button'),'share','Перенести сведения');move.type='submit';transfer.append(move);box.append(transfer);
  transfer.onsubmit=async e=>{e.preventDefault();if(!confirm('Перенести личные сведения на Telegram ID '+dest.value+'? Старый аккаунт потеряет верификацию.'))return;move.disabled=true;try{await api('/community/users/'+uid+'/personal/transfer',{method:'POST',body:{target_telegram_id:Number(dest.value)}});await reload();}catch(err){status.textContent=err.message;}finally{move.disabled=false;}};
 }
 for(const event of ['pointerdown','keydown','touchstart'])document.addEventListener(event,()=>{activeAt=Date.now();},{passive:true});
 async function heartbeat(){if(!token||!me||hasDeveloperAccess(me)||document.hidden||Date.now()-activeAt>120000)return;try{await api('/community/presence',{method:'POST'});}catch{}}
 setInterval(()=>{void heartbeat();if(!document.hidden&&token)document.querySelectorAll('.user-avatar[data-user-id]').forEach(n=>{if(n.getClientRects().length)updateAvatar(n,n.dataset.userId);});},60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden){activeAt=Date.now();void heartbeat();}});setTimeout(heartbeat,3000);
 Object.assign(errors,{personal_details_locked:'Сведения уже сохранены. Изменить их может только разработчик.',phone_verification_required:'Перед созданием кампании подтвердите номер в профиле.',personal_transfer_target_filled:'У нового аккаунта уже есть личные сведения.',personal_transfer_empty:'Нет сведений для переноса.',personal_transfer_same_user:'Выберите другой аккаунт.'});
 return {decorate,show,invited,leaders,ownerTools,backRoute:()=>returnRoute};
})();
