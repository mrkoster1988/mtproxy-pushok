'use strict';
const $=id=>document.getElementById(id);
const miniAppMode=document.body.dataset.miniapp==='true';
const sessionKey=miniAppMode?'pushok-miniapp-session':'pushok-session';
let token=miniAppMode?'':sessionStorage.getItem(sessionKey)||'',me=null,channels=[],tariffs=[],nodes=[],ownCampaigns=[],operatorCampaigns=[];
let authGeneration=0,enterGeneration=0;
const labels={draft:'Черновик',pending:'На модерации',approved:'Одобрено',rejected:'Отклонено',changes_requested:'Нужны исправления',not_required:'Оплата после одобрения',awaiting_payment:'Ожидает оплаты',proof_submitted:'Чек на проверке',verified:'Оплачено',refunded:'Возврат',not_scheduled:'Слот не назначен',scheduled:'Запланировано',active:'Размещение активно',completed:'Завершено',cancelled:'Отменено'};
const errors={operator_decision_changed:'Решение уже изменилось или кампания перешла к оплате или размещению. Обновите список.',sbp_connection_invalid:"Проверьте Shop ID, домен и ключ.",sbp_connection_has_payments:"По магазину уже есть счета. Смена магазина, домена или режима требует переноса и сверки платежей.",sbp_connection_check_failed:"ЮKassa не подтвердила подключение. Проверьте ключ и повторите.",sbp_connection_shop_not_ready:"Проверьте режим магазина, доступность СБП и статус подключения в ЮKassa.",team_user_must_login:'Пользователь должен сначала войти в кабинет через Telegram.',team_cannot_remove_self:'Нельзя снять права администратора с самого себя.',team_last_admin:'Нельзя удалить последнего администратора.',evidence_integrity_failed:'Скриншот повреждён или изменён после загрузки.',evidence_invalid_metadata:'Данные файла некорректны.',evidence_file_missing:'Файл не найден в хранилище.',invalid_integrity_limit:'Проверка ограничена 1–100 файлами.',evidence_empty:'Выберите изображение.',evidence_too_large:'Скриншот должен быть меньше 5 МБ.',evidence_corrupt_image:'Не удалось прочитать изображение.',unsupported_evidence_format:'Поддерживаются только PNG и JPEG.',evidence_format_mismatch:'Тип файла не соответствует изображению.',evidence_dimensions_invalid:'Изображение слишком велико.',evidence_already_attached:'У этого отчёта уже есть скриншот.',evidence_observation_unavailable:'Добавить файл можно только к собственному отчёту за последние сутки.',evidence_storage_unavailable:'Хранилище скриншотов временно недоступно.',placement_not_ready_for_observation:'Проверять показ можно только в запланированный период после подтверждённой оплаты.',observation_timezone_required:'Для проверки нужен часовой пояс.',observation_outside_placement_window:'Время наблюдения должно быть внутри рекламного периода.',observation_not_recent:'Наблюдение должно быть выполнено не более 10 минут назад.',observation_note_too_short:'Укажите подробности проверки (от 12 символов).',active_slot_hold_not_found:'Активной брони нет.',slot_start_requires_timezone:'Укажите время с часовым поясом.',slot_start_out_of_range:'Выберите время в пределах ближайших 90 дней.',campaign_not_holdable:'Бронирование доступно только после модерации и до подтверждения оплаты.',slot_held_by_another_campaign:'Место занято другой бронью.',slot_hold_conflict:'Бронь уже существует. Обновите состояние.',invalid_calendar_range:'Выберите период в пределах ближайших 90 дней, не больше 31 дня.',invalid_calendar_timezone:'Часовой пояс не поддерживается.',active_tariff_not_found:'Тариф больше недоступен. Обновите страницу.',payment_instructions_not_configured:'Реквизиты ещё не настроены. Дождитесь инструкции администратора перед оплатой.',campaign_not_editable:'Заявку сейчас нельзя редактировать или отправить повторно.',channel_in_review_or_approved:'Этот канал связан с заявкой на проверке или одобренным размещением. Его изменение заблокировано.',admin_tariff_required:'Тариф доступен только администратору.',admin_tariff_must_be_free:'Админский тариф всегда бесплатный: 0 ₽, без Stars.',admin_role_required:'Нужны права администратора.',owner_role_required:'Ключи и подключения доступны только разработчику.',team_owner_protected:'Права разработчика защищены.',invalid_proxy_host:'Укажите домен или IP без протокола и порта.',proxy_node_already_exists:'Этот адрес и порт уже добавлены.',proxy_node_has_placements:'На узле есть запланированное или активное размещение. Сначала завершите его.',authentication_required:'Войдите через Telegram.',session_revoked_or_expired:'Сессия завершена. Войдите снова.',telegram_login_expired:'Данные входа устарели. Повторите вход.',telegram_login_replayed:'Повторите вход через Telegram.',telegram_login_invalid_signature:'Telegram не подтвердил вход.',database_unavailable:'База данных временно недоступна.',invalid_telegram_channel_url:'Укажите ссылку вида https://t.me/channel.',channel_title_required:'Введите название канала.',channel_already_added:'Этот канал уже добавлен.',payment_proof_too_large:'Файл должен быть меньше 5 МБ.',unsupported_payment_proof_format:'Загрузите JPEG, PNG или PDF.',payment_proof_content_type_mismatch:'Тип файла не соответствует содержимому.',payment_proof_storage_unavailable:'Хранилище чеков временно недоступно.',proxy_node_slot_unavailable:'Этот слот уже занят. Выберите другое время или узел.',placement_not_in_active_window:'Активация доступна только в назначенное время.',campaign_not_ready_for_placement:'Для размещения нужны одобрение канала и подтверждённая оплата.',operator_role_required:'Нужны права оператора.',payment_proof_not_expected:'Заявка сейчас не ожидает чек.',campaign_not_draft:'Эта заявка уже отправлена.',campaign_not_pending:'Заявка уже обработана.',payment_proof_not_pending_review:'Оплата уже обработана.',channel_or_active_tariff_not_found:'Выберите свой канал и действующий тариф.',schedule_start_must_be_future:'Выберите время в будущем.'};
Object.assign(errors,{
 support_bot_token_invalid:'Проверьте формат токена из BotFather.',
 support_bot_wrong_name:'Telegram вернул другой @username. Проверьте ник и токен выбранного бота.',
 support_bot_cabinet:'Выберите отдельного бота поддержки. Этот бот обслуживает кабинет.',
 support_bot_bad_image:'Выберите JPG, PNG или WebP обычных пропорций.',
 support_bot_settings_changed:'Настройки изменились. Откройте раздел заново перед сохранением.',
 support_bot_has_webhook:'У бота уже настроен webhook. Сначала отключите прежнюю интеграцию.',
 support_bot_unavailable:'Telegram не подтвердил подключение. Проверьте токен и повторите попытку.',
 promotion_binding_busy:'Автопривязка ещё выполняется или ожидает подтверждения Telegram. Ручное сохранение пока недоступно. Обновите состояние подключения после завершения проверки.',
 promotion_binding_scheduler_busy:'Планировщик сейчас обновляет размещения. Повторите сохранение через несколько секунд.',
 promotion_tag_already_bound:'Этот тег уже сохранён для другого канала или узла. Проверьте выбранный канал и его тег в @MTProxyBot.',
 telegram_binding_confirmation_required:'Подтвердите, что тег связан именно с выбранным каналом в Telegram.',
 classic_node_required:'Выберите действующий узел с классическим MTProxy.',
 channel_not_found:'Канал не найден или находится в архиве. Обновите список и выберите действующий канал.'
});
let noticeTimer;
function notice(message,ok=false,{actions=[],persistent=false}={}){
 clearTimeout(noticeTimer);const box=$('notice');
 const close=button('×',()=>{box.hidden=true;clearTimeout(noticeTimer);});close.setAttribute('aria-label','Закрыть уведомление');
 box.replaceChildren(el('span',message,'notice-message'),close);
 if(actions.length){const buttons=el('div',undefined,'notice-actions');buttons.append(...actions);box.append(buttons);}
 box.className=ok?'ok':'';box.hidden=false;box.dataset.route=location.hash;
 if(!persistent)noticeTimer=setTimeout(()=>{box.hidden=true;},5000);
}
function hasDeveloperAccess(user){return Boolean(user?.is_developer||user?.is_owner);}
function el(tag,text,cls){const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(cls)x.className=cls;return x;}
function appendFormattedText(node,text){
  text.split(/(\*\*[^*\n]+\*\*|__[^_\n]+__)/g).forEach((part,index)=>{
    if(index%2)node.append(el(part.startsWith('**')?'strong':'em',part.slice(2,-2)));
    else node.append(document.createTextNode(part));
  });
}
function button(text,fn){const b=el('button',text);b.type='button';b.addEventListener('click',()=>run(fn,b));return b;}
function clearCabinetSession({preserveUnboundFlow=false,expired=false,clearWorkspace=true}={}){
 const generation=++authGeneration;++enterGeneration;++calendarRequest;
 token='';me=null;sessionStorage.removeItem(sessionKey);
 window.pushokFlowAccount?.reset({preserveUnbound:preserveUnboundFlow});
 $('workspace').hidden=true;$('login').hidden=miniAppMode;$('logout').hidden=true;
 if(clearWorkspace){
  window.pushokSBP?.reset();window.pushokChannelSubscriptionSettings?.reset();window.pushokAI?.reset();window.pushokProvisioning?.reset();window.pushokGateway?.reset();
  window.cabinet?.logout({preserveUnboundFlow});
 }
 if(expired&&miniAppMode)window.pushokMiniApp.expired({preserveUnboundFlow});
 return generation;
}
async function api(path,{method='GET',body,signal,session=token}={}){
 const headers={},requestToken=session,requestGeneration=authGeneration;if(requestToken)headers.Authorization='Bearer '+requestToken;
 if(body&&!(body instanceof FormData))headers['Content-Type']='application/json';
 const loginRead=method==='GET' && ['/me','/miniapp/config','/public-config','/channels','/me/tariffs','/operator/proxy-nodes','/campaigns','/operator/campaigns','/operator/campaigns?archived=true'].includes(path);
 const controller=!signal && (loginRead || path.startsWith('/auth/telegram')) ? new AbortController() : null;
 const timer=controller && setTimeout(()=>controller.abort(),15000);
 try {
  let r;try{r=await fetch('/api/v1'+path,{method,headers,signal:signal||controller?.signal,cache:'no-store',body:body instanceof FormData?body:body?JSON.stringify(body):undefined});}catch{throw Error('Нет связи с сервером. Проверьте соединение и повторите действие.');}
  if(!r.ok){let d;try{const data=await r.json();d=data?.detail??data?.error;}catch{d='';}if(r.status===401&&requestToken&&token===requestToken&&requestGeneration===authGeneration){clearCabinetSession({preserveUnboundFlow:window.pushokFlowAccount?.unbound()===true,expired:true});}
   const error=Error(errors[d]||(r.status===422?'Проверьте заполненные поля.':typeof d==='string'&&d.endsWith('_not_configured')?'Вход ещё не настроен администратором.':'Не удалось выполнить действие. Попробуйте снова.'));error.status=r.status;error.code=typeof d==='string'?d:'';if(d?.code==='vpn_order_issuing')error.orderId=d.order_id;throw error;}
  return r.status===204?null:await r.json();
 } finally { if(controller)clearTimeout(timer); }
}
async function run(fn,b){if(b)b.disabled=true;try{await fn();}catch(e){notice(e.message);}finally{if(b)b.disabled=false;}}
function option(select,value,text){const o=el('option',text);o.value=value;select.append(o);}
function money(n){return new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',minimumFractionDigits:0,maximumFractionDigits:2}).format(n/100);}
function date(v){return v?new Date(v).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}):'Не назначено';}
function showWorkspace(view){
 if(window.cabinet){window.cabinet.go(view==='settings'?'settings':view==='operator'?'moderation':'campaigns');return;}
 const admin=me?.role==='admin';
 const selected=(view==='settings'&&admin)?'settings':view==='operator'?'operator':'advertiser';
 for(const part of ['advertiser','operator','settings']){
  $(part).hidden=part!==selected;
  const tab=$(part+'-tab');tab.classList.toggle('selected',part===selected);
  tab.setAttribute('aria-pressed',String(part===selected));
 }
}
function operatorView(on){showWorkspace(on?'operator':'advertiser');}
function showSettingsPane(pane){
 $('notice').hidden=true;
 const selected=['payments','tariffs','system'].includes(pane)?pane:'payments';
 for(const name of ['payments','tariffs','system']){
  $('settings-'+name).hidden=name!==selected;
 }
}
async function renderLoadingSettings(){
 if(me?.role!=='admin')return;
 const fields=$('loading-title-fields');fields.disabled=true;
 try{
  const config=await api('/admin/miniapp/loading');
  $('loading-title-input').value=config.loading_title;
  $('loading-title-preview').textContent=config.loading_title;
  fields.disabled=false;
 }catch(e){$('loading-title-status').textContent='Не удалось загрузить настройки. Откройте раздел повторно.';throw e;}
}
$('loading-title-input').addEventListener('input',()=>{
 $('loading-title-preview').textContent=$('loading-title-input').value.trim()||'Пушок готовит кабинет…';
});
$('loading-title-form').addEventListener('submit',async event=>{
 event.preventDefault();const fields=$('loading-title-fields'),status=$('loading-title-status');
 if(fields.disabled)return;
 fields.disabled=true;status.textContent='Сохраняем…';
 try{
  const config=await api('/admin/miniapp/loading',{method:'PUT',body:{loading_title:$('loading-title-input').value.trim()}});
  $('loading-title-input').value=config.loading_title;$('loading-title-preview').textContent=config.loading_title;
  status.textContent='Сохранено. Текст появится при следующем открытии Mini App.';
 }catch{status.textContent='Не удалось сохранить. Повторите попытку.';}
 finally{fields.disabled=false;}
});
let paymentEditorSession=null;
const paymentMethods=['stars','sbp'];
function paymentDraft(){
 let dirty=false;
 for(const method of paymentMethods){
  const input=$('enable-'+method);
  if(!input.hasAttribute('data-saved'))continue;
  const changed=String(input.checked)!==input.dataset.saved;
  dirty=dirty||changed;
  $('settings-'+method+'-status').textContent=input.disabled?'Недоступен':input.checked?'Включён':'Выключен';
 }
 $('payment-switch-feedback').textContent=dirty?'Есть изменения':'';
}
function showPaymentControls(config,replaceDraft){
 for(const method of paymentMethods){
  const input=$('enable-'+method);
  if(replaceDraft||paymentEditorSession!==token||String(input.checked)===input.dataset.saved)input.checked=config[method];
  input.dataset.saved=String(config[method]);
 }
 paymentEditorSession=token;
 $('enable-sbp').disabled=!config.sbp_configured;
 if(!config.sbp_configured)$('enable-sbp').checked=false;
 const note=$('payment-config-note');
 note.textContent=config.sbp_configured?(config.sbp_test_mode?'Тестовый магазин':'Рабочий магазин'):(hasDeveloperAccess(me)?'Подключите магазин ЮKassa':'Магазин подключает разработчик');
 note.dataset.mode=config.sbp_configured?(config.sbp_test_mode?'test':'live'):'missing';
 $('payment-switch-fields').disabled=false;
 paymentDraft();
}
function resetPaymentSettings(){
 paymentEditorSession=null;
 $('payment-switch-fields').disabled=true;
 for(const method of paymentMethods){
  const input=$('enable-'+method);input.checked=false;delete input.dataset.saved;
  $('settings-'+method+'-status').textContent='Загрузка…';
 }
 $('payment-config-note').textContent='';$('payment-switch-feedback').textContent='';
 $('sbp-review-list').replaceChildren();$('wallet-settings').replaceChildren();
 window.pushokSBP?.reset();
 for(const cell of document.querySelectorAll('.payment-metrics dd'))cell.textContent='—';
 $('settings-payments-note').textContent='';
}
async function renderPaymentSettings(){
 if(me?.role!=='admin')return;
 const session=token,valid=()=>session===token&&me?.role==='admin';
 try{
  const {stars,sbp}=await api('/admin/payments/status');if(!valid())return;
  const values={'stars-tariffs':stars.priced_active_tariffs,'stars-success':stars.recorded_successes,
   'stars-review':stars.needs_manual_review,'sbp-success':sbp.recorded_successes,'sbp-pending':sbp.pending_checks,
   'sbp-review':sbp.needs_manual_review,'sbp-background':sbp.background_enabled?'Включена':'Выключена'};
  for(const [key,value] of Object.entries(values))$('payment-'+key).textContent=String(value);
  $('settings-payments-note').textContent='';
 }catch(e){
  if(!valid())return;
  for(const cell of document.querySelectorAll('.payment-metrics dd'))cell.textContent='—';
  $('settings-payments-note').textContent='Не удалось загрузить данные сверки. '+e.message;
 }
}
let sbpReviewRequest=0;
async function renderSBPReview(offset=0){
 if(me?.role!=='admin')return;
 const request=++sbpReviewRequest,session=token,target=$('sbp-review-list');
 target.replaceChildren(el('p','Загрузка попыток СБП…','muted'));
 const reasons={creation_unknown:'Результат создания неизвестен. Нужна сверка с банком по сохранённой попытке; новый счёт здесь не создаётся.',
  configuration_changed:'Магазин или режим изменён. Проверьте серверные настройки.',
  verification_requires_review:'Ответ провайдера не прошёл проверку. Проверьте сумму, валюту, магазин и режим.',
  provider_retry:'Сетевая ошибка: запланирована повторная проверка.',awaiting_provider:'Ожидается результат у провайдера.'};
 try{
  const data=await api('/admin/payments/sbp/attempts?offset='+offset+'&limit=20');
  if(request!==sbpReviewRequest||session!==token||me?.role!=='admin')return;
  target.replaceChildren();
  if(!data.background_enabled)target.append(el('p','Фоновая сверка выключена сервером. Повторы недоступны.','muted'));
  if(!data.items.length)target.append(el('p','Ожидающих и спорных попыток нет.','empty'));
  for(const a of data.items){
   const card=el('article',undefined,'settings-method');card.dataset.attemptId=a.id;
   card.append(el('h4','Кампания '+a.campaign_id),
    el('p',money(a.amount)+' · '+(a.sbp_test_mode===true?'Тестовый режим':a.sbp_test_mode===false?'Боевой режим':'Режим не сохранён')),
    el('p','Попытка: '+a.id,'muted'),
    el('p','Платёж провайдера: '+(a.provider_payment_id||'ID ещё не получен'),'muted'),
    el('p',reasons[a.reason]||'Требуется проверка состояния.'),
    el('p','Сетевых ошибок подряд: '+a.sbp_check_failures+' · Следующая проверка: '+date(a.sbp_next_check_at),'muted'));
   if(a.can_retry){
    const form=el('form'),label=el('label','Причина повторной сверки'),note=el('textarea');
    note.required=true;note.minLength=12;note.maxLength=300;note.rows=2;
    note.placeholder='Что проверили и почему повторяем (12–300 символов)';label.append(note);
    const submit=el('button','Повторить серверную проверку');submit.type='submit';
    form.append(label,submit);form.addEventListener('submit',event=>{
     event.preventDefault();if(!form.reportValidity())return;
     run(async()=>{
      await api('/admin/payments/sbp/attempts/'+a.id+'/retry',{method:'POST',body:{revision:a.sbp_check_revision,note:note.value.trim()}});
      notice('Повтор поставлен в очередь. Это ещё не подтверждение оплаты.',true);
      await Promise.all([renderSBPReview(offset),renderPaymentSettings()]);
     },submit);
    });card.append(form);
   }
   target.append(card);
  }
  const nav=el('div',undefined,'actions');
  nav.append(button('Обновить список',()=>renderSBPReview(offset)));
  if(offset)nav.append(button('Предыдущие попытки',()=>renderSBPReview(Math.max(0,offset-20))));
  if(data.has_more)nav.append(button('Следующие попытки',()=>renderSBPReview(offset+20)));
  target.append(nav);
 }catch(e){
  if(request!==sbpReviewRequest||session!==token||me?.role!=='admin')return;
  target.replaceChildren(el('p','Не удалось получить очередь: '+e.message,'muted'),button('Повторить загрузку',()=>renderSBPReview(offset)));
 }
}
Object.assign(errors,{sbp_reconciliation_disabled:'Фоновая сверка выключена сервером.',
 sbp_review_note_required:'Укажите причину повторной проверки: 12–300 символов.',
 sbp_attempt_not_found:'Попытка не найдена.',sbp_attempt_not_retryable:'Попытку уже нельзя отправить на повтор. Обновите список.',
 sbp_attempt_changed:'Состояние изменилось. Обновите список перед повтором.'});

