'use strict';
window.pushokLicenses=(()=>{
 let epoch=0,target=null;
 const limits=[['max_channels','Каналы'],['max_open_campaigns','Незавершённые кампании'],['max_campaigns_per_period','Новые кампании за срок лицензии']];
 Object.assign(errors,{license_required:'Нужна действующая лицензия. Откройте Профиль → Лицензия.',license_channels_limit:'Достигнут лимит каналов лицензии. Архивируйте ненужный канал. Новые лимиты продления начнут действовать в следующем периоде.',license_open_campaigns_limit:'Достигнут лимит незавершённых кампаний. Дождитесь завершения или закройте ненужные заявки.',license_period_limit:'Использован лимит новых кампаний за срок лицензии.',license_plan_required:'Сначала создайте и включите хотя бы один тариф лицензии.',license_disabled:'Лицензирование сейчас отключено.',license_plan_changed:'Условия тарифа изменились. Обновите список перед покупкой.',license_plan_not_found:'Тариф лицензии недоступен.',license_free_used:'Бесплатный период этого тарифа уже использован.',license_pending_invoice:'У вас есть открытый счёт на другой тариф. Завершите или закройте его в истории ниже.',license_order_not_found:'Счёт не найден.',license_invoice_expired:'Срок оплаты счёта истёк. Закройте его и выберите тариф заново.'});
 const states={pending:'Ожидает оплаты',paid:'Оплачено',closed:'Закрыт',refunded:'Возвращено'};
 function panel(title){const p=el('section',undefined,'panel');p.append(el('h2',title));return p;}
 function field(form,title,type,v=''){const label=el('label',title),i=el('input');i.type=type;i.setAttribute('aria-label',title);if(type==='checkbox')i.checked=Boolean(v);else i.value=v??'';label.append(i);form.append(label);return i;}
 function submit(form,title,fn){const b=el('button',title,'primary');b.type='submit';form.append(b);form.addEventListener('submit',e=>{e.preventDefault();void run(fn,b);});return b;}
 function terms(root,p){const dl=el('dl',undefined,'campaign-facts');dl.append(el('dt','Срок'),el('dd',p.days+' дн.'));for(const [key,label] of limits)dl.append(el('dt',label),el('dd',p[key]===null?'Без лимита':String(p[key])));root.append(dl);}
 async function buy(p){const result=await api('/licenses/checkout',{method:'POST',body:{plan_id:p.id,revision:p.revision}});if(result.invoice_url){if(miniAppMode)await window.pushokMiniApp.payInvoice(result.invoice_url);else window.open(result.invoice_url,'_blank','noopener,noreferrer');}else notice(result.test_mode?'Тестовый период обработан. Рабочая лицензия не активирована.':'Лицензия оформлена.',true);if(target)await load(target);}
 function renderPlan(root,p,available,used){const card=panel(p.name);terms(card,p);const alreadyUsed=!p.price_xtr&&used.includes(p.id),action=button(alreadyUsed?'Уже использован':p.price_xtr?'Выбрать':'Получить период',()=>buy(p));action.disabled=alreadyUsed||Boolean(p.price_xtr&&!available);const actions=el('div',undefined,'license-plan-actions');actions.append(el('strong',p.price_xtr?p.price_xtr+' Stars':'Бесплатно'),action);card.append(actions);if(p.price_xtr&&!available)card.append(el('small','Оплата Stars временно недоступна.','muted'));root.append(card);}
 function editor(root,p={name:'',days:30,price_xtr:0,max_channels:null,max_open_campaigns:null,max_campaigns_per_period:null,active:false}){
  const section=panel(p.id?'Изменить тариф':'Новый тариф'),f=el('form');section.append(f);const name=field(f,'Название','text',p.name),days=field(f,'Срок, дней','number',p.days),price=field(f,'Цена, Stars','number',p.price_xtr);
  name.required=true;name.maxLength=80;days.required=true;days.min=1;days.max=3650;days.step=1;price.required=true;price.min=0;price.max=1000000;price.step=1;
  const fields={};for(const [key,label] of limits){fields[key]=field(f,label,'number',p[key]);fields[key].min=0;fields[key].max=1000000;fields[key].step=1;fields[key].placeholder='Без лимита';}
  f.append(el('small','Пустое поле — без лимита; 0 — действие недоступно.','muted'));const active=field(f,'Тариф доступен для покупки','checkbox',p.active);
  submit(f,'Сохранить',async()=>{const body={name:name.value.trim(),days:Number(days.value),price_xtr:Number(price.value),active:active.checked};for(const [key] of limits)body[key]=fields[key].value===''?null:Number(fields[key].value);await api('/admin/licenses/plans'+(p.id?'/'+p.id:''),{method:p.id?'PUT':'POST',body});await load(root);notice('Тариф сохранён. Уже купленные условия не изменились.',true);});
  const cancel=button('Отмена',()=>section.remove());f.append(cancel);root.prepend(section);name.focus();
 }
 async function admin(root,version,session){
  const data=await api('/admin/licenses');if(version!==epoch||session!==token)return;
  const area=panel('Управление лицензиями'),form=el('form'),enabled=field(form,'Лицензия нужна для новых каналов и кампаний','checkbox',data.settings.enabled);form.append(el('p','Оплаченные размещения продолжаются. История и продление доступны после истечения лицензии. Администраторы и операторы работают без ограничений.','muted'));
  submit(form,'Сохранить режим',async()=>{await api('/admin/licenses/settings',{method:'PUT',body:{enabled:enabled.checked}});await load(root);notice('Режим лицензирования сохранён.',true);});area.append(form);
  area.append(button('Добавить тариф',()=>editor(root)));
  for(const p of data.plans){const r=el('div',undefined,'license-plan-admin'),text=el('div');text.append(el('strong',p.name),el('small',p.days+' дн. · '+p.price_xtr+' Stars · '+(p.active?'Доступен':'Скрыт'),'muted'));const edit=button('Изменить',()=>editor(root,p));window.pushokViews.iconAction(edit,'edit','Изменить тариф '+p.name);r.append(text,edit);area.append(r);}
  area.append(el('p','Изменения относятся к следующим покупкам. Бесплатный тариф доступен каждому пользователю один раз. Продление начинается после текущего оплаченного периода.','muted'));root.append(area);
 }
 async function load(root){
  target=root;const version=++epoch,session=token;root.classList.add('licenses-page');root.replaceChildren(el('p','Загрузка лицензии…','muted'));
  try{
   const data=await api('/licenses');if(version!==epoch||session!==token)return;root.replaceChildren();
   const current=panel('Моя лицензия'),now=Date.now(),g=data.grants.find(g=>new Date(g.starts_at)<=now);root.append(current);
   if(!data.enabled)current.append(el('p','Лицензирование отключено. Кабинет работает без ограничений лицензии.'));
   else if(data.staff_exempt)current.append(el('p','Доступ администратора или оператора — без ограничений лицензии.'));
   else if(!g)current.append(el('p','Нет действующей лицензии. Выберите тариф для новых каналов и кампаний.'));
   if(g){current.append(el('h3',g.terms.name),el('p','До '+date(g.ends_at),'muted'));const usage=el('dl',undefined,'campaign-facts');for(const [key,label] of limits)usage.append(el('dt',label+' · использовано'),el('dd',String(data.usage[key.slice(4)])+' / '+(g.terms[key]===null?'∞':g.terms[key])));current.append(usage);}
   for(const future of data.grants.filter(g=>new Date(g.starts_at)>now))current.append(el('p',future.terms.name+' · с '+date(future.starts_at)+' до '+date(future.ends_at),'muted'));
   current.append(button('Обновить статус',()=>load(root)));
   if(data.enabled){root.append(el('p','Лицензия открывает возможности кабинета. Размещения оплачиваются отдельно. Продление добавляется после текущего срока.','muted'));const grid=el('div',undefined,'license-plans');data.plans.forEach(p=>renderPlan(grid,p,data.stars_available,data.used_free_plan_ids));root.append(grid);}
   if(data.orders.length){const history=window.pushokViews.disclosure('История покупок',[]);for(const o of data.orders){const row=el('article',undefined,'card');row.append(el('strong',o.terms.name),el('p',states[o.status]+(o.test_mode?' · тест':'')+' · '+o.amount+' Stars','muted'));if(o.status==='pending')row.append(button('Открыть счёт',async()=>{const r=await api('/licenses/orders/'+o.id+'/resume',{method:'POST',body:{}});if(miniAppMode)await window.pushokMiniApp.payInvoice(r.invoice_url);else window.open(r.invoice_url,'_blank','noopener,noreferrer');await load(root);}),button('Закрыть счёт',async()=>{await api('/licenses/orders/'+o.id+'/close',{method:'POST',body:{}});await load(root);}));history.append(row);}root.append(history);}
   if(me.role==='admin')await admin(root,version,session);
  }catch(e){if(version===epoch&&session===token){root.replaceChildren(el('p',e.message));root.append(button('Повторить',()=>load(root)));}}
 }
 return {load,reset(){epoch++;target?.replaceChildren();target=null;}};
})();
