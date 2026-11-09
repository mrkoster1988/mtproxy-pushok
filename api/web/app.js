'use strict';
const $=id=>document.getElementById(id);
let token=sessionStorage.getItem('pushok-session')||'',me=null,channels=[],tariffs=[],nodes=[];
const labels={draft:'Черновик',pending:'На модерации',approved:'Канал одобрен',rejected:'Отклонено',changes_requested:'Нужны исправления',not_required:'Оплата после одобрения',awaiting_payment:'Ожидается оплата',proof_submitted:'Чек на проверке',verified:'Оплата подтверждена',refunded:'Возврат',not_scheduled:'Слот не назначен',scheduled:'Запланировано',active:'Размещение активно',completed:'Завершено',cancelled:'Отменено'};
const errors={evidence_integrity_failed:'Скриншот повреждён или изменён после загрузки.',evidence_invalid_metadata:'Данные файла некорректны.',evidence_file_missing:'Файл не найден в хранилище.',invalid_integrity_limit:'Проверка ограничена 1–100 файлами.',evidence_empty:'Выберите изображение.',evidence_too_large:'Скриншот должен быть меньше 5 МБ.',evidence_corrupt_image:'Не удалось прочитать изображение.',unsupported_evidence_format:'Поддерживаются только PNG и JPEG.',evidence_format_mismatch:'Тип файла не соответствует изображению.',evidence_dimensions_invalid:'Изображение слишком велико.',evidence_already_attached:'У этого отчёта уже есть скриншот.',evidence_observation_unavailable:'Добавить файл можно только к собственному отчёту за последние сутки.',evidence_storage_unavailable:'Хранилище скриншотов временно недоступно.',placement_not_ready_for_observation:'Проверять показ можно только в запланированный период после подтверждённой оплаты.',observation_timezone_required:'Для проверки нужен часовой пояс.',observation_outside_placement_window:'Время наблюдения должно быть внутри рекламного периода.',observation_not_recent:'Наблюдение должно быть выполнено не более 10 минут назад.',observation_note_too_short:'Укажите подробности проверки (от 12 символов).',active_slot_hold_not_found:'Активной брони нет.',slot_start_requires_timezone:'Укажите время с часовым поясом.',slot_start_out_of_range:'Выберите время в пределах ближайших 90 дней.',campaign_not_holdable:'Бронирование доступно только после модерации и до подтверждения оплаты.',slot_held_by_another_campaign:'Место занято другой бронью.',slot_hold_conflict:'Бронь уже существует. Обновите состояние.',invalid_calendar_range:'Выберите период в пределах ближайших 90 дней, не больше 31 дня.',invalid_calendar_timezone:'Часовой пояс не поддерживается.',active_tariff_not_found:'Тариф больше недоступен. Обновите страницу.',payment_instructions_not_configured:'Реквизиты ещё не настроены. Дождитесь инструкции администратора перед оплатой.',campaign_not_editable:'Заявку сейчас нельзя редактировать или отправить повторно.',channel_in_review_or_approved:'Этот канал связан с заявкой на проверке или одобренным размещением. Его изменение заблокировано.',admin_role_required:'Действие доступно владельцу.',invalid_proxy_host:'Укажите домен или IP без протокола и порта.',proxy_node_already_exists:'Этот адрес и порт уже добавлены.',proxy_node_has_placements:'На узле есть запланированное или активное размещение. Сначала завершите его.',authentication_required:'Войдите через Telegram.',session_revoked_or_expired:'Сессия завершена. Войдите снова.',telegram_login_expired:'Данные входа устарели. Повторите вход.',telegram_login_replayed:'Повторите вход через Telegram.',telegram_login_invalid_signature:'Telegram не подтвердил вход.',database_unavailable:'База данных временно недоступна.',invalid_telegram_channel_url:'Укажите ссылку вида https://t.me/channel.',channel_title_required:'Введите название канала.',channel_already_added:'Этот канал уже добавлен.',payment_proof_too_large:'Файл должен быть меньше 5 МБ.',unsupported_payment_proof_format:'Загрузите JPEG, PNG или PDF.',payment_proof_content_type_mismatch:'Тип файла не соответствует содержимому.',payment_proof_storage_unavailable:'Хранилище чеков временно недоступно.',proxy_node_slot_unavailable:'Этот слот уже занят. Выберите другое время или узел.',placement_not_in_active_window:'Активация доступна только в назначенное время.',campaign_not_ready_for_placement:'Для размещения нужны одобрение канала и подтверждённая оплата.',operator_role_required:'Нужны права оператора.',payment_proof_not_expected:'Заявка сейчас не ожидает чек.',campaign_not_draft:'Эта заявка уже отправлена.',campaign_not_pending:'Заявка уже обработана.',payment_proof_not_pending_review:'Оплата уже обработана.',channel_or_active_tariff_not_found:'Выберите свой канал и действующий тариф.',schedule_start_must_be_future:'Выберите время в будущем.'};
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