let adminSettingsLoad=null;
function loadAdminSettings(){
 if(me?.role!=='admin')return Promise.resolve();
 if(adminSettingsLoad)return adminSettingsLoad;
 $('tariff-admin-fields').disabled=true;
 adminSettingsLoad=(async()=>{
  const results=await Promise.allSettled([loadControl({payments:true}),api('/admin/icons').then(showIcons),
   window.pushokVerification.settings(),window.pushokGrowth.referralSettings(),window.pushokAI?.load(),window.pushokProvisioning?.load(),window.pushokGateway?.load(),window.pushokDefaultSponsor.load(),window.pushokTeam.load(),renderLoadingSettings(),renderPaymentSettings(),window.pushokWallet.settings(),renderHomeBanner(),renderSBPReview(),renderTariffAdmin(),renderCoreReadiness(),renderLaunchReadiness(),window.pushokChannelSubscriptionSettings?.load()]);
  window.pushokInterface?.settings();const failed=results.find(x=>x.status==='rejected');if(failed)throw failed.reason;
 })().finally(()=>{$('tariff-admin-fields').disabled=false;adminSettingsLoad=null;});
 return adminSettingsLoad;
}

const holdEligibility=c=>c.moderation_status==='approved' &&
 ['awaiting_payment','proof_submitted','rejected'].includes(c.payment_status) &&
 c.placement_status==='not_scheduled';

