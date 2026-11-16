'use strict';
// Один поиск существующих аккаунтов для ролей, подарков и персональных предложений.
window.pushokUsers=(()=>{
 const roles={admin:'Администратор',operator:'Оператор',advertiser:'Рекламодатель'};
 function caption(user){const name=[user.first_name,user.last_name].filter(Boolean).join(' '),handle=user.username?'@'+user.username:'';return [name.replace(/^@/,'').toLowerCase()===user.username?.toLowerCase()?'':name,handle,'ID '+user.telegram_user_id].filter(Boolean).join(' · ');}
 function picker(parent,{label='Пользователь: @никнейм, имя или ID',required=true,allowUnknownId=false,value='',onSelect=()=>{}}={}){
  const root=el('div',undefined,'user-picker'),line=el('div',undefined,'user-picker-line'),input=el('input'),l=el('label',label),results=el('div',undefined,'user-picker-results'),status=el('p',undefined,'muted');
  input.type='search';input.autocomplete='off';input.maxLength=100;input.required=required;input.setAttribute('aria-label',label);input.placeholder='@username, имя или Telegram ID';input.value=value;status.setAttribute('role','status');
  let selected=null,selectedText='',serial=0;const session=token;
  const current=seq=>seq===serial&&session===token&&me?.role==='admin'&&root.isConnected;
  function set(user){selected=user;selectedText=user.username?'@'+user.username:String(user.telegram_user_id);input.value=selectedText;results.replaceChildren();status.textContent=caption(user);onSelect(user);}
  function display(rows){results.replaceChildren();for(const user of rows){const b=button(caption(user),()=>set(user));b.prepend(window.pushokViews.userAvatar(user.id,[user.first_name,user.last_name].filter(Boolean).join(' ')));b.classList.add('user-choice');results.append(b);}status.textContent=rows.length?'Выберите пользователя из списка.':'Пользователь не найден. Он должен открыть кабинет.';}
  async function find(){const query=input.value.trim(),seq=++serial;if(!query){results.replaceChildren();status.textContent='Введите никнейм, имя или ID.';return null;}status.textContent='Поиск…';try{const data=await api('/admin/users?query='+encodeURIComponent(query));if(!current(seq))return null;display(data.items);return data.items;}catch(e){if(current(seq))status.textContent=e.message;throw e;}}
  async function resolve(){if(selected&&input.value===selectedText)return selected;const query=input.value.trim().replace(/^@/,'');if(!query&&!required)return null;const rows=await find();if(!rows)return null;const matches=rows.filter(u=>String(u.telegram_user_id)===query||u.id===query||u.username?.toLowerCase()===query.toLowerCase());if(matches.length===1){set(matches[0]);return matches[0];}if(allowUnknownId&&!rows.length&&/^[1-9][0-9]{0,15}$/.test(query)&&Number.isSafeInteger(Number(query))&&Number(query)<2**52){const user={telegram_user_id:Number(query),first_name:'Получатель ещё не входил'};set(user);return user;}throw Error(rows.length?'Выберите нужного пользователя в результатах поиска.':'Пользователь не найден. Попросите его открыть кабинет.');}
  const search=button('Найти',find);search.setAttribute('aria-label','Найти пользователя: '+label);line.append(input,search);l.append(line);root.append(l,status,results);parent.append(root);
  input.addEventListener('input',()=>{serial++;selected=null;results.replaceChildren();status.textContent='';onSelect(null);});input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();void find().catch(()=>{});}});
  return {root,input,set,resolve,disable(flag){root.hidden=flag;input.disabled=flag;search.disabled=flag;}};
 }
 function directory(target){
  if(!['admin','operator'].includes(me?.role)){target.replaceChildren();return;}
  const session=token,uid=me.id;let page=0,serial=0;
  const form=el('form',undefined,'directory-search'),search=el('input'),role=el('select'),visited=el('input'),vl=el('label','Только входившие в приложение'),list=el('div',undefined,'user-directory'),summary=el('p',undefined,'muted'),nav=el('nav',undefined,'profile-pagination');
  search.type='search';search.placeholder='@username, имя или ID';search.setAttribute('aria-label','Поиск пользователей');search.maxLength=100;role.setAttribute('aria-label','Роль пользователя');option(role,'','Все роли');Object.entries(roles).forEach(([k,v])=>option(role,k,v));visited.type='checkbox';vl.prepend(visited);const submit=el('button','Найти');submit.type='submit';form.append(search,role,submit,vl);target.replaceChildren(form,summary,list,nav);
  async function load(){const seq=++serial;summary.textContent='Загрузка пользователей…';try{const data=await api('/admin/users?'+new URLSearchParams({query:search.value.trim(),role:role.value,visited:visited.checked,page}));if(seq!==serial||session!==token||uid!==me?.id||!target.isConnected||location.hash!=='#/users')return;list.replaceChildren();nav.replaceChildren();summary.textContent='Пользователей: '+data.total;
   for(const user of data.items){const row=el('article',undefined,'card directory-user');const access=window.pushokTeam.roleControl(user);row.append(window.pushokProfiles.compact(user,'users'),access.badge,access.editor,el('p',user.last_login_at?'Последний вход: '+date(user.last_login_at):'Вход в приложение ещё не зафиксирован.','muted'));list.append(row);}
   if(!data.items.length)list.append(el('p','Пользователи не найдены. Измените поиск или фильтр.','empty'));
   if(page)nav.append(button('Предыдущие',()=>{page--;return load();}));if(data.more)nav.append(button('Следующие',()=>{page++;return load();}));
  }catch(e){if(seq===serial&&session===token&&target.isConnected){summary.textContent=e.message;list.replaceChildren(button('Повторить',load));}}}
  form.addEventListener('submit',e=>{e.preventDefault();page=0;void load();});role.addEventListener('change',()=>{page=0;void load();});visited.addEventListener('change',()=>{page=0;void load();});void load();
 }
 return {picker,directory,caption};
})();