const holdEligibility=c=>c.moderation_status==='approved' &&
 ['awaiting_payment','proof_submitted','rejected'].includes(c.payment_status) &&
 c.placement_status==='not_scheduled';

async function renderHoldPanel(c,panel){
 panel.replaceChildren(el('h4','Резервирование слота'),el('p','Бронь действует 15 минут. Оплату проверяет оператор; размещение не начинается автоматически.','muted'));
 let hold=null;
 try{hold=await api('/campaigns/'+c.id+'/slot-hold');}
 catch(e){
  if(e.message!==errors.active_slot_hold_not_found){
   panel.append(el('p',e.message,'muted'));
   panel.append(button('Повторить проверку',()=>renderHoldPanel(c,panel)));return;
  }
 }
 if(hold){
  const status=el('p',undefined,'hold-timer');
  panel.append(status,el('p','Назначенный период: '+date(hold.starts_at)+' — '+date(hold.ends_at),'muted'));
  const initial=Math.max(0,Number(hold.remaining_seconds)||0),since=performance.now();
  const update=()=>{
   const seconds=Math.max(0,Math.ceil(initial-(performance.now()-since)/1000));
   status.textContent=seconds?'До освобождения слота: '+Math.floor(seconds/60)+' мин '+String(seconds%60).padStart(2,'0')+' сек':'Срок брони истёк. Обновите состояние.';
   return seconds;
  };
  update();
  const ticker=setInterval(()=>{if(!panel.isConnected||!update())clearInterval(ticker);},1000);
  panel.append(button('Обновить состояние',()=>renderHoldPanel(c,panel)));
  panel.append(button('Отменить бронь',async()=>{
   if(!confirm('Освободить временно зарезервированный слот?'))return;
   await api('/campaigns/'+c.id+'/slot-hold',{method:'DELETE'});
   await renderHoldPanel(c,panel);await loadAvailability();notice('Бронь отменена.',true);
  }));
  return;
 }
 const start=el('input');start.type='datetime-local';start.required=true;start.setAttribute('aria-label','Начало брони');
 const candidate=c.requested_start_at?new Date(c.requested_start_at):new Date(Date.now()+24*3600*1000);
 const proposed=candidate.getTime()>Date.now()?candidate:new Date(Date.now()+24*3600*1000);
 start.value=localDay(proposed)+'T'+String(proposed.getHours()).padStart(2,'0')+':'+String(proposed.getMinutes()).padStart(2,'0');
 const select=el('select');select.setAttribute('aria-label','Свободный прокси');
 option(select,'','Сначала найдите свободные прокси');
 select.disabled=true;
 start.addEventListener('change',()=>{select.replaceChildren();option(select,'','Обновите список свободных прокси');select.disabled=true;});
 const hint=el('p','Выберите время, затем проверьте наличие свободного прокси.','muted');
 const find=button('Найти свободные прокси',async()=>{
  if(!start.value)throw Error('Укажите начало размещения.');
  const requested=new Date(start.value);
  if(Number.isNaN(requested.getTime()))throw Error('Неверные дата или время.');
  const data=await api('/campaigns/'+c.id+'/slot-options?'+new URLSearchParams({start_at:requested.toISOString()}));
  select.replaceChildren();option(select,'','Выберите прокси');
  data.nodes.forEach(n=>option(select,n.id,n.title));
  select.disabled=!data.nodes.length;
  hint.textContent=data.nodes.length?'Найдено свободных прокси: '+data.nodes.length+'. Доступность проверится ещё раз при брони.':'На это время свободных прокси нет.';
 });
 const book=button('Забронировать на 15 минут',async()=>{
  if(!select.value||!start.value)throw Error('Сначала выберите свободный прокси и время.');
  await api('/campaigns/'+c.id+'/slot-hold',{method:'POST',body:{
   proxy_node_id:select.value,start_at:new Date(start.value).toISOString()
  }});
  await renderHoldPanel(c,panel);await loadAvailability();notice('Слот временно забронирован. Оплата ещё не подтверждена.',true);
 });
 panel.append(start,find,select,hint,book);
}
function addHoldAction(c,box,actions){
 if(!holdEligibility(c))return;
 actions.append(button('Бронь на 15 минут',async()=>{
  let panel=box.querySelector('.slot-hold-panel');
  if(panel){panel.remove();return;}
  panel=el('section',undefined,'slot-hold-panel');
  box.append(panel);await renderHoldPanel(c,panel);
 }));
}

