'use strict';
window.pushokChannelSubscriptionSettings=(()=>{
 let epoch=0,sessionKey=null;
 async function load(){
  const host=$('channel-subscription-settings'),session=token,uid=me?.id,version=++epoch;
  if(!host)return;
  host.hidden=me?.role!=='admin';
  if(host.hidden)return;
  sessionKey=session;
  host.replaceChildren(el('p','Загружаем настройки подписки…','muted'));
  const valid=()=>version===epoch&&session===token&&uid===me?.id;
  try{
   const saved=await api('/admin/channel-subscription-settings');
   if(!valid())return;
   const form=el('form'),fields=el('fieldset'),enabled=el('input'),channel=el('input');
   enabled.type='checkbox';enabled.checked=saved.enabled;
   channel.type='url';channel.required=true;channel.maxLength=120;channel.autocomplete='off';channel.autocapitalize='none';channel.spellcheck=false;channel.value=saved.channel_url;channel.placeholder='https://t.me/pushok_proxy';
   const enabledLabel=el('label');enabledLabel.className='settings-subscription-toggle';enabledLabel.append(enabled,el('span','Требовать подписку на канал перед подключением'));
   const channelLabel=el('label','Публичная ссылка Telegram-канала');channelLabel.append(channel);
   const help=el('p','После перехода кнопка «Назад» в Telegram возвращает пользователя в оставшееся открытым приложение. Для проверки бот должен быть администратором канала.','muted');
   const save=el('button','Сохранить настройку');save.type='submit';save.className='primary';
   const status=el('p',undefined,'settings-subscription-status');status.setAttribute('role','status');
   fields.append(enabledLabel,channelLabel,help,save,status);form.append(fields);
   form.addEventListener('submit',async event=>{
    event.preventDefault();if(save.disabled)return;save.disabled=true;status.textContent='Сохраняем…';
    try{
     const result=await api('/admin/channel-subscription-settings',{method:'PUT',body:{enabled:enabled.checked,channel_url:channel.value.trim()}});
     if(!valid())return;
     status.textContent='Настройки подписки сохранены.';
     if(result.channel_url)channel.value=result.channel_url;
    }catch(error){if(valid())status.textContent=({
      admin_role_required:'Настройки доступны только администратору.',
      channel_subscription_settings_invalid:'Укажите публичную ссылку вида https://t.me/username.'
     })[error.message]||'Не удалось сохранить настройку. Проверьте ссылку и повторите.';}
    finally{save.disabled=false;}
   });
   host.replaceChildren(el('h3','Подписка на канал перед подключением'),form);
  }catch(error){if(valid())host.replaceChildren(el('h3','Подписка на канал перед подключением'),el('p','Не удалось загрузить настройки. '+(error.message||''),'muted'));}
 }
 function reset(){epoch++;sessionKey=null;const host=$('channel-subscription-settings');if(host){host.replaceChildren();host.hidden=true;}}
 return {load,reset};
})();
