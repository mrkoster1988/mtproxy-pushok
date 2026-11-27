'use strict';
window.pushokVpnSettings=(()=>{
 const groups=[
   {key:'expiry',title:'Окончание подписки',icon:'⏳'},
   {key:'never',title:'Первое подключение',icon:'👋'},
   {key:'idle',title:'Давно без VPN',icon:'🐾'}
 ];
 const heads={expiry:'Сколько ещё работает VPN?',never:'Первый выход с Пушком',idle:'Пушок на связи'};
 function field(label,input){const l=el('label',label,'vpn-field');l.append(input);return l;}
 function number(value,min,max){const n=el('input');n.type='number';n.value=value;n.min=min;n.max=max;n.step=1;n.required=true;return n;}
 function switcher(value,title){const n=el('input');n.type='checkbox';n.checked=value;n.setAttribute('aria-label',title);return n;}
 function form(title){const f=el('form',null,'vpn-policy-card');f.append(el('h2',title));return f;}
 function action(f,save){
   const bar=el('div',null,'vpn-save'),submit=el('button','Сохранить','labeled-action'),note=el('small',null,'muted');
   submit.type='submit';note.setAttribute('role','status');bar.append(submit,note);f.append(bar);
   f.onsubmit=async(e)=>{e.preventDefault();if(submit.disabled||!f.reportValidity())return;submit.disabled=true;
     try{await save();note.textContent='Сохранено';}catch(err){note.textContent=err.message;}finally{submit.disabled=false;}};
 }
 function preview(box,template,group,days){
   box.replaceChildren(el('strong',group.icon+' '+heads[group.key]));
   const p=el('p'),date=new Date(Date.now()+(group.key==='expiry'?days:-days)*86400000);
   const text=template.replaceAll('{срок}',days+' '+({one:'день',few:'дня',many:'дней',other:'дней'})[new Intl.PluralRules('ru').select(days)]).replaceAll('{дней}',String(days)).replaceAll('{имя}','Александр').replaceAll('{дата}',date.toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'}));
   appendFormattedText(p,text);
   box.append(p,el('small','Pushok ИИ','muted'));
 }
 function notifications(host,record){
   const f=form('Telegram Stars и уведомления'),p=record.policy,readers=[];
   const stars=el('select');for(const [key,title] of [['inherit','Как настроено в кабинете'],['on','Включено'],['off','Выключено']]){
     const opt=el('option',title);opt.value=key;stars.append(opt);}
   stars.value=p.stars_enabled==null?'inherit':p.stars_enabled?'on':'off';
   f.append(field('Приём Stars за VPN через бота кабинета',stars));
   for(const group of groups){
     const k=group.key,d=el('details',null,'vpn-rule'),s=el('summary'),header=el('span',group.icon+' '+group.title);
     const state=el('small',null,'vpn-rule-state');s.append(header,state);d.append(s);d.open=k==='expiry';
     const inner=el('div',null,'vpn-rule-body'),on=switcher(p[k+'_enabled'],'Включить '+group.title);
     const enabled=field('Отправлять уведомление',on);enabled.classList.add('vpn-switch');inner.append(enabled);
     const grid=el('div',null,'vpn-rule-grid');let days,repeat;
     if(k==='expiry'){
       days=el('input');days.value=p.expiry_days.join(', ');days.required=true;days.maxLength=55;
       grid.append(field('За сколько дней до окончания',days));
       days.oninput=()=>days.setCustomValidity('');
     }else{
       days=number(p[k+'_days'],1,365);repeat=number(p[k+'_repeat'],0,365);
       grid.append(field(k==='idle'?'Дней без подключения':'Дней после регистрации',days),field('Повтор через, дней',repeat));
     }
     inner.append(grid,el('small',k==='expiry'?'Введите сроки через запятую.':'0 в поле повтора — отправить один раз.','muted'));
     if(k==='never'){
       const stamp=record.last_first_notice_at;
       const when=stamp?new Date(stamp*1000).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'})+' мск':'';
       inner.append(el('small',when?'Последняя подтверждённая отправка · '+when:'Подтверждённых отправок пока нет.','muted'));
     }
     const msg=el('textarea');msg.value=p[k+'_text'];msg.required=true;msg.maxLength=700;msg.rows=3;
     inner.append(field('Текст сообщения',msg));
     const tools=el('div',null,'vpn-format');tools.setAttribute('role','group');tools.setAttribute('aria-label','Форматирование и подстановки');
     for(const [name,mark] of [['Жирный','**'],['Курсив','__'],['Имя','{имя}'],['Дни','{дней}'],['Дни словами','{срок}'],['Дата','{дата}']]){
       const b=el('button',mark==='**'?'Ж':mark==='__'?'К':name);b.title=name;b.setAttribute('aria-label',name);b.type='button';b.onclick=()=>{const a=msg.selectionStart,z=msg.selectionEnd;
         const replacement=mark.startsWith('{')?mark:mark+(msg.value.slice(a,z)||'текст')+mark;
         msg.setRangeText(replacement,a,z,'end');msg.focus();paint();};tools.append(b);
     }
     const sample=el('div',null,'vpn-notice-preview');sample.setAttribute('aria-label','Предпросмотр сообщения');
     const paint=()=>{state.textContent=on.checked?'Включено':'Выключено';state.dataset.on=String(on.checked);
       preview(sample,msg.value,group,parseInt(days.value,10)||1);};
     inner.addEventListener('input',paint);inner.append(tools,sample);d.append(inner);f.append(d);paint();
     readers.push(next=>{next[k+'_enabled']=on.checked;next[k+'_text']=msg.value;
       if(k==='expiry'){const list=days.value.split(',').map(x=>Number(x.trim()));
         if(!list.length||list.length>12||list.some(x=>!Number.isInteger(x)||x<1||x>365)||new Set(list).size!==list.length){
           d.open=true;days.setCustomValidity('Введите от 1 до 12 разных сроков, каждый от 1 до 365 дней.');days.reportValidity();throw Error(days.validationMessage);}
         next.expiry_days=list;
       }else{next[k+'_days']=Number(days.value);next[k+'_repeat']=Number(repeat.value);}});
   }
   action(f,async()=>{const policy={stars_enabled:stars.value==='inherit'?null:stars.value==='on'};for(const read of readers)read(policy);
     const result=await api('/vpn/settings',{method:'PUT',body:{version:record.version,policy}});record.version=result.version;});
   host.replaceChildren(f);
 }
 function access(host,policy){
   const f=form('Выдача подключения'),on=switcher(policy.enabled,'Автоматическая выдача'),days=number(policy.days,1,30),capacity=number(policy.max_users,1,100000);
   const toggle=field('Автоматическая выдача подписки',on);toggle.classList.add('vpn-switch');f.append(toggle);
   const grid=el('div',null,'vpn-rule-grid');grid.append(field('Срок для аккаунта, дней',days),field('Лимит автовыдач',capacity));f.append(grid);
   f.append(el('small','Выдано: '+policy.used+' · Гостевой доступ: 7 дней','muted'));
   action(f,async()=>{const result=await api('/vpn/settings/access',{method:'PUT',body:{version:policy.version,enabled:on.checked,days:Number(days.value),max_users:Number(capacity.value)}});policy.version=result.version;});
   host.replaceChildren(f);
 }
 async function show(host){
   if(!host||!(me?.role==='admin'||hasDeveloperAccess(me)))return;
   const session=token,notices=el('div','Загрузка уведомлений…'),issuance=el('div','Загрузка условий…'),plans=el('div','Загрузка тарифов…'),links=el('nav',null,'vpn-policy-links');
   for(const [title,url] of [['Подписки и расход','#/vpn'],['Настройки платежей','#/management/payments']]){
     const a=el('a',title,'ui-button');a.href=url;links.append(a);}
   host.replaceChildren(links,plans,notices,issuance);
   await Promise.all([[notices,'/vpn/settings',notifications],[issuance,'/vpn/settings/access',access],[plans,'/vpn/settings/plans',window.pushokVpnPlans.catalog]].map(async([node,path,render])=>{
     try{const data=await api(path);if(session===token&&node.isConnected)render(node,data);}
     catch(e){if(session===token&&node.isConnected)node.replaceChildren(el('p',e.message),button('Обновить',()=>show(host)));}
   }));
 }
 return {show};
})();
