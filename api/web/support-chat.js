'use strict';
window.pushokSupport=(()=>{
 const copy={
  ru:{chat:'Открыть чат в Telegram ↗',settings:'Настроить бота Pushok ИИ',team:'Обращения пользователей',kind:'Тип обращения',question:'Вопрос',problem:'Проблема',idea:'Предложение',message:'Ваш вопрос или предложение',human:'Позвать сотрудника',close:'Закрыть обращение',send:'Отправить',fresh:'Новое обращение',case:'Обращение',me:'Вы',user:'Пользователь',staff:'Сотрудник',open:'Открыто',closed:'Закрыто',waiting_staff:'Ожидает сотрудника',auto:'Авто',quick:['Подключить Flow','Как пополнить Stars?','Расход трафика']},
  en:{chat:'Open Telegram chat ↗',settings:'Bot settings',team:'Team inbox',kind:'Topic',question:'Question',problem:'Problem',idea:'Idea',message:'Your question or suggestion',human:'Talk to a person',close:'Close conversation',send:'Send',fresh:'New conversation',case:'Conversation',me:'You',user:'Customer',staff:'Team member',open:'Open',closed:'Closed',waiting_staff:'Waiting for the team',auto:'Automatic',quick:['Connect Flow','How do I top up Stars?','Data usage']},
  es:{chat:'Abrir chat en Telegram ↗',settings:'Ajustes del bot',team:'Bandeja del equipo',kind:'Tema',question:'Pregunta',problem:'Problema',idea:'Idea',message:'Tu pregunta o sugerencia',human:'Hablar con el equipo',close:'Cerrar consulta',send:'Enviar',fresh:'Nueva consulta',case:'Consulta',me:'Tú',user:'Usuario',staff:'Equipo',open:'Abierta',closed:'Cerrada',waiting_staff:'En espera del equipo',auto:'Automático',quick:['Conectar Flow','¿Cómo recargar Stars?','Consumo de datos']}
 };
 async function show(host){
  const session=token, panel=el('section',undefined,'panel vpn-policy-card');
  host.replaceChildren(panel);
  const live=()=>token===session&&panel.isConnected;
  const hint=(window.Telegram?.WebApp?.initDataUnsafe?.user?.language_code||navigator.language||'ru').slice(0,40);
  const pref=await api('/support/language',{session});
  if(!live())return;
  let preference=pref.preference, team=false, current=null, asStaff=false, rev=0, listRev=0, intent,key;
  const owner=!!(me?.is_owner||me?.is_developer), staff=owner||['admin','operator','moderator'].includes(me?.role);
  team=staff&&location.hash==='#/help/team';
  async function draw(draft='',selectedKind='question'){
   const automatic=hint.toLowerCase().split(/[-_]/)[0], locale=preference==='auto'?(Object.hasOwn(copy,automatic)?automatic:'en'):preference;
   const t=copy[locale]||copy.ru;
   const initialCase=current; rev++; listRev++; panel.replaceChildren();panel.lang=locale;
   const language=el('select');language.setAttribute('aria-label','Язык / Language / Idioma');
   for(const [k,v] of Object.entries({auto:t.auto,ru:'Русский',en:'English',es:'Español'})){const opt=el('option',v);opt.value=k;language.append(opt);}
   language.value=preference;
   const header=el('div',undefined,'support-chat-heading');header.append(el('h2','Pushok ИИ'),language);panel.append(header);
   const tg=el('a',t.chat,'ui-button labeled-action');tg.href='/ai-support';tg.target='_blank';tg.rel='noopener noreferrer';panel.append(tg);
   if(owner){const link=el('a',t.settings,'ui-button labeled-action');link.href='#/management/ai-bot';panel.append(link);}
   const list=el('div',undefined,'support-case-list'),thread=el('div'),feedback=el('p',undefined,'muted');
   feedback.setAttribute('role','status');
   if(staff){const toggle=el('input');toggle.type='checkbox';toggle.checked=team;const label=el('label',t.team);label.append(toggle);panel.append(label);
    toggle.onchange=()=>{team=toggle.checked;clear();void load();};}
   const chips=el('div',undefined,'support-quick'),entry=el('form',undefined,'vpn-rule-body'),kind=el('select'),msg=el('textarea');
   for(const k of ['question','problem','idea']){const o=el('option',t[k]);o.value=k;kind.append(o);}
   kind.setAttribute('aria-label',t.kind);msg.placeholder=t.message;msg.setAttribute('aria-label',t.message);msg.required=true;msg.maxLength=2000;
   const human=el('input'),close=el('input'),humanLabel=el('label',t.human),closeLabel=el('label',t.close);
   human.type=close.type='checkbox';humanLabel.append(human);closeLabel.append(close);
   const send=el('button',t.send,'ui-button labeled-action');send.type='submit';entry.append(kind,msg,humanLabel,closeLabel,send);
   const fresh=button(t.fresh,()=>clear());
   for(const prompt of t.quick)chips.append(button(prompt,()=>{msg.value=prompt;msg.focus();}));
   panel.append(list,thread,fresh,chips,entry,feedback);
   const mounted=()=>live()&&panel.contains(entry);
   function clear(){++rev;current=null;asStaff=false;thread.replaceChildren();kind.hidden=false;humanLabel.hidden=false;closeLabel.hidden=true;
    entry.hidden=chips.hidden=fresh.hidden=team;msg.value='';human.checked=close.checked=false;feedback.textContent='';}
   async function open(id){
    const turn=++rev;
    try{const data=await api('/support/cases/'+id,{session});if(!mounted()||turn!==rev)return;
     current=id;asStaff=team&&data.can_reply_as_staff;entry.hidden=team&&!asStaff;kind.hidden=chips.hidden=true;humanLabel.hidden=true;closeLabel.hidden=!asStaff;
     human.checked=close.checked=false;msg.value='';feedback.textContent='';thread.replaceChildren(el('h3',t.case+' №'+id+' · '+t[data.state]));
     for(const row of data.messages){
      const line=el('article',undefined,'vpn-notice-preview support-chat-message'),body=el('p');line.dataset.sender=row.sender;body.dir='auto';
      if(row.sender==='user')body.textContent=row.body;else appendFormattedText(body,row.body);
      const when=el('time',new Date(row.created_at).toLocaleString(locale,{dateStyle:'short',timeStyle:'short'}),'muted');when.dateTime=row.created_at;
      line.append(el('strong',({user:team?t.user:t.me,'ai-agent':'Pushok ИИ',staff:t.staff})[row.sender]),when,body);thread.append(line);}
     if(!team&&data.state==='open')thread.append(button(t.human,()=>submit(msg.value.trim()||t.human,true)));
    }catch(e){if(mounted()&&turn===rev)feedback.textContent=e.message;}
   }
   async function load(){const seq=++listRev;try{const data=await api('/support/cases?team='+team,{session});if(!mounted()||seq!==listRev)return;
    list.replaceChildren();for(const row of data.items)list.append(button((row.subject||t[row.category])+' · '+t[row.state]+(team?' · '+row.reply_locale.toUpperCase():''),()=>open(row.id)));
   }catch(e){if(mounted()&&seq===listRev)feedback.textContent=e.message;}}
   async function submit(text,handoff){
    if(send.disabled||!text)return;const turn=rev;send.disabled=true;language.disabled=true;
    const fp=JSON.stringify([current,asStaff,text,kind.value,handoff,close.checked]);if(fp!==key){key=fp;intent=crypto.randomUUID();}
    try{const row=await api(asStaff?'/support/cases/'+current+'/staff':'/support/cases',{method:'POST',session,body:asStaff?{request_id:intent,text,close:close.checked}:{request_id:intent,text,case_id:current,category:kind.value,human:handoff,locale_hint:hint}});
     if(!mounted()||turn!==rev)return;key=null;await open(row.id||current);await load();
    }catch(e){if(mounted()&&turn===rev)feedback.textContent=e.message;}finally{send.disabled=false;language.disabled=false;}
   }
   entry.onsubmit=e=>{e.preventDefault();void submit(msg.value.trim(),human.checked);};
   language.onchange=async()=>{language.disabled=true;try{
    const result=await api('/support/language',{method:'PUT',session,body:{preference:language.value}});
    if(!mounted())return;preference=result.preference;await draw(msg.value,kind.value);
   }catch(e){if(mounted()){language.disabled=false;language.value=preference;feedback.textContent=e.message;}}};
   clear();await load();if(initialCase&&mounted())await open(initialCase);
   if(mounted()){msg.value=draft;kind.value=selectedKind;}
  }
  await draw();
 }
 async function showBotSettings(host){
  host.replaceChildren();
  if(!hasDeveloperAccess(me))return;
  const inbox=el('a','Переписка с пользователями','ui-button labeled-action');inbox.href='#/help/team';host.append(inbox);
  const session=token;
  const form=el('form',undefined,'panel vpn-policy-card');
  const live=()=>token===session&&host.contains(form);
  const handle=el('a');handle.target='_blank';handle.rel='noopener noreferrer';
  const desc=el('p','Личный чат поддержки · ');desc.append(handle);
  const userInput=field(form,'@username бота','text',33);
  userInput.placeholder='@pushok_help_bot';userInput.pattern='@?[A-Za-z][A-Za-z0-9_]{1,28}[Bb][Oo][Tt]';
  userInput.title='От 5 до 32 латинских букв, цифр или подчёркиваний. Окончание — bot.';userInput.autocapitalize='none';userInput.spellcheck=false;
  const input=field(form,'Токен бота из BotFather','password',280);
  input.autocomplete='new-password';input.spellcheck=false;
  const note=el('p','Проверяем подключение…','muted');note.setAttribute('role','status');
  const save=submit('Подключить бота');
  form.prepend(el('h2','Подключение'),desc);
  form.append(el('small','Для другого бота укажите его @username и токен из BotFather.','muted'),save,note);
  const naming=el('form',undefined,'panel vpn-policy-card');
  naming.append(el('h2','Имя в Telegram'));
  const nameInput=field(naming,'Отображаемое имя','text',64);nameInput.required=true;nameInput.placeholder='Pushok ИИ';
  const rename=submit('Изменить имя в Telegram'),nameStatus=el('p','','muted');nameStatus.setAttribute('role','status');
  naming.append(el('small','Это имя видно в шапке чата. Адрес для поиска и ссылок задаётся полем @username.','muted'),rename,nameStatus);
  host.append(form,naming);naming.hidden=true;save.disabled=true;
  let identity;
  function display(info){
   identity=info;handle.textContent='@'+info.username;handle.href='https://t.me/'+info.username;
   userInput.value=handle.textContent;userInput.required=true;
   input.value='';input.required=!info.configured;save.textContent=info.configured?'Сохранить подключение':'Подключить бота';
   note.textContent=info.configured?'Бот подключён':'Бот ожидает подключения';
   nameInput.value=info.display_name||'';naming.hidden=!info.configured;
  }
  form.onsubmit=async event=>{
   event.preventDefault();if(save.disabled)return;
   save.disabled=true;note.textContent='Проверяем данные в Telegram…';
   try{
    const body={username:userInput.value.trim()};if(input.value.trim())body.token=input.value.trim();
    const info=await api('/support/bot',{method:'PUT',body,session});
    if(live())display(info);
   }catch(err){if(live())note.textContent=err.message;}
   finally{if(live())save.disabled=false;}
  };
  naming.onsubmit=async event=>{
   event.preventDefault();if(rename.disabled||!identity)return;rename.disabled=true;
   try{const info=await api('/support/bot/name',{method:'PUT',body:{username:identity.username,name:nameInput.value.trim()},session});
    if(live()){nameInput.value=info.display_name;nameStatus.textContent='Имя обновлено в Telegram';}}
   catch(err){if(live())nameStatus.textContent=err.message;}
   finally{if(live())rename.disabled=false;}
  };
  const artwork=el('form',undefined,'panel vpn-policy-card support-appearance');host.append(artwork);
  const styleLoad=showAppearance(artwork,session,live);
  try{const info=await api('/support/bot',{session});if(live()){display(info);save.disabled=false;}}
  catch(err){if(live())note.textContent=err.message;}
  await styleLoad;
 }
 function field(parent,title,type,max){
  const label=el('label',title,'vpn-field');
  const control=el(type==='textarea'?'textarea':'input');
  if(type!=='textarea')control.type=type;
  if(max)control.maxLength=max;
  label.append(control);parent.append(label);return control;
 }
 function submit(title){const btn=el('button',title,'ui-button labeled-action');btn.type='submit';return btn;}
 async function readPhoto(file){
  if(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>24*1024*1024)throw Error('Выберите JPG, PNG или WebP до 24 МБ.');
  const url=URL.createObjectURL(file);
  try{
   const img=new Image();img.src=url;await img.decode();
   if(!img.naturalWidth||!img.naturalHeight)throw Error('Не удалось прочитать изображение.');
   const scale=Math.min(1,1280/Math.max(img.naturalWidth,img.naturalHeight)),canvas=document.createElement('canvas');
   canvas.width=Math.max(1,Math.round(img.naturalWidth*scale));canvas.height=Math.max(1,Math.round(img.naturalHeight*scale));
   const ctx=canvas.getContext('2d');ctx.fillStyle='#102635';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(img,0,0,canvas.width,canvas.height);
   return canvas.toDataURL('image/jpeg',0.86);
  }finally{URL.revokeObjectURL(url);}
 }
 async function showAppearance(form,session,live){
  form.append(el('h2','Оформление сообщений'));
  const status=el('p','Загружаем образец…','muted');status.setAttribute('role','status');form.append(status);
  let data;
  try{data=await api('/support/bot/appearance',{session});}
  catch(err){if(live())status.textContent=err.message;return;}
  if(!live())return;
  const values=data.settings,inputs={};
  function textField(parent,key,title,max,type='text'){
   const item=field(parent,title,type,max);item.value=values[key];item.required=true;inputs[key]=item;return item;
  }
  const title=textField(form,'title','Заголовок приветствия',64);
  const welcome=textField(form,'welcome','Текст приветствия',500,'textarea');welcome.rows=5;
  const toolbar=el('div',undefined,'support-toolbar');toolbar.setAttribute('role','group');toolbar.setAttribute('aria-label','Форматирование приветствия');
  function formatButton(label,hint,wrap,placeholder){
   const control=el('button',label);control.type='button';control.title=hint;control.setAttribute('aria-label',hint);
   control.onclick=()=>{const a=welcome.selectionStart,b=welcome.selectionEnd,selected=welcome.value.slice(a,b);
    const add=wrap?wrap+(selected||placeholder)+wrap:placeholder;
    if(welcome.value.length-b+a+add.length>welcome.maxLength)return;
    welcome.setRangeText(add,a,b,'end');welcome.focus();if(wrap&&!selected)welcome.setSelectionRange(a+wrap.length,a+add.length-wrap.length);
    render();
   };toolbar.append(control);
  }
  formatButton('Ж','Полужирный','**','текст');formatButton('К','Курсив','__','текст');
  for(const icon of ['🐾','💡','✨','🙂'])formatButton(icon,'Вставить '+icon,'',icon);
  form.append(toolbar);
  const artLabel=el('label','Картинка приветствия','vpn-field'),mode=el('select');
  for(const [key,name] of [['mascot','Фирменный Пушок'],['custom','Моя картинка'],['none','Без картинки']]){
   const opt=el('option',name);opt.value=key;mode.append(opt);
  }mode.value=values.image_mode;artLabel.append(mode);form.append(artLabel);
  const upload=field(form,'Выбрать фото с устройства','file');upload.accept='image/jpeg,image/png,image/webp';
  let newPhoto='',reading=false,photoTurn=0;
  upload.onchange=async()=>{
   const turn=++photoTurn;newPhoto='';if(!upload.files?.[0]){reading=false;render();return;}
   reading=true;status.textContent='Готовим фото…';render();
   try{const src=await readPhoto(upload.files[0]);if(turn===photoTurn){newPhoto=src;status.textContent='';}}
   catch(err){if(turn===photoTurn){upload.value='';status.textContent=err.message;}}
   finally{if(live()&&turn===photoTurn){reading=false;render();}}
  };
  const details=el('details',undefined,'support-extra');details.append(el('summary','Кнопки, эмодзи и ответы'));form.append(details);
  for(const [key,text] of [['emoji','Добавлять эмодзи в заголовки и кнопки'],['buttons','Показывать кнопки под приветствием']]){
   const label=el('label',undefined,'support-toggle'),item=el('input');item.type='checkbox';item.checked=values[key];inputs[key]=item;label.append(item,document.createTextNode(text));details.append(label);
  }
  const toneLabel=el('label','Стиль ответов ИИ','vpn-field'),tone=el('select');
  for(const [key,label] of [['playful','Живой, с юмором'],['calm','Спокойный, по делу']]){
   const opt=el('option',label);opt.value=key;tone.append(opt);
  }
  tone.value=values.tone||'playful';toneLabel.append(tone);details.append(toneLabel);inputs.tone=tone;
  const buttons=el('div');details.append(buttons);
  const actions=[['question','💬','Кнопка вопроса'],['idea','💡','Кнопка предложения'],['human','👤','Кнопка сотрудника']];
  for(const [key,,label] of actions)textField(buttons,key,label,24);
  textField(details,'case_title','Заголовок ответа ИИ',48);textField(details,'staff_title','Заголовок ответа сотрудника',64);
  const preview=el('section',undefined,'support-preview'),top=el('div',undefined,'support-preview-top'),tabs=el('select');
  tabs.setAttribute('aria-label','Сообщение для предпросмотра');
  for(const [key,label] of [['welcome','Приветствие'],['case_title','Ответ ИИ'],['staff_title','Ответ сотрудника']]){
   const opt=el('option',label);opt.value=key;tabs.append(opt);
  }
  top.append(el('h3','Предпросмотр'),tabs);const bubble=el('div',undefined,'support-bubble');preview.append(top,bubble);
  const save=submit('Сохранить оформление');form.append(preview,save,status);status.textContent='';
  let saving=false;
  function render(){
   upload.parentElement.hidden=mode.value!=='custom';buttons.hidden=!inputs.buttons.checked;
   save.disabled=saving||reading;const icon=glyph=>inputs.emoji.checked?glyph+' ':'';
   bubble.replaceChildren();
   const body=el('div',undefined,'support-bubble-text');const key=tabs.value;
   if(key==='welcome'){
    const src=mode.value==='mascot'?data.mascot_path:mode.value==='custom'?(newPhoto||data.image_path):null;
    if(src){const img=el('img');img.src=src;img.alt=mode.value==='mascot'?'Пушок':'Картинка приветствия';bubble.append(img);}
    body.append(el('strong',icon('🐾')+title.value),document.createTextNode('\n\n'));appendFormattedText(body,welcome.value);
    bubble.append(body);
    if(inputs.buttons.checked){for(const [action,glyph] of actions)bubble.append(el('span',icon(glyph)+inputs[action].value,'support-preview-button'));}
    else body.append(document.createTextNode('\n\n/question — задать вопрос\n/idea — предложить идею\n/human — позвать сотрудника\n/new — начать заново'));
   }else{
    const isAI=key==='case_title';
    body.append(el('strong',icon(isAI?'💬':'👤')+inputs[key].value+(isAI?' №42':'')),document.createTextNode(isAI?'\n\nРасход по приложениям можно посмотреть в Pushok Flow.':'\n\nСпасибо за подробности! Команда проверит ваше обращение.'));
    bubble.append(body);
   }
  }
  tabs.onchange=render;form.addEventListener('input',render);mode.onchange=render;render();
  form.onsubmit=async event=>{
   event.preventDefault();if(saving||reading)return;
   if(mode.value==='custom'&&!newPhoto&&!data.image_path){status.textContent='Сначала выберите картинку.';upload.focus();return;}
   saving=true;render();status.textContent='Сохраняем…';
   const settings={image_mode:mode.value};
   for(const [k,v] of Object.entries(inputs))settings[k]=v.type==='checkbox'?v.checked:v.value.trim();
   const body={settings,revision:data.revision};if(mode.value==='custom'&&newPhoto)body.image_base64=newPhoto.split(',')[1];
   try{
    const result=await api('/support/bot/appearance',{method:'PUT',body,session});
    if(live()){data=result;newPhoto='';upload.value='';status.textContent='Оформление сохранено';}
   }catch(err){if(live())status.textContent=err.message;}
   finally{if(live()){saving=false;render();}}
  };
 }
 return {show,showBotSettings};
})();
