'use strict';
// Один поиск существующих аккаунтов для ролей, подарков и персональных предложений.
window.pushokUsers=(()=>{
 function caption(user){const name=[user.first_name,user.last_name].filter(Boolean).join(' '),handle=user.username?'@'+user.username:'';return [name.replace(/^@/,'').toLowerCase()===user.username?.toLowerCase()?'':name,handle,'ID '+user.telegram_user_id].filter(Boolean).join(' · ');}
 function picker(parent,{label='Пользователь: @никнейм, имя или ID',required=true,allowUnknownId=false,value='',onSelect=()=>{}}={}){
  const root=el('div',undefined,'user-picker'),line=el('div',undefined,'user-picker-line'),input=el('input'),l=el('label',label),results=el('div',undefined,'user-picker-results'),status=el('p',undefined,'muted');
  input.type='search';input.autocomplete='off';input.maxLength=100;input.required=required;input.setAttribute('aria-label',label);input.placeholder='@username, имя или Telegram ID';input.value=value;status.setAttribute('role','status');
  let selected=null,selectedText='',serial=0;const session=token;
  const current=seq=>seq===serial&&session===token&&me?.role==='admin'&&root.isConnected;
  function set(user){selected=user;selectedText=user.username?'@'+user.username:String(user.telegram_user_id);input.value=selectedText;results.replaceChildren();status.textContent=caption(user);onSelect(user);}
  function display(rows){results.replaceChildren();for(const user of rows){const b=button('',()=>set(user));b.setAttribute('aria-label',caption(user));const copy=window.pushokProfiles.names(user);copy.append(el('small','ID '+user.telegram_user_id,'muted'));b.append(window.pushokViews.userAvatar(user.id,[user.first_name,user.last_name].filter(Boolean).join(' ')),copy);b.classList.add('user-choice');results.append(b);}status.textContent=rows.length?'Выберите пользователя из списка.':'Пользователь не найден. Он должен открыть кабинет.';}
  async function find(){const query=input.value.trim(),seq=++serial;if(!query){results.replaceChildren();status.textContent='Введите никнейм, имя или ID.';return null;}status.textContent='Поиск…';try{const data=await api('/admin/users?query='+encodeURIComponent(query));if(!current(seq))return null;display(data.items);return data.items;}catch(e){if(current(seq))status.textContent=e.message;throw e;}}
  async function resolve(){if(selected&&input.value===selectedText)return selected;const query=input.value.trim().replace(/^@/,'');if(!query&&!required)return null;const rows=await find();if(!rows)return null;const matches=rows.filter(u=>String(u.telegram_user_id)===query||u.id===query||u.username?.toLowerCase()===query.toLowerCase());if(matches.length===1){set(matches[0]);return matches[0];}if(allowUnknownId&&!rows.length&&/^[1-9][0-9]{0,15}$/.test(query)&&Number.isSafeInteger(Number(query))&&Number(query)<2**52){const user={telegram_user_id:Number(query),first_name:'Получатель ещё не входил'};set(user);return user;}throw Error(rows.length?'Выберите нужного пользователя в результатах поиска.':'Пользователь не найден. Попросите его открыть кабинет.');}
  const search=button('Найти',find);search.setAttribute('aria-label','Найти пользователя: '+label);line.append(input,search);l.append(line);root.append(l,status,results);parent.append(root);
  input.addEventListener('input',()=>{serial++;selected=null;results.replaceChildren();status.textContent='';onSelect(null);});input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();void find().catch(()=>{});}});
  return {root,input,set,resolve,disable(flag){root.hidden=flag;input.disabled=flag;search.disabled=flag;}};
 }
 let directoryView=null;
 function directory(target){
  if(!['admin','operator'].includes(me?.role)){target.replaceChildren();return;}
  const session=token,uid=me.id;
  if(directoryView?.session!==session||directoryView.uid!==uid)directoryView={session,uid,sort:'recent',query:'',role:'',visited:false,page:0};
  const view=directoryView;
  let page=view.page,serial=0;
  const form=el('form',undefined,'directory-search'),search=el('input'),role=el('select'),visited=el('input'),vl=el('label','Только входившие в приложение'),list=el('div',undefined,'user-directory'),summary=el('p',undefined,'muted'),nav=el('nav',undefined,'profile-pagination');
  const sorting=el('select'),sortLabel=el('label','Сортировка');
  sortLabel.className='directory-sort';sorting.setAttribute('aria-label','Сортировка пользователей');
  for(const [key,title] of [['recent','По последнему входу'],['newest','Сначала новые'],['oldest','Сначала ранние'],['name','По имени (А–Я)']])option(sorting,key,title);
  sorting.value=view.sort;sortLabel.append(sorting);
  search.type='search';search.placeholder='@username, имя или ID';search.setAttribute('aria-label','Поиск пользователей');search.maxLength=100;role.setAttribute('aria-label','Категория пользователя');option(role,'','Все категории');for(const key of ['user','advertiser','friends','moderator','operator','admin','developer'])option(role,key,window.pushokTeam.roles[key]);visited.type='checkbox';vl.prepend(visited);const submit=el('button','Найти');submit.type='submit';search.value=view.query;role.value=view.role;visited.checked=view.visited;form.append(search,role,submit,sortLabel,vl);target.replaceChildren(form,window.pushokProfiles.agentCard('users'),summary,list,nav);
  list.addEventListener('friend-mark-changed',()=>{if(view.role==='friends')void load();});
  async function load(){const seq=++serial;Object.assign(view,{query:search.value.trim(),role:role.value,visited:visited.checked,sort:sorting.value,page});summary.textContent='Загрузка пользователей…';try{const data=await api('/admin/users?'+new URLSearchParams({query:search.value.trim(),category:role.value,visited:visited.checked,sort:sorting.value,page}));if(seq!==serial||session!==token||uid!==me?.id||!form.isConnected||location.hash!=='#/users')return;list.replaceChildren();nav.replaceChildren();summary.textContent='Пользователей: '+data.total;
   for(const user of data.items){const row=el('article',undefined,'card directory-user');const access=window.pushokTeam.roleControl(user);row.append(window.pushokProfiles.compact(user,'users'),access.badge,access.editor,el('p',user.last_login_at?'Последний вход: '+date(user.last_login_at):'Вход в приложение ещё не зафиксирован.','muted'));list.append(row);}
   if(!data.items.length)list.append(el('p','Пользователи не найдены. Измените поиск или фильтр.','empty'));
   if(page)nav.append(button('Предыдущие',()=>{page--;return load();}));if(data.more)nav.append(button('Следующие',()=>{page++;return load();}));
  }catch(e){if(seq===serial&&session===token&&form.isConnected){summary.textContent=e.message;list.replaceChildren(button('Повторить',load));}}}
  form.addEventListener('submit',e=>{e.preventDefault();page=0;void load();});sorting.addEventListener('change',()=>{page=0;void load();});role.addEventListener('change',()=>{page=0;void load();});visited.addEventListener('change',()=>{page=0;void load();});void load();
 }
 return {picker,directory,caption};
})();