async function renderHoldPanel(c,panel){
 panel.replaceChildren(el('h4','Резервирование слота'),el('p','Бронь удерживает слот на 15 минут. После подтверждения оплаты агент запускает размещение в назначенный период.','muted'));
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
 if(me?.role!=='admin'||!holdEligibility(c))return;
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

function showCampaignReport(c, box,{refresh=false,embedded=false}={}){
 const old=box.querySelector('.campaign-report');
 if(old&&!refresh){old.remove();return;}
 const panel=old||el('section',undefined,'campaign-report'),session=token,request=(panel.reportRequest||0)+1;panel.reportRequest=request;
 const current=()=>panel.isConnected&&session===token&&request===panel.reportRequest;
 panel.setAttribute('aria-label','Отчёт о рекламной кампании');
 if(!old){const analytics=box.matches('.campaign-page')&&box.querySelector('.campaign-analytics');if(analytics)analytics.after(panel);else box.append(panel);}
 panel.querySelector(':scope > .report-status')?.remove();
 const status=el('div',undefined,'report-status'),message=el('p','Загружаем сведения…','muted');status.setAttribute('role','status');status.append(message);panel.prepend(status);panel.setAttribute('aria-busy','true');
 return api('/campaigns/'+c.id+'/report').then(data=>{
  if(!current())return;
  const observationsOpen=panel.querySelector('.report-observations')?.open;
  panel.replaceChildren();
  const h=el('h4',embedded?'Сведения о размещении':'Отчёт о размещении · '+data.channel.title),header=el('div',undefined,'report-header');header.append(h);panel.append(header);
  if(!embedded)header.append(window.pushokViews.iconAction(button('',()=>panel.remove()),'close','Закрыть отчёт'));
  const pairs=[
   ['Тариф',data.tariff.name+' · '+money(data.tariff.price_kopecks)],
   ['Модерация',labels[data.moderation_status]||data.moderation_status],
   ['Оплата',data.tariff.price_kopecks===0?'Без оплаты':labels[data.payment_status]||data.payment_status],
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
  if(!embedded)panel.append(grid);
  else {const period=el('dl',undefined,'approval-facts');for(const [title,value] of [['Начало',date(data.scheduled_start_at)],['Окончание',date(data.scheduled_end_at)]]){const row=el('div');row.append(el('dt',title),el('dd',value));period.append(row);}panel.append(period);}
  if(window.pushokCampaignAnalytics)panel.append(window.pushokCampaignAnalytics.panel({id:c.id,archived_at:data.placement_status==='completed'?true:null}));
  const observations=window.pushokViews.disclosure('Дополнительные наблюдения в Telegram',[],'report-observations');
  observations.open=Boolean(observationsOpen);
  const checks=data.operator_observations;
  observations.append(el('p','Показ виден: '+checks.visible+' · Показ не найден: '+checks.not_visible));
  observations.append(el('p','Последний результат: '+
   (checks.latest_result==='visible'?'оператор сообщил, что показ виден':checks.latest_result==='not_visible'?
    'оператор сообщил, что показ не найден':'нет отчёта оператора')+
   (checks.latest_observed_at?' · '+date(checks.latest_observed_at):'')));
  observations.append(el('p','Приватных скриншотов в журнале: '+checks.private_screenshot_count+
   '. Файлы доступны только операторам.','muted'));
  observations.append(el('p','Это необязательные наблюдения оператора. Автоматический запуск и завершение не требуют заполнения этого отчёта.','muted'));
  if(checks.total||checks.private_screenshot_count)panel.append(observations);
  panel.append(el('h4','История действий'));
  const eventNames={campaign_approved_by_ai:'Одобрено: Pushok ИИ',campaign_rejected_by_ai:'Отклонено: Pushok ИИ',campaign_submitted:'Заявка отправлена',campaign_moderated:'Модерация',campaign_pricing_changed:'Тариф или скидка изменены администратором',
   sbp_invoice_created:'Выставлен счёт СБП',sbp_payment_verified:'СБП подтверждена сервером',sbp_payment_canceled:'СБП отменена',
   payment_proof_submitted:'Чек отправлен',payment_reviewed:'Проверка оплаты',
   placement_auto_scheduled:'Автоматическое расписание',placement_activated_by_agent:'Агент подтвердил запуск',placement_completed_by_agent:'Агент подтвердил завершение',placement_scheduled:'Время назначено',placement_activated_manually:'Оператор активировал',
   placement_completed_manually:'Оператор завершил'};
  if(data.timeline.length){
   const history=el('ol',undefined,'report-history');
   for(const event of data.timeline){const row=el('li'),at=el('time',date(event.at));at.dateTime=event.at;row.append(el('span',eventNames[event.event]||'Событие'),at);history.append(row);}
   panel.append(history);
  }else panel.append(el('p','История пока пуста.','muted'));
  window.pushokControls.help(panel,[el('p','Запуск и завершение отражены в истории событий. Количество просмотров спонсора здесь не измеряется. Наблюдения оператора и скриншоты показаны, если они сохранены.')],'О данных отчёта');
  if(embedded){const docs=el('div',undefined,'report-documents');docs.append(button('Скачать CSV',()=>downloadCampaignCsv(c)));panel.append(docs);}
  panel.dataset.reportLoaded='true';
 }).catch(error=>{
  if(!current())return;
  message.textContent=panel.dataset.reportLoaded==='true'?'Не удалось обновить сведения. Показаны ранее загруженные данные.':'Не удалось загрузить историю и документы.';
  if(!status.isConnected)panel.prepend(status);
  status.append(window.pushokViews.iconAction(button('',()=>showCampaignReport(c,box,{refresh:true,embedded})),'refresh','Повторить загрузку отчёта'));
 }).finally(()=>{if(current())panel.setAttribute('aria-busy','false');});
}

function manualPaymentActions(c,box,actions){
  if(c.price_kopecks_snapshot>0&&!miniAppMode&&c.moderation_status==='approved'&&['awaiting_payment','rejected','proof_submitted','verified'].includes(c.payment_status)){
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
  if(c.price_kopecks_snapshot>0&&!miniAppMode&&c.moderation_status==='approved'&&['awaiting_payment','rejected'].includes(c.payment_status)){
   const input=el('input');input.type='file';input.accept='image/jpeg,image/png,application/pdf';input.setAttribute('aria-label','Подтверждение оплаты');
   actions.append(input,button('Загрузить чек',async()=>{if(!input.files[0])throw Error('Выберите чек.');if(input.files[0].size>5*1024*1024)throw Error('Файл должен быть меньше 5 МБ.');const data=new FormData();data.append('file',input.files[0]);await api('/campaigns/'+c.id+'/payment-proofs',{method:'POST',body:data});await refresh();notice('Чек отправлен на проверку.',true);}));
  }
}

function card(c,op,{compact=false,showPayment=false}={}){
 const overview=showPayment&&!op&&!compact;
 const box=el('section',undefined,overview?'campaign-detail campaign-page':'campaign-detail');
 box.append(window.pushokViews.campaignSummary(c,op?'moderation':'campaign',{actions:showPayment&&!overview,overview}));
 const timingContent=[window.pushokViews.progress(c),window.pushokViews.facts(c,overview,!!box.querySelector('.campaign-window'))];
 if(overview)timingContent.push(window.pushokViews.statusSteps(c,true),el('small','Заявка #'+c.id.slice(0,8),'campaign-reference'));
 const timing=window.pushokViews.disclosure('Этапы и сроки размещения',timingContent,'inline-help campaign-timing');
 const timingHeading=timing.querySelector('summary');
 timingHeading.prepend(window.pushokViews.icon('calendar'));timingHeading.append(window.pushokViews.icon('chevron'));
 box.append(timing);
 let reportButton;
 if(!op){
  reportButton=button('Отчёт',()=>showCampaignReport(c,box));
  reportButton.setAttribute('aria-label','Отчёт по кампании');reportButton.className='campaign-stat-action';
  reportButton.prepend(window.pushokViews.icon('chart'));
  box.append(window.pushokCampaignAnalytics.panel(c,compact?null:reportButton));
 }
 if(c.growth_offer_name)box.append(el('p','Скидка: '+c.growth_offer_name+' · '+c.discount_percent_snapshot+'%.'+(miniAppMode?'':' Цена до скидки: '+money(c.base_price_kopecks_snapshot)+'.'),'muted'));
 if(overview&&(c.ai_checked_at||c.moderator_comment||c.payment_review_comment)){
  const notes=[];
  if(c.ai_description)notes.push(el('p',c.ai_description,'campaign-ai-description'));
  if(c.moderator_comment&&c.moderator_comment!==c.ai_description)notes.push(el('p',c.moderator_comment));
  if(c.payment_review_comment)notes.push(el('p','Проверка оплаты · '+c.payment_review_comment));
  if(c.ai_checked_at){const at=el('time','Pushok ИИ · '+date(c.ai_checked_at));at.dateTime=c.ai_checked_at;notes.push(at);}
  const review=window.pushokViews.disclosure('Проверка кампании',notes,'campaign-review-fold');
  const summary=review.querySelector('summary');summary.prepend(window.pushokViews.icon('moderation'));summary.append(window.pushokViews.icon('chevron'));
  review.open=['changes_requested','rejected'].includes(c.moderation_status)||c.payment_status==='rejected';box.append(review);
 }else{
  if(c.moderator_comment)box.append(el('p','Модерация: '+c.moderator_comment));
  if(c.payment_review_comment)box.append(el('p','Проверка оплаты: '+c.payment_review_comment));
 }
 const actions=el('div',undefined,'actions');box.append(actions);
 if(!op&&window.pushokManagement)box.append(window.pushokManagement.campaignActions(c,false,overview));
 const change=async(path,body)=>{await api('/operator/campaigns/'+c.id+'/'+path,{method:'POST',body});notice('Изменения сохранены.',true);await refresh();};
 if(!op){
  if(c.moderation_status==='approved'&&c.price_kopecks_snapshot>0&&!location.hash.startsWith('#/payments/')){const pay=el('a',c.payment_status==='verified'?'Оплата':'Оплатить',c.payment_status==='verified'?'ui-button':'ui-button primary');pay.setAttribute('aria-label',c.payment_status==='verified'?'Посмотреть оплату':'Перейти к оплате');if(c.payment_status==='verified'&&!overview)window.pushokViews.iconAction(pay,'wallet','Посмотреть оплату');else{pay.classList.add('labeled-action');pay.prepend(window.pushokViews.icon('wallet'));}pay.href='#/payments/'+c.id;actions.append(pay);}
  if(compact){const reports=el('details',undefined,'order-reports');reports.append(el('summary','История и отчёт'),reportButton);box.append(reports);}
  if(overview||['scheduled','active','completed'].includes(c.placement_status)){
   const observationButton=button('Автоматика',async()=>{
    let panel=box.querySelector('.automation-panel');if(panel){panel.remove();observationButton.setAttribute('aria-expanded','false');return;}
    panel=el('section',undefined,'observation-panel automation-panel');panel.dataset.promotionCampaign=c.id;panel.setAttribute('aria-label','Статус автоматического размещения');if(overview)actions.after(panel);else box.append(panel);
    observationButton.setAttribute('aria-expanded','true');await updatePromotionPanel(panel);
   });observationButton.classList.add('labeled-action');observationButton.setAttribute('aria-expanded','false');observationButton.setAttribute('aria-label','Статус автоматического размещения');observationButton.prepend(window.pushokViews.icon('status'));actions.append(observationButton);
  }
  addHoldAction(c,box,actions);
  if(!overview)manualPaymentActions(c,box,actions);
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
     const note=prompt('Опишите проверку в своём Telegram через прокси этой кампании, без подписки на канал: видна ли отметка «Спонсор прокси»? Второй аккаунт не нужен (не менее 12 символов):');
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
    if(confirm('Тег применён вручную или агентом, и вы записали положительное наблюдение показа за последние 10 минут?'))await change('activate');
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
  if(c.placement_status==='active')actions.append(button('Подтвердить завершение',async()=>{
   if(!confirm('Тег кампании снят вручную или агентом? В подключённом к этому прокси Telegram без подписки канал кампании больше не показан как спонсор? Второй аккаунт не нужен. Если исходный тег ведёт на тот же канал, возврат настроек не подтверждает прекращение показа.'))return;
   await change('complete',{tag_removed_confirmed:true,verification_method:'separate_unsubscribed_client'});
  }));
 }
 if(overview){
  box.firstElementChild.after(actions);
  const live=el('p',undefined,'campaign-live-line');
  live.dataset.promotionSummary='inline';live.dataset.promotionCampaign=c.id;
  actions.after(live);
  setTimeout(()=>{if(live.isConnected)void updatePromotionPanel(live);},0);
  box.querySelector('.campaign-analytics')?.after(timing);
 }
 return box;
}
const engineTitles={official_mtproxy:'MTProxy Classic',official_docker:'MTProxy Docker (тест)',telemt:'Telemt (адаптер)'};
function engineRequest(form=$('node-form')){
 const data=Object.fromEntries(new FormData(form));
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
 const availability={installer_available_unverified:'установщик есть, реальная установка не проверена',experimental_image:'экспериментальный контейнер',adapter_only:'только адаптер, установщик ещё не готов'};
 $('engine-advice').textContent='План: '+plan.title+' · '+(availability[plan.availability]||'требуется отдельная проверка')+
  '. Не установлено и не проверено. Смена работающего прокси не выполняется.';
}));
let launchReadinessEpoch=0,launchCheckAttempt=null;
function clearLaunchCheck(){launchCheckAttempt=null;$('launch-check-form').reset();$('launch-check-details').open=false;launchCheckInstructions();}
function launchCheckInstructions(){
 const selected=$('launch-check-type').selectedOptions[0];
 $('launch-check-instructions').textContent=selected?.dataset.instructions||'Выберите проверку, которую уже выполнили.';
 $('launch-check-steps').required=$('launch-check-result').value==='passed';
}
$('launch-check-type').addEventListener('change',launchCheckInstructions);
$('launch-check-result').addEventListener('change',launchCheckInstructions);
$('launch-refresh').addEventListener('click',()=>run(renderLaunchReadiness,$('launch-refresh')));
$('launch-check-form').addEventListener('submit',event=>{
 event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;
 void run(async()=>{
  const session=token;
  const at=new Date($('launch-check-at').value);if(!Number.isFinite(at.getTime())||at.getTime()>Date.now())throw Error('Укажите время уже выполненной проверки.');
  const payload={check_id:$('launch-check-type').value,result:$('launch-check-result').value,checked_at:at.toISOString(),note:$('launch-check-note').value.trim(),steps_confirmed:$('launch-check-steps').checked,performed_on_vps:$('launch-check-vps').checked};
  if(payload.note.length<24)throw Error('Опишите проверку и результат: минимум 24 символа.');
  const signature=JSON.stringify(payload);if(!launchCheckAttempt||launchCheckAttempt.signature!==signature)launchCheckAttempt={signature,id:crypto.randomUUID()};
  $('launch-check-fields').disabled=true;
  try{
   const result=await api('/admin/launch-readiness/checks',{method:'POST',body:{...payload,request_id:launchCheckAttempt.id}});
   if(token!==session||me?.role!=='admin')return;
   clearLaunchCheck();await renderLaunchReadiness();
   if(token===session)notice('Отчёт сохранён для v'+result.version+'. Сводка обновлена; настройки оплаты и прокси не менялись.',true);
  }finally{if(token===session)$('launch-check-fields').disabled=false;}
 },$('launch-check-save'));
});
Object.assign(errors,{launch_check_request_conflict:'Такой отчёт уже записан с другими данными. Обновите страницу перед новой проверкой.'});
async function renderLaunchReadiness(){
 if(me?.role!=='admin')return;
 const target=$('launch-readiness'),session=token,epoch=++launchReadinessEpoch;
 const current=()=>token===session&&me?.role==='admin'&&epoch===launchReadinessEpoch;
 try{
  const data=await api('/admin/launch-readiness');if(!current())return;
  target.replaceChildren();
  const summary=el('div',undefined,'readiness-summary');
  for(const [title,ready,pending,total] of [['Бесплатный пилот',data.ready_for_free_pilot,data.pending_free,data.free_total],['Платный запуск',data.ready_for_public_launch,data.pending_paid,data.paid_total]]){
   const box=el('section',undefined,'readiness-summary-item');box.append(el('h4',title),el('strong',(total-pending.length)+' из '+total),el('p',ready?'Проверки пройдены по сохранённым результатам':'Осталось проверок: '+pending.length));summary.append(box);
  }
  target.append(summary,el('p','Сводка: '+date(data.checked_at)+' · установленная версия v'+data.version,'muted'));
  const s=data.statistics;target.append(el('p','Активных узлов в реестре: '+s.nodes_active+' · заявок: '+s.campaigns_total+' · запланировано: '+s.placements_scheduled+' · активных размещений: '+s.placements_active+' · завершено: '+s.placements_completed,'muted'));
  const labels={verified:'Проверено автоматически',reported:'Подтверждено вручную',failed:'Проверка не пройдена',stale:'Нужна повторная проверка',not_verified:'Нет результата в кабинете'};
  const list=el('ul',undefined,'readiness-checks');
  for(const step of data.checks.filter(x=>!['data_present','missing'].includes(x.status))){
   const item=el('li',undefined,'readiness-check');item.dataset.check=step.id;item.dataset.status=step.status;
   item.append(el('h4',step.title),el('span',labels[step.status]||'Нет результата в кабинете','readiness-state'));
   const at=step.observed_at||step.checked_at;if(at)item.append(el('p','Проверка: '+date(at)+(step.version?' · v'+step.version:''),'muted'));
   if(step.node_name)item.append(el('p','Узел: '+step.node_name,'muted'));
   if(step.proof==='operator_telegram_observation')item.append(el('p','Источник: запись оператора о показе в Telegram. Это результат на дату проверки, а не текущая доступность.','muted'));
   if(step.proof==='operator_campaign_completion')item.append(el('p','Источник: проверка показа, активация и подтверждение завершения одной кампании.','muted'));
   if(step.proof==='administrator_acceptance_report')item.append(el('p','Источник: отчёт администратора об испытании на VPS.','muted'));
   if(step.note){const details=el('details'),heading=el('summary','Описание проверки');details.append(heading,el('p',step.note));item.append(details);}
   if(!['verified','reported'].includes(step.status))item.append(el('p',step.action));
   if(step.campaign_id){const link=el('a','Открыть контрольную кампанию ↗');link.href='#/moderation/'+step.campaign_id;item.append(link);}
   if(step.id==='ci_acceptance'){const link=el('a','Результаты сборки в GitHub ↗');link.href='https://github.com/mrkoster1988/mtproxy-pushok/actions';link.target='_blank';link.rel='noopener noreferrer';item.append(link);}
   list.append(item);
  }
  target.append(list,el('p',data.note,'muted'));
  const select=$('launch-check-type'),old=select.value;
  select.replaceChildren(new Option('Выберите проверку',''));
  for(const item of data.manual_check_types){const option=new Option(item.title,item.id);option.dataset.instructions=item.instructions;select.append(option);}
  if([...select.options].some(x=>x.value===old))select.value=old;launchCheckInstructions();
 }catch(e){if(current())target.replaceChildren(el('p','Не удалось обновить сводку: '+e.message+'. Повторите проверку.','muted'));}
}

async function renderCoreReadiness(){
 if(me?.role!=='admin')return;const session=token;
 try{
  const report=await api('/admin/pushok-core/readiness');if(token!==session||me?.role!=='admin')return;
  $('core-readiness').textContent='Экспериментальный Pushok Source Core v'+report.core_version+'. Это отдельное ядро: результаты ниже не описывают работающий MTProxy Classic и не входят в приёмку кабинета. API не проверяет и не переключает работающую службу.';
  const plan=await api('/admin/pushok-core/diagnostic-plan');if(token!==session||me?.role!=='admin')return;
  $('core-test-plan').replaceChildren(...plan.steps.map(step=>el('li',(step.verification==='ci_only'?'Проверка в CI: ':'Ещё не подтверждено для экспериментального ядра: ')+step.title,step.verification==='ci_only'?'core-check-ci':'core-check-pending')));
 }catch{if(token===session&&me?.role==='admin'){$('core-readiness').textContent='Не удалось получить сведения об экспериментальном ядре. Состояние работающего MTProxy проверяется в разделе «Спонсоры».';$('core-test-plan').replaceChildren();}}
}
$('launch-evidence-integrity').addEventListener('click',()=>run(async()=>{
 const session=token,report=await api('/admin/placement-evidence/integrity?limit=50');if(token!==session||me?.role!=='admin')return;
 notice('Проверено файлов: '+report.checked+'; целых: '+report.healthy+'; проблем: '+report.issues.length+'. Проверяется сохранность скриншотов; факт показа подтверждает оператор.',!report.issues.length);
},$('launch-evidence-integrity')));
let adminTariffs=[],tariffDraftId=null,tariffEditorDirty=false,previousTariffId='';
Object.assign(errors,{tariff_request_conflict:'Тариф с этим запросом уже сохранён с другими условиями. Выберите его из списка или начните новый тариф.'});

function tariffLabel(t){return t.name+' · '+(t.price_kopecks===0?'Бесплатно':miniAppMode?(t.stars_price_xtr>0?t.stars_price_xtr+' XTR':'Цена Stars не задана'):money(t.price_kopecks));}
function updatePublicPrice(){
 const publicTariffs=tariffs.filter(t=>!t.admin_only);
 const t=publicTariffs.find(x=>x.duration_seconds===86400)||publicTariffs[0];
 if(!t){
  $('hero-tariff-price').textContent='Уточняйте цену';
  $('hero-tariff-duration').textContent='';
  return;
 }
 $('hero-tariff-price').textContent=money(t.price_kopecks);
 $('hero-tariff-duration').textContent='/ '+t.name;
}
function previewTariffAdmin(){
 const amount=Number($('tariff-admin-hours').value),unit=Number($('tariff-admin-unit').value);
 const rubText=$('tariff-admin-rub').value.trim(),rub=Number(rubText),free=rubText!==''&&rub===0;
 $('tariff-admin-free').checked=free;
 $('tariff-admin-hours').max=String(31536000/unit);
 const summary=$('tariff-admin-preview');summary.replaceChildren(el('strong',$('tariff-admin-name').value.trim()||'Название тарифа'));
 summary.append(el('p','Длительность: '+(amount>0?amount+(unit===86400?' дн.':' ч'):'не задана')));
 summary.append(el('p',free&&Number($('tariff-admin-stars').value)>0?'Для бесплатного тарифа очистите цену Stars или укажите 0.':free?'Бесплатно · без счёта Stars и СБП':rubText!==''&&rub>=1?money(Math.round(rub*100))+($('tariff-admin-stars').value>0?' / '+$('tariff-admin-stars').value+' Stars':' · цена Stars не задана'):'Укажите 0 ₽ или цену от 1 ₽.'));
 summary.append(el('small',($('tariff-admin-audience').value==='admin'?'Только администратору':'Всем рекламодателям')+' · '+($('tariff-admin-active').checked?'Доступен для новых заявок':'Выключен')));
}
function tariffEditorMode(creating,privateTariff){
 $('tariff-admin-mode').textContent=creating?'Новый тариф':'Редактирование тарифа';
 $('tariff-admin-save').textContent=creating?'Создать':'Сохранить';
 $('tariff-admin-save').setAttribute('aria-label',creating?'Создать тариф':'Сохранить настройки тарифа');
 $('tariff-admin-cancel').hidden=!creating;
 if($('tariff-admin-delete'))$('tariff-admin-delete').hidden=creating;
 $('tariff-admin-audience').disabled=!creating;
 $('tariff-admin-free').disabled=privateTariff;
 $('tariff-admin-rub').readOnly=privateTariff;$('tariff-admin-stars').readOnly=privateTariff;
 $('tariff-admin-stars').placeholder=privateTariff?'Не требуется':'Не настроено';
 $('tariff-admin-access').hidden=!privateTariff;
}
function newTariffAdmin(){
 previousTariffId=$('tariff-admin-select').value||previousTariffId;
 tariffDraftId=crypto.randomUUID();tariffEditorDirty=false;
 if(!$('tariff-admin-select').querySelector('option[value=""]'))option($('tariff-admin-select'),'','Новый тариф');
 $('tariff-admin-select').value='';$('tariff-admin-name').value='';
 $('tariff-admin-hours').value='1';$('tariff-admin-unit').value='86400';
 $('tariff-admin-rub').value='300.00';$('tariff-admin-stars').value='';
 $('tariff-admin-audience').value='public';$('tariff-admin-active').checked=true;
 tariffEditorMode(true,false);previewTariffAdmin();
 $('tariff-admin-status').textContent='Заполните условия и нажмите «Создать». Нулевая цена делает тариф бесплатным.';
}
function fillTariffAdmin(){
 const tariff=adminTariffs.find(t=>t.id===$('tariff-admin-select').value);
 if(!tariff){newTariffAdmin();return;}
 tariffDraftId=null;tariffEditorDirty=false;previousTariffId=tariff.id;
 $('tariff-admin-name').value=tariff.name;
 $('tariff-admin-unit').value='3600';$('tariff-admin-hours').value=tariff.duration_seconds/3600;
 $('tariff-admin-rub').value=(tariff.price_kopecks/100).toFixed(2);
 $('tariff-admin-stars').value=tariff.stars_price_xtr===null?'':String(tariff.stars_price_xtr);
 $('tariff-admin-active').checked=tariff.is_active;
 $('tariff-admin-audience').value=tariff.admin_only?'admin':'public';
 tariffEditorMode(false,!!tariff.admin_only);previewTariffAdmin();
}
async function renderTariffAdmin({selectedId=null}={}){
 if(me?.role!=='admin')return;
 const session=token;
 try{
  const data=await api('/admin/tariffs');if(session!==token||me?.role!=='admin')return;
  adminTariffs=data;
  const select=$('tariff-admin-select'),previous=selectedId??select.value;
  const preserve=!selectedId&&(tariffDraftId||tariffEditorDirty)&&(!previous||data.some(t=>t.id===previous));
  select.replaceChildren();adminTariffs.forEach(t=>option(select,t.id,tariffLabel(t)+(t.admin_only?' · администратор':'')+(t.is_active?'':' · выключен')));
  if(tariffDraftId&&!selectedId)option(select,'','Новый тариф');
  if(adminTariffs.some(t=>t.id===previous)||preserve)select.value=previous;
  if(!preserve)fillTariffAdmin();
  if(!tariffDraftId)$('tariff-admin-status').textContent='Изменения действуют для новых заявок. Доступ тарифа задаётся при создании; способы оплаты — во вкладке «Платежи».';
 }catch(e){if(session===token&&me?.role==='admin')$('tariff-admin-status').textContent='Не удалось прочитать тарифы: '+e.message;}
}
$('tariff-admin-select').addEventListener('change',fillTariffAdmin);
$('tariff-admin-new').addEventListener('click',()=>{newTariffAdmin();$('tariff-admin-name').focus();});
$('tariff-admin-cancel').addEventListener('click',()=>{
 tariffDraftId=null;tariffEditorDirty=false;
 $('tariff-admin-select').querySelector('option[value=""]')?.remove();
 $('tariff-admin-select').value=adminTariffs.some(t=>t.id===previousTariffId)?previousTariffId:adminTariffs[0]?.id||'';
 fillTariffAdmin();
});
$('tariff-admin-free').addEventListener('change',()=>{
 $('tariff-admin-rub').value=$('tariff-admin-free').checked?'0.00':'300.00';
 if($('tariff-admin-free').checked)$('tariff-admin-stars').value='';
 tariffEditorDirty=true;previewTariffAdmin();
});
$('tariff-admin-audience').addEventListener('change',()=>{
 const privateTariff=$('tariff-admin-audience').value==='admin';
 if(privateTariff){$('tariff-admin-rub').value='0.00';$('tariff-admin-stars').value='';}
 tariffEditorMode(!!tariffDraftId,privateTariff);tariffEditorDirty=true;previewTariffAdmin();
});
$('tariff-admin-form').addEventListener('input',event=>{
 if(['tariff-admin-select','tariff-admin-free'].includes(event.target.id))return;
 tariffEditorDirty=true;previewTariffAdmin();
});
$('tariff-admin-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{
 const chosen=$('tariff-admin-select').value,creating=!!tariffDraftId,session=token;
 const hours=Number($('tariff-admin-hours').value)*Number($('tariff-admin-unit').value)/3600,rub=Number($('tariff-admin-rub').value);
 const duration=Math.round(hours*3600),kopecks=Math.round(rub*100);
 const starValue=$('tariff-admin-stars').value.trim(),stars=starValue===''?null:Number(starValue);
 if(!Number.isInteger(duration)||duration<3600||duration>31536000||
    !Number.isInteger(kopecks)||kopecks<0||kopecks>100000000||
    (stars!==null&&(!Number.isInteger(stars)||stars<0||stars>1000000)))
  throw Error('Укажите допустимую цену, длительность и количество Stars.');
 if(kopecks>0&&kopecks<100)throw Error('Для СБП укажите от 1 ₽; для бесплатного тарифа — 0 ₽.');
 if(kopecks===0&&stars>0)throw Error('Для бесплатного тарифа укажите 0 Stars или оставьте поле пустым.');
 const body={name:$('tariff-admin-name').value.trim(),duration_seconds:duration,price_kopecks:kopecks,stars_price_xtr:stars,is_active:$('tariff-admin-active').checked};
 if(creating)Object.assign(body,{request_id:tariffDraftId,admin_only:$('tariff-admin-audience').value==='admin'});
 $('tariff-admin-fields').disabled=true;
 try{
  const saved=await api(creating?'/admin/tariffs':'/admin/tariffs/'+chosen,{method:creating?'POST':'PUT',body});
  if(session!==token||me?.role!=='admin')return;
  tariffDraftId=null;tariffEditorDirty=false;
  await renderTariffAdmin({selectedId:saved.id});
  tariffs=await api('/me/tariffs');updatePublicPrice();
  $('tariff-select').replaceChildren();tariffs.forEach(t=>option($('tariff-select'),t.id,tariffLabel(t)));
  await loadAvailability();await renderPaymentSettings();
  notice(creating?'Тариф создан.': 'Настройки тарифа сохранены. Условия прежних заявок сохранены.',true);
 }finally{if(session===token&&me?.role==='admin')$('tariff-admin-fields').disabled=false;}
},e.submitter);});

