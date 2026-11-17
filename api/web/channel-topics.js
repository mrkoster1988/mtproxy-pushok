'use strict';
// Анализ независим от формы: отправку можно продолжить, не дожидаясь подсказки.
window.pushokTopics={create({locked,onChange}){
 const select=$('order-topic'),status=$('order-topic-status'),retry=$('order-topic-detect');
 let trigger=null;const sync=()=>{if(trigger){trigger.querySelector('span').textContent=select.selectedOptions[0]?.textContent||'Не указывать';trigger.disabled=select.disabled;}};
 let channel='',verifiedChannel='',automatic=true,requested=false,epoch=0,controller=null;
 const normalize=value=>{const match=(value||'').trim().match(/^(?:@|https:\/\/(?:t\.me|telegram\.me)\/)?([a-z][a-z0-9_]{3,31})\/?$/i);return match?match[1].toLowerCase():'';};
 function stop(){epoch++;controller?.abort();controller=null;retry.disabled=false;status.removeAttribute('aria-busy');}
 function reset(){stop();channel='';verifiedChannel='';automatic=true;requested=false;select.value='';sync();status.textContent='Можно изменить подсказку или оставить «Не указывать».';}
 function context(value){const next=normalize(value);if(channel!==next){reset();channel=next;}}
 async function detect(){
  if(locked()||!channel||!(trigger||select).getClientRects().length)return;
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
   select.value=option?option.value:'';sync();
   if(option){status.textContent=(data.engine==='ai'?'ИИ предложил: ':'Автоподсказка: ')+option.textContent+(data.basis==='posts'?' · по публичным публикациям и описанию.':' · по названию и описанию.');}
   else status.textContent='Недостаточно данных для уверенной подсказки. Выберите тематику или оставьте поле пустым.';
   onChange();
  }catch{
   if(current())status.textContent='Не удалось определить тематику. Можно выбрать вручную или продолжить без неё.';
  }finally{
   clearTimeout(timeout);if(version===epoch){controller=null;retry.disabled=false;status.removeAttribute('aria-busy');}
  }
 }
 select.addEventListener('change',()=>{sync();stop();automatic=false;requested=true;status.textContent=select.value?'Выбрано вами. Автоподсказка не изменит выбор.':'Тематика не указана. Это не мешает созданию кампании.';onChange();});
 retry.addEventListener('click',()=>{if(channel)void detect();else status.textContent='Сначала укажите публичный канал.';});
 function install(){
  const v=window.pushokViews;select.hidden=true;select.tabIndex=-1;
  trigger=button('',()=>{sync();query.value='';draw();dialog.showModal();query.focus();});trigger.className='topic-trigger';trigger.setAttribute('aria-label','Тематика канала');trigger.setAttribute('aria-haspopup','dialog');trigger.append(v.icon('channel'),el('span'),v.icon('chevron'));select.after(trigger);
  const dialog=el('dialog',undefined,'topic-dialog'),head=el('div',undefined,'topic-dialog-heading'),query=el('input'),items=el('div',undefined,'topic-options');dialog.setAttribute('aria-label','Выбор тематики канала');query.type='search';query.placeholder='Найти тематику';query.setAttribute('aria-label','Найти тематику');
  head.append(el('h2','Тематика канала'),v.iconAction(button('',()=>dialog.close()),'close','Закрыть выбор тематики'));dialog.append(head,query,items);document.body.append(dialog);
  function draw(){items.replaceChildren();const q=query.value.toLocaleLowerCase('ru');for(const o of select.options){if(q&&!o.textContent.toLocaleLowerCase('ru').includes(q))continue;const b=button(o.textContent,()=>{if(locked())return;select.value=o.value;select.dispatchEvent(new Event('change',{bubbles:true}));dialog.close();});b.className='topic-option';b.setAttribute('aria-pressed',String(o.value===select.value));if(o.value===select.value)b.append(v.icon('check'));items.append(b);}if(!items.children.length)items.append(el('p','Ничего не найдено','muted'));}
  query.addEventListener('input',draw);dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});dialog.addEventListener('close',()=>trigger.focus({preventScroll:true}));window.addEventListener('hashchange',()=>dialog.close());new MutationObserver(sync).observe(select,{attributes:true,attributeFilter:['disabled']});sync();
 }
 if(!window.pushokViews)document.addEventListener('DOMContentLoaded',install,{once:true});else install();
 select.form.addEventListener('reset',reset);
 reset();
 function resume(){if(verifiedChannel===channel&&automatic&&!requested)void detect();}
 return {context,reset,detect,resume,verified(value){verifiedChannel=normalize(value);resume();},
  value:()=>select.value||null,label:()=>select.selectedOptions[0]?.textContent||'',
  restore(value){stop();automatic=false;requested=true;select.value=value||'';sync();status.textContent='Тематика сохранена вместе с отправляемой заявкой.';}};
}};
