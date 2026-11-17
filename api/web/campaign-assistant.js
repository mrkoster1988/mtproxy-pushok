'use strict';
// Ответ модели — обычный текст. Все изменения выполняются штатными формами.
window.pushokAssistant=(()=>{
 const requests=new Set();
 function reset(){for(const controller of requests)controller.abort();requests.clear();}
 function panel(c){
  const host=el('details',undefined,'campaign-assistant'),heading=el('summary');
  heading.append(window.pushokViews.icon('info'),el('span','ИИ-помощник'));host.append(heading);
  const body=el('div',undefined,'assistant-body'),status=el('p','Разбор заявки, рекомендации и черновик комментария.','muted');
  const actions=el('div',undefined,'assistant-actions'),output=el('section',undefined,'assistant-output');
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
   card.append(el('p',review.limitation,'muted'));output.append(card);
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
    tools.append(button('Скопировать',async()=>{if(!valid())return;try{await navigator.clipboard.writeText(draft.value);if(valid())status.textContent='Черновик скопирован.';}catch{if(valid()){draft.focus();draft.select();status.textContent='Выделите и скопируйте текст.';}}}));
    const comment=host.closest('.approval-card')?.querySelector('textarea[aria-label="Комментарий рекламодателю"]');
    if(comment)tools.append(button('Вставить в комментарий',()=>{
     if(!valid()||!comment.isConnected)return;
     if(comment.value.trim()&&!confirm('Заменить набранный комментарий черновиком ИИ?'))return;
     comment.value=draft.value;comment.dispatchEvent(new Event('input',{bubbles:true}));comment.closest('details').open=true;comment.focus();
     status.textContent='Черновик вставлен. Проверьте текст и выберите решение в форме. Он ещё не отправлен.';
    }));output.append(tools);
   }
   output.append(el('p','Рекомендации могут быть неточными. Изменения и отправка сообщения выполняются вами в форме заявки.','muted'));
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
    const data=await request('/operator/ai-capabilities');if(!valid())return;actions.replaceChildren();
    for(const [id,kind,title] of [['campaign_review','review','Разобрать заявку'],['launch_diagnostics','launch','Проверить запуск']]){
     if(data.capabilities.includes(id)){const b=button(title,()=>analyze(kind));b.dataset.analysisKind=kind;actions.append(b);}
    }
    status.textContent=actions.childElementCount?'Выберите задачу. Анализ запускается только по нажатию.':'Помощник выключен. Владелец может включить его в настройках ИИ.';
    if(me.is_owner){const link=el('a','Настройки ИИ');link.href='#/settings';link.addEventListener('click',()=>setTimeout(()=>$('settings-system-tab')?.click(),0));body.append(link);}
   }catch{if(valid()){loaded=false;status.textContent='Не удалось загрузить возможности.';actions.replaceChildren(button('Повторить',load));}}
  }
  host.addEventListener('toggle',()=>{if(host.open)void load();});return host;
 }
 return {panel,reset};
})();