let nodeEditorSession='';
function renderNodes(){
 $('node-admin').hidden=!['admin','operator'].includes(me?.role);
 const drafts=new Map(nodeEditorSession===token?[...$('nodes-list').querySelectorAll('.proxy-node')].map(row=>[row.dataset.nodeId,[...row.querySelectorAll('.node-actions>form')]]):[]);nodeEditorSession=token;
 $('nodes-list').replaceChildren(...nodes.map(n=>{
  const box=el('div',undefined,'card proxy-node'),head=el('div',undefined,'proxy-node-heading'),copy=el('div');
  box.dataset.nodeId=n.id;copy.append(el('h3',n.name));head.append(window.pushokViews.icon('server'),copy);box.append(head,el('p',n.host+':'+n.port,'proxy-node-address muted'));
  const meta=el('div',undefined,'proxy-node-meta');meta.append(el('span',n.status==='active'?'В расписании':'На паузе','badge badge--'+(n.status==='active'?'approved':'paused')),el('span',engineTitles[n.target_engine]||'Движок: автовыбор','muted'));box.append(meta);
  if(me?.role==='admin'){
  const actions=window.pushokManagement?.nodeActions(n)||el('div'),controls=actions.querySelector('.node-action-buttons')||el('div');head.append(controls);box.append(actions);actions.append(...(drafts.get(n.id)||[]));
  const plan=button('Изменить план движка',()=>{
   actions.querySelector('.management-form')?.remove();
   if(box.querySelector('.node-plan-form')){box.querySelector('.node-plan-form').remove();return;}
   const form=el('form',undefined,'node-plan-form'),inputs={};
   form.append(el('h4','План движка'));
   // Редактирование плана относится к этому узлу, а не к скрытой форме добавления.
   for(const [name,title,values,selected] of [
    ['environment','Среда',[['production','Основной сервер'],['staging','Тестовый стенд']],n.target_engine&&n.target_engine!=='official_mtproxy'?'staging':'production'],
    ['deployment','Способ развёртывания',[['any','Автовыбор'],['native','systemd / native'],['docker','Docker']],['native','docker'].includes(n.deployment_profile)?n.deployment_profile:'any'],
    ['engine_mode','Выбор движка',[['auto','Автоматически'],['manual','Вручную']],n.engine_selection_mode==='manual'?'manual':'auto'],
    ['preferred_engine','Движок',Object.entries(engineTitles),n.target_engine||'official_mtproxy']]){
    const label=el('label',title),input=el('select');input.name=name;values.forEach(([v,t])=>option(input,v,t));input.value=selected;label.append(input);form.append(label);inputs[name]=input;
   }
   const toggle=()=>{inputs.preferred_engine.closest('label').hidden=inputs.engine_mode.value!=='manual';};inputs.engine_mode.addEventListener('change',toggle);toggle();
   const save=el('button','Сохранить'),cancel=button('Отмена',()=>form.remove()),formActions=el('div',undefined,'sponsor-form-actions');save.type='submit';save.setAttribute('aria-label','Сохранить план');save.prepend(window.pushokViews.icon('save'));cancel.prepend(window.pushokViews.icon('close'));formActions.append(save,cancel);form.append(formActions,window.pushokViews.disclosure('Что изменится',[el('p','Сохраняется только план. Работающий прокси не переключается.','muted')],'inline-help'));
   form.addEventListener('submit',event=>{event.preventDefault();run(async()=>{await api('/admin/proxy-nodes/'+n.id+'/engine-plan',{method:'PATCH',body:engineRequest(form)});form.remove();await refresh();notice('План сохранён. Проверка и установка выполняются отдельно.',true);},save);});
   actions.append(form);inputs.environment.focus();
  });window.pushokViews.iconAction(plan,'settings','Изменить план движка');controls.append(plan);
  const pause=button(n.status==='active'?'Поставить на паузу':'Возобновить',async()=>{
   await api('/admin/proxy-nodes/'+n.id+'/status',{method:'PATCH',body:{status:n.status==='active'?'paused':'active'}});
   await refresh();notice('Состояние узла сохранено.',true);
  });window.pushokViews.iconAction(pause,'power',n.status==='active'?'Поставить на паузу':'Возобновить');controls.append(pause);
  const remove=controls.querySelector('.danger-text');if(remove)controls.append(remove);
  }return box;
 }));
 if(!nodes.length)$('nodes-list').append(window.pushokViews.emptyLine('Узлов пока нет','server'));
}
$('node-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{
 const data=Object.fromEntries(new FormData(e.target));data.port=Number(data.port);
 if(data.engine_mode==='auto')delete data.preferred_engine;
 await api('/admin/proxy-nodes',{method:'POST',body:data});e.target.reset();$('engine-mode').dispatchEvent(new Event('change'));$('node-create').open=false;await refresh();notice('Узел добавлен в реестр.',true);
},e.submitter);});
async function refresh({background=false}={}){
 if(!token)return;
 if(!background)window.pushokViews?.clearLogos();
 const refreshSession=token,refreshUser=me?.id,refreshGeneration=authGeneration;
 const current=()=>token===refreshSession&&me?.id===refreshUser&&refreshGeneration===authGeneration&&(!background||!editingControl());
 const selectedChannel=$('channel-select').value,selectedTariff=$('tariff-select').value;
 const [own,currentChannels,currentTariffs]=await Promise.all([api('/campaigns'),api('/channels'),api('/me/tariffs')]);
 if(!current())return;
 const calendarChanged=JSON.stringify(tariffs.map(t=>[t.id,t.duration_seconds]))!==JSON.stringify(currentTariffs.map(t=>[t.id,t.duration_seconds]));
 channels=currentChannels;tariffs=currentTariffs;updatePublicPrice();
 $('channel-select').replaceChildren();option($('channel-select'),'','Выберите канал');channels.forEach(c=>option($('channel-select'),c.id,c.title));
 if(channels.some(c=>c.id===selectedChannel))$('channel-select').value=selectedChannel;
 $('tariff-select').replaceChildren();tariffs.forEach(t=>option($('tariff-select'),t.id,tariffLabel(t)));
 if(tariffs.some(t=>t.id===selectedTariff))$('tariff-select').value=selectedTariff;
 if(calendarChanged){try{await loadAvailability();}catch(e){if(current())notice(e.message);}}
 if(!current())return;
 if(!window.cabinet)$('campaigns').replaceChildren(...own.map(c=>card(c,false)));$('campaign-count').textContent=own.length+' заявок';
 ownCampaigns=own;
 if(!own.length&&!window.cabinet)$('campaigns').append(el('div','Заявок пока нет. Добавьте канал и создайте первое размещение.','empty'));
 if(me&&['operator','admin'].includes(me.role)){
  const currentNodes=await api('/operator/proxy-nodes');if(!current())return;nodes=currentNodes;renderNodes();
  if(me.role==='admin'&&!$('settings').hidden)await loadAdminSettings();
  const [working,archived]=await Promise.all([api('/operator/campaigns'),api('/operator/campaigns?archived=true')]);if(!current())return;const queue=[...working,...archived];operatorCampaigns=queue;
  $('queue-count').textContent=working.length+' заявок';
  if(window.pushokApproval)window.pushokApproval.sync(queue);
  else $('queue').replaceChildren(...working.map(c=>card(c,true)));
 }
 if(!current())return;
 window.pushokOrder?.sync();
 if(window.cabinet)window.cabinet.sync(own,{background});
}
function confirmShopLogin(profile, session) {
 const query = new URLSearchParams(location.search);
 const fromBot = query.get('shop_subscriptions') === '1';
 if (!fromBot && query.get('shop_login') !== '1') return false;
 const dialog = $('shop-login-consent');
 const feedback = $('shop-login-result');
 feedback.textContent='';
 $('shop-login-person').textContent = profile.first_name+' · Telegram ID '+profile.telegram_user_id;
 const cancel = () => location.replace('/shop/');
 dialog.oncancel = cancel;
 $('shop-login-cancel').onclick = cancel;
 $('shop-login-confirm').onclick = async () => {
  const submit = $('shop-login-confirm');
  if(submit.disabled)return;
  submit.disabled = true;
  feedback.textContent='Подтверждаем вход…';
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),15000);
  const signal=controller.signal;
  try {
   if (token !== session || me?.id !== profile.id) throw Error();
   if (fromBot) {
    const begin = await fetch('/shop/api/auth/mtproxy/start', {method:'POST', signal,credentials:'same-origin',
      headers:{'Content-Type':'application/json'}, body:'{}'});
    if (begin.status === 409) { location.assign('/shop/?view=account'); return; }
    if (!begin.ok) throw Error();
   }
   const start = await fetch('/shop/api/auth/mtproxy/challenge', {signal,credentials:'same-origin',cache:'no-store'});
   if(start.status===409){feedback.textContent='Запрос истёк. Вернитесь в магазин и начните вход заново.';return;}
   if (!start.ok) throw Error();
   const {challenge} = await start.json();
   const {ticket} = await api('/flow/shop-browser-ticket',{method:'POST',session,signal,body:{challenge,consent:true}});
   if (token !== session || me?.id !== profile.id) throw Error();
   const result = await fetch('/shop/api/auth/mtproxy/finish',{method:'POST',signal,credentials:'same-origin',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket})});
   if (!result.ok) throw Error();
   location.replace('/shop/?view=account');
  } catch {
   feedback.textContent=signal.aborted?'Сервер долго отвечает. Вернитесь в магазин, чтобы проверить вход.':'Вход не завершён. Попробуйте ещё раз или вернитесь в магазин.';
  } finally {
   clearTimeout(timeout);
   submit.disabled=false;
  }
 };
 if (!dialog.open) dialog.showModal();
 return true;
}
async function enter(){
 const session=token,generation=authGeneration,entry=++enterGeneration;
 const current=()=>Boolean(session&&token===session&&generation===authGeneration&&entry===enterGeneration);
 if(!current())return false;
 try{
  const profile=await api('/me');if(!current())return false;
  me=profile;
  if(confirmShopLogin(profile,session))return true;
  window.pushokFlowAccount?.authenticated();
  await window.pushokGrowth.claimProxyInvite();if(!current())return false;
  const loadedChannels=await api('/channels');if(!current())return false;channels=loadedChannels;
  const loadedTariffs=await api('/me/tariffs');if(!current())return false;tariffs=loadedTariffs;updatePublicPrice();
  const isOperator=['operator','admin'].includes(me.role);
  const loadedNodes=isOperator?await api('/operator/proxy-nodes'):[];if(!current())return false;nodes=loadedNodes;
  $('identity').textContent=me.first_name||'Рекламодатель';$('logout').hidden=false;
  $('login').hidden=true;$('workspace').hidden=false;$('operator-tab').hidden=!isOperator;
  $('settings-tab').hidden=me.role!=='admin';if(!window.cabinet)showWorkspace('advertiser');showSettingsPane('payments');
  $('channel-select').replaceChildren();option($('channel-select'),'','Выберите канал');channels.forEach(c=>option($('channel-select'),c.id,c.title));
  $('tariff-select').replaceChildren();tariffs.forEach(t=>option($('tariff-select'),t.id,tariffLabel(t)));
  try{await refresh();}finally{if(current()){window.cabinetBootReady=true;if(window.cabinet)window.cabinet.enter();}}
  if(!current())return false;await loadAvailability();return current();
 }catch(error){if(!current())return false;throw error;}
}

