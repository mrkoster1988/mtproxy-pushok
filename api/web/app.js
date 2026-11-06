'use strict';
const $=id=>document.getElementById(id);
let token=sessionStorage.getItem('pushok-session')||'',me=null,channels=[],tariffs=[],nodes=[];
const labels={draft:'Черновик',pending:'На модерации',approved:'Канал одобрен',rejected:'Отклонено',changes_requested:'Нужны исправления',not_required:'Оплата после одобрения',awaiting_payment:'Ожидается оплата',proof_submitted:'Чек на проверке',verified:'Оплата подтверждена',refunded:'Возврат',not_scheduled:'Слот не назначен',scheduled:'Запланировано',active:'Размещение активно',completed:'Завершено',cancelled:'Отменено'};
const errors={campaign_not_editable:'Заявку сейчас нельзя редактировать или отправить повторно.',channel_in_review_or_approved:'Этот канал связан с заявкой на проверке или одобренным размещением. Его изменение заблокировано.',admin_role_required:'Действие доступно владельцу.',invalid_proxy_host:'Укажите домен или IP без протокола и порта.',proxy_node_already_exists:'Этот адрес и порт уже добавлены.',proxy_node_has_placements:'На узле есть запланированное или активное размещение. Сначала завершите его.',authentication_required:'Войдите через Telegram.',session_revoked_or_expired:'Сессия завершена. Войдите снова.',telegram_login_expired:'Данные входа устарели. Повторите вход.',telegram_login_replayed:'Повторите вход через Telegram.',telegram_login_invalid_signature:'Telegram не подтвердил вход.',database_unavailable:'База данных временно недоступна.',invalid_telegram_channel_url:'Укажите ссылку вида https://t.me/channel.',channel_title_required:'Введите название канала.',channel_already_added:'Этот канал уже добавлен.',payment_proof_too_large:'Файл должен быть меньше 5 МБ.',unsupported_payment_proof_format:'Загрузите JPEG, PNG или PDF.',payment_proof_content_type_mismatch:'Тип файла не соответствует содержимому.',payment_proof_storage_unavailable:'Хранилище чеков временно недоступно.',proxy_node_slot_unavailable:'Этот слот уже занят. Выберите другое время или узел.',placement_not_in_active_window:'Активация доступна только в назначенное время.',campaign_not_ready_for_placement:'Для размещения нужны одобрение канала и подтверждённая оплата.',operator_role_required:'Нужны права оператора.',payment_proof_not_expected:'Заявка сейчас не ожидает чек.',campaign_not_draft:'Эта заявка уже отправлена.',campaign_not_pending:'Заявка уже обработана.',payment_proof_not_pending_review:'Оплата уже обработана.',channel_or_active_tariff_not_found:'Выберите свой канал и действующий тариф.',schedule_start_must_be_future:'Выберите время в будущем.'};
function notice(message,ok=false){$('notice').textContent=message;$('notice').className=ok?'ok':'';$('notice').hidden=false;}
function el(tag,text,cls){const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(cls)x.className=cls;return x;}
function button(text,fn){const b=el('button',text);b.type='button';b.addEventListener('click',()=>run(fn,b));return b;}
async function api(path,{method='GET',body}={}){
 const headers={};if(token)headers.Authorization='Bearer '+token;
 if(body&&!(body instanceof FormData))headers['Content-Type']='application/json';
 const r=await fetch('/api/v1'+path,{method,headers,body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
 if(!r.ok){let d;try{d=(await r.json()).detail;}catch{d='';}if(r.status===401&&token){token='';sessionStorage.removeItem('pushok-session');$('workspace').hidden=true;$('login').hidden=false;$('logout').hidden=true;}
 throw Error(errors[d]||(r.status===422?'Проверьте заполненные поля.':typeof d==='string'&&d.endsWith('_not_configured')?'Вход ещё не настроен администратором.':'Не удалось выполнить действие. Попробуйте снова.'));}
 return r.status===204?null:r.json();
}
async function run(fn,b){if(b)b.disabled=true;try{await fn();}catch(e){notice(e.message);}finally{if(b)b.disabled=false;}}
function option(select,value,text){const o=el('option',text);o.value=value;select.append(o);}
function money(n){return new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:0}).format(n/100);}
function date(v){return v?new Date(v).toLocaleString('ru-RU'):'Не назначено';}
function operatorView(on){$('advertiser').hidden=on;$('operator').hidden=!on;$('advertiser-tab').classList.toggle('selected',!on);$('operator-tab').classList.toggle('selected',on);}
function card(c,op){
 const box=el('article',undefined,'card'),head=el('div',undefined,'cardhead');
 head.append(el('h3',(c.channel_title||channels.find(x=>x.id===c.channel_id)?.title||'Заявка')+' · '+c.id.slice(0,8)),el('span',labels[c.placement_status]||c.placement_status,'badge'));box.append(head);
 if(c.channel_url){try{const u=new URL(c.channel_url);if(u.protocol==='https:'&&['t.me','telegram.me'].includes(u.hostname)){const link=el('a','Открыть канал');link.href=u.href;link.target='_blank';link.rel='noopener noreferrer';box.append(link);}}catch{}}
 const meta=el('div',undefined,'meta');
 [labels[c.moderation_status],labels[c.payment_status],c.tariff_name_snapshot+' · '+money(c.price_kopecks_snapshot),'Начало: '+date(c.scheduled_start_at)].forEach(t=>meta.append(el('span',t)));box.append(meta);
 if(c.moderator_comment)box.append(el('p','Модерация: '+c.moderator_comment));
 if(c.payment_review_comment)box.append(el('p','Проверка оплаты: '+c.payment_review_comment));
 const actions=el('div',undefined,'actions');box.append(actions);
 const change=async(path,body)=>{await api('/operator/campaigns/'+c.id+'/'+path,{method:'POST',body});notice('Изменения сохранены.',true);await refresh();};
 if(!op){
  if(['draft','changes_requested'].includes(c.moderation_status)){
   actions.append(button('Исправить канал',async()=>{
    if(box.querySelector('.correction-form'))return;
    const form=el('form',undefined,'correction-form');
    const title=el('input');title.value=c.channel_title||channels.find(x=>x.id===c.channel_id)?.title||'';title.required=true;title.maxLength=255;title.setAttribute('aria-label','Название канала');
    const url=el('input');url.value=c.channel_url||channels.find(x=>x.id===c.channel_id)?.telegram_url||'';url.type='url';url.required=true;url.setAttribute('aria-label','Ссылка на канал');
    const save=el('button','Сохранить исправления');save.type='submit';
    form.append(title,url,save,button('Отмена',async()=>form.remove()));
    form.addEventListener('submit',e=>{e.preventDefault();run(async()=>{
     await api('/campaigns/'+c.id+'/channel',{method:'PATCH',body:{title:title.value,telegram_url:url.value}});
     channels=await api('/channels');await refresh();notice('Канал исправлен. Теперь отправьте заявку на модерацию.',true);
    },save);});
    box.append(form);title.focus();
   }));
  }
  if(['draft','changes_requested'].includes(c.moderation_status))actions.append(button(c.moderation_status==='changes_requested'?'Отправить повторно':'Отправить на модерацию',async()=>{await api('/campaigns/'+c.id+'/submit',{method:'POST'});await refresh();notice('Заявка отправлена на проверку.',true);}));
  if(c.moderation_status==='approved'&&['awaiting_payment','rejected'].includes(c.payment_status)){
   const input=el('input');input.type='file';input.accept='image/jpeg,image/png,application/pdf';input.setAttribute('aria-label','Подтверждение оплаты');
   actions.append(input,button('Загрузить чек',async()=>{if(!input.files[0])throw Error('Выберите чек.');if(input.files[0].size>5*1024*1024)throw Error('Файл должен быть меньше 5 МБ.');const data=new FormData();data.append('file',input.files[0]);await api('/campaigns/'+c.id+'/payment-proofs',{method:'POST',body:data});await refresh();notice('Чек отправлен на проверку.',true);}));
  }
 }else{
  if(c.moderation_status==='pending'){
   actions.append(button('Одобрить канал',()=>change('moderation',{decision:'approved'})));
   for(const [decision,text] of [['changes_requested','Запросить исправления'],['rejected','Отклонить канал']])actions.append(button(text,async()=>{const comment=prompt('Комментарий рекламодателю');if(comment===null)return;if(!comment.trim())throw Error('Введите комментарий.');await change('moderation',{decision,comment});}));
  }
  if(c.payment_status==='proof_submitted'){
   actions.append(button('Посмотреть чеки',async()=>{const proofs=await api('/operator/campaigns/'+c.id+'/payment-proofs');const list=el('div',undefined,'actions');for(const proof of proofs)list.append(button(proof.original_file_name,async()=>{const r=await fetch('/api/v1/operator/campaigns/'+c.id+'/payment-proofs/'+proof.id,{headers:{Authorization:'Bearer '+token}});if(!r.ok)throw Error('Не удалось скачать чек.');const url=URL.createObjectURL(await r.blob()),a=el('a');a.href=url;a.download=proof.original_file_name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);}));box.append(list);}));
   actions.append(button('Подтвердить оплату',()=>change('payment-review',{decision:'verified'})),button('Отклонить чек',async()=>{const comment=prompt('Причина отклонения чека');if(comment===null)return;if(!comment.trim())throw Error('Введите причину.');await change('payment-review',{decision:'rejected',comment});}));
  }
  if(c.moderation_status==='approved'&&c.payment_status==='verified'&&c.placement_status==='not_scheduled'){
   const select=el('select');select.setAttribute('aria-label','Прокси-узел');option(select,'','Выберите узел');nodes.filter(n=>n.status==='active').forEach(n=>option(select,n.id,n.name));
   const start=el('input');start.type='datetime-local';start.setAttribute('aria-label','Начало размещения');
   actions.append(select,start,button('Назначить слот',async()=>{if(!select.value||!start.value)throw Error('Выберите узел и время начала.');await change('schedule',{proxy_node_id:select.value,start_at:new Date(start.value).toISOString()});}));
  }
  if(c.placement_status==='scheduled')actions.append(button('Тег включён — активировать',async()=>{if(confirm('Спонсорский тег вручную применён на узле и проверен?'))await change('activate');}));
  if(c.placement_status==='active')actions.append(button('Тег выключен — завершить',async()=>{if(confirm('Спонсорский тег выключен на узле?'))await change('complete');}));
 }
 return box;
}
function renderNodes(){
 $('node-admin').hidden=me?.role!=='admin';
 $('nodes-list').replaceChildren(...nodes.map(n=>{
  const box=el('div',undefined,'card');
  box.append(el('h3',n.name),el('p',n.host+':'+n.port+' · '+(n.status==='active'?'Доступен для размещения':'На паузе')));
  box.append(button(n.status==='active'?'Поставить на паузу':'Возобновить',async()=>{
   await api('/admin/proxy-nodes/'+n.id+'/status',{method:'PATCH',body:{status:n.status==='active'?'paused':'active'}});
   await refresh();notice('Состояние узла сохранено.',true);
  }));return box;
 }));
}
$('node-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{
 const data=Object.fromEntries(new FormData(e.target));data.port=Number(data.port);
 await api('/admin/proxy-nodes',{method:'POST',body:data});e.target.reset();await refresh();notice('Узел добавлен в реестр.',true);
},e.submitter);});
async function refresh(){
 if(!token)return;
 const own=await api('/campaigns');$('campaigns').replaceChildren(...own.map(c=>card(c,false)));$('campaign-count').textContent=own.length+' заявок';
 if(!own.length)$('campaigns').append(el('div','Заявок пока нет. Добавьте канал и создайте первое размещение.','empty'));
 if(me&&['operator','admin'].includes(me.role)){
  nodes=await api('/operator/proxy-nodes');renderNodes();
  const queue=await api('/operator/campaigns'+($('queue-filter').value?'?'+$('queue-filter').value:''));
  $('queue').replaceChildren(...queue.map(c=>card(c,true)));if(!queue.length)$('queue').append(el('div','В этой очереди пока нет заявок.','empty'));
 }
}
async function enter(){
 me=await api('/me');channels=await api('/channels');tariffs=await api('/tariffs');
 const isOperator=['operator','admin'].includes(me.role);nodes=isOperator?await api('/operator/proxy-nodes'):[];
 $('identity').textContent=me.first_name||'Рекламодатель';$('logout').hidden=false;$('login').hidden=true;$('workspace').hidden=false;$('operator-tab').hidden=!isOperator;
 $('channel-select').replaceChildren();option($('channel-select'),'','Выберите канал');channels.forEach(c=>option($('channel-select'),c.id,c.title));
 $('tariff-select').replaceChildren();tariffs.forEach(t=>option($('tariff-select'),t.id,t.name+' · '+money(t.price_kopecks)));
 await refresh();
}
window.onTelegramAuth=data=>run(async()=>{const session=await api('/auth/telegram',{method:'POST',body:data});token=session.access_token;sessionStorage.setItem('pushok-session',token);await enter();notice('Вход выполнен.',true);});
$('channel-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{await api('/channels',{method:'POST',body:Object.fromEntries(new FormData(e.target))});e.target.reset();await enter();notice('Канал добавлен.',true);},e.submitter);});
$('campaign-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{await api('/campaigns',{method:'POST',body:Object.fromEntries(new FormData(e.target))});await refresh();notice('Черновик создан. Отправьте его на модерацию.',true);},e.submitter);});
$('logout').addEventListener('click',()=>run(async()=>{await api('/auth/logout',{method:'POST'});sessionStorage.removeItem('pushok-session');location.reload();}));
$('refresh').addEventListener('click',()=>run(refresh));
$('queue-filter').addEventListener('change',()=>run(refresh));
$('advertiser-tab').addEventListener('click',()=>operatorView(false));
$('operator-tab').addEventListener('click',()=>operatorView(true));
run(async()=>{const config=await api('/public-config');if(config.telegram_bot_username&&/^[A-Za-z0-9_]+$/.test(config.telegram_bot_username)){
 const script=document.createElement('script');script.src='https://telegram.org/js/telegram-widget.js?22';script.async=true;script.dataset.telegramLogin=config.telegram_bot_username;script.dataset.size='large';script.dataset.onauth='onTelegramAuth(user)';$('telegram-login').append(script);$('login-note').textContent='Вход подтверждается в Telegram.';
}else $('login-note').textContent='Администратору нужно настроить Telegram-бота для входа.';
 if(token)await enter();
});
