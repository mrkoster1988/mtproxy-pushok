'use strict';
window.pushokEditorial=function(host){
 if(!hasDeveloperAccess(me))return;
 const session=token,root=el('details',undefined,'panel editorial');
 root.append(el('summary','Посты для Telegram-канала'));host.append(root);
 const status=el('p',undefined,'muted'),list=el('div',undefined,'editorial-list'),edit=el('div');
 status.setAttribute('role','status');
 const request=(path,method='GET',body)=>api('/editorial'+path,{method,session,body});
 function select(values,label){
  const input=el('select');input.setAttribute('aria-label',label);
  for(const [value,title] of Object.entries(values)){const option=el('option',title);option.value=value;input.append(option);}
  return input;
 }
 const products={flow:'Pushok Flow',miniapp:'Pushok Mini App'}, languages={ru:'Русский',en:'English',es:'Español'};
 const states={draft:'Черновик',sending:'Отправка начата',published:'Опубликован',uncertain:'Проверьте публикацию в канале'};
 const product=select(products,'Продукт'),brief=el('textarea');brief.maxLength=500;brief.placeholder='Что рассказать подписчикам?';
 const errors={editorial_ai_disabled:'В настройках ИИ включите «Посты и иллюстрации о Pushok».',
  editorial_changed:'Черновик уже изменился. Откройте его ещё раз.',
  editorial_send_unknown:'Ответ Telegram не получен. Проверьте канал перед следующей публикацией.',
  editorial_send_rejected:'Telegram отклонил отправку. Проверьте права бота в канале.',
  editorial_channel_owner:'Для публикации нужны права на отправку постов в этом канале.',
  editorial_image_failed:'Иллюстрацию создать не удалось. Можно выбрать фирменного Пушка.',
  editorial_generation_failed:'Не удалось подготовить текст. Можно начать с шаблона.'};
 let busy=false,selected=null,locale='ru',dirty=false,creating=null;
 const live=()=>root.isConnected&&token===session;
 async function run(job){if(busy)return;busy=true;status.textContent='';root.setAttribute('aria-busy','true');
  try{await job();}catch(e){if(live())status.textContent=errors[e.code]||errors[e.message]||e.message;}
  finally{busy=false;root.removeAttribute('aria-busy');}}
 const acceptDiscard=()=>!dirty||window.confirm('Закрыть несохранённые изменения?');
 async function loadList(){
  const data=await request('');if(!live())return;list.replaceChildren();
  for(const item of data.items){
   const day=new Date(item.created_at).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'});
   list.append(button(products[item.product]+' · '+day+' · '+states[item.state],()=>run(async()=>{if(acceptDiscard())await open(item.id);})));
  }
 }
 async function open(id){const data=await request('/'+id);if(live()){selected=data;dirty=false;render();}}
 async function create(useAI){
  if(!acceptDiscard())return;
  const fingerprint=JSON.stringify([product.value,brief.value,useAI]);
  if(creating?.fingerprint!==fingerprint)creating={fingerprint,id:crypto.randomUUID()};
  const result=await request('','POST',{id:creating.id,product:product.value,brief:brief.value,use_ai:useAI});
  creating=null;await open(result.id);await loadList();
 }
 const actions=el('div',undefined,'support-quick');actions.append(button('Подготовить с ИИ',()=>run(()=>create(true))),button('Начать с шаблона',()=>run(()=>create(false))));
 root.append(product,brief,actions,status,edit,list);
 root.addEventListener('toggle',()=>{if(root.open)void run(loadList);});
 function render(){
  const row=selected;edit.replaceChildren();if(!row)return;
  const path='/'+row.id,lang=select(languages,'Язык публикации'),art=select({mascot:'Фирменный Пушок',...(row.image?{generated:'Иллюстрация ИИ'}:{}),none:'Без картинки'},'Иллюстрация');
  if(row.state!=='draft'&&row.locale)locale=row.locale;lang.value=locale;lang.disabled=row.state!=='draft';art.value=row.art;
  const fields=el('div',undefined,'support-quick');fields.append(lang,art);
  const text=el('textarea');text.maxLength=950;text.rows=5;text.value=row.variants[locale];text.setAttribute('aria-label','Текст публикации');
  const preview=el('article',undefined,'support-bubble'),controls=el('div',undefined,'support-quick');
  function draw(){preview.replaceChildren();if(art.value!=='none'){
    const img=el('img');img.src=art.value==='mascot'?'/assets/pushok-pencil.png':row.image;img.alt='Иллюстрация публикации';preview.append(img);}
   const copy=el('div',undefined,'support-bubble-text');appendFormattedText(copy,text.value);preview.append(copy);}
  async function save(){if(!dirty)return;const result=await request(path,'PUT',{locale,text:text.value,art:art.value,revision:row.revision});
   row.variants[locale]=text.value;row.art=art.value;row.revision=result.revision;dirty=false;}
  lang.onchange=()=>{const next=lang.value;lang.value=locale;void run(async()=>{await save();locale=next;render();});};
  text.oninput=art.onchange=()=>{dirty=true;draw();};draw();
  const editable=row.state==='draft';art.disabled=text.disabled=!editable;
  edit.append(el('h3',products[row.product]+' · '+states[row.state]),fields);
  if(editable)edit.append(text);edit.append(preview);
  if(!editable){if(row.channel)edit.append(el('p',row.channel,'muted'));return;}
  controls.append(button('Сохранить',()=>run(async()=>{await save();status.textContent='Сохранено';})),
   button('Нарисовать с ИИ',()=>run(async()=>{await save();status.textContent='Пушок рисует…';await request(path+'/image?revision='+row.revision,'POST',{});await open(row.id);status.textContent='Иллюстрация готова';})));
  const channel=el('input');channel.placeholder='@имя_канала';channel.setAttribute('aria-label','Канал для публикации');channel.value=row.channel||'';
  const publish=button('Утвердить и опубликовать',()=>run(async()=>{
   const target=channel.value.trim();
   if(!/^@[A-Za-z][A-Za-z0-9_]{4,31}$/.test(target)){channel.focus();status.textContent='Укажите @имя канала';return;}
   await save();if(!window.confirm('Опубликовать этот вариант ('+languages[locale]+') в '+target+'?'))return;
   await request(path+'/publish','POST',{channel:target,locale,revision:row.revision});await open(row.id);await loadList();status.textContent='Пост опубликован';
  }));
  edit.append(controls,channel,publish);
 }
};