function localDay(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
const calendarZone=Intl.DateTimeFormat().resolvedOptions().timeZone||'Europe/Moscow';
let calendarRequest=0;
function clearRequestedDate(){$('requested-start').value='';$('calendar-selection').textContent='Дата пока не выбрана.';for(const b of $('availability-days').children)b.setAttribute('aria-pressed','false');window.pushokOrder?.summary();}
async function loadAvailability(){
 $('tariff-access-hint').hidden=!tariffs.find(t=>t.id===$('tariff-select').value)?.admin_only;
 const request=++calendarRequest;clearRequestedDate();
 if(!$('tariff-select').value)return;
 $('availability-days').replaceChildren(el('p','Загрузка свободных дат…','muted'));
 const params=new URLSearchParams({tariff_id:$('tariff-select').value,date_from:$('calendar-from').value,days:'14',timezone_name:calendarZone,start_time:$('calendar-time').value||'12:00'});
 try{
  const data=await api('/availability?'+params);
  if(request!==calendarRequest)return;
  $('availability-days').replaceChildren(...data.days.map(day=>{
   const calendarLabels={available:'Свободно: '+day.available_nodes,unconfigured:'Автоподбор',occupied:'Занято → позже',past:'Время прошло',invalid_local_time:'Нет такого времени',ambiguous_local_time:'Неоднозначное время'};
   const b=button(new Date(day.start_at).toLocaleDateString('ru-RU',{day:'numeric',month:'short'})+' · '+(calendarLabels[day.status]||'Недоступно'),async()=>{
    clearRequestedDate();b.setAttribute('aria-pressed','true');$('requested-start').value=day.start_at;$('calendar-selection').textContent='Желаемое начало: '+date(day.start_at)+(['occupied','unconfigured'].includes(day.status)?' · после оплаты подберём ближайшее свободное время':'');window.pushokOrder?.summary();
   });
   const display=new Date(day.start_at);b.setAttribute('aria-label',b.textContent);b.dataset.availability=day.status;
   b.replaceChildren(el('small',display.toLocaleDateString('ru-RU',{weekday:'short',month:'short'})),el('strong',String(display.getDate())),el('span',calendarLabels[day.status]||'Недоступно'));
   b.disabled=!['available','unconfigured','occupied'].includes(day.status);b.setAttribute('aria-pressed','false');return b;
  }));
 }catch(e){if(request===calendarRequest){$('availability-days').replaceChildren(el('p','Не удалось получить доступные даты.','muted'));throw e;}}
}
$('calendar-zone').textContent='Часовой пояс: '+calendarZone+'. Длительность — по выбранному тарифу.';
$('calendar-from').value=localDay(new Date());$('calendar-from').min=localDay(new Date());const calendarLastDay=new Date();calendarLastDay.setDate(calendarLastDay.getDate()+77);$('calendar-from').max=localDay(calendarLastDay);
$('calendar-clear').addEventListener('click',clearRequestedDate);
for(const id of ['calendar-from','calendar-time','tariff-select'])$(id).addEventListener('change',()=>run(loadAvailability));

window.onTelegramAuth=data=>run(async()=>{
 const generation=clearCabinetSession({preserveUnboundFlow:window.pushokFlowAccount?.unbound()===true,clearWorkspace:Boolean(me||window.cabinetBootReady)});
 try{
  const session=await api('/auth/telegram',{method:'POST',body:data,session:''});
  if(generation!==authGeneration)return;
  token=session.access_token;sessionStorage.setItem(sessionKey,token);
  if(await enter()&&generation===authGeneration)notice('Вход выполнен.',true);
 }catch(error){if(generation===authGeneration)throw error;}
});
$('channel-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{await e.target.elements.telegram_url.pushokChannelCheck?.check();const data=Object.fromEntries(new FormData(e.target)),match=data.telegram_url.trim().match(/^(?:@|https:\/\/(?:t\.me|telegram\.me)\/)?([a-z][a-z0-9_]{3,31})\/?$/i);if(!match)throw Error('Укажите публичный @username или ссылку https://t.me/channel.');data.telegram_url='https://t.me/'+match[1].toLowerCase();await api('/channels',{method:'POST',body:data});e.target.reset();await enter();if(location.hash==='#/channels/new')window.cabinet?.go('channels');notice('Канал добавлен.',true);},e.submitter);});

const passkeysAvailable = window.isSecureContext &&
 typeof window.PublicKeyCredential?.parseCreationOptionsFromJSON === 'function' &&
 typeof window.PublicKeyCredential?.parseRequestOptionsFromJSON === 'function' &&
 typeof window.PublicKeyCredential?.prototype?.toJSON === 'function';
$('passkey-sign-in').hidden=!passkeysAvailable;
$('passkey-settings').hidden=!passkeysAvailable;
async function usePasskey(register) {
 const method=register?'register':'login', session=register?token:'', generation=authGeneration;
 const options=await api('/auth/passkeys/'+method+'/options',{method:'POST',session});
 if(generation!==authGeneration)return;
 let credential;
 try {
  credential=register?await navigator.credentials.create({publicKey:PublicKeyCredential.parseCreationOptionsFromJSON(options)}):
   await navigator.credentials.get({publicKey:PublicKeyCredential.parseRequestOptionsFromJSON(options)});
 } catch(e){
  if(e.name==='NotAllowedError')throw Error('Подтверждение отменено или подходящий ключ входа не найден.');
  throw Error('Не удалось открыть ключ входа на этом устройстве.');
 }
 if(!credential || generation!==authGeneration)return;
 const result=await api('/auth/passkeys/'+method+'/finish',
  {method:'POST',session,body:{credential:JSON.stringify(credential.toJSON())}});
 if(generation!==authGeneration)return;
 if(register){notice('Ключ добавлен. Теперь можно входить без Telegram.',true);return;}
 token=result.access_token;sessionStorage.setItem(sessionKey,token);await enter();
}
$('passkey-sign-in').addEventListener('click',e=>run(()=>usePasskey(false),e.currentTarget));
$('passkey-register').addEventListener('click',e=>run(()=>usePasskey(true),e.currentTarget));
$('passkey-remove').addEventListener('click',e=>run(async()=>{
 if(!confirm('Удалить все ключи входа этого аккаунта? Вход через Telegram останется доступен.'))return;
 await api('/auth/passkeys/remove',{method:'POST'});notice('Ключи входа удалены.',true);
},e.currentTarget));
$('logout').addEventListener('click',()=>run(async()=>{
 const session=token,generation=clearCabinetSession();
 if(miniAppMode)window.pushokMiniApp.signedOut();
 if(!session)return;
 try{await api('/auth/logout',{method:'POST',session});}
 catch(error){if(generation===authGeneration)throw error;return;}
 if(generation===authGeneration&&!miniAppMode)location.reload();
}));
$('refresh').addEventListener('click',()=>run(async()=>{await refresh();await loadAvailability();},$('refresh')));
$('queue-filter').addEventListener('change',()=>run(refresh));
$('advertiser-tab').addEventListener('click',()=>operatorView(false));
$('operator-tab').addEventListener('click',()=>operatorView(true));
$('settings-tab').addEventListener('click',()=>run(async()=>{
 if(me?.role!=='admin')return;
 showWorkspace('settings');
 await loadAdminSettings();
}));
run(async()=>{
 if(miniAppMode){await window.pushokMiniApp.start();return;}
 const shopRequest=new URLSearchParams(location.search).get('shop_login')==='1';
 $('shop-login-email').hidden=!shopRequest;
 if(shopRequest){
  const login=$('login');
  login.querySelector('h2').textContent='Войти в Pushok Shop';
  login.querySelector('.login-intro').textContent='Войдите в кабинет MTProxy. Затем подтвердите вход в магазин.';
  document.querySelector('main').prepend(login);
 }
 api('/tariffs').then(items=>{if(!me){tariffs=items;updatePublicPrice();}}).catch(()=>{if(!me)$('hero-tariff-price').textContent='Цена временно недоступна';});
 const config=await api('/public-config');window.cabinetBotConfig=config;if(window.cabinet)window.cabinet.config(config);window.pushokFlowAccount?.configure(config);
 const botShortcut=document.querySelector('.hero-link-primary');
 const validBot=/^[A-Za-z0-9_]{5,32}$/.test(config.telegram_bot_username||'');
 if(validBot){botShortcut.href='https://t.me/'+config.telegram_bot_username;botShortcut.removeAttribute('aria-disabled');}
 else{botShortcut.removeAttribute('href');botShortcut.setAttribute('aria-disabled','true');botShortcut.textContent='Telegram-бот не настроен';}
 if(validBot){
 const script=document.createElement('script');script.src='https://telegram.org/js/telegram-widget.js?22';script.async=true;script.onerror=()=>{$('login-note').textContent='Не удалось загрузить вход через Telegram. Проверьте соединение или используйте другой доступный способ входа.';};script.dataset.telegramLogin=config.telegram_bot_username;script.dataset.size='large';script.dataset.onauth='onTelegramAuth(user)';$('telegram-login').append(script);$('login-note').textContent=shopRequest?'Подтвердите вход в Telegram или используйте ранее добавленный ключ доступа. Для входа в магазин по email откройте ссылку ниже.':'Вход подтверждается в Telegram.';
}else $('login-note').textContent='Администратору нужно настроить Telegram-бота для входа.';
 const generation=authGeneration;
 if(!token){
  const controller=new AbortController();
  const deadline=setTimeout(()=>controller.abort(),12000);
  try {
   const resume=await fetch('/api/v1/auth/browser/resume',{signal:controller.signal,method:'POST',headers:{'X-Pushok-Cabinet':'resume'},credentials:'same-origin',cache:'no-store'});
   if(resume.ok && generation===authGeneration && !token){
    const saved=await resume.json();
    if(generation===authGeneration && !token && typeof saved.access_token==='string'){
     token=saved.access_token;sessionStorage.setItem(sessionKey,token);
    }
   }
  }catch{} finally { clearTimeout(deadline); } // The normal sign-in controls remain available offline.
 }
 if(token && generation===authGeneration){
  if(await enter() && generation===authGeneration){
   await api('/auth/browser/remember',{method:'POST'}).catch(()=>{});
  }
 }
});

let displayedVersion=null,softwareControl=null,softwareRelease=null,softwareSession=null;
let updateScreen=null;
function showUpdateScreen(show,requestedAt=null){
 if(show&&!updateScreen){
  const frame=document.createElement('iframe');
  frame.className='update-screen-frame';
  frame.title='Идёт обновление';
  const params=new URLSearchParams({embedded:'1'});
  if(requestedAt)params.set('requested_at',requestedAt);
  frame.src='/update/screen.html?'+params.toString();
  document.body.append(frame);
  updateScreen=frame;
 }else if(!show&&updateScreen){
  updateScreen.remove();
  updateScreen=null;
 }
}
let controlFlight=null,releaseFlight=null,lastReleaseRequest=0,autoSaveSession=null;
function clearSoftware(){
 showUpdateScreen(false);
 displayedVersion=null;softwareControl=null;softwareRelease=null;softwareSession=null;controlFlight=null;releaseFlight=null;lastReleaseRequest=0;
 autoSaveSession=null;$('software-auto-row').hidden=true;$('software-auto-status').textContent='';
 $('software-updates').hidden=true;$('software-update-notice').hidden=true;
 $('software-installed').textContent='Получаем версию…';$('software-available').textContent='Проверяем…';
 $('software-update-status').textContent='';$('software-release-status').textContent='Проверяем наличие новой версии…';$('software-checked').textContent='Проверка ещё не выполнена.';$('software-changelog').hidden=true;$('software-update').disabled=true;
}
function renderSoftware(){
 if(me?.role!=='admin')return;
 const c=softwareControl,r=softwareRelease,busy=Boolean(c&&(c.requested||c.update.state==='running'));
 if(c)showUpdateScreen(busy,c.requested_at||c.update?.at);
 if(c)$('software-installed').textContent='v'+c.version;
 const known=r?.available_version;
 $('software-available').textContent=known?'v'+known+(r.state==='unavailable'?' (ранее)':''):r?'Не удалось узнать':'Проверяем…';
 const texts={available:'Доступна новая версия.',current:'Установлена актуальная версия.',ahead:'Установленная версия новее опубликованной.',unavailable:'Не удалось проверить новую версию. Повторите проверку позже.'};
 $('software-release-status').textContent=r?(texts[r.state]||texts.unavailable):'Проверяем наличие новой версии…';
 $('software-release-badge').textContent=({available:'Есть обновление',current:'Актуально',ahead:'Новее релиза',unavailable:'Нет данных'})[r?.state]||'Проверяем';
 $('software-checked').textContent=r?.checked_at?'Последняя успешная проверка: '+date(r.checked_at):'Успешной проверки пока нет.';
 if(r?.state==='unavailable'&&r.attempted_at)$('software-checked').textContent+=' Последняя попытка: '+date(r.attempted_at);
 const changes=$('software-changelog');changes.hidden=!r?.commit;
 if(r?.commit)changes.href='https://github.com/mrkoster1988/mtproxy-pushok/blob/'+encodeURIComponent(r.commit)+'/CHANGELOG.md';
 const states={idle:'Установка ещё не запускалась.',running:'Обновление устанавливается…',succeeded:'Последняя установка завершена.',failed:'Последняя установка не завершена. Проверьте журнал службы обновления на VPS.'};
 const stages={preflight:'проверка окружения',backup:'резервная копия',source:'получение кода',build:'сборка',migration:'обновление базы',restart:'перезапуск',health:'проверка API',proxy:'настройка прокси'};
 if(c){
 let status=!c.updater_installed?'Служба обновления ещё не установлена на VPS.':c.requested?'Запрос принят. Ожидаем запуск.':states[c.update.state]||'Состояние установки неизвестно.';
 if(['running','failed'].includes(c.update.state)&&stages[c.update.stage])status+=' Этап: '+stages[c.update.stage]+'.';
 if(c.requested&&c.requested_at){status+=' '+date(c.requested_at);if(Date.now()-Date.parse(c.requested_at)>120000)status+=' Запуск задерживается. Служба автоматически повторяет проверку очереди; если ожидание сохраняется, проверьте журнал обновления на VPS.';}
 else if(c.update.at)status+=' '+date(c.update.at);
 $('software-update-status').textContent=status;
 }
 const auto=$('software-auto'),owner=hasDeveloperAccess(me);
 $('software-auto-row').hidden=!owner;
 auto.disabled=!owner||!c?.auto_supported||autoSaveSession===token;
 if(autoSaveSession!==token)auto.checked=c?.auto_enabled===true;
 const autoStatus=$('software-auto-status');
 autoStatus.textContent=!owner||!c?'':!c.auto_supported?'Для автообновления требуется новая версия службы на VPS.':c.auto_enabled?'Сервер проверяет стабильные выпуски каждый час, даже при закрытом приложении.':'Автообновление выключено.';
 if(owner&&c?.auto_enabled&&c.auto_checked_at){
  autoStatus.textContent+=' Последняя проверка: '+date(c.auto_checked_at)+'.';
  if(c.auto_check_state==='unavailable')autoStatus.textContent+=' Не удалось получить версию. Сервер повторит проверку.';
 }
 const retry=c?.update?.state==='failed'&&r?.state==='current';
 $('software-update').disabled=!c?.updater_installed||busy||!(r?.state==='available'||retry);
 $('software-update').textContent=busy?(c.requested?'Ожидаем запуск…':'Обновление выполняется…'):r?.state==='available'?'Установить v'+r.available_version:retry?'Повторить установку v'+r.available_version:'Установить обновление';
 const banner=$('software-update-notice');
 if(banner){banner.hidden=r?.state!=='available';$('software-update-notice-text').textContent=r?.state==='available'?'Доступно обновление v'+r.available_version+' · установлено v'+r.installed_version:'';}
}
async function loadControl({payments=false,replaceDraft=false}={}){
 if(me?.role!=='admin')return;
 const session=token;
 // Requests are shared only within this authenticated session.
 if(softwareSession!==session){softwareControl=null;softwareRelease=null;displayedVersion=null;controlFlight=null;releaseFlight=null;lastReleaseRequest=0;softwareSession=session;}
 if(!controlFlight){const pending=api('/admin/control');controlFlight=pending;pending.finally(()=>{if(controlFlight===pending)controlFlight=null;}).catch(()=>{});}
 let c;
 try{c=await controlFlight;}catch(error){
  if(payments&&session===token&&me?.role==='admin'){
   $('payment-switch-fields').disabled=true;
   $('payment-switch-feedback').textContent='Не удалось загрузить настройки. Обновите раздел.';
  }
  throw error;
 }
 if(session!==token||me?.role!=='admin')return;
 if(displayedVersion&&displayedVersion!==c.version&&c.update.state==='succeeded'){location.reload();return;}
 displayedVersion=c.version;softwareControl=c;
 if(payments){
 void window.pushokSBP?.load();
 showPaymentControls(c,replaceDraft);
 }
 renderSoftware();
}
async function checkSoftware(force=false){
 if(me?.role!=='admin')return;
 if(!force&&softwareRelease&&Date.now()-lastReleaseRequest<300000)return;
 if(releaseFlight)return releaseFlight;
 const session=token;lastReleaseRequest=Date.now();$('software-check').disabled=true;
 const pending=(async()=>{
 try{const data=await api('/admin/control/release');if(session!==token||me?.role!=='admin')return;softwareRelease=data;}
 catch(e){if(session!==token||me?.role!=='admin')return;softwareRelease={...softwareRelease,state:'unavailable',attempted_at:new Date().toISOString()};}
 finally{if(session===token&&me?.role==='admin'){$('software-check').disabled=false;renderSoftware();}}
 })();releaseFlight=pending;
 try{await pending;}finally{if(releaseFlight===pending)releaseFlight=null;}
}
async function refreshSoftware(force=false){
 if(me?.role!=='admin')return;
 const session=token;
 try{await loadControl();}catch(e){if(session!==token||me?.role!=='admin')return;softwareControl=null;$('software-update-status').textContent='Кабинет временно недоступен. Повторная проверка через 10 секунд…';$('software-update').disabled=true;}
 if(session===token&&me?.role==='admin')await checkSoftware(force);
}
$('payment-switches').addEventListener('change',paymentDraft);
$('payment-switches').addEventListener('submit',event=>{
 event.preventDefault();const fields=$('payment-switch-fields');
 if(fields.disabled||me?.role!=='admin'||paymentEditorSession!==token)return;
 const session=token;fields.disabled=true;
 run(async()=>{
  try{
   await api('/admin/control/payments',{method:'PUT',body:{stars:$('enable-stars').checked,sbp:$('enable-sbp').checked}});
  }catch(error){if(session===token)fields.disabled=false;throw error;}
  if(session!==token)return;
  await loadControl({payments:true,replaceDraft:true});
  await Promise.all([renderPaymentSettings(),renderSBPReview()]);
  if(session===token)notice('Настройки оплаты сохранены.',true);
 });
});
$('software-auto').addEventListener('change',()=>run(async()=>{
 const session=token,enabled=$('software-auto').checked;
 if(!hasDeveloperAccess(me)||autoSaveSession===session)return;
 autoSaveSession=session;renderSoftware();
 try{
  const result=await api('/admin/control/auto-update',{method:'PUT',body:{enabled}});
  if(session===token&&softwareControl)softwareControl.auto_enabled=result.enabled;
  if(session===token)await loadControl();
 }finally{if(session===token){autoSaveSession=null;renderSoftware();}}
}));
$('software-check').addEventListener('click',()=>refreshSoftware(true));
$('software-update').addEventListener('click',()=>run(async()=>{
 $('software-update').disabled=true;
 try{await api('/admin/control/update',{method:'POST'});await loadControl();}
 catch(e){await refreshSoftware();throw e;}
}));
setInterval(()=>{
 if(me?.role!=='admin'||document.hidden)return;
 const busy=softwareControl?.requested||softwareControl?.update?.state==='running';
 if(busy||!softwareControl||location.hash.startsWith('#/profile')||Date.now()-lastReleaseRequest>=300000)refreshSoftware();
},10000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&me?.role==='admin')refreshSoftware();});

