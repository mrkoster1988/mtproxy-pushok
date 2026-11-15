'use strict';
window.pushokDefaultSponsor=(()=>{
 const fields=$('default-sponsor-fields'),select=$('default-sponsor-channel'),node=$('default-sponsor-node'),status=$('default-sponsor-status');
 async function load(){
  if(me?.role!=='admin')return;
  const session=token;fields.disabled=true;
  try{
   const data=await api('/admin/default-sponsor');if(session!==token||me?.role!=='admin')return;
   select.replaceChildren();option(select,'','Не выбран · возвращать исходный тег прокси');
   data.channels.forEach(c=>option(select,c.id,c.title+' · '+c.telegram_url));select.value=data.channel_id||'';
   node.replaceChildren();option(node,'','Не менять привязки тегов');data.nodes.forEach(n=>option(node,n.id,n.name));
   const missing=data.nodes.filter(n=>!data.prepared_node_ids.includes(n.id));
   status.textContent=data.channel_id?(missing.length?'Канал выбран. Не подготовлен тег для узлов: '+missing.map(n=>n.name).join(', '):'Канал подготовлен для доступных узлов. Кампании имеют приоритет.'):'Канал по умолчанию не выбран: агент возвращает исходный тег из конфигурации прокси.';
   fields.disabled=false;
  }catch(e){status.textContent='Не удалось загрузить канал по умолчанию.';throw e;}
 }
 $('default-sponsor-form').addEventListener('submit',async event=>{
  event.preventDefault();if(fields.disabled)return;
  const channel=select.value,tag=$('default-sponsor-tag').value.trim(),nodeId=node.value;
  if((tag||nodeId)&&(!channel||!nodeId||!/^[0-9a-fA-F]{32}$/.test(tag)||!$('default-sponsor-confirmed').checked)){
   status.textContent='Для подготовки тега выберите канал и узел, укажите 32 символа тега из MTProxyBot и подтвердите привязку.';return;
  }
  fields.disabled=true;status.textContent='Сохраняем…';
  try{
   if(tag)await api('/admin/promotion-bindings',{method:'PUT',body:{node_id:nodeId,channel_id:channel,ad_tag:tag,telegram_binding_confirmed:true}});
   await api('/admin/default-sponsor',{method:'PUT',body:{channel_id:channel||null}});
   $('default-sponsor-tag').value='';$('default-sponsor-confirmed').checked=false;
   await load();
  }catch(e){status.textContent=e.message;fields.disabled=false;}
 });
 return {load};
})();
