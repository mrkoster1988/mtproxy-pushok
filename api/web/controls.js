'use strict';
// Общие поля, справка и списки. Исходные select остаются частью формы и API браузера.
window.pushokControls=(()=>{
 const connected=new WeakSet();let active=null,queued=false;
 const v=()=>window.pushokViews;
 function help(block,nodes,title){
  nodes=nodes.filter(Boolean);if(!nodes.length||!block)return;
  let heading=block.querySelector(':scope > summary,:scope > .panel-heading,:scope > .block-heading,:scope > .analytics-header,:scope > .report-header,:scope > .assistant-review-heading');
  if(!heading){const title=block.querySelector(':scope > div > h3')?.parentElement;if(title){heading=el('div',undefined,'block-heading');title.before(heading);heading.append(title);}}
  if(!heading){const h=block.querySelector(':scope > h2,:scope > h3,:scope > h4,:scope > legend');if(!h)return;if(h.tagName==='LEGEND'){heading=h;heading.classList.add('block-heading');}else{heading=el('div',undefined,'block-heading');h.before(heading);heading.append(h);}}
  let body=block.querySelector(':scope > .block-help');
  if(!body){body=el('div',undefined,'block-help');body.id='help-'+crypto.randomUUID();body.hidden=true;
   const toggle=v().iconAction(button('',()=>{body.hidden=!body.hidden;if(!body.hidden&&block.matches('details'))block.open=true;toggle.setAttribute('aria-expanded',String(!body.hidden));}),'info',title||'Пояснения: '+heading.textContent.trim());
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
   const confirmations=[...form.querySelectorAll('input[type=checkbox],input[type=radio]')];
   if(confirmations.some(input=>(direct.compareDocumentPosition(input)&Node.DOCUMENT_POSITION_FOLLOWING)&&buttons.some(b=>input.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING)))continue;
   fieldActions(field,buttons);
  }
 }
 function label(select){return select.getAttribute('aria-label')||[...select.labels].map(l=>[...l.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('')).join(' ').trim()||'Выберите вариант';}
 function unavailable(select,trigger=select){return !select.isConnected||select.matches(':disabled')||select.multiple||select.size>1||!trigger.isConnected||trigger.matches(':disabled')||!trigger.getClientRects().length||Boolean(trigger.closest('[hidden]'));}
 function close(){
  if(!active)return;const state=active;active=null;state.observer.disconnect();state.select.removeEventListener('change',state.refresh);state.select.removeEventListener('input',state.refresh);
  state.trigger.setAttribute('aria-expanded','false');state.dialog.close();state.dialog.remove();
  if(!unavailable(state.select,state.trigger))state.trigger.focus({preventScroll:true});
 }
 function open(select,{trigger=select,title:caption=label(select),query:initialQuery='',searchLabel='Поиск по вариантам',closeLabel='Закрыть список',alwaysSearch=false}={}){
  if(unavailable(select,trigger))return;close();
  const dialog=el('dialog',undefined,'select-dialog'),header=el('div',undefined,'select-heading'),title=el('h2',caption),search=el('input'),list=el('div',undefined,'select-options');
  title.id='select-'+crypto.randomUUID();dialog.setAttribute('aria-labelledby',title.id);list.setAttribute('role','listbox');list.setAttribute('aria-label',caption);
  header.append(title,v().iconAction(button('',close),'close',closeLabel));dialog.append(header);
  search.type='search';search.placeholder='Найти вариант';search.setAttribute('aria-label',searchLabel);search.autocomplete='off';search.value=initialQuery;
  if(alwaysSearch||select.options.length>8||initialQuery)dialog.append(search);dialog.append(list);
  function options(){return [...list.querySelectorAll('button:not(:disabled)')];}
  function focusOption(b){if(!b)return;for(const item of options())item.tabIndex=item===b?0:-1;b.focus({preventScroll:true});b.scrollIntoView({block:'nearest'});}
  function paint(){
   if(unavailable(select,trigger)){if(active?.dialog===dialog)close();return;}
   const focused=list.contains(document.activeElement)?document.activeElement.dataset.optionIndex:null,scroll=list.scrollTop;
   list.replaceChildren();const query=search.value.trim().toLocaleLowerCase('ru');let count=0,group=null;
   for(const option of select.options){
    const parent=option.parentElement;if(option.hidden||(parent!==select&&parent.hidden)||!option.textContent.toLocaleLowerCase('ru').includes(query))continue;
    if(parent.tagName==='OPTGROUP'&&parent!==group){group=parent;list.append(el('div',parent.label,'select-group'));}
    const b=button('',()=>{
     if(unavailable(select,trigger)||option.closest('select')!==select||option.disabled||option.hidden||parent.disabled||(parent!==select&&parent.hidden)){close();return;}
     const changed=select.selectedIndex!==option.index;select.selectedIndex=option.index;close();
     if(changed){select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}));}
    });
    b.classList.add('select-option');b.dataset.optionIndex=String(option.index);b.setAttribute('role','option');b.setAttribute('aria-selected',String(option.selected));b.disabled=option.disabled||parent.disabled||false;b.tabIndex=-1;
    b.append(el('span',option.textContent));if(option.selected)b.append(v().icon('check'));list.append(b);count++;
   }
   if(!count){const empty=el('p','Ничего не найдено','muted');empty.setAttribute('role','status');list.append(empty);}
   const enabled=options(),current=enabled.find(b=>b.getAttribute('aria-selected')==='true')||enabled[0];if(current)current.tabIndex=0;
   if(focused!==null){const next=enabled.find(b=>b.dataset.optionIndex===focused)||current;if(next)focusOption(next);else if(search.isConnected)search.focus();else header.querySelector('button').focus();}else list.scrollTop=scroll;
  }
  search.addEventListener('input',paint);dialog.addEventListener('cancel',e=>{e.preventDefault();close();});dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)close();}});
  dialog.addEventListener('keydown',e=>{
   if(!['ArrowDown','ArrowUp','Home','End'].includes(e.key)||(e.target===search&&['Home','End'].includes(e.key)))return;
   const enabled=options();if(!enabled.length)return;e.preventDefault();const i=enabled.indexOf(document.activeElement),down=e.key==='ArrowDown';
   const next=e.key==='Home'?0:e.key==='End'?enabled.length-1:i<0?(down?0:enabled.length-1):(i+(down?1:-1)+enabled.length)%enabled.length;focusOption(enabled[next]);
  });
  const refresh=()=>{if(active?.dialog===dialog)paint();},observer=new MutationObserver(refresh);
  observer.observe(select,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['disabled','hidden','label','selected','value']});select.addEventListener('change',refresh);select.addEventListener('input',refresh);
  active={select,trigger,dialog,observer,refresh};trigger.setAttribute('aria-expanded','true');paint();document.body.append(dialog);dialog.showModal();
  if(initialQuery||alwaysSearch)search.focus();else focusOption(list.querySelector('[aria-selected=true]:not(:disabled)')||options()[0]);
 }
 function enhance(){queued=false;
  for(const select of document.querySelectorAll('select')){if(connected.has(select)||select.hidden||select.multiple||select.size>1||select.id==='order-topic')continue;connected.add(select);select.classList.add('pushok-select');select.setAttribute('aria-haspopup','dialog');select.setAttribute('aria-expanded','false');
   select.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();open(select);});select.addEventListener('mousedown',e=>e.preventDefault());select.addEventListener('click',e=>{e.preventDefault();if(active?.select!==select)open(select);});
   select.addEventListener('keydown',e=>{if(e.ctrlKey||e.metaKey||(e.altKey&&!['ArrowDown','ArrowUp'].includes(e.key)))return;if(['Enter',' ','ArrowDown','ArrowUp','F4'].includes(e.key)){e.preventDefault();open(select);}else if(e.key.length===1){e.preventDefault();open(select,{query:e.key});}});
  }
  forms();
  for(const note of document.querySelectorAll('[data-note],.block-note')){
   if(note.closest('.block-help')){note.removeAttribute('data-note');note.classList.remove('block-note');continue;}
   let block=note.closest('section,details,fieldset,form,.approval-body,.panel,.core-health');
   if(block?.matches('section.panel')&&block.parentElement.matches('details.wallet-disclosure'))block=block.parentElement;
   if(block?.closest('details.settings-section')&&!block.querySelector(':scope > h2,:scope > h3,:scope > h4'))block=block.closest('details.settings-section');
   while(block&&!block.querySelector(':scope > summary,:scope > h2,:scope > h3,:scope > h4,:scope > legend,:scope > div > h3,:scope > .block-heading,:scope > .panel-heading'))block=block.parentElement?.closest('section,details,.approval-body,.panel,.core-health');
   help(block,[note]);
  }
  if(active&&unavailable(active.select,active.trigger))close();
 }
 function schedule(){if(!queued){queued=true;requestAnimationFrame(enhance);}}
 new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','hidden']});window.addEventListener('hashchange',close);schedule();
 return {help,fieldActions,enhance,open,close};
})();
