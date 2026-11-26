'use strict';
// Подключается только в /miniapp. initDataUnsafe не используется для входа.
window.pushokMiniApp=(()=>{
 let app=null,busy=false,loaded=false;
 const cacheKey='pushok-miniapp-init-hash',exitKey='pushok-miniapp-signed-out';
 const panel=document.createElement('section');panel.id='miniapp-login';panel.className='panel';
 const title=document.createElement('h1');title.textContent='Пушок готовит кабинет…';
 const status=document.createElement('p');status.setAttribute('role','status');
 const retry=document.createElement('button');retry.type='button';retry.textContent='Повторить вход';
 const runner=document.createElement('div');runner.className='pushok-loading-cat';
 runner.setAttribute('role','img');runner.setAttribute('aria-label','Пушок в бирюзовом худи');
 for(const part of ['base','tail']){
  const img=document.createElement('img');img.src='/assets/pushok-loading-cat.webp';img.alt='';img.className='pushok-cat-'+part;
  img.width=850;img.height=638;runner.append(img);
 }
 panel.append(runner,title,status,retry);document.querySelector('main').prepend(panel);
 document.body.classList.add('miniapp-mode');
 function message(text,canRetry=false){document.body.classList.add('miniapp-loading');panel.classList.toggle('load-failed',canRetry);panel.hidden=false;status.textContent=text;retry.hidden=!canRetry;}
 function clear(){phoneControls=null;token='';sessionStorage.removeItem(sessionKey);sessionStorage.removeItem(cacheKey);}
 function end(text,{preserveUnboundFlow=false}={}){
  clear();if(window.cabinet)window.cabinet.logout({preserveUnboundFlow});
  $('workspace').hidden=true;$('login').hidden=true;$('logout').hidden=true;
  message(text);syncNavigation();
 }
 let phoneControls=null;
 function phoneSettings(target){
  if(!app)return;
  if(phoneControls){target.append(phoneControls);return;}
  const section=document.createElement('section');section.className='panel';
  const heading=document.createElement('h3');heading.textContent='На телефоне';section.append(heading);
  function action(label,callback){
   const button=document.createElement('button');button.type='button';button.textContent=label;
   button.addEventListener('click',()=>callback(button));section.append(button);return button;
  }
  if(app.isVersionAtLeast?.('8.0')){
   const shortcut=action('Добавить ярлык кабинета',()=>app.addToHomeScreen());
   function added(){shortcut.textContent='Ярлык добавлен';shortcut.disabled=true;}
   app.onEvent('homeScreenAdded',added);
   app.checkHomeScreenStatus(state=>{
    if(state==='added')added();
    if(state==='unsupported')shortcut.hidden=true;
   });
  }
  const notify=document.createElement('a');notify.textContent='Подключить уведомления';
  notify.className='ui-button labeled-action';notify.href='https://t.me/mtproxy_pushok_bot?start=notifications';notify.target='_blank';notify.rel='noopener noreferrer';
  const hint=document.createElement('small');hint.className='muted';hint.textContent='Уведомления приходят в @mtproxy_pushok_bot. Если вы впервые открываете чат, нажмите «Старт».';
  const sender=String(window.cabinetBotConfig?.telegram_bot_username||'').replace(/^@/,'').toLowerCase();
  if(sender==='mtproxy_pushok_bot' && app.isVersionAtLeast?.('6.9') && typeof app.requestWriteAccess==='function'){
   const permission=action('Разрешить уведомления в Telegram',button=>{
    button.disabled=true;
    try{app.requestWriteAccess(granted=>{
     button.disabled=granted;
     hint.textContent=granted?'Telegram разрешил боту писать вам. Статус каждой отправки виден в истории.':'Можно разрешить сообщения позже или открыть чат вручную.';
     if(granted)button.textContent='Уведомления разрешены';
    });}catch{button.disabled=false;hint.textContent='Откройте чат бота и нажмите «Старт».';}
   });
   if(app.initDataUnsafe?.user?.allows_write_to_pm===true){permission.textContent='Уведомления разрешены';permission.disabled=true;}
   notify.textContent='Открыть чат бота';
  }
  section.append(notify,hint);
  if(section.childElementCount>1){phoneControls=section;target.append(section);}
 }
 function syncNavigation(){
  if(app?.SettingsButton&&app.isVersionAtLeast?.('7.0')){
   if(token&&me&&window.cabinetBootReady&&hasDeveloperAccess(me))app.SettingsButton.show();
   else app.SettingsButton.hide();
  }
  if(!app?.BackButton)return;
  const root=!me||!window.cabinetBootReady||!location.hash||location.hash==='#/dashboard';
  if(root)app.BackButton.hide();else app.BackButton.show();
 }
 function back(){
  const help=document.querySelector('.top-help[open]');if(help){help.open=false;return;}
  window.cabinet?.back();
 }
 function inset(value){const n=Number(value);return Number.isFinite(n)?Math.max(0,Math.min(160,n)):0;}
 function theme(){
  document.body.dataset.miniappTheme=app.colorScheme==='dark'?'dark':'light';
  const root=document.documentElement;
  // Системная строка и кнопки Telegram занимают последовательные области сверху.
  root.style.setProperty('--mini-safe-top',(inset(app.safeAreaInset?.top)+inset(app.contentSafeAreaInset?.top))+'px');
  for(const side of ['bottom','left','right'])root.style.setProperty('--mini-safe-'+side,
   Math.max(inset(app.safeAreaInset?.[side]),inset(app.contentSafeAreaInset?.[side]))+'px');
 }
 async function sdk(){
  if(window.Telegram?.WebApp)return window.Telegram.WebApp;
  await new Promise((resolve,reject)=>{
   const script=document.createElement('script');script.src='/assets/vendor/telegram-web-app.baa788a6f686.js';
   // Та же поставка и домен: внешний telegram.org не блокирует вход клиента.
   const timer=setTimeout(()=>{script.remove();reject(Error('sdk_unavailable'));},8000);
   script.onload=()=>{clearTimeout(timer);if(window.Telegram?.WebApp)resolve();else{script.remove();reject(Error('sdk_unavailable'));}};script.onerror=()=>{clearTimeout(timer);script.remove();reject(Error('sdk_unavailable'));};
   document.head.append(script);
  });
  return window.Telegram?.WebApp;
 }
 async function start(){
  if(busy)return;busy=true;retry.disabled=true;message('Проверяем вход через Telegram…');
  const generation=authGeneration,current=()=>generation===authGeneration;
  try{
   const config=await api('/miniapp/config');
   if(!current())return;
   title.textContent=config.loading_title||'Пушок готовит кабинет…';
   if(!config.enabled){message('Mini App пока не включён разработчиком. Попробуйте позже.');return;}
   const publicConfig=await api('/public-config');if(!current())return;window.cabinetBotConfig=publicConfig;window.cabinet?.config(publicConfig);
   app=await sdk();
   if(!current())return;
   if(!app?.initData){clear();message('Откройте Mini App из меню Telegram-бота.');return;}
   if(!loaded){
    loaded=true;app.ready?.();app.expand?.();theme();
    app.onEvent?.('themeChanged',theme);app.onEvent?.('safeAreaChanged',theme);app.onEvent?.('contentSafeAreaChanged',theme);app.onEvent?.('fullscreenChanged',theme);
    app.BackButton?.onClick(back);
    if(app.isVersionAtLeast?.('7.0'))app.SettingsButton?.onClick(()=>{
     if(token&&hasDeveloperAccess(me))window.cabinet?.go('profile/settings');
    });
   }
   const signedParams=new URLSearchParams(app.initData),startParam=signedParams.get('start_param');
   const launch=window.pushokLinks.capture(startParam);
   const fingerprint=signedParams.get('hash');
   const newLaunch=sessionStorage.getItem(cacheKey)!==fingerprint;
   const clean=new URL(location.href);
   for(const key of [...clean.searchParams.keys()])if(key.startsWith('tgWebApp')||key==='sbp_campaign')clean.searchParams.delete(key);
   if((newLaunch&&launch.startsWith('r_'))||!clean.hash.startsWith('#/'))clean.hash='/dashboard';
   if(newLaunch&&['profile','wallet','campaigns','moderation','sponsors','create','channels'].includes(startParam))clean.hash='/'+startParam;
   const noticeRoute=/^(archive|moderation|campaign)_([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})$/.exec(startParam||'');
   if(newLaunch&&noticeRoute)clean.hash='/'+noticeRoute[1]+'/'+noticeRoute[2];
   if(newLaunch&&startParam==='wallet_topup')clean.hash='/wallet/topup';
   if(newLaunch&&startParam==='ai_bot_settings')clean.hash='/management/ai-bot';
   const vpnLaunch=clean.searchParams.get('vpn')||(newLaunch&&({vpn:'home',vpn_renew:'renew'})[startParam]);
   if(['renew','home'].includes(vpnLaunch)||clean.searchParams.get('shop_subscriptions')==='1'){
    clean.hash=vpnLaunch==='renew'?'/vpn/renew':'/vpn';
    clean.searchParams.delete('vpn');clean.searchParams.delete('shop_subscriptions');
   }
   history.replaceState(null,'',clean);
   if(!fingerprint){clear();message('Не удалось проверить вход. Закройте Mini App и откройте заново.');return;}
   if(sessionStorage.getItem(exitKey)===fingerprint){end('Вы вышли. Закройте Mini App и откройте заново для нового входа.');return;}
   const cached=sessionStorage.getItem(cacheKey)===fingerprint?sessionStorage.getItem(sessionKey):null;
   if(cached)token=cached;
   else{
    clear();
    const session=await api('/auth/telegram/miniapp',{method:'POST',body:{init_data:app.initData}});
    if(!current())return;
    token=session.access_token;sessionStorage.setItem(sessionKey,token);sessionStorage.setItem(cacheKey,fingerprint);sessionStorage.removeItem(exitKey);
   }
   const entered=await enter();if(!current()||!entered)return;panel.hidden=true;document.body.classList.remove('miniapp-loading');syncNavigation();await window.pushokLinks.apply();
  }catch(e){
   if(!current())return;
   if(!token)message(e.code==='user_blocked'?e.message:e.message==='sdk_unavailable'?'Не удалось загрузить компонент приложения. Проверьте соединение и повторите.':
    'Вход не завершён. Закройте Mini App и откройте заново. Если ошибка повторяется, обратитесь к разработчику.',e.message==='sdk_unavailable'||!e.status||e.status>=500);
   else message('Не удалось загрузить кабинет. Проверьте соединение и повторите.',true);
  }finally{busy=false;retry.disabled=false;}
 }
 document.addEventListener('click',event=>{
  const link=event.target.closest('a');if(!link||!app?.openTelegramLink)return;
  try{const url=new URL(link.href);if(url.protocol==='https:'&&url.hostname==='t.me'&&!url.username&&!url.password){if(!event.defaultPrevented){app.openTelegramLink(url.href);event.preventDefault();}}}catch{}
 });
 retry.addEventListener('click',start);
 message('Подключение к Telegram…');
 return {start,syncNavigation,phoneSettings,canPay(){return typeof app?.openInvoice==='function';},
  payInvoice(url){return new Promise((resolve,reject)=>{
   if(typeof url==='string')url=url.replace('https://t.me/invoice/','https://t.me/$');
   if(!/^https:\/\/t\.me\/\$[A-Za-z0-9_-]{1,256}$/.test(url)||typeof app?.openInvoice!=='function'){reject(Error('Обновите Telegram для оплаты Stars.'));return;}
   try{app.openInvoice(url,resolve);}catch{reject(Error('Не удалось открыть счёт. Обновите статус перед повтором.'));}
  });},expired(options){end('Сессия завершена. Закройте Mini App и откройте заново.',options);},
  signedOut(){const hash=sessionStorage.getItem(cacheKey);if(hash)sessionStorage.setItem(exitKey,hash);end('Вы вышли. Закройте Mini App и откройте заново для нового входа.');}};
})();
