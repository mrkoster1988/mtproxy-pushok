'use strict';
// Анализ независим от формы: отправку можно продолжить, не дожидаясь подсказки.
window.pushokTopics={create({locked,onChange}){
 const select=$('order-topic'),status=$('order-topic-status'),retry=$('order-topic-detect');
 let channel='',automatic=true,requested=false,epoch=0,controller=null;
 const normalize=value=>{const match=(value||'').trim().match(/^(?:@|https:\/\/(?:t\.me|telegram\.me)\/)?([a-z][a-z0-9_]{3,31})\/?$/i);return match?match[1].toLowerCase():'';};
 function stop(){epoch++;controller?.abort();controller=null;retry.disabled=false;status.removeAttribute('aria-busy');}
 function reset(){stop();channel='';automatic=true;requested=false;select.value='';status.textContent='Можно изменить подсказку или оставить «Не указывать».';}
 function context(value){const next=normalize(value);if(channel!==next){reset();channel=next;}}
 async function detect(){
  if(locked()||!channel)return;
  stop();automatic=true;requested=true;
  const version=epoch,session=token,currentChannel=channel;
  const active=new AbortController();controller=active;
  const timeout=setTimeout(()=>active.abort(),12000);
  const current=()=>version===epoch&&session===token&&currentChannel===channel&&automatic&&!locked();
  retry.disabled=true;status.setAttribute('aria-busy','true');status.textContent='Определяем тематику… Можно продолжить без неё.';
  try{
   const data=await api('/channel-topics/suggest?query='+encodeURIComponent(channel),{signal:active.signal});
   if(!current())return;
   const option=[...select.options].find(item=>item.value===data.topic);
   select.value=option?option.value:'';
   if(option){status.textContent=(data.engine==='ai'?'ИИ предложил: ':'Автоподсказка: ')+option.textContent+(data.basis==='posts'?' · по публичным публикациям и описанию.':' · по названию и описанию.');}
   else status.textContent='Недостаточно данных для уверенной подсказки. Выберите тематику или оставьте поле пустым.';
   onChange();
  }catch{
   if(current())status.textContent='Не удалось определить тематику. Можно выбрать вручную или продолжить без неё.';
  }finally{
   clearTimeout(timeout);if(version===epoch){controller=null;retry.disabled=false;status.removeAttribute('aria-busy');}
  }
 }
 select.addEventListener('change',()=>{stop();automatic=false;requested=true;status.textContent=select.value?'Выбрано вами. Автоподсказка не изменит выбор.':'Тематика не указана. Это не мешает созданию кампании.';onChange();});
 retry.addEventListener('click',()=>{if(channel)void detect();else status.textContent='Сначала укажите публичный канал.';});
 select.form.addEventListener('reset',reset);
 reset();
 return {context,reset,detect,verified(value){if(normalize(value)===channel&&automatic&&!requested)void detect();},
  value:()=>select.value||null,label:()=>select.selectedOptions[0]?.textContent||'',
  restore(value){stop();automatic=false;requested=true;select.value=value||'';status.textContent='Тематика сохранена вместе с отправляемой заявкой.';}};
}};