function showIcons(data){
 const names={miniapp:'Mini App',advertise:'Разместить рекламу',campaigns:'Мои кампании',tariffs:'Тарифы',channels:'Наш канал',help:'Помощь'};
 const target=$('icons-mapping');target.replaceChildren();
 const captions=['Защита','Календарь','Реклама','Оплата','Новости','Telegram','Проверка','Отчёты','Доступ','Помощь','Подарки','Настройки'];
 const gallery=el('div',undefined,'emoji-gallery');
 data.items.forEach((item,i)=>{
  const tile=el('figure'),image=el('img');image.alt='PushokIcons '+(i+1);
  if(item.image?.startsWith('data:image/png;base64,'))image.src=item.image;
  else image.alt='Миниатюра недоступна';
  tile.append(image,el('figcaption',captions[i]||'Значок '+(i+1)));gallery.append(tile);
 });target.append(window.pushokViews.disclosure('Все значки набора · '+data.items.length,[gallery],'emoji-catalog'));
 for(const [key,name] of Object.entries(names)){
  const label=el('label',name,'emoji-mapping'),select=el('select');select.name=key;option(select,'','Не выбрана');
  data.items.forEach((item,i)=>option(select,item.id,captions[i]||'Значок '+(i+1)));
  const thumb=el('img');thumb.className='emoji-selection';thumb.alt='Выбранная иконка';
  const update=()=>{const item=data.items.find(x=>x.id===select.value);thumb.hidden=!item?.image;if(item?.image?.startsWith('data:image/png;base64,'))thumb.src=item.image;};
  select.value=data.mapping[key]||'';select.addEventListener('change',update);update();
  const row=el('span',undefined,'emoji-mapping-row');row.append(thumb,select);label.append(row);target.append(label);
 }
 $('icons-status').textContent=data.items.length?'Получено иконок: '+data.items.length+'. Фирменное меню включается только при выборе иконок всех кнопок главного меню. Иначе используются обычные эмодзи.':'Набор ещё не загружен.';
}
$('icons-import').addEventListener('click',()=>run(async()=>{showIcons(await api('/admin/icons/import',{method:'POST'}));},$('icons-import')));
$('icons-form').addEventListener('submit',e=>{e.preventDefault();run(async()=>{
 const mapping=Object.fromEntries([...new FormData(e.target)].filter(([,v])=>v));
 await api('/admin/icons',{method:'PUT',body:mapping});$('icons-status').textContent='Сохранено. Откройте меню бота для проверки.';
});});

