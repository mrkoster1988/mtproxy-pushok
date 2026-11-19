'use strict';
window.pushokModeratorQueue=(()=>{
 const drafts=new Map();
 let owner='',page=0,sequence=0,lastView='';
 async function render(){
  const session=token,request=++sequence,root=$('queue');
  const current=()=>request===sequence&&session===token&&me?.role==='moderator'&&location.hash.split('/')[1]==='moderation';
  if(owner!==session){owner=session;page=0;lastView='';drafts.clear();root.replaceChildren();}
  $('queue-filter').hidden=true;
  $('queue-count').hidden=true;
  try{
   const data=await api('/moderation/queue?page='+page);
   if(!current())return;
   if(!data.items.length&&page){page--;return render();}
   const view=JSON.stringify([page,data]);
   if(view===lastView&&root.dataset.viewer==='moderator')return;
   const list=document.createDocumentFragment();
   list.append(el('h2','Заявки на проверку'));
   let showingOperatorDecisions=false;
   for(const item of data.items){
    if(item.operator_decision_id&&!showingOperatorDecisions){
     list.append(el('h2','Решения операторов'));showingOperatorDecisions=true;
    }
    const card=el('article',undefined,'card'),heading=el('h3'),channel=el('a',item.title||item.telegram_url);
    channel.href=item.telegram_url;channel.target='_blank';channel.rel='noopener noreferrer';
    heading.append(channel);card.append(heading);
    if(item.operator_decision_id)card.append(el('p','Решение оператора: '+({approved:'одобрено',changes_requested:'запрошены исправления',rejected:'отклонено'}[item.moderation_status]),'muted'));
    if(item.reason)card.append(el('p',item.reason));
    if(item.description)card.append(el('p',item.description,'muted'));
    const reasonLabel=el('label','Комментарий рекламодателю'),reason=el('textarea');
    reason.maxLength=2000;reason.rows=2;reason.value=drafts.get(item.id)||'';
    reason.addEventListener('input',()=>{if(reason.value)drafts.set(item.id,reason.value);else drafts.delete(item.id);});
    reasonLabel.append(reason);card.append(reasonLabel);
    const actions=el('div',undefined,'actions');
    for(const [decision,label] of [['approved','Одобрить'],['changes_requested','Запросить исправления'],['rejected','Отклонить']]){
     if(decision===item.moderation_status)continue;
     const action=button(label,async()=>{
      const comment=reason.value.trim()||null;
      if((decision!=='approved'||item.operator_decision_id)&&!comment){reason.focus();throw Error('Укажите причину для рекламодателя.');}
      await api('/moderation/queue/'+item.id,{method:'POST',body:{decision,comment,operator_decision_id:item.operator_decision_id}});
      if(session===token&&me?.role==='moderator'&&location.hash.split('/')[1]==='moderation'){
       drafts.delete(item.id);notice('Решение сохранено.',true);await render();
      }
     });
     action.classList.add('labeled-action');
     if(decision==='approved')action.classList.add('action-positive');
     if(decision==='rejected')action.classList.add('action-negative');
     actions.append(action);
    }
    card.append(actions);list.append(card);
   }
   if(!data.items.length)list.append(el('p','Заявок на проверку сейчас нет.','empty'));
   if(page)list.append(button('Назад',()=>{page--;return render();}));
   if(data.more)list.append(button('Далее',()=>{page++;return render();}));
   root.replaceChildren(list);root.dataset.viewer='moderator';lastView=view;
  }catch(error){
   if(current()){lastView='';root.replaceChildren(el('p',error.message),button('Повторить',render));}
  }
 }
 return {render};
})();
