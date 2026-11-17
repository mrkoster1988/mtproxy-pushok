'use strict';
window.pushokGateway=(()=>{
 let epoch=0,profileEpoch=0,settingsEpoch=0,connectionAt=0,connectionSession='',dirty=false;
 Object.assign(errors,{
  gateway_settings_changed:'Настройки изменились. Обновите их перед сохранением.',
  gateway_port_conflict:'Порт занят узлом или служебным интерфейсом. Выберите другой диапазон.',
  gateway_disable_before_change:'Сначала выключите единый вход. Смена портов изменит выданные ссылки.',
  proxy_interest_invalid:'Выберите тему из списка.'
 });
 const statuses={ready:'Единый вход работает',disabled:'Единый вход выключен',dependency_missing:'Нужно установить компонент единого входа на VPS',
  no_nodes:'Нет доступных узлов с общим ключом',port_busy:'Порт занят другой службой',check_failed:'Не удалось проверить единый вход'};
 function reset(){window.cabinet?.proxyConnection(window.cabinetBotConfig?.proxy_url||'');epoch++;profileEpoch++;settingsEpoch++;connectionAt=0;connectionSession='';dirty=false;$('proxy-gateway-settings')?.replaceChildren();}
 async function connection(force=false){
  if(!me||!token||!force&&connectionSession===token&&Date.now()-connectionAt<20000)return;
  const session=token,user=me.id,version=++epoch;connectionSession=session;connectionAt=Date.now();
  try{const data=await api('/me/proxy-connection');if(session===token&&user===me?.id&&version===epoch)window.cabinet?.proxyConnection(data.url);}
  catch{if(version===epoch)connectionAt=0;}
 }
 function profile(target){
  const card=el('details',undefined,'panel proxy-interest');card.id='proxy-interest';
  card.append(el('summary','Мои интересы'));
  const body=el('div',undefined,'ai-settings-content');card.append(body);target.append(card);
  const session=token,user=me?.id,version=++profileEpoch;
  const current=()=>session===token&&user===me?.id&&version===profileEpoch&&card.isConnected;
  let loaded=false;
  card.addEventListener('toggle',async()=>{
   if(!card.open||loaded)return;loaded=true;body.replaceChildren(el('p','Загружаем…','muted'));
   try{
    const data=await api('/me/proxy-interest');if(!current())return;
    const form=el('form'),label=el('label','Интересующая тема'),select=el('select');select.id='proxy-interest-topic';
    select.append(new Option('Не указывать',''));for(const item of data.topics)select.append(new Option(item.label,item.id));select.value=data.topic||'';label.append(select);
    const note=el('p','Необязательно. Сохраняем только выбранную вами тему для подбора узла со спонсором по интересам. Переписку и подписки Telegram не читаем. Выберите «Не указывать», чтобы удалить тему.','muted');
    const feedback=el('p',undefined,'muted');feedback.setAttribute('role','status');
    const actions=el('div',undefined,'actions'),save=el('button','Сохранить тему');save.type='submit';
    const connect=el('a','Подключиться','ui-button');connect.hidden=true;connect.target='_blank';connect.rel='noopener noreferrer';
    actions.append(save,connect);form.append(note,label,actions,feedback);body.replaceChildren(form);
    async function updateLink(){
     const result=await api('/me/proxy-connection');if(!current())return;
     if(/^https:\/\/t\.me\/proxy\?/.test(result.url)){connect.href=result.url;connect.hidden=false;connect.textContent=result.personalized?'Подключиться по интересам':'Подключиться';}
     else connect.hidden=true;
     feedback.textContent=result.personalized?'Telegram откроет ссылку подключения. При смене темы подключитесь по новой ссылке. Если подходящий узел недоступен, выберем обычный.':
      data.topic?'Тема сохранена. Пока доступно обычное подключение — тематический вход ещё не готов.':'Подключение без подбора по интересам.';
    }
    form.onsubmit=e=>{e.preventDefault();void run(async()=>{
     select.disabled=true;try{const saved=await api('/me/proxy-interest',{method:'PUT',body:{topic:select.value||null}});if(!current())return;data.topic=saved.topic;await updateLink();await connection(true);}finally{select.disabled=false;}
    },save);};
    await updateLink();
   }catch(error){if(current()){loaded=false;body.replaceChildren(el('p',error.message,'muted'));}}
  });
 }
 async function load(force=false){
  const host=$('proxy-gateway-settings');if(!host||!me?.is_owner)return;
  if(dirty&&!force)return;
  const version=++settingsEpoch,session=token,user=me.id;
  const current=()=>version===settingsEpoch&&session===token&&user===me?.id&&me?.is_owner;
  const saved=await api('/admin/proxy-gateway');if(!current())return;dirty=false;
  const box=el('details',undefined,'ai-settings-card');box.id='proxy-gateway-card';box.append(el('summary','Единый вход и интересы'));
  const content=el('div',undefined,'ai-settings-content'),form=el('form'),fields=el('fieldset');
  function checkbox(title,id,value){const label=el('label',title,'ai-switch'),input=el('input');input.type='checkbox';input.id=id;input.checked=value;label.append(input);fields.append(label);return input;}
  const enabled=checkbox('Включить единый адрес','proxy-gateway-enabled',saved.enabled);
  const label=el('label','Порт общего подключения'),port=el('input');port.type='number';port.id='proxy-gateway-port';port.min=1024;port.max=65000;port.step=1;port.required=true;port.value=saved.port;label.append(port);fields.append(label);
  const interests=checkbox('Подключение по выбранной теме','proxy-gateway-interests',saved.interests_enabled);
  const range=el('p',undefined,'muted');function showRange(){range.textContent=interests.checked?'Нужны открытые TCP-порты '+port.value+'–'+(Number(port.value)+saved.topic_count)+': основной и '+saved.topic_count+' тематических.':'Нужен открытый TCP-порт '+port.value+'.';}showRange();fields.append(range);
  fields.addEventListener('input',()=>{dirty=true;showRange();});
  const save=el('button','Сохранить единый вход');save.type='submit';
  const refresh=button('Обновить',()=>load(true));refresh.type='button';const actions=el('div',undefined,'actions');actions.append(save,refresh);form.append(fields,actions);
  const agent=saved.agent,status=el('p',!agent?'Ожидаем отчёт агента':!agent.current?'Настройки сохранены — ожидаем применения':statuses[agent.code]||'Ожидаем проверку','muted');status.id='proxy-gateway-status';status.setAttribute('role','status');
  const info=el('p','Одна ссылка для доступных узлов этого VPS. При отказе узла следующее соединение пойдёт на резервный. Открытое соединение может прерваться. Для защиты от отказа всего VPS нужен второй сервер.','muted');
  content.append(info,form,status);
  if(agent?.current&&agent.code==='ready'){
   content.append(el('p','Доступно узлов: '+agent.nodes+' · Исключено: '+agent.excluded,'muted'));
   if(agent.nodes===1)content.append(el('p','Сейчас подключён один узел. Для резерва включите «Подготовить один резервный прокси» в разделе «ИИ · Новые прокси».','muted'));
  }
  const help=el('details');help.append(el('summary','Первое подключение на VPS'),el('p','После обновления проекта выполните команду один раз. Она установит HAProxy и службу проверки. Основной MTProxy продолжит работать. У провайдера VPS разрешите показанные выше TCP-порты.','muted'),el('code','sudo bash /opt/mtproxy-pushok/scripts/install_proxy_gateway.sh --install-dependency'),el('p','Старые дополнительные экземпляры без общего ключа исключаются. После планового перезапуска они смогут участвовать. Уже сохранённые прямые ссылки остаются прямыми — для единого входа подключитесь заново с главной.','muted'));content.append(help);
  form.onsubmit=e=>{e.preventDefault();void run(async()=>{fields.disabled=true;try{await api('/admin/proxy-gateway',{method:'PUT',body:{enabled:enabled.checked,port:Number(port.value),interests_enabled:interests.checked,revision:saved.revision}});if(current()){dirty=false;await load(true);notice('Настройки сохранены. Агент применит их после проверки.',true);}}finally{fields.disabled=false;}},save);};
  box.append(content);host.replaceChildren(box);
 }
 return {profile,load,connection,reset};
})();
