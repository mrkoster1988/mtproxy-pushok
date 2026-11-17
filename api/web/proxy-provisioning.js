'use strict';
window.pushokProvisioning=(()=>{
 let generation=0,controller=null,dirty=false,busy=false,session='';
 const labels={disabled:'Выключено',no_demand:'Свободных слотов достаточно',waiting:'Ожидаем проверку очереди',
  provisioning:'Готовим новый прокси',active:'Прокси создан',ready:'Агент готов',
  limit_reached:'Достигнут лимит или требуется проверка ошибки',daily_limit:'Не более одного нового прокси в сутки',
  evaluation_cooldown:'Следующая оценка очереди — через 15 минут',ai_unavailable:'ИИ не ответил — повторим позже',
  resources_low:'Недостаточно свободных ресурсов VPS',owner_unavailable:'Подключите аккаунт владельца Telegram',
  port_busy:'Порт занят',host_unavailable:'Проверьте установку агента на VPS',
  registration_uncertain:'Telegram не подтвердил создание. Проверьте /myproxies перед повтором.',
  registration_prompt_invalid:'Ответ @MTProxyBot изменился — нужна проверка',proxy_not_found:'Регистрация ещё не найдена',
  registration_secret_mismatch:'Регистрация не соответствует секрету экземпляра',
  proxy_ambiguous:'Не удалось однозначно определить прокси',proxy_identity_not_verified:'Адрес прокси не подтверждён',
  telegram_flood_wait:'Telegram ограничил частоту запросов',telegram_unavailable:'Telegram временно недоступен',
  bot_response_timeout:'Telegram не ответил вовремя',start_failed:'Новый экземпляр не запустился',
  policy_changed:'Условия изменились — продолжите подготовку кнопкой ниже',retry_later:'Ожидаем повторную попытку',
  agent_busy:'Агент занят другой операцией',connection_busy:'Аккаунт Telegram занят другой проверкой',
  agent_setup_failed:'Не удалось установить службу нового экземпляра'};
 function reset(){generation++;controller?.abort();controller=null;dirty=false;busy=false;session='';$('proxy-provisioning-settings')?.replaceChildren();}
 async function load(force=false){
  const host=$('proxy-provisioning-settings');if(!host||!me?.is_owner){reset();return;}
  if(!force&&session===token&&(dirty||busy))return;
  controller?.abort();controller=new AbortController();const signal=controller.signal;
  const current=++generation,ownSession=token,user=me.id;session=token;
  const valid=()=>current===generation&&ownSession===token&&user===me?.id&&me?.is_owner;
  host.replaceChildren(el('p','Проверяем автосоздание прокси…','muted'));
  try{
   const saved=await api('/admin/proxy-provisioning',{signal});if(!valid())return;
   dirty=false;busy=false;
   const box=el('details',undefined,'ai-settings-card');box.id='proxy-provisioning-card';
   const heading=el('summary'),title=el('span','ИИ · Новые прокси','ai-settings-title');
   title.prepend(window.pushokViews.icon('server'));heading.append(title,el('span',saved.enabled?'Включено':'Выключено','ai-state'));box.append(heading);
   const content=el('div',undefined,'ai-settings-content'),form=el('form'),fields=el('fieldset');
   const info=el('p','При нехватке слотов ИИ может создать дополнительный экземпляр на этом VPS. Серверы не покупаются. Новые подключения распределяются между готовыми прокси; отдельный порт сам по себе не увеличивает аудиторию.','muted');
   const row=el('label','Разрешить автоматическое создание','ai-switch'),enabled=el('input');enabled.type='checkbox';enabled.id='proxy-provisioning-enabled';enabled.checked=saved.enabled;row.append(enabled);fields.append(row);
   function number(label,id,value,min,max){const l=el('label',label),input=el('input');input.type='number';input.id=id;input.value=value;input.min=min;input.max=max;input.step=1;input.required=true;l.append(input);fields.append(l);return input;}
   const maximum=number('Максимум дополнительных экземпляров','proxy-provisioning-max',saved.max_instances,1,3);
   const wait=number('Создавать, если очередь задерживает запуск на минут','proxy-provisioning-wait',saved.wait_minutes,90,10080);
   const port=number('Первый порт для новых экземпляров','proxy-provisioning-port',saved.first_port,1024,65533);
   fields.append(el('p','Не более одного нового экземпляра в сутки. Перед запуском проверяются память, загрузка VPS и свободный порт. Неудачная подготовка повторяется до 5 раз с тем же портом.','muted'));
   const status=el('p',undefined,'ai-settings-status');status.id='proxy-provisioning-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
   const agent=saved.agent;
   status.textContent=!saved.ai_ready?'В настройках ИИ выше включите разрешение на создание прокси.':!agent?.recent?'Нет свежего отчёта. После обновления VPS агент появится здесь.':labels[agent.code]||'Проверьте состояние подготовки.';
   if(agent?.checked_at)content.append(el('p','Агент проверен: '+date(agent.checked_at),'muted'));
   const actions=el('div',undefined,'ai-settings-actions'),save=el('button','Сохранить','primary'),refresh=el('button','Обновить');save.type='submit';save.id='proxy-provisioning-save';refresh.type='button';refresh.addEventListener('click',()=>load(true));actions.append(save,refresh);fields.append(actions);form.append(fields);
   form.addEventListener('input',()=>{dirty=true;status.textContent='Есть несохранённые изменения.';});
   form.addEventListener('submit',async event=>{
    event.preventDefault();if(busy||!valid()||!form.reportValidity())return;
    busy=true;fields.disabled=true;status.textContent='Сохраняем…';
    try{
     await api('/admin/proxy-provisioning',{method:'PUT',signal,body:{enabled:enabled.checked,max_instances:Number(maximum.value),wait_minutes:Number(wait.value),first_port:Number(port.value),revision:saved.revision}});
     if(valid()){busy=false;dirty=false;await load(true);if(ownSession===token&&me?.is_owner){const fresh=$('proxy-provisioning-card');if(fresh)fresh.open=true;}}
    }catch(error){if(valid())status.textContent=error.code==='provisioning_ai_required'?'Сначала включите ИИ и разрешение на создание прокси.':error.code==='provisioning_changed'?'Настройки изменились в другом окне. Нажмите «Обновить».':'Не удалось сохранить настройки.';}
    finally{if(valid()){busy=false;fields.disabled=false;}}
   });
   content.append(info,form,status);
   const jobs=el('div',undefined,'proxy-provisioning-jobs');
   for(const job of saved.jobs){
    const card=el('article',undefined,'proxy-provisioning-job');
    card.append(el('strong',job.name),el('p',job.host+':'+job.port,'muted'),el('p',job.state==='active'?'Создан · '+(job.status==='active'?'Доступен для размещений':'На паузе'):labels[job.error_code]||'Подготовка · попыток: '+job.attempts,'muted'));
    if(job.state==='pending')card.append(el('p','Повтор не раньше: '+date(job.retry_at),'muted'));
    if(job.state!=='active'){
     const retry=el('button','Продолжить подготовку');retry.type='button';retry.disabled=!saved.enabled||!saved.ai_ready;
     retry.addEventListener('click',async()=>{if(busy||!valid())return;busy=true;retry.disabled=true;
      try{await api('/admin/proxy-provisioning/'+job.id+'/retry',{method:'POST',signal});if(valid()){busy=false;await load(true);if(ownSession===token&&me?.is_owner)$('proxy-provisioning-card').open=true;}}
      catch(error){if(valid())status.textContent='Не удалось продолжить. Проверьте настройки и обновите состояние.';}
      finally{if(valid()){busy=false;retry.disabled=false;}}
     });card.append(retry);
    }
    jobs.append(card);
   }
   content.append(jobs);box.append(content);host.replaceChildren(box);
  }catch(error){if(valid())host.replaceChildren(el('p','Не удалось загрузить автосоздание прокси.','muted'),button('Повторить',()=>load(true)));}
 }
 return {load,reset};
})();
