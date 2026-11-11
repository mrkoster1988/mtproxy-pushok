'use strict';
// Подключается только в /miniapp. initDataUnsafe не используется для входа.
window.pushokMiniApp=(()=>{
 let app=null,busy=false,loaded=false;
 const cacheKey='pushok-miniapp-init-hash',exitKey='pushok-miniapp-signed-out';
 const panel=document.createElement('section');panel.id='miniapp-login';panel.className='panel';
 const title=document.createElement('h1');title.textContent='Кабинет MTProxy Pushok';
 const status=document.createElement('p');status.setAttribute('role','status');
 const retry=document.createElement('button');retry.type='button';retry.textContent='Повторить вход';
 panel.append(title,status,retry);document.querySelector('main').prepend(panel);
 document.body.classList.add('miniapp-mode');
 function message(text,canRetry=false){panel.hidden=false;status.textContent=text;retry.hidden=!canRetry;}
 function clear(){token='';sessionStorage.removeItem(sessionKey);sessionStorage.removeItem(cacheKey);}
 function end(text){
  clear();if(window.cabinet)window.cabinet.logout();
  $('workspace').hidden=true;$('login').hidden=true;$('logout').hidden=true;
  message(text);syncNavigation();
 }
 function syncNavigation(){
  if(!app?.BackButton)return;
  const root=!me||!window.cabinetBootReady||!location.hash||location.hash==='#/dashboard';
  if(root)app.BackButton.hide();else app.BackButton.show();
 }
 function back(){
  if(document.querySelector('.cabinet-sidebar.is-open')){document.querySelector('.menu-toggle')?.click();return;}
  const parts=location.hash.replace(/^#\/?/,'').split('/');
  const target=parts[0]==='campaign'||parts[0]==='create'?'campaigns':parts[0]==='payments'&&parts[1]?'payments':'dashboard';
  window.cabinet?.go(target);
 }
 function theme(){
  document.body.dataset.miniappTheme=app.colorScheme==='dark'?'dark':'light';
  const root=document.documentElement;
  for(const [key,value] of Object.entries({
   '--mini-safe-top':Math.max(app.safeAreaInset?.top||0,app.contentSafeAreaInset?.top||0),
   '--mini-safe-bottom':Math.max(app.safeAreaInset?.bottom||0,app.contentSafeAreaInset?.bottom||0),
   '--mini-safe-left':Math.max(app.safeAreaInset?.left||0,app.contentSafeAreaInset?.left||0),
   '--mini-safe-right':Math.max(app.safeAreaInset?.right||0,app.contentSafeAreaInset?.right||0)
  }))root.style.setProperty(key,Math.max(0,Math.min(160,Number(value)||0))+'px');
 }
 async function sdk(){
  if(window.Telegram?.WebApp)return window.Telegram.WebApp;
  await new Promise((resolve,reject)=>{
   const script=document.createElement('script');script.src='https://telegram.org/js/telegram-web-app.js';
   const timer=setTimeout(()=>{script.remove();reject(Error('sdk_unavailable'));},8000);
   script.onload=()=>{clearTimeout(timer);resolve();};script.onerror=()=>{clearTimeout(timer);script.remove();reject(Error('sdk_unavailable'));};
   document.head.append(script);
  });
  return window.Telegram?.WebApp;
 }
 async function start(){
  if(busy)return;busy=true;retry.disabled=true;message('Проверяем вход через Telegram…');
  try{
   const config=await api('/miniapp/config');
   if(!config.enabled){message('Mini App пока не включён владельцем. Попробуйте позже.');return;}
   const publicConfig=await api('/public-config');window.cabinetBotConfig=publicConfig;window.cabinet?.config(publicConfig);
   app=await sdk();
   if(!app?.initData){clear();message('Откройте Mini App из меню Telegram-бота.');return;}
   if(!loaded){
    loaded=true;app.ready?.();app.expand?.();theme();
    app.onEvent?.('themeChanged',theme);app.onEvent?.('safeAreaChanged',theme);app.onEvent?.('contentSafeAreaChanged',theme);
    app.BackButton?.onClick(back);
   }
   const clean=new URL(location.href);
   for(const key of [...clean.searchParams.keys()])if(key.startsWith('tgWebApp')||key==='sbp_campaign')clean.searchParams.delete(key);
   if(!clean.hash.startsWith('#/'))clean.hash='/dashboard';
   history.replaceState(null,'',clean);
   const fingerprint=new URLSearchParams(app.initData).get('hash');
   if(!fingerprint){clear();message('Не удалось проверить вход. Закройте Mini App и откройте заново.');return;}
   if(sessionStorage.getItem(exitKey)===fingerprint){end('Вы вышли. Закройте Mini App и откройте заново для нового входа.');return;}
   const cached=sessionStorage.getItem(cacheKey)===fingerprint?sessionStorage.getItem(sessionKey):null;
   if(cached)token=cached;
   else{
    clear();
    const session=await api('/auth/telegram/miniapp',{method:'POST',body:{init_data:app.initData}});
    token=session.access_token;sessionStorage.setItem(sessionKey,token);sessionStorage.setItem(cacheKey,fingerprint);sessionStorage.removeItem(exitKey);
   }
   await enter();panel.hidden=true;syncNavigation();
  }catch(e){
   if(!token)message(e.message==='sdk_unavailable'?'Не удалось загрузить Telegram. Проверьте соединение и повторите.':
    'Вход не завершён. Закройте Mini App и откройте заново. Если ошибка повторяется, обратитесь к владельцу.',e.message==='sdk_unavailable'||!e.status||e.status>=500);
   else message('Не удалось загрузить кабинет. Проверьте соединение и повторите.',true);
  }finally{busy=false;retry.disabled=false;}
 }
 document.addEventListener('click',event=>{
  const link=event.target.closest('a');if(!link||!app?.openTelegramLink)return;
  try{const url=new URL(link.href);if(url.protocol==='https:'&&url.hostname==='t.me'&&!url.username&&!url.password){event.preventDefault();app.openTelegramLink(url.href);}}catch{}
 });
 retry.addEventListener('click',start);
 message('Подключение к Telegram…');
 return {start,syncNavigation,expired(){end('Сессия завершена. Закройте Mini App и откройте заново.');},
  signedOut(){const hash=sessionStorage.getItem(cacheKey);if(hash)sessionStorage.setItem(exitKey,hash);end('Вы вышли. Закройте Mini App и откройте заново для нового входа.');}};
})();
