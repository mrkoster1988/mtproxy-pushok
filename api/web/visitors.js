'use strict';
window.pushokVisitors=(()=>{
 let timer,visitId,session='',epoch=0;
 async function ping(){if(!token||token!==session||document.hidden)return;try{await api('/visits',{method:'POST',body:{visit_id:visitId}});}catch{ /* Следующая попытка не мешает кабинету. */ }}
 function start(){if(session===token&&visitId)return;clearInterval(timer);session=token;visitId=crypto.randomUUID();void ping();timer=setInterval(ping,60000);}
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)void ping();});
 async function load(root,q='',offset=0){
  if(!root||me?.role!=='admin')return;const version=++epoch,owner=token;
  root.replaceChildren(el('p','Загружаем посещения…','muted'));
  try{const data=await api('/admin/visitors?'+new URLSearchParams({q,offset}));if(version!==epoch||owner!==token)return;
   root.replaceChildren();const form=el('form',undefined,'visitor-search'),input=el('input');input.type='search';input.value=q;input.placeholder='Имя, @username или Telegram ID';input.setAttribute('aria-label','Найти посетителя');const submit=el('button','Найти');submit.type='submit';form.append(input,submit);form.addEventListener('submit',e=>{e.preventDefault();void load(root,input.value);});
   root.append(form,el('p','Посетителей: '+data.total,'muted'),el('p','Видны авторизованные входы. Активность обновляется раз в минуту, пока кабинет открыт. Старые данные — из сохранившихся сессий.','muted'));
   const list=el('div',undefined,'visitor-list');for(const u of data.items){const row=el('article',undefined,'visitor-row');row.append(el('h3',[u.first_name,u.last_name].filter(Boolean).join(' ')||'Пользователь'),el('p',(u.username?'@'+u.username+' · ':'')+'ID '+u.telegram_user_id),el('p',({admin:'Администратор',operator:'Оператор',advertiser:'Рекламодатель'})[u.role]),el('p','Последняя активность: '+date(u.last_visit)),el('p','Первый вход: '+date(u.first_visit)+' · Открытий: '+u.visits));list.append(row);}if(!data.items.length)list.append(el('p','Посетителей по этому запросу нет.'));root.append(list);
   const actions=el('div',undefined,'actions');if(offset)actions.append(button('Назад',()=>load(root,q,Math.max(0,offset-100))));if(offset+data.items.length<data.total)actions.append(button('Следующие',()=>load(root,q,offset+100)));actions.append(button('Обновить',()=>load(root,q,offset)));root.append(actions);
  }catch(e){if(version===epoch&&owner===token)root.replaceChildren(el('p',e.message),button('Повторить',()=>load(root,q,offset)));}
 }
 return {start,load,reset(){clearInterval(timer);session='';visitId=null;epoch++;}};
})();

if(typeof me!=="undefined"&&me&&typeof token!=="undefined"&&token)window.pushokVisitors.start();
