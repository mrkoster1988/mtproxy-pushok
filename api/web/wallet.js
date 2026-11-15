'use strict';
window.pushokWallet=(()=>{
 let epoch=0,target=null,adminTelegram='',historyPage=0,adminPage=0;
 const names={RUB:'Рубли',XTR:'Stars',BONUS:'Бонусные рубли'};
 const kinds={topup:'Пополнение',payment:'Оплата размещения',refund:'Возврат на баланс',manual_credit:'Ручное начисление',manual_debit:'Ручное списание',reversal:'Отмена корректировки',provider_refund:'Возврат провайдером'};
 const statuses={created:'Выпуск счёта не завершён',pending:'Ожидает оплаты',succeeded:'Подтверждено',failed:'Счёт закрыт',refunded:'Возвращено провайдером',review:'Требуется сверка'};
 Object.assign(errors,{
  wallet_request_conflict:'Этот запрос уже использован с другими данными. Обновите журнал и проверьте операцию.',
  wallet_insufficient_funds:'Недостаточно средств. Пополните баланс или уменьшите сумму.',
  wallet_limit_exceeded:'Превышен предел остатка на балансе.',wallet_stars_only_in_telegram:'В Telegram размещение оплачивается Stars. Выберите баланс Stars.',
  wallet_campaign_not_payable:'Заявка ещё не готова к оплате или уже имеет открытый счёт. Проверьте её статус.',
  wallet_price_changed:'Цена изменилась. Обновите карточку перед оплатой.',wallet_provider_refund_debt:'Есть задолженность после возврата провайдером. Пополните соответствующий баланс или обратитесь в поддержку.',
  wallet_payment_refunded:'Эта оплата уже возвращена. Для нового размещения создайте заявку.',
  wallet_stop_placement_first:'Сначала остановите активное размещение и дождитесь подтверждения агента.',
  wallet_payment_not_found:'Заявка не оплачивалась с внутреннего баланса.',wallet_use_payment_refund:'Используйте возврат оплаты по заявке. Эта операция не является ручной корректировкой.',
  wallet_user_not_found:'Пользователь с таким Telegram ID ещё не входил в кабинет.',wallet_entry_not_found:'Операция не найдена.',
  wallet_topup_in_progress:'Уже есть незавершённое пополнение этой валюты. Откройте его ниже.',wallet_topup_not_found:'Пополнение не найдено.',
  wallet_provider_unavailable:'Ответ платёжной системы пока не получен. Запрос сохранён: повторите его, не создавая новый.',
  wallet_provider_mismatch:'Платёж требует сверки администратором. Не оплачивайте его повторно.',wallet_resume_topup:'Нажмите «Продолжить выпуск счёта».',
  wallet_topup_limit:'Превышен предел суммы пополнения.'
 });
 function amount(n,c){return c==='XTR'?new Intl.NumberFormat('ru-RU').format(n)+' Stars':money(n)+(c==='BONUS'?' бонусами':'');}
 function minor(value,c){const s=String(value).trim().replace(',','.');if(!(c==='XTR'?/^\d{1,8}$/:/^\d{1,8}(?:\.\d{1,2})?$/).test(s))throw Error('Введите положительную сумму: Stars целыми числами, рубли — до двух знаков после запятой.');const [a,b='']=s.split('.');const n=c==='XTR'?Number(a):Number(a)*100+Number(b.padEnd(2,'0'));if(!Number.isSafeInteger(n)||n<0||n>100000000)throw Error('Сумма слишком велика.');return n;}
 function value(n,c){return c==='XTR'?String(n):(n/100).toFixed(2);}
 function panel(title){const p=el('section',undefined,'panel');p.append(el('h2',title));return p;}
 function field(form,label,type,v=''){const box=el('label',label),input=el(type==='select'?'select':'input');if(type!=='select')input.type=type;input.value=v;box.append(input);form.append(box);return input;}
 function opt(select,label,v){const o=el('option',label);o.value=v;select.append(o);}
 function currencies(form,label,starsOnly,bonus=false){const select=field(form,label,'select');(starsOnly?['XTR']:bonus?['RUB','XTR','BONUS']:['RUB','XTR']).forEach(c=>opt(select,names[c],c));return select;}
 function submit(form,label,fn){const b=el('button',label,'primary');b.type='submit';form.append(b);form.addEventListener('submit',e=>{e.preventDefault();void run(fn,b);});return b;}
 function key(){return 'pushok.wallet.pending.'+me.id;}
 function pending(){try{return JSON.parse(sessionStorage.getItem(key())||'null');}catch{return null;}}
 async function remembered(path,body){
  if(pending())throw Error('Сначала повторите незавершённую операцию в начале страницы.');
  const request={path,body:{...body,request_id:crypto.randomUUID()}};
  sessionStorage.setItem(key(),JSON.stringify(request));return replay(request);
 }
 async function replay(request){
  const session=token,storageKey=key();
  try{const result=await api(request.path,{method:'POST',body:request.body});sessionStorage.removeItem(storageKey);return result;}
  catch(e){if(e.status>=400&&e.status<500)sessionStorage.removeItem(storageKey);throw e;}
  finally{if(session===token&&target)await load(target);}
 }
 function balances(root,data){const grid=el('div',undefined,'wallet-balances');for(const c of ['RUB','XTR','BONUS']){const card=el('article',undefined,'card');card.append(el('span',names[c],'muted'),el('strong',amount(data.balances[c],c)));grid.append(card);}root.append(grid);if(data.has_debt)root.append(el('p','Есть задолженность после возврата платёжной системой. Новые оплаты с баланса приостановлены до погашения.','wallet-warning'));}
 async function openInvoice(t){if(t.currency==='XTR'&&miniAppMode)await window.pushokMiniApp.payInvoice(t.invoice_url);else window.open(t.invoice_url,'_blank','noopener,noreferrer');}
 function topupForm(root,data){
  const p=panel('Пополнить баланс'),form=el('form'),c=currencies(form,'Валюта пополнения',miniAppMode||data.stars_only);
  const a=field(form,'Сумма пополнения','text','');a.required=true;a.inputMode='decimal';a.placeholder='Например, 300';
  const hint=el('p',undefined,'muted');form.append(hint);
  const b=submit(form,'Создать счёт на пополнение',async()=>{const t=await remembered('/wallet/topups',{currency:c.value,amount:minor(a.value,c.value)});if(t.invoice_url){notice('Счёт готов. Откройте его в списке пополнений.',true);}});
  function state(){const available=c.value==='XTR'?data.stars_available:data.sbp_available;b.disabled=!available;hint.textContent=available?(c.value==='XTR'?'Оплата через Telegram Stars.':'Пополнение через СБП на странице платёжной системы.'):'Приём этой валюты пока выключен в настройках платежей.';}
  c.addEventListener('change',state);state();p.append(form,el('p','Баланс используется для размещений puShok. Рубли, Stars и бонусы учитываются раздельно и не конвертируются. Тестовые платежи не увеличивают рабочий баланс.','muted'));root.append(p);
 }
 function topupList(root,data){
  const p=panel('Мои пополнения');p.append(el('p','Последние 30 счетов. После оплаты обновите статус; сверка СБП также выполняется автоматически.','muted'));
  if(!data.topups.length)p.append(el('p','Пополнений пока нет.'));
  data.topups.forEach(t=>{const row=el('article',undefined,'card');row.append(el('h3',amount(t.amount,t.currency)),el('p',(statuses[t.status]||t.status)+(t.test_mode?' · ТЕСТОВЫЙ, без зачисления':'')),el('p',date(t.created_at)+' · #'+t.id.slice(0,8),'muted'));
   if(t.refunded_amount)row.append(el('p','Возвращено провайдером: '+amount(t.refunded_amount,t.currency)));
   if(t.review_reason)row.append(el('p','Сверка задерживается. Администратор может проверить этот счёт.','muted'));
   if(t.invoice_url&&!(t.currency==='RUB'&&(miniAppMode||data.stars_only)))row.append(button('Открыть счёт',()=>openInvoice(t)));
   if(t.status==='created')row.append(button('Продолжить выпуск счёта',async()=>{await api('/wallet/topups/'+t.id+'/resume',{method:'POST'});await load(target);}));
   if(['pending','succeeded'].includes(t.status))row.append(button('Проверить пополнение',async()=>{await api('/wallet/topups/'+t.id+'/verify',{method:'POST'});await load(target);}));p.append(row);
  });root.append(p);
 }
 function autopay(root,data){
  const p=panel('Автоматическая оплата'),f=el('form'),prefs=data.preferences;
  const enabled=field(f,'Автоматически оплачивать мои одобренные заявки','checkbox');enabled.checked=prefs.auto_pay;
  const c=currencies(f,'Валюта автосписания',miniAppMode||data.stars_only);c.value=[...c.options].some(o=>o.value===prefs.currency)?prefs.currency:'XTR';
  const limit=field(f,'Максимум за одну заявку','text',value(prefs.per_campaign_limit,c.value));limit.inputMode='decimal';limit.required=true;
  c.addEventListener('change',()=>{limit.value='';});
  const bonus=field(f,'Сначала расходовать бонусные рубли (для RUB)','checkbox');bonus.checked=prefs.use_bonus;
  f.append(el('p','Согласие действует для всех ваших одобренных заявок, в том числе созданных ранее. Списывается цена заявки после скидки в пределах указанного лимита. При недостатке средств заявка ждёт пополнения. Уже выставленный внешний счёт сначала нужно завершить. Проверка выполняется раз в минуту.','muted'));
  submit(f,'Сохранить автосписание',async()=>{await api('/wallet/preferences',{method:'PUT',body:{auto_pay:enabled.checked,currency:c.value,per_campaign_limit:limit.value?minor(limit.value,c.value):0,use_bonus:bonus.checked}});notice(enabled.checked?'Автосписание включено в указанных пределах.':'Автосписание выключено.',true);await load(target);});
  p.append(f);root.append(p);
 }
 function entries(root,data,admin=false,onPage){
  const p=panel(admin?'Журнал пользователя':'История операций');if(!data.entries.length)p.append(el('p','Операций пока нет.'));
  data.entries.forEach(e=>{const row=el('article',undefined,'card wallet-entry');row.append(el('h3',(e.delta>0?'+':'')+amount(e.delta,e.currency)),el('p',kinds[e.kind]||e.kind),el('p',e.reason),el('p','Остаток после операции: '+amount(e.balance_after,e.currency),'muted'),el('p',date(e.created_at)+' · #'+e.id.slice(0,8),'muted'));
   if(e.actor_telegram_id)row.append(el('p','Автор: '+(e.actor_username?'@'+e.actor_username+' · ':'')+e.actor_telegram_id,'muted'));
   if(e.campaign_id){const a=el('a','Открыть заявку →');a.href='#/'+(admin?'moderation/':'create/')+e.campaign_id;row.append(a);}
   if(admin&&['manual_credit','manual_debit'].includes(e.kind)&&!e.reversed){const f=el('form'),reason=field(f,'Причина отмены корректировки','text');reason.required=true;reason.minLength=3;reason.maxLength=500;submit(f,'Отменить корректировку',async()=>{await api('/admin/wallet/entries/'+e.id+'/reverse',{method:'POST',body:{reason:reason.value}});await adminLookup();});row.append(f);}
   if(e.reversed)row.append(el('p','Корректировка отменена отдельной записью.','muted'));p.append(row);
  });
  const nav=el('div',undefined,'inline-actions');if(data.page>0)nav.append(button('Предыдущие',()=>onPage(data.page-1)));if((data.page+1)*data.page_size<data.total)nav.append(button('Следующие',()=>onPage(data.page+1)));p.append(el('p','Операций: '+data.total,'muted'),nav);root.append(p);
 }
 let adminTarget=null,adminSeq=0;
 async function adminLookup(){
  if(!adminTarget||!adminTelegram)return;const node=adminTarget,seq=++adminSeq,session=token,uid=me.id;
  const data=await api('/admin/wallet/users/'+adminTelegram+'?page='+adminPage);
  if(seq!==adminSeq||session!==token||uid!==me?.id||node!==adminTarget)return;
  node.replaceChildren();const name=data.user.username?'@'+data.user.username:data.user.first_name||'Пользователь';node.append(el('h2',name+' · Telegram ID '+data.user.telegram_user_id));balances(node,data);
  node.append(el('p',data.reconciled?'Сверка: остатки совпадают с журналом.':'Ошибка сверки остатков: проверьте данные перед новыми операциями.',data.reconciled?'muted':'wallet-warning'));
  const totals=el('div');data.totals.forEach(r=>totals.append(el('p',names[r.currency]+': начислено '+amount(r.credited,r.currency)+', списано '+amount(r.debited,r.currency))));node.append(totals);
  const adjust=panel('Ручная операция'),f=el('form'),c=currencies(f,'Счёт','',true),direction=field(f,'Операция','select');opt(direction,'Начислить','credit');opt(direction,'Списать','debit');
  const a=field(f,'Сумма операции','text');a.required=true;a.inputMode='decimal';
  const reason=field(f,'Основание (видно пользователю)','text');reason.required=true;reason.minLength=3;reason.maxLength=500;
  f.append(el('p','Для подарка выберите «Бонусные рубли». Начисления RUB и Stars означают подтверждённые средства; укажите основание. Записи нельзя удалить: ошибку отменяйте отдельной корректировкой.','muted'));
  submit(f,'Провести операцию',async()=>{await remembered('/admin/wallet/users/'+data.user.telegram_user_id+'/adjust',{currency:c.value,direction:direction.value,amount:minor(a.value,c.value),reason:reason.value});notice('Операция сохранена в журнале.',true);});adjust.append(f);node.append(adjust);
  node.append(button('Скачать CSV (последние 10 000 операций)',async()=>{const response=await fetch('/api/v1/admin/wallet/users/'+data.user.telegram_user_id+'/export',{headers:{Authorization:'Bearer '+token}});if(!response.ok)throw Error('Не удалось получить журнал.');const url=URL.createObjectURL(await response.blob()),a=el('a');a.href=url;a.download='wallet-'+data.user.telegram_user_id+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
  entries(node,data,true,async page=>{adminPage=page;await adminLookup();});
  const payments=panel('Оплаты с баланса');data.payments.forEach(pay=>{const row=el('article',undefined,'card');row.append(el('h3','Заявка #'+pay.campaign_id.slice(0,8)),el('p',amount(pay.cash_amount,pay.currency)+(pay.bonus_amount?' + '+amount(pay.bonus_amount,'BONUS'):'')),el('p',pay.status==='refunded'?'Возвращено на баланс':pay.automatic?'Автосписание':'Оплачено пользователем'));
   if(pay.status==='paid'){const f=el('form'),reason=field(f,'Основание возврата','text');reason.required=true;reason.minLength=3;reason.maxLength=500;f.append(el('p','Вернётся та же сумма в тех же валютах. Запланированное размещение отменится. Активное сначала нужно остановить. Это возврат на внутренний баланс.','muted'));submit(f,'Вернуть на баланс',async()=>{await api('/admin/wallet/campaigns/'+pay.campaign_id+'/refund',{method:'POST',body:{reason:reason.value}});await adminLookup();});row.append(f);}payments.append(row);});node.append(payments);
 }
 async function load(node){
  if(!node||!me)return;target=node;const version=++epoch,session=token,uid=me.id;node.replaceChildren(el('p','Загружаем баланс…'));adminTarget=null;adminSeq++;
  try{
   const [data,journal]=await Promise.all([api('/wallet'),api('/wallet/entries?page='+historyPage)]);if(version!==epoch||session!==token||uid!==me?.id)return;
   const root=el('div',undefined,'wallet-page');balances(root,data);root.append(button('Обновить баланс',()=>load(node)));
   const request=pending();if(request){const p=panel('Незавершённая операция');p.append(el('p','Ответ не получен. Повтор отправит тот же запрос и не создаст вторую операцию.'),button('Повторить незавершённую операцию',()=>replay(request)));root.append(p);}
   topupForm(root,data);autopay(root,data);topupList(root,data);entries(root,journal,false,async page=>{historyPage=page;await load(node);});
   if(me.role==='admin'){
    const admin=panel('Управление балансами'),f=el('form'),tg=field(f,'Telegram ID пользователя','text',adminTelegram);tg.required=true;tg.inputMode='numeric';tg.pattern='[1-9][0-9]{0,15}';
    submit(f,'Найти пользователя',async()=>{adminTelegram=tg.value;adminPage=0;await adminLookup();});admin.append(f);adminTarget=el('div');admin.append(adminTarget);root.append(admin);
    const review=panel('Сверка пополнений');review.append(button('Показать последние 30 пополнений',async()=>{const rows=await api('/admin/wallet/topups');if(session!==token||uid!==me?.id||version!==epoch)return;let list=review.querySelector('.wallet-review');if(list)list.remove();list=el('div',undefined,'wallet-review');rows.forEach(t=>{const r=el('article',undefined,'card');r.append(el('p','Telegram ID '+t.telegram_user_id+' · '+amount(t.amount,t.currency)),el('p',(statuses[t.status]||t.status)+(t.test_mode?' · Тест':'')),el('p','#'+t.id,'growth-code'));if(t.review_reason)r.append(el('p','Нужна сверка: '+t.review_reason));list.append(r);});review.append(list);}));root.append(review);
   }
   node.replaceChildren(root);if(adminTelegram&&adminTarget)await adminLookup();
  }catch(e){if(version===epoch&&session===token)node.replaceChildren(el('p',e.message),button('Повторить',()=>load(node)));}
 }
 async function payment(c,container){
  const session=token,uid=me?.id;container.replaceChildren(el('p','Проверяем возможность оплаты с баланса…'));
  try{
   const data=await api('/wallet/campaigns/'+c.id);if(!container.isConnected||session!==token||uid!==me?.id)return;container.replaceChildren();
   if(data.payment){container.append(el('h3',data.payment.status==='refunded'?'Оплата возвращена на баланс':'Оплачено с баланса'),el('p',amount(data.payment.cash_amount,data.payment.currency)+(data.payment.bonus_amount?' + '+amount(data.payment.bonus_amount,'BONUS'):'')));return;}
   if(!data.payable){container.append(el('p','Оплата с баланса станет доступна после одобрения заявки, если по ней нет открытого внешнего счёта.','muted'));return;}
   container.append(el('h3','Оплатить с баланса'));const form=el('form'),curr=currencies(form,'Списать со счёта',miniAppMode||data.stars_only),bonus=field(form,'Сначала использовать бонусные рубли','checkbox');bonus.checked=true;
   const total=el('p');form.append(total);const b=submit(form,'Оплатить с баланса',async()=>{const currency=curr.value;await api('/wallet/campaigns/'+c.id+'/pay',{method:'POST',body:{currency,use_bonus:bonus.checked,expected_amount:currency==='RUB'?data.amount_rub:data.amount_xtr}});notice('Размещение оплачено с баланса.',true);await refresh();});
   function state(){const currency=curr.value,price=currency==='RUB'?data.amount_rub:data.amount_xtr,available=data.balances[currency]+(currency==='RUB'&&bonus.checked?data.balances.BONUS:0);bonus.parentElement.hidden=currency!=='RUB';total.textContent=price?'К оплате '+amount(price,currency)+' · доступно '+amount(available,currency):'Цена Stars не настроена для этой заявки.';b.disabled=data.has_debt||!price||available<price;}
   curr.addEventListener('change',state);bonus.addEventListener('change',state);state();container.append(form);const link=el('a','Открыть баланс и пополнить →');link.href='#/wallet';container.append(link);
  }catch(e){if(container.isConnected&&session===token)container.replaceChildren(el('p',e.message));}
 }
 return {load,payment,minor,reset(){epoch++;adminSeq++;if(target)target.replaceChildren();target=null;adminTarget=null;adminTelegram='';historyPage=0;adminPage=0;}};
})();
