'use strict';
window.pushokSupport=(()=>{
 const labels={question:'Вопрос',problem:'Проблема',idea:'Предложение'},state={open:'Открыто',waiting_staff:'Ожидает сотрудника',closed:'Закрыто'};
 async function show(host){
  const session=token, owner=!!(me?.is_owner||me?.is_developer),staff=owner||['admin','operator','moderator'].includes(me?.role);
  const box=el('section',undefined,'panel vpn-policy-card'),thread=el('div'),list=el('div'),feedback=el('p',undefined,'muted');
  host.replaceChildren(box);const live=()=>token===session&&box.isConnected;
  const tg=el('a','Личный чат в Telegram ↗','ui-button labeled-action');tg.href='https://t.me/server_pushok_bot';tg.target='_blank';tg.rel='noopener noreferrer';
  box.append(el('h2','Pushok ИИ'),tg);
  let team=false,current=null,asStaff=false,rev=0;
  const toggle=el('input');toggle.type='checkbox';
  if(staff){const l=el('label','Обращения пользователей');l.append(toggle);box.append(l);toggle.onchange=()=>{team=toggle.checked;clear();void load();};}
  const entry=el('form',undefined,'vpn-rule-body'),kind=el('select'),msg=el('textarea'),human=el('input'),close=el('input');
  for(const [k,v] of Object.entries(labels)){const o=el('option',v);o.value=k;kind.append(o);}
  kind.setAttribute('aria-label','Тип обращения');msg.placeholder='Ваш вопрос или предложение';msg.setAttribute('aria-label','Сообщение');msg.required=true;msg.maxLength=2000;
  const humanLabel=el('label','Передать сотруднику'),closeLabel=el('label','Закрыть обращение');human.type=close.type='checkbox';humanLabel.append(human);closeLabel.append(close);
  const send=el('button','Отправить','ui-button labeled-action');send.type='submit';entry.append(kind,msg,humanLabel,closeLabel,send);
  const fresh=button('Новое обращение',()=>clear());box.append(list,thread,fresh,entry,feedback);
  function clear(){++rev;thread.replaceChildren();current=null;asStaff=false;kind.hidden=false;closeLabel.hidden=true;humanLabel.hidden=false;entry.hidden=team;fresh.hidden=team;msg.value='';feedback.textContent='';}
  async function open(id){const turn=++rev;try{const data=await api('/support/cases/'+id);if(!live()||turn!==rev)return;
   current=id;asStaff=team&&data.can_reply_as_staff;entry.hidden=team&&!asStaff;kind.hidden=true;humanLabel.hidden=asStaff;closeLabel.hidden=!asStaff;
   thread.replaceChildren(el('h3','Обращение №'+id));for(const row of data.messages){
    const line=el('div',undefined,'vpn-notice-preview'),body=el('p');
    if(row.sender==='user')body.textContent=row.body;else appendFormattedText(body,row.body);
    line.append(el('strong',({user:team?'Пользователь':'Вы','ai-agent':'Pushok ИИ',staff:'Сотрудник'})[row.sender]),body);thread.append(line);}
  }catch(e){if(live())feedback.textContent=e.message;}}
  async function load(){const scope=team;try{const data=await api('/support/cases?team='+scope);if(!live()||scope!==team)return;
   list.replaceChildren();for(const row of data.items)list.append(button('№'+row.id+' · '+labels[row.category]+' · '+state[row.state],()=>open(row.id)));
  }catch(e){if(live())feedback.textContent=e.message;}}
  let intent,key;
  entry.onsubmit=async(e)=>{e.preventDefault();if(send.disabled||!msg.value.trim())return;send.disabled=true;
   const text=msg.value.trim(),fp=JSON.stringify([current,asStaff,text,kind.value,human.checked,close.checked]);if(fp!==key){key=fp;intent=crypto.randomUUID();}
   try{const row=await api(asStaff?'/support/cases/'+current+'/staff':'/support/cases',{method:'POST',body:asStaff?{request_id:intent,text,close:close.checked}:{request_id:intent,text,case_id:current,category:kind.value,human:human.checked}});
    if(!live())return;msg.value='';key=null;close.checked=human.checked=false;await open(row.id||current);await load();}
   catch(err){if(live())feedback.textContent=err.message;}finally{send.disabled=false;}};
  clear();await load();
  if(owner&&live()){const d=el('details'),summary=el('summary','Подключение Telegram-бота'),f=el('form'),n=el('input'),save=el('button','Подключить','labeled-action'),notice=el('small');
    n.type='password';n.autocomplete='new-password';n.placeholder='Токен @server_pushok_bot';n.required=true;save.type='submit';f.append(n,save,notice);d.append(summary,f);box.append(d);
    try{const info=await api('/support/bot');notice.textContent=info.configured?'Токен настроен':'Ожидает токен BotFather';}catch(e){notice.textContent=e.message;}
    f.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{await api('/support/bot',{method:'PUT',body:{token:n.value.trim()}});n.value='';notice.textContent='Бот подключён';}catch(err){notice.textContent=err.message;}finally{save.disabled=false;}};}
 }
 return {show};
})();
