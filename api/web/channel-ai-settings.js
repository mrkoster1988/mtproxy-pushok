'use strict';
window.pushokAI=(()=>{
 let generation=0,controller=null,session='',dirty=false,busy=false;
 const messages={
  channel_ai_invalid:'Проверьте модель и ключ: без пробелов и переносов строк.',
  channel_ai_key_required:'Введите ключ OpenAI API.',
  channel_ai_clear_requires_off:'Перед удалением ключа выключите ИИ.',
  channel_ai_changed:'Настройки уже изменены в другом окне. Обновите их перед сохранением.',
  channel_ai_key_rejected:'OpenAI отклонил ключ. Проверьте его или создайте новый.',
  channel_ai_access_denied:'OpenAI не разрешил запрос. Проверьте доступ проекта и сервера.',
  channel_ai_model_unavailable:'Модель недоступна этому ключу. Проверьте название и доступ проекта.',
  channel_ai_model_unsupported:'Модель не поддерживает этот способ определения тематики.',
  channel_ai_provider_limit:'OpenAI сообщил о лимите. Проверьте баланс API и лимиты проекта.',
  channel_ai_check_limited:'Слишком много проверок. Повторите немного позже.',
  channel_ai_response_invalid:'Модель не прошла проверку определения тематики. Выберите другую или повторите.',
  channel_ai_unavailable:'OpenAI не ответил. Проверьте соединение сервера и повторите.'
 };
 function reset(){generation++;controller?.abort();controller=null;session='';dirty=false;busy=false;$('channel-ai-settings')?.replaceChildren();}
 async function request(path,options={}){
  controller?.abort();const own=new AbortController();controller=own;
  const timer=setTimeout(()=>own.abort(),10000);
  try{return await api('/admin/channel-ai'+path,{...options,signal:own.signal});}
  finally{clearTimeout(timer);if(controller===own)controller=null;}
 }
 async function load(force=false){
  const host=$('channel-ai-settings');if(!host||me?.role!=='admin'){reset();return;}
  if(!force&&session===token&&(dirty||busy))return;
  const currentSession=token,uid=me.id,version=++generation;session=token;
  const valid=()=>version===generation&&currentSession===token&&uid===me?.id&&me?.role==='admin';
  host.replaceChildren(el('p','Загружаем настройки ИИ…','muted'));
  try{
   const saved=await request('');if(!valid())return;dirty=false;busy=false;
   const box=el('details',undefined,'ai-settings-card'),heading=el('summary');box.open=!saved.key_saved;
   const title=el('span','ИИ · Возможности','ai-settings-title');title.prepend(window.pushokViews.icon('filter'));
   const badge=el('span',saved.enabled?'Включён':'Выключен','ai-state');badge.classList.toggle('active',saved.enabled);
   heading.append(title,badge);box.append(heading);
   const content=el('div',undefined,'ai-settings-content'),form=el('form'),fields=el('fieldset');
   form.append(fields);
   const row=el('label','Использовать ИИ','ai-switch'),enabled=el('input');enabled.type='checkbox';enabled.id='channel-ai-enabled';enabled.checked=saved.enabled;row.append(enabled);fields.append(row);
   const permissions=el('fieldset',undefined,'ai-permissions');permissions.append(el('legend','Что разрешено ИИ'));
   const choices=[['topics','Предлагать тематику канала'],['campaign_review','Анализировать заявки и готовить комментарии'],['launch_diagnostics','Объяснять проблемы с запуском']];
   const capabilities=choices.map(([id,text])=>{const label=el('label',text,'ai-switch'),input=el('input');input.type='checkbox';input.id='channel-ai-cap-'+id;input.value=id;input.checked=(saved.capabilities||['topics']).includes(id);label.append(input);permissions.append(label);return input;});fields.append(permissions);
   function input(label,id,type='text'){const l=el('label',label),i=el('input');i.id=id;i.type=type;l.append(i);fields.append(l);return i;}
   const model=input('Модель OpenAI','channel-ai-model');model.value=saved.model;model.required=true;model.maxLength=80;model.pattern='[A-Za-z0-9._-]{1,80}';model.spellcheck=false;model.autocomplete='off';model.setAttribute('list','channel-ai-models');
   const models=el('datalist');models.id='channel-ai-models';option(models,'gpt-4.1-mini','gpt-4.1-mini');fields.append(models);
   const key=input('Ключ OpenAI API','channel-ai-key','password');key.maxLength=512;key.autocomplete='new-password';key.spellcheck=false;
   key.placeholder=saved.key_saved?'Сохранён · введите для замены':'Вставьте ключ API';
   const keyState=el('p',saved.source==='invalid'?'Не удалось прочитать настройки. Введите ключ заново.':saved.key_saved?(saved.source==='environment'?'Используется ключ из настроек сервера.':'Ключ хранится на сервере.'):'Ключ ещё не добавлен.','muted');fields.append(keyState);
   const help=window.pushokViews.disclosure('Как работает ИИ',[
    el('p','Тематика определяется при создании заявки. Анализ и диагностика доступны администраторам и операторам по кнопке в карточке модерации. ИИ предлагает следующие шаги и черновик комментария; решения принимает сотрудник.','muted'),
    el('p','Уже есть ChatGPT? Здесь нужен ключ OpenAI API, а не ссылка на профиль. Откройте API Platform с тем же аккаунтом, создайте ключ и вставьте его в поле ниже. Нажмите «Проверить», включите ИИ и разрешение «Предлагать тематику канала», затем нажмите «Сохранить».','muted'),
    el('p','У подходящих подписок ChatGPT Pro и Business доступны API-кредиты. Проверьте льготу в ChatGPT → Настройки → Оплата или в Billing на API Platform и активируйте её для своей API-организации. Наличие подписки само по себе не подключает кабинет.','muted'),
    el('p','Запросы OpenAI API, включая проверку, оплачиваются в вашем API-проекте. Передаются публичные тексты канала и статусы заявки. Ключи, телефоны, балансы и закрытые переписки не передаются. При сбое остаются проверки системы. Полномочия ИИ не включают изменение прав, оплат и расписания.','muted')
   ],'inline-help');
   const link=el('a','Получить ключ OpenAI API ↗');link.href='https://platform.openai.com/api-keys';link.target='_blank';link.rel='noopener noreferrer';help.append(link);
   const credits=el('a','API-кредиты подписки ChatGPT ↗');credits.href='https://developers.openai.com/api/docs/guides/chatgpt-api-credits';credits.target='_blank';credits.rel='noopener noreferrer';const creditsRow=el('p');creditsRow.append(credits);help.append(creditsRow);
   const status=el('p',saved.checked_at?'Последняя успешная проверка: '+date(saved.checked_at):'Подключение ещё не проверено.','ai-settings-status');status.id='channel-ai-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
   const actions=el('div',undefined,'ai-settings-actions'),check=el('button','Проверить'),save=el('button','Сохранить','primary');check.type='button';check.id='channel-ai-check';save.type='submit';save.id='channel-ai-save';actions.append(check,save);fields.append(actions);
   const remove=el('button','Отключить и удалить ключ','ai-remove danger-text');remove.type='button';remove.id='channel-ai-remove';remove.hidden=!saved.key_saved;fields.append(remove);
   const reload=el('button','Обновить настройки');reload.type='button';reload.hidden=true;reload.addEventListener('click',()=>load(true));
   form.addEventListener('input',()=>{dirty=true;status.textContent='Есть несохранённые изменения.';});
   function payload(){const body={enabled:enabled.checked,model:model.value.trim(),revision:saved.revision,capabilities:capabilities.filter(i=>i.checked).map(i=>i.value)};if(key.value.trim())body.key=key.value.trim();return body;}
   async function perform(kind){
    if(busy||!valid()||(kind!=='remove'&&!form.reportValidity()))return;
    const body=kind==='remove'?{enabled:false,model:saved.model,revision:saved.revision,clear_key:true}:payload();
    if(kind==='remove'&&!window.confirm('Отключить ИИ и удалить сохранённый ключ? Для подключения потребуется ввести ключ снова.'))return;
    busy=true;fields.disabled=true;reload.hidden=true;status.textContent=kind==='check'?'Проверяем ключ и модель…':body.enabled?'Проверяем и сохраняем…':'Сохраняем…';
    try{
     const result=await request(kind==='check'?'/check':'',{method:kind==='check'?'POST':'PUT',body});if(!valid())return;
     if(kind==='check'){status.textContent='Проверка пройдена. Нажмите «Сохранить», чтобы применить настройки.';}
     else{
      key.value='';dirty=false;busy=false;await load(true);if(currentSession!==token||me?.role!=='admin')return;
      const fresh=$('channel-ai-status');if(fresh)fresh.textContent=result.enabled?'ИИ включён. Подключение проверено.':kind==='remove'?(saved.source==='environment'?'ИИ отключён. Ключ из настроек сервера больше не используется.':'Ключ удалён. Работают подсказки по ключевым словам.'):'Настройки сохранены. ИИ выключен.';
     }
    }catch(error){if(valid()){status.textContent=messages[error.code]||'Не удалось выполнить запрос. Проверьте соединение и повторите.';reload.hidden=error.code!=='channel_ai_changed';}}
    finally{if(valid()){busy=false;fields.disabled=false;}}
   }
   check.addEventListener('click',()=>perform('check'));remove.addEventListener('click',()=>perform('remove'));
   form.addEventListener('submit',event=>{event.preventDefault();perform('save');});
   content.append(help,form,status,reload);box.append(content);host.replaceChildren(box);
  }catch(error){if(valid())host.replaceChildren(el('p','Не удалось загрузить настройки ИИ.','muted'),button('Повторить',()=>load(true)));}
 }
 return {load,reset};
})();
