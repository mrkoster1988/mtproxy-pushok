"use strict";
window.pushokLinks=(()=>{
 let pending='',busy=false;
 const valid=value=>/^(?:wallet_GIFT-[A-F0-9]{24}|offer_[a-f0-9]{32})$/.test(value||'');
 function capture(value){pending=valid(value)?value:'';}
 function share(payload,text){
  const bot=window.cabinetBotConfig?.telegram_bot_username;
  const a=el('a','Поделиться','ui-button bonus-share');a.prepend(window.pushokViews.icon('share'));
  if(!valid(payload)||!/^[A-Za-z0-9_]{5,32}$/.test(bot||'')){a.setAttribute('aria-disabled','true');a.title='Настройте имя Telegram-бота для ссылки';return a;}
  const url='https://t.me/'+bot+'?startapp='+encodeURIComponent(payload);
  a.href='https://t.me/share/url?url='+encodeURIComponent(url)+'&text='+encodeURIComponent(text);a.target='_blank';a.rel='noopener noreferrer';
  return a;
 }
 async function apply(){
  if(!pending||!me||busy)return;busy=true;
  const payload=pending,session=token,uid=me.id;
  const current=()=>session===token&&uid===me?.id;
  try{
   if(payload.startsWith('wallet_')){
    const result=await api('/wallet/gifts/redeem',{method:'POST',body:{code:payload.slice(7)}});
    if(!current())return;pending='';window.cabinet.go('wallet');
    setTimeout(()=>{if(current())notice(result.already_redeemed?'Подарок уже на вашем балансе.':'Подарок получен: '+money(result.amount)+' бонусами.',true);},0);
   }else{
    const offer=await api('/growth/shared/'+payload.slice(6));if(!current())return;
    await window.pushokOrder.applyOffer(offer);if(!current())return;pending='';window.cabinet.go('create');
    setTimeout(()=>{if(current())notice('Предложение применено к форме заявки. Проверьте канал, тариф и итоговую цену.',true);},0);
   }
  }catch(e){if(current()){
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
 return {capture,share,apply,reset(){pending='';}};
})();
