'use strict';
window.pushokProvisioning=(()=>{
 let generation=0,controller=null,dirty=false,busy=false,session='',expanded;
 const labels={disabled:'Выключено',no_demand:'Дополнительный прокси пока не требуется',waiting:'Ожидаем проверку очереди',
  wait:'ИИ решил подождать следующей оценки очереди',
  provisioning:'Готовим новый прокси',active:'Прокси создан',ready:'Агент готов',
  limit_reached:'Достигнут лимит или требуется проверка ошибки',daily_limit:'Не более одного нового прокси в сутки',
  evaluation_cooldown:'Ожидаем следующую оценку очереди',ai_unavailable:'ИИ не ответил — повторим позже',
  resources_low:'Недостаточно свободных ресурсов VPS',owner_unavailable:'Подключите аккаунт владельца Telegram',
  port_busy:'Порт занят',host_unavailable:'Проверьте установку агента на VPS',
  registration_uncertain:'Telegram не подтвердил создание. Проверьте /myproxies перед повтором.',
  registration_prompt_invalid:'Ответ @MTProxyBot изменился — нужна проверка',proxy_not_found:'Регистрация ещё не найдена',
  registration_secret_mismatch:'Регистрация не соответствует секрету экземпляра',
  registration_conflict:'Этот тег уже занят другим узлом',tag_not_verified:'Тег нового экземпляра не подтверждён',
  proxy_ambiguous:'Не удалось однозначно определить прокси',proxy_identity_not_verified:'Адрес прокси не подтверждён',
  telegram_flood_wait:'Ждём окончания паузы Telegram',telegram_unavailable:'Telegram временно недоступен',
  bot_button_expired:'Кнопка @MTProxyBot изменилась — повторим с новым списком',
  proxy_list_changed:'Список прокси изменился — повторно проверим нужный узел',
  recovery_pending:'Сверяем результат запуска с базой',preparation_interrupted:'Подготовка была прервана',
  bot_response_timeout:'Telegram не ответил вовремя',start_failed:'Новый экземпляр не запустился',
  policy_changed:'Условия изменились — продолжите подготовку кнопкой ниже',retry_later:'Ожидаем повторную попытку',
  agent_busy:'Агент занят другой операцией',connection_busy:'Аккаунт Telegram занят другой проверкой',
  agent_setup_failed:'Не удалось установить службу нового экземпляра',
  native_unavailable:'Файлы MTProxy отсутствуют или недоступны. Выполните восстановление установки на VPS.',
  timer_unavailable:'Таймеры автоматики остановлены. Восстановите хостовый агент.',
  api_unavailable:'Агент не получил настройки из базы. Проверьте службу кабинета.',
  api_timeout:'Кабинет не ответил за 45 секунд. Проверка повторится автоматически.',
  api_invalid_response:'Ответ кабинета не распознан. Обновите кабинет и хостовый агент вместе.',
  port_check_failed:'Не удалось проверить локальные порты. Настройки кабинета получены.',
  unknown:'Пока не проверено',instance_limit:'Все разрешённые экземпляры уже созданы',
  preparation_pending:'Продолжается подготовка ранее созданного экземпляра',
  preparation_failed:'Сначала завершите подготовку с ошибкой в списке ниже'};
 function readinessView(report,saved){
  const card=el('section',undefined,'proxy-readiness');card.id='proxy-readiness';
  card.append(el('h4','Готовность VPS'));
  const policy=report?.checks?.policy;
  const policyMatches=!saved.enabled?policy==='disabled':saved.ai_ready?!['disabled','ai_unavailable'].includes(policy):policy==='ai_unavailable';
  const matching=report?.current_policy===true&&policyMatches;
  const backendFailed=report&&report.checks.api!=='ready';
  const fresh=report?.recent===true&&(matching||backendFailed);
  const pending=report?.recent===true&&!matching&&!backendFailed;
  const title=!report?'Проверка ещё не получена':!report.recent?'Проверка устарела':backendFailed?'Проверка кабинета не завершена':pending?'Ожидаем проверку новых настроек':Object.values(report.checks).every(v=>v==='ready')?'Предварительные проверки пройдены':'Есть ограничения для запуска';
  card.append(el('p',title,'muted'));
  if(report?.checked_at)card.append(el('p','Последняя проверка: '+date(report.checked_at),'muted'));
  if(report?.source)card.append(el('p',report.source==='manual'?'Источник: ручная проверка на сервере':'Источник: проверка по расписанию','muted'));
  if(!fresh)card.append(el('p','Агент проверяет VPS автоматически каждые 5 минут. Нажмите «Обновить», чтобы получить новый отчёт.','muted'));
  if(pending)card.append(el('p','Изменения сохранены. Агент ещё не подтвердил проверку текущих настроек.','muted'));
  const names={host:'Адрес VPS',native:'Файлы MTProxy',timers:'Запуск по расписанию',telegram:'Сессия Telegram',resources:'Ресурсы VPS',api:'Связь с кабинетом',policy:'Разрешение ИИ',ports:'Порты',capacity:'Лимит экземпляров'};
  const list=el('ul',undefined,'proxy-readiness-list');
  for(const [key,name] of Object.entries(names)){
   const awaiting=pending&&['policy','ports','capacity'].includes(key);
   const code=awaiting?'unknown':report?.checks?.[key]||'unknown',row=el('li'),heading=el('strong',name);
   const passed=fresh&&code==='ready';
   heading.prepend(window.pushokViews.icon(passed?'check':'info'));
   if(passed)row.classList.add('is-ready');
   const text=awaiting?'Ожидаем нового отчёта':code==='ready'?(!fresh?'Проверено ранее':key==='telegram'?'Сессия сохранена · без запроса в Telegram':'Проверено'):
    key==='policy'&&code==='ai_unavailable'?'Включите подключение ИИ и разрешение на создание прокси.':
    key==='api'&&code==='agent_busy'?'Агент занят обслуживанием. Проверка повторится автоматически.':
    labels[code]||'Пока не проверено';
   row.append(heading,el('span',text,'muted'));list.append(row);
  }
  card.append(list);
  if(fresh&&report.retry_at)card.append(el('p','Telegram: повтор после '+date(report.retry_at),'muted'));
  card.append(el('p','Готовность VPS не подтверждает регистрацию в Telegram или показ спонсора. Новый прокси создаётся для первого резерва или задержанной очереди при выполнении настроенных условий.','muted'));
  const help=el('details'),summary=el('summary','Проверить на сервере');
  help.append(summary,el('p','Команда обновит отчёт без создания прокси и обращения к ИИ или Telegram.','muted'),el('code','sudo python3 /opt/mtproxy-pushok/scripts/proxy_provisioner.py check'));
  card.append(help);return card;
 }
 function queueView(report,saved){
  const card=el('section',undefined,'proxy-readiness');card.id='proxy-queue';card.append(el('h4','Очередь кампаний'));
  const queue=report?.queue;
  if(!report?.recent||!report.current_policy||report.checks.api!=='ready'||!queue||queue.wait_minutes!==saved.wait_minutes){
   card.append(el('p','Ожидаем свежую проверку очереди.','muted'));return card;
  }
  const reasons={no_nodes:'На этом VPS нет активного узла для оценки очереди.',
   no_campaigns:'Нет подходящих заявок на ближайшие 24 часа.',
   below_threshold:queue.max_delay_seconds===0?'Подходящие заявки помещаются в свободные слоты.':'Задержка очереди ниже порога автосоздания.',
   delayed:'Задержка достигла порога для рассмотрения нового прокси.'};
  card.append(el('p',reasons[queue.reason],'muted'));
  const details=el('details'),summary=el('summary','Расчёт очереди');details.append(summary);
  details.append(el('p','Проверено заявок: '+queue.campaign_count+' · Активных узлов VPS: '+queue.node_count,'muted'));
  details.append(el('p','Заявок с задержкой от '+queue.wait_minutes+' мин: '+queue.delayed_count,'muted'));
  if(queue.node_count&&queue.campaign_count)details.append(el('p','Наибольшая задержка: '+Math.floor(queue.max_delay_seconds/60)+' мин','muted'));
  if(queue.limited)details.append(el('p','Показан расчёт первых 100 подходящих заявок; очередь больше этого списка.','muted'));
  details.append(el('p','Проверено: '+date(report.checked_at),'muted'),el('p','Учитываются одобренные и оплаченные заявки с подтверждённым каналом. Заявки на паузе, с ошибкой привязки или уже начатым показом исключены. Создание также зависит от разрешений, ресурсов и лимитов.','muted'));
  card.append(details);return card;
 }
 function reset(){expanded=undefined;generation++;controller?.abort();controller=null;dirty=false;busy=false;session='';$('proxy-provisioning-settings')?.replaceChildren();}
 async function load(force=false){
  const host=$('proxy-provisioning-settings');if(!host||!me?.is_owner){reset();return;}
  if(!force&&session===token&&(dirty||busy))return;
  controller?.abort();controller=new AbortController();const signal=controller.signal;
  const current=++generation,ownSession=token,user=me.id;session=token;
  const valid=()=>current===generation&&ownSession===token&&user===me?.id&&me?.is_owner;
  const previous=host.querySelector('#proxy-provisioning-card');
  if(previous)expanded=previous.open;
  host.replaceChildren(el('p','Проверяем автосоздание прокси…','muted'));
  try{
   const saved=await api('/admin/proxy-provisioning',{signal});if(!valid())return;
   dirty=false;busy=false;
   const box=el('details',undefined,'ai-settings-card');box.id='proxy-provisioning-card';box.open=expanded??false;
   const heading=el('summary'),title=el('span','ИИ · Новые прокси','ai-settings-title');
   const badge=el('span',saved.enabled?'Включено':'Выключено','ai-state');badge.classList.toggle('active',saved.enabled);
   title.prepend(window.pushokViews.icon('server'));heading.append(title,badge);box.append(heading);
   const content=el('div',undefined,'ai-settings-content'),form=el('form'),fields=el('fieldset');
   const info=el('p','При нехватке слотов ИИ может создать дополнительный экземпляр на этом VPS. Серверы не покупаются. Новые подключения распределяются между готовыми прокси; отдельный порт сам по себе не увеличивает аудиторию.','muted block-note');
   const row=el('label','Разрешить автоматическое создание','ai-switch'),enabled=el('input');enabled.type='checkbox';enabled.id='proxy-provisioning-enabled';enabled.checked=saved.enabled;row.append(enabled);fields.append(row);
   const reserveRow=el('label','Подготовить один резервный прокси','ai-switch'),reserve=el('input');reserve.type='checkbox';reserve.id='proxy-provisioning-reserve';reserve.checked=saved.reserve_enabled===true;reserveRow.append(reserve);fields.append(reserveRow);
   fields.append(el('p','Если дополнительных экземпляров ещё нет, агент подготовит первый без ожидания рекламной очереди и решения модели. Нужно разрешение на автоматическое создание. Уже созданный экземпляр учитывается; подготовка с ошибкой продолжается отдельно.','muted'));
   fields.append(el('p','При включённом едином входе готовый прокси с общим ключом подключится к нему автоматически. Это резерв процесса на том же VPS: новое соединение сможет выбрать другой узел.','muted'));
   function number(label,id,value,min,max){const l=el('label',label),input=el('input');input.type='number';input.id=id;input.value=value;input.min=min;input.max=max;input.step=1;input.required=true;l.append(input);fields.append(l);return input;}
   const maximum=number('Максимум дополнительных экземпляров','proxy-provisioning-max',saved.max_instances,1,3);
   const wait=number('Создавать, если очередь задерживает запуск на минут','proxy-provisioning-wait',saved.wait_minutes,90,10080);
   const port=number('Первый порт для новых экземпляров','proxy-provisioning-port',saved.first_port,1024,65533);
   fields.append(el('p','Не более одного нового экземпляра в сутки. Перед запуском проверяются память, загрузка VPS и свободный порт. Неудачная подготовка повторяется до 5 раз с тем же портом.','muted'));
   const status=el('p',undefined,'ai-settings-status');status.id='proxy-provisioning-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
   const agent=saved.agent;
   status.textContent=!saved.enabled?'Автоматическое создание выключено в настройках.':!saved.ai_ready?'В настройках ИИ выше включите разрешение на создание прокси.':!agent?.recent?'Нет свежего отчёта. Ожидаем проверку агента.':!agent.current_policy||agent.code==='disabled'?'Настройки сохранены. Ожидаем проверку новых настроек агентом.':labels[agent.code]||'Проверьте состояние подготовки.';
   if(agent?.checked_at)content.append(el('p','Последний ответ агента: '+date(agent.checked_at),'muted'));
   if(agent?.recent&&agent.retry_at)content.append(el('p','Telegram: следующая попытка после '+date(agent.retry_at),'muted'));
   if(agent?.recent&&agent.current_policy&&agent.code==='evaluation_cooldown'){
    const next=Date.parse(saved.evaluated_at)+15*60*1000;
    if(Number.isFinite(next)&&next>Date.now())content.append(el('p','Следующая оценка — не раньше '+date(new Date(next).toISOString()),'muted'));
   }
   const actions=el('div',undefined,'ai-settings-actions'),save=el('button','Сохранить','primary'),refresh=el('button','Обновить');save.type='submit';save.id='proxy-provisioning-save';refresh.type='button';refresh.addEventListener('click',()=>load(true));actions.append(save,refresh);fields.append(actions);form.append(fields);
   form.addEventListener('input',()=>{dirty=true;status.textContent='Есть несохранённые изменения.';});
   form.addEventListener('submit',async event=>{
    event.preventDefault();if(busy||!valid()||!form.reportValidity())return;
    busy=true;fields.disabled=true;status.textContent='Сохраняем…';
    try{
     await api('/admin/proxy-provisioning',{method:'PUT',signal,body:{enabled:enabled.checked,reserve_enabled:reserve.checked,max_instances:Number(maximum.value),wait_minutes:Number(wait.value),first_port:Number(port.value),revision:saved.revision}});
     if(valid()){busy=false;dirty=false;await load(true);}
    }catch(error){if(valid())status.textContent=error.code==='provisioning_ai_required'?'Сначала включите ИИ и разрешение на создание прокси.':error.code==='provisioning_changed'?'Настройки изменились в другом окне. Нажмите «Обновить».':'Не удалось сохранить настройки.';}
    finally{if(valid()){busy=false;fields.disabled=false;}}
   });
   content.append(info,form,status,queueView(saved.readiness,saved),readinessView(saved.readiness,saved));
   const jobs=el('div',undefined,'proxy-provisioning-jobs');
   for(const job of saved.jobs){
    const card=el('article',undefined,'proxy-provisioning-job');
    card.append(el('strong',job.name),el('p',job.host+':'+job.port,'muted'),el('p',job.state==='active'?'Создан · '+(job.status==='active'?'Доступен для размещений':'На паузе'):labels[job.error_code]||'Подготовка · попыток: '+job.attempts,'muted'));
    if(job.reason==='reserve')card.append(el('p','Причина: подготовка резерва','muted'));
    const cooling=()=>job.error_code==='telegram_flood_wait'&&Date.parse(job.retry_at)>Date.now();
    if(job.state==='pending'||cooling())card.append(el('p','Повтор не раньше: '+date(job.retry_at),'muted'));
    if(job.state!=='active'){
     const retry=el('button',cooling()?'Пауза Telegram':'Продолжить подготовку');retry.type='button';retry.disabled=!saved.enabled||!saved.ai_ready||cooling();
     if(cooling())card.append(el('p',job.state==='pending'?'Продолжим автоматически после паузы.':'После паузы обновите состояние и продолжите подготовку.','muted'));
     retry.addEventListener('click',async()=>{if(busy||!valid()||cooling())return;busy=true;retry.disabled=true;
      try{await api('/admin/proxy-provisioning/'+job.id+'/retry',{method:'POST',signal});if(valid()){busy=false;await load(true);}}
      catch(error){if(valid())status.textContent=error.code==='provisioning_telegram_cooldown'?'Пауза Telegram ещё действует. Обновите состояние, чтобы увидеть время повтора.':'Не удалось продолжить. Проверьте настройки и обновите состояние.';}
      finally{if(valid()){busy=false;retry.disabled=!saved.enabled||!saved.ai_ready||cooling();}}
     });card.append(retry);
    }
    jobs.append(card);
   }
   content.append(jobs);box.append(content);host.replaceChildren(box);
  }catch(error){if(valid())host.replaceChildren(el('p','Не удалось загрузить автосоздание прокси.','muted'),button('Повторить',()=>load(true)));}
 }
 return {load,reset};
})();