const automationMessages={
 scheduled:['Ожидается автоматический запуск',s=>'Начало '+date(s.scheduled_start_at)+'. Подтверждать показ вручную не нужно.'],
 active:['Размещение запущено автоматически',()=> 'Агент подтвердил применение тега кампании на прокси.'],
 starting:['Ожидаем подтверждение запуска',()=> 'Назначенное время наступило. Статус изменится после ответа агента.'],
 awaiting_confirmation:['Проверяем состояние размещения',()=> 'Свежего подтверждения применения тега этой кампании пока нет.'],
 finishing:['Завершаем размещение',()=> 'Период закончился. После подтверждения снятия или смены тега кампания перейдёт в архив.'],
 completed:['Кампания завершена',()=> 'Отчёт и история размещения сохранены в архиве.'],
 cancelled:['Размещение отменено',()=> 'Автоматический запуск этой кампании отменён.'],
 start_missed:['Запуск не был подтверждён',()=> 'Назначенный период уже прошёл. После восстановления связи с агентом система подберёт новый период; он не считается отработанным.'],
 agent_unavailable:['Нет свежего ответа агента',()=> ['admin','operator'].includes(me?.role)?'Проверьте подключение агента в разделе «Спонсоры и узлы».':'Узел пока не подтвердил текущее размещение. Здесь появится результат следующей проверки.'],
 preparing_tag:['Привязываем канал в Telegram',()=> 'Автоматика готовит канал через @MTProxyBot. После проверки и обновления Telegram будет назначен полный оплаченный период.'],
 selecting_tag:['Подбираем готовый тег',()=> 'Система автоматически выберет ближайший свободный период на узле с подтверждённым тегом этого канала. Длительность тарифа сохранится.'],
 setup_required:['Ожидаем подготовку тега',s=>['admin','operator'].includes(me?.role)?'Подключите аккаунт владельца в настройках запуска: агент сам подготовит привязку через @MTProxyBot. Готовый тег также можно сохранить вручную.':'Готовим тег вашего канала. После подготовки система автоматически выберет ближайший свободный период; оплата сохранена.'],
 node_unavailable:['Узел недоступен для автозапуска',()=> 'Администратору нужно проверить состояние и настройки узла.'],
 awaiting_slot:['Подбираем свободный период',()=> 'Оплата подтверждена. Система автоматически назначит ближайшее свободное время на полную длительность тарифа.'],
 paused:['Автоподбор приостановлен',()=> 'Администратору нужно возобновить автоматическое назначение периода.'],
 awaiting_payment:['Ожидается оплата',()=> 'После подтверждения оплаты система подберёт период размещения.'],
 awaiting_moderation:['Ожидается модерация',()=> 'Автоматическое размещение станет доступно после одобрения канала и оплаты.']
};
async function updatePromotionPanel(panel){
 const inline=panel.dataset.promotionSummary==='inline';
 if(inline&&panel.parentElement?.querySelector('.automation-panel'))return;
 const session=token,request=(panel.promotionRequest||0)+1;panel.promotionRequest=request;
 if(!panel.hasChildNodes()){
  if(panel.dataset.promotionSummary)panel.textContent='Получаем статус автоматики…';
  else{panel.append(el('h4','Автоматическое размещение'),el('p','Получаем состояние…','automation-message'),el('p','','automation-agent muted'),el('p','','automation-error muted'),button('Закрыть',()=>{panel.closest('.campaign-detail')?.querySelector('[aria-label="Статус автоматического размещения"]')?.setAttribute('aria-expanded','false');panel.remove();}));}
 }
 try{
  const state=await api('/campaigns/'+panel.dataset.promotionCampaign+'/promotion-status');
  if(!panel.isConnected||session!==token||request!==panel.promotionRequest)return;
  const message=automationMessages[state.automation_status]||['Состояние уточняется',()=> 'Обновите данные. Подтверждения запуска пока нет.'];
  panel.dataset.automationStatus=state.automation_status||'unknown';
  if(inline){panel.textContent=message[0];return;}
  if(panel.dataset.promotionSummary){
   let heading=panel.querySelector('h3'),note=panel.querySelector('.automation-explanation');
   if(!heading){heading=el('h3');note=el('p',undefined,'automation-explanation');panel.replaceChildren(heading);window.pushokControls.help(panel,[note],'О текущем этапе');}
   heading.textContent=message[0];note.textContent=message[1](state);return;
  }
  const summary=panel.parentElement?.querySelector('.campaign-live-line');
  if(summary){summary.textContent=message[0];summary.dataset.automationStatus=panel.dataset.automationStatus;}
  panel.querySelector('h4').textContent=message[0];panel.querySelector('.automation-message').textContent=message[1](state);
  panel.querySelector('.automation-agent').textContent=state.checked_at?'Последний ответ агента: '+date(state.checked_at):'Ответ агента ещё не получен.';
  panel.querySelector('.automation-error').textContent='';
 }catch(e){
  if(!panel.isConnected||session!==token||request!==panel.promotionRequest)return;
  const text='Не удалось обновить статус автоматики. Повторите обновление.';
  if(panel.dataset.promotionSummary)panel.textContent=text;else panel.querySelector('.automation-error').textContent=text;
 }
}
async function renderPromotionStatus(c,parent){
 const text=el('section',undefined,'approval-automation-summary');text.dataset.promotionCampaign=c.id;text.dataset.promotionSummary='true';parent.append(text);await updatePromotionPanel(text);
}

