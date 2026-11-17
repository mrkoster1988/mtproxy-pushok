'use strict';
// Общие поля, справка и списки. Исходные select остаются частью формы и API браузера.
window.pushokControls=(()=>{
 const connected=new WeakSet();let active=null,queued=false;
 const v=()=>window.pushokViews;
 function help(block,nodes,title){
  nodes=nodes.filter(Boolean);if(!nodes.length||!block)return;
  let heading=block.querySelector(':scope > summary,:scope > .panel-heading,:scope > .block-heading,:scope > .analytics-header,:scope > .report-header,:scope > .assistant-review-heading');
  if(!heading){const h=block.querySelector(':scope > h2,:scope > h3,:scope > h4,:scope > legend');if(!h)return;if(h.tagName==='LEGEND'){heading=h;heading.classList.add('block-heading');}else{heading=el('div',undefined,'block-heading');h.before(heading);heading.append(h);}}
  let body=block.querySelector(':scope > .block-help');
  if(!body){body=el('div',undefined,'block-help');body.id='help-'+crypto.randomUUID();body.hidden=true;
   const toggle=v().iconAction(button('',()=>{body.hidden=!body.hidden;toggle.setAttribute('aria-expanded',String(!body.hidden));}),'info',title||'Пояснения: '+heading.textContent.trim());
   toggle.classList.add('block-info');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls',body.id);
   toggle.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();});heading.append(toggle);heading.after(body);
  }
  for(const node of nodes){node.removeAttribute('data-note');node.classList.remove('block-note');body.append(node);}return body;
 }
 function fieldActions(field,buttons){
  if(!field||!buttons.length||field.closest('.field-action-row'))return;
  const container=field.closest('label')||field,scope=container.parentElement;
  if(!scope||buttons.some(b=>!scope.contains(b)))return;
  const row=el('div',undefined,'field-action-row'),actions=el('div',undefined,'field-actions');container.before(row);row.append(container,actions);buttons.forEach(b=>{if(!b.classList.contains('icon-action')){const title=b.getAttribute('aria-label')||b.textContent.trim();v().iconAction(b,b.dataset.actionIcon||'check',title);}actions.append(b);});return row;
 }
 function forms(){
  for(const form of document.querySelectorAll('form,fieldset')){
   if(form.closest('.topic-dialog,.select-dialog')||form.matches('.directory-search'))continue;
   const buttons=[...form.querySelectorAll(':scope > button,:scope > .inline-actions > button,:scope > .actions > button,:scope > .role-actions > button')].filter(n=>!n.hidden);
   if(!buttons.length)continue;
   const inputs=[...form.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select,textarea')].filter(n=>n.closest('form,fieldset')===form&&!n.hidden&&!n.closest('.field-action-row,.user-picker'));
   const field=inputs.at(-1);if(!field)continue;
   // Не переносим действия через чекбоксы согласия или вложенные блоки.
   const direct=field.closest('label')||field;
   if(direct.parentElement!==form)continue;
   fieldActions(field,buttons);
  }
 }
 function label(select){return select.getAttribute('aria-label')||[...select.labels].map(l=>[...l.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('')).join(' ').trim()||'Выберите вариант';}
 function close(){if(active){const state=active;active=null;state.dialog.close();state.dialog.remove();if(state.select.isConnected&&!state.select.disabled)state.select.focus({preventScroll:true});}}
 function open(select){
  if(select.matches(':disabled')||select.hidden||select.multiple||select.size>1||select.closest('[hidden]'))return;
  close();const dialog=el('dialog',undefined,'select-dialog'),header=el('div',undefined,'select-heading'),title=el('h2',label(select)),search=el('input'),list=el('div',undefined,'select-options');
  title.id='select-'+crypto.randomUUID();dialog.setAttribute('aria-labelledby',title.id);list.setAttribute('role','listbox');list.setAttribute('aria-label',label(select));
  header.append(title,v().iconAction(button('',close),'close','Закрыть список'));dialog.append(header);
  search.type='search';search.placeholder='Найти вариант';search.setAttribute('aria-label','Поиск по вариантам');search.autocomplete='off';if(select.options.length>8)dialog.append(search);dialog.append(list);
  function paint(){list.replaceChildren();const query=search.value.trim().toLocaleLowerCase('ru');let count=0;
   for(const option of select.options){if(option.hidden||option.textContent.toLocaleLowerCase('ru').indexOf(query)<0)continue;
    const b=button('',()=>{if(select.matches(':disabled')||!select.isConnected||option.closest('select')!==select||option.disabled||option.parentElement.disabled){close();return;}select.selectedIndex=option.index;select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}));close();});
    b.classList.add('select-option');b.setAttribute('role','option');b.setAttribute('aria-selected',String(option.selected));b.disabled=option.disabled||option.parentElement.disabled||false;
    b.append(el('span',option.textContent));if(option.selected)b.append(v().icon('check'));list.append(b);count++;
   }if(!count)list.append(el('p','Ничего не найдено','muted'));
  }
  search.addEventListener('input',paint);dialog.addEventListener('cancel',e=>{e.preventDefault();close();});dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close();}});
  dialog.addEventListener('keydown',e=>{if(!['ArrowDown','ArrowUp','Home','End'].includes(e.key)||e.target===search)return;const options=[...list.querySelectorAll('button:not(:disabled)')];if(!options.length)return;e.preventDefault();const i=options.indexOf(document.activeElement),next=e.key==='Home'?0:e.key==='End'?options.length-1:(i+(e.key==='ArrowDown'?1:-1)+options.length)%options.length;options[next].focus();});
  paint();document.body.append(dialog);active={select,dialog};dialog.showModal();(list.querySelector('[aria-selected=true]:not(:disabled)')||list.querySelector('button:not(:disabled)')||search).focus();
 }
 function enhance(){queued=false;
  for(const select of document.querySelectorAll('select')){if(connected.has(select)||select.hidden||select.multiple||select.size>1||select.id==='order-topic')continue;connected.add(select);select.classList.add('pushok-select');select.setAttribute('aria-haspopup','dialog');
   select.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();open(select);});select.addEventListener('mousedown',e=>e.preventDefault());select.addEventListener('click',e=>{e.preventDefault();if(active?.select!==select)open(select);});
   select.addEventListener('keydown',e=>{if(['Enter',' ','ArrowDown','ArrowUp','F4'].includes(e.key)){e.preventDefault();open(select);}});
  }
  forms();
  for(const note of document.querySelectorAll('[data-note],.block-note')){
   if(note.closest('.block-help')){note.removeAttribute('data-note');note.classList.remove('block-note');continue;}
   let block=note.closest('section,details,fieldset,form,.approval-body,.panel,.core-health');
   if(block?.matches('section.panel')&&block.parentElement.matches('details.wallet-disclosure'))block=block.parentElement;
   if(block?.closest('details.settings-section')&&!block.querySelector(':scope > h2,:scope > h3,:scope > h4'))block=block.closest('details.settings-section');
   while(block&&!block.querySelector(':scope > summary,:scope > h2,:scope > h3,:scope > h4,:scope > legend,:scope > .block-heading,:scope > .panel-heading'))block=block.parentElement?.closest('section,details,.approval-body,.panel,.core-health');
   help(block,[note]);
  }
  if(active&&(!active.select.isConnected||active.select.disabled||active.select.closest('[hidden]')))close();
 }
 function schedule(){if(!queued){queued=true;requestAnimationFrame(enhance);}}
 new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});window.addEventListener('hashchange',close);schedule();
 return {help,fieldActions,enhance,close};
})();