async function downloadCampaignCsv(c){
 const result=await fetch('/api/v1/campaigns/'+c.id+'/report.csv',{
  headers:{Authorization:'Bearer '+token}
 });
 if(!result.ok){
  if(result.status===401)throw Error('Войдите повторно для скачивания отчёта.');
  if(result.status===404)throw Error('Кампания не найдена или недоступна.');
  throw Error('Не удалось скачать отчёт. Попробуйте снова.');
 }
 const url=URL.createObjectURL(await result.blob());
 const link=el('a');
 link.href=url;link.download='mtproxy-pushok-report-'+c.id+'.csv';
 link.click();
 setTimeout(()=>URL.revokeObjectURL(url),60000);
}

function showCampaignReport(c, box){
 const old=box.querySelector('.campaign-report');
 if(old){old.remove();return;}
 const panel=el('section',undefined,'campaign-report');
 panel.setAttribute('aria-label','Отчёт о рекламной кампании');
 box.append(panel);
 panel.append(el('p','Загружаем подтверждённые сведения…','muted'));
 return api('/campaigns/'+c.id+'/report').then(data=>{
  panel.replaceChildren();
  const h=el('h4','Отчёт о размещении · '+data.channel.title);
  panel.append(h);
  const pairs=[
   ['Тариф',data.tariff.name+' · '+money(data.tariff.price_kopecks)],
   ['Модерация',labels[data.moderation_status]||data.moderation_status],
   ['Оплата',labels[data.payment_status]||data.payment_status],
   ['Размещение',labels[data.placement_status]||data.placement_status],
   ['Начало',date(data.scheduled_start_at)],
   ['Окончание',date(data.scheduled_end_at)]
  ];
  const grid=el('div',undefined,'report-grid');
  for(const [key,value] of pairs){
   const row=el('div',undefined,'report-cell');
   row.append(el('span',key,'muted'),el('strong',value));
   grid.append(row);
  }
  panel.append(grid);
  panel.append(el('h4','Проверки оператора'));
  const checks=data.operator_observations;
  panel.append(el('p','Показ виден: '+checks.visible+' · Показ не найден: '+checks.not_visible));
  panel.append(el('p','Последний результат: '+
   (checks.latest_result==='visible'?'оператор сообщил, что показ виден':checks.latest_result==='not_visible'?
    'оператор сообщил, что показ не найден':'нет отчёта оператора')+
   (checks.latest_observed_at?' · '+date(checks.latest_observed_at):'')));
  panel.append(el('p','Приватных скриншотов в журнале: '+checks.private_screenshot_count+
   '. Файлы доступны только операторам.','muted'));
  panel.append(el('h4','История действий'));
  const eventNames={campaign_submitted:'Заявка отправлена',campaign_moderated:'Модерация',
   payment_proof_submitted:'Чек отправлен',payment_reviewed:'Проверка оплаты',
   placement_scheduled:'Время назначено',placement_activated_manually:'Оператор активировал',
   placement_completed_manually:'Оператор завершил'};
  if(data.timeline.length){
   const history=el('ol',undefined,'report-history');
   for(const event of data.timeline)history.append(el('li',(eventNames[event.event]||'Событие')+
      ' · '+date(event.at)));
   panel.append(history);
  }else panel.append(el('p','История пока пуста.','muted'));
  panel.append(el('p','Проверки выполнены вручную. Количество показов Telegram API не подтверждено.','muted'));
  panel.append(button('Скачать CSV',()=>downloadCampaignCsv(c)));
  panel.append(button('Закрыть отчёт',()=>panel.remove()));
 }).catch(error=>{panel.remove();throw error;});
}