Object.assign(errors,{schedule_before_requested_start:'Выберите время не раньше желаемого начала заявки.'});

// Автообновление статусов без вмешательства в редактируемую форму.
let statusRefreshBusy=false;
function editingControl(){
 if(window.pushokAssistant?.isBusy()||document.querySelector('dialog[open],input:focus,textarea:focus,select:focus'))return true;
 return [...document.querySelectorAll('.assistant-output:not(:empty),details[open]:not(.assistant-open)')].some(node=>node.getClientRects().length);
}
async function refreshStatuses(){
 const [,route,campaignId]=location.hash.split('/');
 const view=route||'dashboard';
 const orderResult=view==='create'&&Boolean(campaignId);
 if(!me||!token||document.hidden||statusRefreshBusy)return;
 if(!orderResult&&!['dashboard','campaigns','campaign','payments','moderation'].includes(view))return;
 if(editingControl())return;
 statusRefreshBusy=true;
 try{await refresh({background:true});}catch{}finally{statusRefreshBusy=false;}
}
setInterval(refreshStatuses,20000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refreshStatuses();});

async function renderHomeBanner(){
 if(me?.role!=='admin')return;const session=token;const data=await api('/admin/home-banner');if(session!==token||me?.role!=='admin')return;
 const title=$('home-banner-title'),subtitle=$('home-banner-subtitle'),preview=$('home-banner-preview');title.value=data.title;subtitle.value=data.subtitle;
 const show=()=>preview.replaceChildren(el('strong',title.value),el('p',subtitle.value));title.oninput=subtitle.oninput=show;show();
 $('home-banner-reset').onclick=()=>{title.value='Добро пожаловать в puShok';subtitle.value='Подключайтесь к MTProxy';show();};
 $('home-banner-form').onsubmit=async e=>{e.preventDefault();try{await api('/admin/home-banner',{method:'PUT',body:{title:title.value,subtitle:subtitle.value}});window.cabinet.config({...window.cabinetBotConfig,home_title:title.value.trim(),home_subtitle:subtitle.value.trim()});notice('Текст главной сохранён.',true);}catch(err){notice(err.message);}};
}
