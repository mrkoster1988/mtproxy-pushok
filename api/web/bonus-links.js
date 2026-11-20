"use strict";
window.pushokLinks=(()=>{
 let pending='',busy=false;
 const valid=value=>/^(?:wallet_GIFT-[A-F0-9]{24}|(?:offer|pay)_[a-f0-9]{32}|r_[A-HJ-NP-Z2-9]{8})$/.test(value||'');
 function capture(value){pending=valid(value)?value:'';}
 function share(payload,text){
  const bot=window.cabinetBotConfig?.telegram_bot_username;
  const a=el('a','Поделиться','ui-button bonus-share');a.prepend(window.pushokViews.icon('share'));
  if(!valid(payload)||!/^[A-Za-z0-9_]{5,32}$/.test(bot||'')){a.setAttribute('aria-disabled','true');a.title='Настройте имя Telegram-бота для ссылки';return a;}
  const url='https://t.me/'+bot+'?startapp='+encodeURIComponent(payload);
  a.href='https://t.me/share/url?url='+encodeURIComponent(url)+'&text='+encodeURIComponent(text);a.target='_blank';a.rel='noopener noreferrer';
  return a;
 }
 function giftShare(gift,row){
  const action=window.pushokViews.iconAction(button('',async()=>{
   const session=token,uid=me?.id,app=window.Telegram?.WebApp;
   const native=Boolean(app?.shareMessage&&app.isVersionAtLeast?.('8.0'));
   const data=await api('/admin/wallet/gifts/'+gift.id+'/share?prepare='+native,{method:'POST'});
   if(session!==token||uid!==me?.id||!row.isConnected)return;
   if(native&&data.message_id){
    try{app.shareMessage(data.message_id,sent=>{if(sent&&session===token&&uid===me?.id)notice('Сертификат отправлен.',true);});return;}catch{}
   }
   row.querySelector('.gift-share-preview')?.remove();
   const preview=el('section',undefined,'gift-share-preview'),heading=el('div',undefined,'panel-heading'),image=el('img');
   heading.append(el('h4','Подарочный сертификат'),window.pushokViews.iconAction(button('',()=>preview.remove()),'close','Закрыть сертификат'));
   image.src=data.image_path;image.alt='Подарочный сертификат puShok на '+money(gift.amount)+' бонусами на рекламу';image.width=1200;image.height=720;
   const actions=el('div',undefined,'inline-actions'),send=el('a','Поделиться текстом','ui-button'),download=el('a','','ui-button');
   send.href=data.share_url;send.target='_blank';send.rel='noopener noreferrer';send.prepend(window.pushokViews.icon('share'));
   download.href=data.image_path;download.download='pushok-certificate.jpg';window.pushokViews.iconAction(download,'download','Скачать сертификат');
   const copy=window.pushokViews.iconAction(button('',async()=>{await navigator.clipboard.writeText(data.text);notice('Текст и ссылка скопированы.',true); }),'copy','Скопировать текст сертификата');
   actions.append(send,download,copy);preview.append(heading,image,el('p',data.text,'gift-share-text'),actions);row.append(preview);
   preview.scrollIntoView({block:'nearest'});
  }),'share','Поделиться');
  action.classList.add('gift-share');return action;
 }
 async function apply(){
  if(!pending||!me||busy)return;busy=true;
  const payload=pending,session=token,uid=me.id;
  const current=()=>session===token&&uid===me?.id;
  try{
   if(payload.startsWith('r_')){
    await api('/growth/referral',{method:'POST',body:{code:payload.slice(2)}});
    if(current())pending='';
   }else if(payload.startsWith('pay_')){
    const id=payload.slice(4).replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/,'$1-$2-$3-$4-$5');
    pending='';window.cabinet.go('payments/'+id);
   }else if(payload.startsWith('wallet_')){
    const result=await api('/wallet/gifts/redeem',{method:'POST',body:{code:payload.slice(7)}});
    if(!current())return;pending='';window.cabinet.go('wallet');
    setTimeout(()=>{if(current())notice(result.already_redeemed?'Подарок уже на вашем балансе.':'Подарок получен: '+money(result.amount)+' бонусами.',true);},0);
   }else{
    const offer=await api('/growth/shared/'+payload.slice(6));if(!current())return;
    await window.pushokOrder.applyOffer(offer);if(!current())return;pending='';window.cabinet.go('create');
    setTimeout(()=>{if(current())notice('Предложение применено к форме заявки. Проверьте канал, тариф и итоговую цену.',true);},0);
   }
  }catch(e){if(current()){
   if(payload.startsWith('r_')&&e.status===409){pending='';return;}
   const gift=payload.startsWith('wallet_');
   let message=e.message;
   if(e.code==='wallet_gift_wrong_recipient'){
    const account=me.username?'@'+me.username:[me.first_name,me.last_name].filter(Boolean).join(' ')||'Без никнейма';
    message='Подарок предназначен другому аккаунту Telegram. Сейчас вы вошли как '+account+' (ID '+me.telegram_user_id+'). Откройте ссылку из аккаунта получателя или попросите отправителя проверить получателя в карточке подарка.';
   }
   if(gift)message+=' Код: GIFT-…'+payload.slice(-8)+'.';
   const retryable=!e.status||e.status>=500||e.status===429;
   notice(message,false,{actions:retryable?[button('Повторить',apply)]:[],persistent:true});
  }}
  finally{busy=false;}
 }
 return {capture,share,giftShare,apply,reset(){pending='';}};
})();