function card(c,op){
 const box=el('article',undefined,'card'),head=el('div',undefined,'cardhead');
 head.append(el('h3',(c.channel_title||channels.find(x=>x.id===c.channel_id)?.title||'Заявка')+' · '+c.id.slice(0,8)),el('span',labels[c.placement_status]||c.placement_status,'badge badge--'+c.placement_status));box.append(head);
 if(c.channel_url){try{const u=new URL(c.channel_url);if(u.protocol==='https:'&&['t.me','telegram.me'].includes(u.hostname)){const link=el('a','Открыть канал');link.href=u.href;link.target='_blank';link.rel='noopener noreferrer';box.append(link);}}catch{}}
 const meta=el('div',undefined,'meta');
 [labels[c.moderation_status],labels[c.payment_status],c.tariff_name_snapshot+' · '+money(c.price_kopecks_snapshot),'Желаемое начало: '+date(c.requested_start_at),'Назначено: '+date(c.scheduled_start_at)].forEach(t=>meta.append(el('span',t)));box.append(meta);
 if(c.moderator_comment)box.append(el('p','Модерация: '+c.moderator_comment));
 if(c.payment_review_comment)box.append(el('p','Проверка оплаты: '+c.payment_review_comment));
 const actions=el('div',undefined,'actions');box.append(actions);
 const change=async(path,body)=>{await api('/operator/campaigns/'+c.id+'/'+path,{method:'POST',body});notice('Изменения сохранены.',true);await refresh();};
 if(!op){
  actions.append(button('Отчёт по кампании',()=>showCampaignReport(c,box)));
  actions.append(button('Выгрузить CSV',()=>downloadCampaignCsv(c)));
  if(['scheduled','active','completed'].includes(c.placement_status)){
   actions.append(button('Статус проверки показа',async()=>{
    const data=await api('/campaigns/'+c.id+'/display-observation');
    const panel=el('section',undefined,'observation-panel');
    const title=data.result==='visible'?'Оператор сообщил: показ виден':
     data.result==='not_visible'?'Оператор сообщил: показ не найден':'Отчёт о показе ещё не получен';
    panel.append(el('h4',title));
    if(data.observed_at)panel.append(el('p','Время наблюдения: '+date(data.observed_at)));
    panel.append(el('p','Это ручное наблюдение оператора, не подтверждение Telegram API.','muted'));
    panel.append(button('Закрыть',()=>panel.remove()));box.append(panel);
   }));
  }
  addHoldAction(c,box,actions);
  if(c.moderation_status==='approved'&&['awaiting_payment','rejected','proof_submitted','verified'].includes(c.payment_status)){
   actions.append(button('Реквизиты и сумма',async()=>{
    if(box.querySelector('.payment-instructions'))return;
    const data=await api('/campaigns/'+c.id+'/payment-instructions');
    const panel=el('section',undefined,'payment-instructions');panel.style.overflowWrap='anywhere';
    panel.append(el('h3','К оплате: '+new Intl.NumberFormat('ru-RU',{style:'currency',currency:data.currency}).format(data.amount_kopecks/100)));
    panel.append(el('p',data.instructions));
    const reference=el('p','Комментарий к переводу: '+data.reference);reference.style.overflowWrap='anywhere';panel.append(reference);
    panel.append(el('p','После перевода загрузите чек. Оператор проверит поступление вручную.','muted'),button('Скрыть реквизиты',async()=>panel.remove()));box.append(panel);
   }));
  }
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
   const start=el('input');start.type='datetime-local';if(c.requested_start_at){const d=new Date(c.requested_start_at);start.value=localDay(d)+'T'+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}start.setAttribute('aria-label','Начало размещения');
   actions.append(select,start,button('Назначить слот',async()=>{if(!select.value||!start.value)throw Error('Выберите узел и время начала.');await change('schedule',{proxy_node_id:select.value,start_at:new Date(start.value).toISOString()});}));
  }
  if(c.placement_status==='scheduled'){
   const screenshot=el('input');screenshot.type='file';
   screenshot.accept='image/png,image/jpeg';
   screenshot.setAttribute('aria-label','Скриншот ручной проверки (необязательно)');
   actions.append(screenshot);
   for(const [result,label] of [['visible','Показ виден — записать'],['not_visible','Показ не обнаружен']]){
    actions.append(button(label,async()=>{
     const note=prompt('Опишите проверку в отдельном аккаунте Telegram, не подписанном на канал (не менее 12 символов):');
     if(note===null)return;if(note.trim().length<12)throw Error('Опишите наблюдение подробнее (не менее 12 символов).');
     const report=await api('/operator/campaigns/'+c.id+'/display-observations',{method:'POST',body:{
      result,method:'separate_unsubscribed_client',observed_at:new Date().toISOString(),note:note.trim()
     }});
     if(screenshot.files[0]){
      const form=new FormData();form.append('file',screenshot.files[0]);
      try{await api('/operator/campaigns/'+c.id+'/display-observations/'+report.id+'/evidence',{method:'POST',body:form});}
      catch(e){await refresh();notice('Отчёт записан, но скриншот не прикреплён: '+e.message);return;}
     }
     await refresh();notice(result==='visible'?'Отчёт оператора сохранён. Проверьте статус перед активацией.':'Отрицательное наблюдение записано. Активация заблокирована.',true);
    }));
   }
   actions.append(button('Активировать после проверки',async()=>{
    if(confirm('Вы записали положительное наблюдение за последние 10 минут и вручную включили тег?'))await change('activate');
   }));
  }
  if(['scheduled','active','completed'].includes(c.placement_status))
   actions.append(button('Журнал проверок',async()=>{
    const rows=await api('/operator/campaigns/'+c.id+'/display-observations');
    const panel=el('section',undefined,'observation-panel');
    panel.append(el('h4','Отчёты оператора (не автоматическая проверка Telegram)'));
    if(!rows.length)panel.append(el('p','Проверок пока нет.','muted'));
    for(const report of rows){
     panel.append(el('p',(report.result==='visible'?'Показ виден':'Показ не найден')+
      ' · '+date(report.observed_at)+' · '+report.note));
     if(report.evidence_id){
      panel.append(button('Скачать скриншот №'+report.id,async()=>{
       const path='/api/v1/operator/campaigns/'+c.id+'/display-observations/'+report.id+'/evidence';
       const response=await fetch(path,{headers:{Authorization:'Bearer '+token}});
       if(!response.ok)throw Error('Не удалось скачать скриншот.');
       const url=URL.createObjectURL(await response.blob()),a=el('a');
       a.href=url;
       a.download='sponsor-evidence-'+report.id+(response.headers.get('content-type')?.includes('jpeg')?'.jpg':'.png');
       a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
      }));
     }else if(report.observer_user_id===me.id){
      const input=el('input');input.type='file';input.accept='image/png,image/jpeg';
      input.setAttribute('aria-label','Добавить скриншот к отчёту '+report.id);
      panel.append(input,button('Прикрепить скриншот №'+report.id,async()=>{
       if(!input.files[0])throw Error('Выберите PNG или JPEG.');
       const form=new FormData();form.append('file',input.files[0]);
       await api('/operator/campaigns/'+c.id+'/display-observations/'+report.id+'/evidence',{method:'POST',body:form});
       panel.remove();notice('Скриншот сохранён, отчёт неизменен.',true);
      }));
     }
    }
    panel.append(button('Закрыть',()=>panel.remove()));box.append(panel);
   }));
  if(c.placement_status==='active')actions.append(button('Тег выключен — завершить',async()=>{
   if(!confirm('Вы вручную выключили спонсорский тег и проверили в отдельном Telegram-клиенте, не подписанном на канал, что спонсор отсутствует?'))return;
   await change('complete',{tag_removed_confirmed:true,verification_method:'separate_unsubscribed_client'});
  }));
 }
 return box;
}
const engineTitles={official_mtproxy:'MTProxy Classic',official_docker:'MTProxy Docker (тест)',telemt:'Telemt (адаптер)'};
function engineRequest(){
 const data=Object.fromEntries(new FormData($('node-form')));
 const policy={deployment:data.deployment,environment:data.environment,engine_mode:data.engine_mode};
 if(policy.engine_mode==='manual')policy.preferred_engine=data.preferred_engine||null;
 return policy;
}
$('engine-mode').addEventListener('change',()=>{
 const manual=$('engine-mode').value==='manual';
 $('preferred-engine-row').hidden=!manual;
 $('preferred-engine').required=manual;
});
$('engine-preview').addEventListener('click',()=>run(async()=>{
 const plan=await api('/admin/proxy-engines/recommend',{method:'POST',body:engineRequest()});
 $('engine-advice').textContent='План: '+plan.title+' · '+plan.availability+
  '. Не установлено и не проверено. Смена работающего прокси не выполняется.';
}));
async function renderNetworkPlan(){
 if(me?.role!=='admin')return;
 const target=$('network-plan');target.replaceChildren(el('p','Читаем состояние расписания…','muted'));
 try{
  const data=await api('/admin/network/promotion-plan');
  target.replaceChildren(el('p','Узлов: '+data.node_count+' · свой канал: '+data.summary.own_channel_planned+
   ' · платные слоты: '+data.summary.paid_channel_planned+
   ' · без тегов: '+data.summary.tag_registration_required+
   ' · конфликтов: '+data.summary.overlap_conflict+'.','muted'));
  const captions={own_channel_planned:'Свой канал (план)',paid_channel_planned:'Коммерческий слот (план)',
    paid_placement_not_approved:'Платёж/модерация требуют проверки',
    tag_registration_required:'Тег не зарегистрирован',node_paused:'Узел приостановлен',
    overlap_conflict:'Конфликт расписания'};
  const list=el('ul',undefined,'core-checklist');
  for(const n of data.nodes){
   list.append(el('li',n.name+' · '+(captions[n.state]||n.state)+
    (n.target_channel_url?' · '+n.target_channel_url:'')));
  }
  target.append(list,el('p','Ни один спонсор не переключён программно; наличие тега и записи не подтверждает реальные показы.','muted'));
 }catch(e){target.replaceChildren(el('p','План сети недоступен: '+e.message,'muted'));}
}
$('network-refresh').addEventListener('click',()=>run(renderNetworkPlan));
async function renderLaunchReadiness(){
 if(me?.role!=='admin')return;
 const target=$('launch-readiness');
 target.replaceChildren(el('p','Загрузка сведений из БД…','muted'));
 try{
  const data=await api('/admin/launch-readiness');
  target.replaceChildren();
  const s=data.statistics;
  target.append(el('p','Узлы: '+s.nodes_total+' (активных в реестре: '+s.nodes_active+
   ', с зарегистрированным тегом: '+s.nodes_with_registered_tag+'). Заявки: '+
   s.campaigns_total+', на модерации: '+s.moderation_pending+
   ', чеки на проверке: '+s.payments_pending+'.','muted'));
  target.append(el('p','Оплата подтверждена оператором: '+s.payments_verified+
   ' · Размещений запланировано: '+s.placements_scheduled+
   ' · Вручную отмечено активными: '+s.placements_active+
   ' · Завершено: '+s.placements_completed+
   ' · Ручных проверок: '+s.manual_observations+'.','muted'));
  const list=el('ul',undefined,'core-checklist');
  for(const step of data.checks){
   const state=step.status==='data_present'?'Данные в реестре: ':step.status==='missing'?
    'Нет данных в реестре: ':'Пока не проверено: ';
   list.append(el('li',state+step.title+(step.status==='data_present'?' ('+step.count+')':''),
     step.status==='not_verified'?'core-check-pending':'core-check-ci'));
  }
  target.append(list,el('p','Готовность к публичному запуску: НЕ ПОДТВЕРЖДЕНА. Наличие узла или тега в БД не означает, что прокси работает.','muted'));
 }catch(e){
  target.replaceChildren(el('p','Не удалось собрать сводку готовности: '+e.message,'muted'));
 }
}

