'use strict';
window.pushokAppLicense=(()=>{
 let epoch=0,root=null,paymentTimer=0;
 const limits=[['max_nodes','Узлы'],['max_admins','Администраторы'],['max_campaigns','Незавершённые кампании']];
 Object.assign(errors,{application_license_required:'Для новых операций нужна действующая лицензия приложения. Продление выполняет администратор.',application_license_limit_max_nodes:'Достигнут лимит узлов лицензии приложения.',application_license_limit_max_admins:'Достигнут лимит администраторов лицензии приложения.',application_license_limit_max_campaigns:'Достигнут лимит кампаний лицензии приложения. Дождитесь завершения текущих.',license_plan_required:'Сначала включите хотя бы один тариф.',license_plan_changed:'Тариф изменился. Обновите список перед покупкой.',license_plan_not_found:'Тариф недоступен.',license_pending_invoice:'У установки есть открытый счёт. Завершите или закройте его в истории.',license_sales_disabled:'Продажа лицензий отключена.',license_server_not_configured:'Адрес сервера лицензий ещё не настроен.',license_key_not_configured:'Ключ проверки лицензий ещё не настроен.',license_server_unavailable:'Сервер лицензий временно недоступен. Повторите проверку позже.'});
 const states={pending:'Ожидает оплаты',paid:'Оплачено',closed:'Закрыт',refunded:'Возвращено'};
 function panel(title){const n=el('section',undefined,'panel');n.append(el('h2',title));return n;}
 function input(form,title,type,value){const label=el('label',title),i=el('input');i.type=type;i.setAttribute('aria-label',title);if(type==='checkbox')i.checked=!!value;else i.value=value??'';label.append(i);form.append(label);return i;}
 function terms(n,p,usage){const dl=el('dl',undefined,'campaign-facts');for(const [k,t] of limits)dl.append(el('dt',t),el('dd',(usage?usage[k]+' / ':'')+(p[k]===null?'∞':p[k])));n.append(dl);}
 function submit(form,title,action){const b=el('button',title,'primary');b.type='submit';form.append(b);form.onsubmit=e=>{e.preventDefault();void run(action,b);};}
 function editor(p={name:'',days:30,price_xtr:100,max_nodes:null,max_admins:null,max_campaigns:null,active:false}){
  const area=panel(p.id?'Изменить тариф':'Новый тариф'),f=el('form');area.append(f);
  const name=input(f,'Название','text',p.name),days=input(f,'Срок, дней','number',p.days),price=input(f,'Цена, Stars','number',p.price_xtr);name.required=true;name.maxLength=80;days.min=1;days.max=3650;days.required=true;price.min=1;price.max=1000000;price.required=true;
  const fields={};for(const [k,t] of limits){const i=input(f,t,'number',p[k]);i.min=k==='max_admins'?1:0;i.max=1000000;i.step=1;i.placeholder='Без лимита';fields[k]=i;}
  f.append(el('small','Пустое поле — без лимита. Лимиты относятся ко всей установке.','muted'));const active=input(f,'Доступен для покупки','checkbox',p.active);
  submit(f,'Сохранить тариф',async()=>{const body={name:name.value.trim(),days:Number(days.value),price_xtr:Number(price.value),active:active.checked};for(const[k]of limits)body[k]=fields[k].value===''?null:Number(fields[k].value);await api('/admin/license/plans'+(p.id?'/'+p.id:''),{method:p.id?'PUT':'POST',body});await load(root);notice('Тариф сохранён. Купленные условия сохраняются.',true);});f.append(button('Отмена',()=>area.remove()));root.prepend(area);name.focus();
 }
 async function openInvoice(url){if(miniAppMode)await window.pushokMiniApp.payInvoice(url);else window.open(url,'_blank','noopener,noreferrer');}
 async function check(){await api('/admin/license/sync',{method:'POST',body:{}});await load(root);}
 function watchPayment(id){
  clearTimeout(paymentTimer);const target=root,session=token;let attempts=0;
  async function poll(){
   if(target!==root||session!==token||!target?.isConnected||!location.hash.startsWith('#/license'))return;
   try{const state=await api('/admin/license/status'),order=state.envelope?.payload.orders.find(o=>o.id===id);
    if(order&&['paid','refunded','closed'].includes(order.status)){if(target===root&&session===token&&location.hash.startsWith('#/license')){await load(target);if(order.status==='paid')notice(order.test_mode?'Тестовый платёж подтверждён. Лицензия не активируется.':'Оплата лицензии подтверждена.',true);}return;}
   }catch{ return; }
   if(++attempts<24)paymentTimer=setTimeout(poll,5000);
  }
  paymentTimer=setTimeout(poll,1500);
 }
 function seller(data){
  const area=panel('Продажа лицензий'),form=el('form'),enabled=input(form,'Продажа включена','checkbox',data.settings.enabled);submit(form,'Сохранить режим',async()=>{await api('/admin/license/settings',{method:'PUT',body:{enabled:enabled.checked}});await load(root);});area.append(form,button('Добавить тариф',()=>editor()));root.append(area);
  for(const p of data.plans){const row=el('div',undefined,'license-plan-admin'),text=el('div');text.append(el('strong',p.name),el('small',p.days+' дн. · '+p.price_xtr+' Stars · '+(p.active?'Доступен':'Скрыт'),'muted'));const edit=button('Изменить',()=>editor(p));window.pushokViews.iconAction(edit,'edit','Изменить тариф '+p.name);row.append(text,edit);area.append(row);}
  const setup=window.pushokViews.disclosure('Подключение установок',[]);setup.append(el('p','Публичный ключ проверки. Передаётся установщику покупателя; ключ подписи остаётся на вашем сервере.','muted'),el('code',data.public_key));root.append(setup);
  const sales=window.pushokViews.disclosure('Последние покупки',[]);if(!data.orders.length)sales.append(el('p','Покупок пока нет.','muted'));for(const o of data.orders){const r=el('article',undefined,'card');r.append(el('strong',o.terms.name),el('p',states[o.status]+' · '+o.amount+' Stars'+(o.test_mode?' · тест':'')),el('small','Установка '+o.installation_id.slice(0,12)+' · Telegram ID '+o.payer_id,'muted'));sales.append(r);}root.append(sales);
 }
 function client(data){
  const p=data.state.envelope?.payload,now=Math.floor(Date.now()/1000),current=p?.grants.find(g=>g.starts_at<=now&&g.ends_at>now),valid=current&&p.lease_until>now,area=panel('Лицензия этой установки');root.append(area);
  area.append(el('p',valid?current.terms.name:'Нужна покупка или продление'));
  if(valid){area.append(el('small','До '+date(new Date(current.ends_at*1000).toISOString()),'muted'));terms(area,current.terms,data.usage);}
  for(const g of p?.grants||[])if(g.starts_at>now)area.append(el('p',g.terms.name+' · с '+date(new Date(g.starts_at*1000).toISOString()),'muted'));
  area.append(el('small','Установка '+(data.state.installation_id||'').slice(0,12),'muted'),button('Проверить лицензию',check));
  if(data.error||data.state.last_error)area.append(el('p',errors[data.error||data.state.last_error]||'Проверка лицензии временно недоступна.','muted'));
  const grid=el('div',undefined,'license-plans');root.append(grid);
  for(const plan of data.plans){const card=panel(plan.name);card.append(el('p',plan.days+' дн.'));terms(card,plan);const actions=el('div',undefined,'license-plan-actions'),buy=button('Купить',async()=>{const order=await api('/admin/license/checkout',{method:'POST',body:{plan_id:plan.id,revision:plan.revision}});await openInvoice(order.invoice_url);await check();watchPayment(order.order_id);});buy.disabled=!data.available;actions.append(el('strong',plan.price_xtr+' Stars'),buy);card.append(actions);grid.append(card);}
  root.append(window.pushokViews.disclosure('Условия продления',[el('p','Лицензия действует на эту установку. Новая покупка добавляет период после текущего. Рекламодатели не покупают лицензию приложения. При истечении срока новые операции ограничиваются; история и выполнение оплаченных кампаний сохраняются.','muted')]));
  const history=window.pushokViews.disclosure('История покупок',[]);for(const o of p?.orders||[]){const row=el('article',undefined,'card');row.append(el('strong',o.terms.name),el('p',states[o.status]+' · '+o.amount+' Stars'+(o.test_mode?' · тест':''),'muted'));if(o.status==='pending'&&!o.precheckout_at){if(new Date(o.expires_at)>new Date())row.append(button('Открыть счёт',async()=>{await openInvoice(o.invoice_url);await check();watchPayment(o.id);}));row.append(button('Закрыть счёт',async()=>{await api('/admin/license/orders/'+o.id+'/close',{method:'POST',body:{}});await check();}));}history.append(row);}root.append(history);
 }
 async function load(target){root=target;const generation=++epoch,session=token;target.classList.add('licenses-page');target.replaceChildren(el('p','Загрузка лицензии…','muted'));try{const data=await api('/admin/license');if(generation!==epoch||session!==token)return;target.replaceChildren();if(data.mode==='seller')seller(data);else if(data.mode==='client')client(data);else target.append(el('p','Этот экземпляр работает без лицензирования. Режим продажи или покупки лицензии задаётся при настройке установки.','muted'));}catch(e){if(generation===epoch&&session===token){target.replaceChildren(el('p',e.message));target.append(button('Повторить',()=>load(target)));}}}
 return {load,reset(){clearTimeout(paymentTimer);epoch++;root?.replaceChildren();root=null;}};
})();
