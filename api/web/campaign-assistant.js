'use strict';
// Ответ модели — обычный текст. Все изменения выполняются штатными формами.
window.pushokAssistant=(()=>{
 const requests=new Set();
 function reset(){for(const controller of requests)controller.abort();requests.clear();}
 function panel(c,{expanded=false,decisionNode=null}={}){
  const host=el('details',undefined,'campaign-assistant'),heading=el('summary');
  heading.append(window.pushokViews.icon('info'),el('span','ИИ-помощник'));host.append(heading);
  if(expanded){host.open=true;host.classList.add('assistant-open');heading.addEventListener('click',e=>e.preventDefault());}
  const body=el('div',undefined,'assistant-body'),status=el('p','Разбор заявки, рекомендации и черновик комментария.','muted');
  const actions=el('div',undefined,'assistant-actions'),output=el('section',undefined,'assistant-output');
  const saved=el('section',undefined,'assistant-saved');body.append(saved);
  status.setAttribute('role','status');status.setAttribute('aria-live','polite');body.append(status,actions,output);host.append(body);
  let busy=false,loaded=false,epoch=0;const session=token,uid=me.id;
  const valid=()=>host.isConnected&&host.getClientRects().length>0&&session===token&&uid===me?.id&&['admin','operator'].includes(me?.role);
  async function request(path,options={}){
   const controller=new AbortController();requests.add(controller);
   const timer=setTimeout(()=>controller.abort(),20000);
   try{return await api(path,{...options,signal:controller.signal});}
   finally{clearTimeout(timer);requests.delete(controller);}
  }
  function list(title,items){if(!items?.length)return;const section=el('section'),ul=el('ul');section.append(el('h4',title),ul);items.forEach(text=>ul.append(el('li',text)));output.append(section);}
  function contentReview(review){
   if(!review)return;
   const state=['flagged','not_detected','uncertain'].includes(review.assessment)?review.assessment:'uncertain';
   const card=el('section',undefined,'assistant-content-review '+state),header=el('div',undefined,'assistant-review-heading');
   header.append(el('h4','Контент 18+'),el('span',review.label,'assistant-review-badge'));card.append(header,el('p',review.detail));
   card.append(el('p','Текстов публикаций: '+review.posts_count+' · '+({ai:'Оценка ИИ',rules:'Проверка маркеров',none:'Без оценки'}[review.engine]||'Без оценки'),'muted'));
   if(review.evidence?.length){
    const details=el('details',undefined,'assistant-evidence'),summary=el('summary','На чём основан вывод');details.append(summary);
    review.evidence.forEach(item=>{const quote=el('figure');quote.append(el('figcaption',item.source_label),el('blockquote',item.text));details.append(quote);});card.append(details);
   }
   output.append(card);window.pushokControls.help(card,[el('p',review.limitation,'muted')],'Границы проверки контента');
  }
  function render(result){
   output.replaceChildren();output.append(el('p','Снимок: '+date(result.generated_at),'muted'));
   if(result.kind==='review')contentReview(result.content_review);
   list('Проверено системой',result.facts);
   output.append(el('h4',result.engine==='ai'?'Рекомендации ИИ':'Проверки без ИИ'),el('p',result.advice.summary));
   if(result.kind==='review')output.append(el('p',result.posts_count?'Публичных публикаций в анализе: '+result.posts_count+'.':'Тексты публикаций недоступны. Содержание канала полностью не проверено.','muted'));
   list('Что проверить',result.advice.checks);list('Следующие шаги',result.advice.next_steps);
   if(result.advice.advertiser_message){
    const label=el('label','Черновик для рекламодателя'),draft=el('textarea');draft.value=result.advice.advertiser_message;draft.rows=4;draft.maxLength=1500;draft.setAttribute('aria-label','Черновик для рекламодателя');label.append(draft);output.append(label);
    const tools=el('div',undefined,'assistant-actions');
    tools.append(window.pushokViews.iconAction(button('',async()=>{if(!valid())return;try{await navigator.clipboard.writeText(draft.value);if(valid())status.textContent='Черновик скопирован.';}catch{if(valid()){draft.focus();draft.select();status.textContent='Выделите и скопируйте текст.';}}}),'copy','Скопировать черновик'));
    const comment=host.closest('.approval-card')?.querySelector('textarea[aria-label="Комментарий рекламодателю"]');
    if(comment)tools.append(window.pushokViews.iconAction(button('',()=>{
     if(!valid()||!comment.isConnected)return;
     if(comment.value.trim()&&!confirm('Заменить набранный комментарий черновиком ИИ?'))return;
     comment.value=draft.value;comment.dispatchEvent(new Event('input',{bubbles:true}));comment.closest('details').open=true;comment.focus();
     status.textContent='Черновик вставлен. Проверьте текст и выберите решение в форме. Он ещё не отправлен.';
    }),'edit','Вставить в комментарий'));output.append(tools);window.pushokControls.fieldActions(draft,[...tools.children]);tools.remove();
   }

  }
  async function analyze(kind){
   if(busy||!valid())return;busy=true;const version=++epoch;
   actions.querySelectorAll('button').forEach(b=>b.disabled=true);output.replaceChildren();status.textContent='Проверяем данные и готовим разбор…';
   try{
    const result=await request('/operator/campaigns/'+c.id+'/ai-analysis',{method:'POST',body:{kind}});
    if(!valid()||version!==epoch)return;render(result);
    status.textContent=result.engine==='ai'?'Разбор готов. Изменения не выполнялись.':'ИИ временно недоступен. Показаны проверки системы.';
   }catch(error){if(valid()&&version===epoch){status.textContent={campaign_ai_disabled:'Эта возможность ИИ выключена владельцем.',campaign_ai_limited:'Слишком много запросов. Повторите через минуту.',campaign_ai_changed:'Данные или настройки изменились. Обновите заявку и повторите.'}[error.code]||'Не удалось подготовить разбор. Повторите запрос.';}}
   finally{if(valid()&&version===epoch){busy=false;actions.querySelectorAll('button').forEach(b=>b.disabled=false);}}
  }
  async function load(){
   if(loaded||!valid())return;loaded=true;status.textContent='Проверяем доступные возможности…';
   try{
    const [data,moderation]=await Promise.all([request('/operator/ai-capabilities'),request('/community/moderation/'+c.id)]);if(!valid())return;actions.replaceChildren();
    const check=moderation?.check;saved.replaceChildren();
    if(check){
     saved.append(el('p',(check.rejected_by_agent?'Причина отказа: ':'')+check.reason));
     if(check.evidence?.length){const proof=el('details'),title=el('summary','На чём основано решение');proof.append(title);check.evidence.forEach(item=>proof.append(el('blockquote',item.quote)));saved.append(proof);}
    }
    else saved.append(el('p',c.moderation_status==='pending'?'Заявка ожидает проверки. Сохранённого разбора пока нет.':'Сохранённого разбора ИИ для этой заявки нет.'));
    if((check?.approved_by_agent||check?.rejected_by_agent)&&decisionNode){const link=el('a','Агент ИИ');link.href='#/user/ai-agent';link.addEventListener('click',()=>window.pushokProfiles.setBackRoute('moderation/'+c.id));decisionNode.replaceChildren(document.createTextNode(check.rejected_by_agent?'Отклонено: ':'Одобрено: '),link);decisionNode.classList.add('ai-decision');}
    for(const [id,kind,title] of [['campaign_review','review','Разобрать заявку'],['launch_diagnostics','launch','Проверить запуск']]){
     if(data.capabilities.includes(id)){const b=button(title,()=>analyze(kind));b.dataset.analysisKind=kind;actions.append(b);}
    }
    status.textContent=actions.childElementCount?'':'Новый анализ сейчас недоступен.';
    window.pushokControls.help(host,[el('p','Здесь отображается последняя сохранённая проверка. Кнопка «Разобрать заявку» запускает новый анализ. Решение по заявке можно отправить в разделе «Модерация».')],'О работе ИИ-помощника');
   }catch{if(valid()){loaded=false;status.textContent='Не удалось загрузить возможности.';actions.replaceChildren(button('Повторить',load));}}
  }
  host.addEventListener('toggle',()=>{if(host.open)void load();});if(expanded)setTimeout(()=>void load(),0);return host;
 }
 return {panel,reset,isBusy:()=>requests.size>0};
})();