async function renderCoreReadiness(){
 if(me?.role!=='admin')return;
 try{
  const report=await api('/admin/pushok-core/readiness');
  const message=(report.source_pinned?'Исходный код закреплён. ':'Источник не подтверждён. ')+
   'Проверка бинарника на этом сервере: '+(report.local_build_verified===true?'подтверждена':'не проводилась')+
   '. Работа MTProxy: '+(report.runtime_status==='not_checked'?'не проверялась':report.runtime_status)+
   '. Подключение Telegram и показ спонсора не подтверждены. Никаких действий на сервере.';
  $('core-readiness').textContent=message;
  $('core-readiness').append(button('Проверить скриншоты',async()=>{
   const report=await api('/admin/placement-evidence/integrity?limit=50');
   const problems=report.issues.map(issue=>'#'+issue.observation_id+': '+issue.issue).join('; ');
   notice('Проверено: '+report.checked+'; целых файлов: '+report.healthy+
    (problems?'; ошибки: '+problems:'; ошибок в проверенной выборке нет.')+
    ' Это не подтверждение показа Telegram.',!report.issues.length);
  }));
  const plan=await api('/admin/pushok-core/diagnostic-plan');
  $('core-test-plan').replaceChildren(...plan.steps.map(step =>
   el('li',(step.verification==='ci_only'?'Проверка в CI: ':'Ещё не подтверждено: ')+step.title,
      step.verification==='ci_only'?'core-check-ci':'core-check-pending')));

 }catch{
  $('core-readiness').textContent='Не удалось получить состояние подготовки ядра. Это не означает, что прокси работает.';
  $('core-test-plan').replaceChildren();
 }
}
function renderNodes(){
 $('node-admin').hidden=me?.role!=='admin';
 $('nodes-list').replaceChildren(...nodes.map(n=>{
  const box=el('div',undefined,'card');
  box.append(el('h3',n.name),el('p',n.host+':'+n.port+' · '+(n.status==='active'?'Доступен для расписания':'На паузе')));
  box.append(el('p','План движка: '+(engineTitles[n.target_engine]||'Не выбран (старый узел)')+
    ' · '+(n.engine_selection_mode||'legacy')+'. Установка и доступность не проверены.','muted'));
  box.append(button('Изменить план движка',async()=>{
   if(!confirm('Изменить только план движка? Реальный прокси не будет переключён.'))return;
   await api('/admin/proxy-nodes/'+n.id+'/engine-plan',{method:'PATCH',body:engineRequest()});
   await refresh();notice('План сохранён. Проверка и установка выполняются отдельно.',true);
  }));
  box.append(button(n.status==='active'?'Поставить на паузу':'Возобновить',async()=>{
   await api('/admin/proxy-nodes/'+n.id+'/status',{method:'PATCH',body:{status:n.status==='active'?'paused':'active'}});
   await refresh();notice('Состояние узла сохранено.',true);
  }));return box;
 }));
}
$('node-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{
 const data=Object.fromEntries(new FormData(e.target));data.port=Number(data.port);
 if(data.engine_mode==='auto')delete data.preferred_engine;
 await api('/admin/proxy-nodes',{method:'POST',body:data});e.target.reset();await refresh();notice('Узел добавлен в реестр.',true);
},e.submitter);});
async function refresh(){
 if(!token)return;
 const own=await api('/campaigns');$('campaigns').replaceChildren(...own.map(c=>card(c,false)));$('campaign-count').textContent=own.length+' заявок';
 if(!own.length)$('campaigns').append(el('div','Заявок пока нет. Добавьте канал и создайте первое размещение.','empty'));
 if(me&&['operator','admin'].includes(me.role)){
  nodes=await api('/operator/proxy-nodes');renderNodes();if(me.role==='admin'){await renderCoreReadiness();await renderLaunchReadiness();await renderNetworkPlan();}
  const queue=await api('/operator/campaigns'+($('queue-filter').value?'?'+$('queue-filter').value:''));
  $('queue-count').textContent=queue.length+' заявок';
  $('queue').replaceChildren(...queue.map(c=>card(c,true)));if(!queue.length)$('queue').append(el('div','В этой очереди пока нет заявок.','empty'));
 }
}
async function enter(){
 me=await api('/me');channels=await api('/channels');tariffs=await api('/tariffs');
 const isOperator=['operator','admin'].includes(me.role);nodes=isOperator?await api('/operator/proxy-nodes'):[];
 $('identity').textContent=me.first_name||'Рекламодатель';$('logout').hidden=false;$('login').hidden=true;$('workspace').hidden=false;$('operator-tab').hidden=!isOperator;
 $('channel-select').replaceChildren();option($('channel-select'),'','Выберите канал');channels.forEach(c=>option($('channel-select'),c.id,c.title));
 $('tariff-select').replaceChildren();tariffs.forEach(t=>option($('tariff-select'),t.id,t.name+' · '+money(t.price_kopecks)));
 await refresh();await loadAvailability();
}

function localDay(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
const calendarZone=Intl.DateTimeFormat().resolvedOptions().timeZone||'Europe/Moscow';
let calendarRequest=0;
function clearRequestedDate(){$('requested-start').value='';$('calendar-selection').textContent='Дата пока не выбрана.';for(const b of $('availability-days').children)b.setAttribute('aria-pressed','false');}
async function loadAvailability(){
 const request=++calendarRequest;clearRequestedDate();
 if(!$('tariff-select').value)return;
 $('availability-days').replaceChildren(el('p','Загрузка свободных дат…','muted'));
 const params=new URLSearchParams({tariff_id:$('tariff-select').value,date_from:$('calendar-from').value,days:'14',timezone_name:calendarZone,start_time:$('calendar-time').value||'12:00'});
 try{
  const data=await api('/availability?'+params);
  if(request!==calendarRequest)return;
  $('availability-days').replaceChildren(...data.days.map(day=>{
   const calendarLabels={available:'Свободно: '+day.available_nodes,occupied:'Нет мест',past:'Время прошло',invalid_local_time:'Нет такого времени',ambiguous_local_time:'Неоднозначное время'};
   const b=button(new Date(day.start_at).toLocaleDateString('ru-RU',{day:'numeric',month:'short'})+' · '+(calendarLabels[day.status]||'Недоступно'),async()=>{
    clearRequestedDate();b.setAttribute('aria-pressed','true');$('requested-start').value=day.start_at;$('calendar-selection').textContent='Желаемое начало: '+date(day.start_at);
   });b.disabled=day.status!=='available'||day.available_nodes<1;b.setAttribute('aria-pressed','false');return b;
  }));
 }catch(e){if(request===calendarRequest){$('availability-days').replaceChildren(el('p','Не удалось получить доступные даты.','muted'));throw e;}}
}
$('calendar-zone').textContent='Часовой пояс: '+calendarZone+'. Длительность — по выбранному тарифу.';
$('calendar-from').value=localDay(new Date());$('calendar-from').min=localDay(new Date());const calendarLastDay=new Date();calendarLastDay.setDate(calendarLastDay.getDate()+77);$('calendar-from').max=localDay(calendarLastDay);
$('calendar-clear').addEventListener('click',clearRequestedDate);
for(const id of ['calendar-from','calendar-time','tariff-select'])$(id).addEventListener('change',()=>run(loadAvailability));

window.onTelegramAuth=data=>run(async()=>{const session=await api('/auth/telegram',{method:'POST',body:data});token=session.access_token;sessionStorage.setItem('pushok-session',token);await enter();notice('Вход выполнен.',true);});
$('channel-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{await api('/channels',{method:'POST',body:Object.fromEntries(new FormData(e.target))});e.target.reset();await enter();notice('Канал добавлен.',true);},e.submitter);});
$('campaign-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{const payload=Object.fromEntries(new FormData(e.target));if(!payload.requested_start_at)delete payload.requested_start_at;await api('/campaigns',{method:'POST',body:payload});await refresh();notice('Черновик создан. Отправьте его на модерацию.',true);},e.submitter);});
$('logout').addEventListener('click',()=>run(async()=>{await api('/auth/logout',{method:'POST'});sessionStorage.removeItem('pushok-session');location.reload();}));
$('refresh').addEventListener('click',()=>run(async()=>{await refresh();await loadAvailability();}));
$('queue-filter').addEventListener('change',()=>run(refresh));
$('advertiser-tab').addEventListener('click',()=>operatorView(false));
$('operator-tab').addEventListener('click',()=>operatorView(true));
run(async()=>{const config=await api('/public-config');if(config.telegram_bot_username&&/^[A-Za-z0-9_]+$/.test(config.telegram_bot_username)){
 const script=document.createElement('script');script.src='https://telegram.org/js/telegram-widget.js?22';script.async=true;script.dataset.telegramLogin=config.telegram_bot_username;script.dataset.size='large';script.dataset.onauth='onTelegramAuth(user)';$('telegram-login').append(script);$('login-note').textContent='Вход подтверждается в Telegram.';
}else $('login-note').textContent='Администратору нужно настроить Telegram-бота для входа.';
 if(token)await enter();
});
