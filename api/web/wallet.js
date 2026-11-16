'use strict';
window.pushokWallet=(()=>{
 let epoch=0,overviewEpoch=0,target=null,adminTelegram='',historyPage=0,adminPage=0,giftPage=0,settingsEpoch=0;
 const names={RUB:'Рубли',XTR:'Остаток Stars прежнего кошелька',BONUS:'Бонусные рубли'};
 const kinds={gift_credit:'Подарочный код',topup:'Пополнение',payment:'Оплата размещения',refund:'Возврат на баланс',manual_credit:'Ручное начисление',manual_debit:'Ручное списание',reversal:'Отмена корректировки',provider_refund:'Возврат провайдером',referral_reward:'Реферальная награда',referral_reversal:'Отмена реферальной награды'};
 const statuses={created:'Выпуск счёта не завершён',pending:'Ожидает оплаты',succeeded:'Подтверждено',failed:'Счёт закрыт',refunded:'Возвращено провайдером',review:'Требуется сверка'};
 Object.assign(errors,{wallet_stars_topup_retired:'Stars оплачиваются напрямую из Telegram. Новые пополнения внутреннего кошелька Stars закрыты.',growth_rub_only:'Промокоды и подарки действуют только для рублёвых заказов.',
  wallet_disabled:'Внутренний баланс выключен администратором. Остатки и история сохранены; можно оплатить заявку напрямую.',
  wallet_gift_unavailable:'Код недоступен: проверьте получателя, срок действия и лимит активаций.',wallet_gift_expired:'Укажите срок действия в будущем.',
  wallet_request_conflict:'Этот запрос уже использован с другими данными. Обновите журнал и проверьте операцию.',
  wallet_insufficient_funds:'Недостаточно средств. Пополните баланс или уменьшите сумму.',
  wallet_limit_exceeded:'Превышен предел остатка на балансе.',wallet_stars_only_in_telegram:'В Telegram размещение оплачивается напрямую Stars. Рублёвый баланс используется в веб-кабинете.',
  wallet_campaign_not_payable:'Заявка ещё не готова к оплате или уже имеет открытый счёт. Проверьте её статус.',
  wallet_price_changed:'Цена изменилась. Обновите карточку перед оплатой.',wallet_provider_refund_debt:'Есть задолженность после возврата платежа или отмены награды. Проверьте журнал и обратитесь в поддержку.',
  wallet_payment_refunded:'Эта оплата уже возвращена. Для нового размещения создайте заявку.',
  wallet_stop_placement_first:'Время размещения уже началось или есть наблюдения о показе. Сначала подтвердите его остановку; при задержке ответа агента дождитесь обновления статуса.',
  wallet_payment_not_found:'Заявка не оплачивалась с внутреннего баланса.',wallet_use_payment_refund:'Используйте возврат оплаты по заявке. Эта операция не является ручной корректировкой.',
  wallet_user_not_found:'Пользователь с таким Telegram ID ещё не входил в кабинет.',wallet_entry_not_found:'Операция не найдена.',
  wallet_topup_in_progress:'Уже есть незавершённое пополнение этой валюты. Откройте его ниже.',wallet_topup_not_found:'Пополнение не найдено.',
  wallet_provider_unavailable:'Ответ платёжной системы пока не получен. Запрос сохранён: повторите его, не создавая новый.',
  wallet_provider_mismatch:'Платёж требует сверки администратором. Не оплачивайте его повторно.',wallet_resume_topup:'Нажмите «Продолжить выпуск счёта».',
  wallet_topup_limit:'Превышен предел суммы пополнения.'
 });
 let expanded={};
 function amount(n,c){return c==='XTR'?new Intl.NumberFormat('ru-RU').format(n)+' Stars':money(n)+(c==='BONUS'?' бонусами':'');}
 function minor(value,c){const s=String(value).trim().replace(',','.');if(!(c==='XTR'?/^\d{1,8}$/:/^\d{1,8}(?:\.\d{1,2})?$/).test(s))throw Error('Введите положительную сумму: Stars целыми числами, рубли — до двух знаков после запятой.');const [a,b='']=s.split('.');const n=c==='XTR'?Number(a):Number(a)*100+Number(b.padEnd(2,'0'));if(!Number.isSafeInteger(n)||n<0||n>100000000)throw Error('Сумма слишком велика.');return n;}
 function value(n,c){return c==='XTR'?String(n):(n/100).toFixed(2);}
 function panel(title){const p=el('section',undefined,'panel');p.append(el('h2',title));return p;}
 function foldPanels(root,data){
  const folds={'Пополнить баланс':['topup','Пополнить','plus'],'Активировать подарочный код':['gift','Подарочный код','gift'],'Автоматическая оплата':['autopay','Автооплата · '+(data.preferences.auto_pay?'включена':'выключена'),'refresh'],'Мои Stars в Telegram':['telegram','Личные Stars в Telegram','star'],'Мои пополнения':['topups','Счета на пополнение','wallet'],'Управление балансами':['admin','Управление балансами','users'],'Сверка пополнений':['review','Сверка пополнений','check'],'Подарочные коды на баланс':['gifts','Выдать подарок','gift']};
  for(const p of [...root.children]){const h=p.querySelector(':scope > h2'),entry=folds[h?.textContent];if(!entry)continue;
   const [key,title,name]=entry,d=el('details',undefined,'disclosure-panel wallet-disclosure'),summary=el('summary',title);summary.prepend(window.pushokViews.icon(name));d.dataset.walletSection=key;
   d.open=Boolean(expanded[key]||(key==='topups'&&data.topups.some(t=>['created','pending','review'].includes(t.status))));
   if(key==='topup')d.classList.add('wallet-topup-disclosure');
   d.addEventListener('toggle',()=>{if(d.isConnected)expanded[key]=d.open;});p.before(d);h.remove();d.append(summary,p);
  }
 }

 function field(form,label,type,v=''){const box=el('label',label),input=el(type==='select'?'select':'input');if(type!=='select')input.type=type;input.value=v;input.setAttribute('aria-label',label);box.append(input);form.append(box);return input;}
 function opt(select,label,v){const o=el('option',label);o.value=v;select.append(o);}
 function currencies(form,label,starsOnly,bonus=false){const select=field(form,label,'select');(starsOnly?['XTR']:bonus?['RUB','BONUS']:['RUB']).forEach(c=>opt(select,names[c],c));return select;}
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
 function balances(root,data){
  const grid=el('div',undefined,'wallet-balances'),primary='RUB';
  for(const c of [primary,'BONUS',...(data.balances.XTR?['XTR']:[])]){
   const card=el('article',undefined,'card');card.dataset.currency=c;card.classList.toggle('wallet-primary',c===primary);
   const heading=el('span',names[c],'wallet-currency');heading.prepend(window.pushokViews.icon(c==='BONUS'?'gift':'wallet'));
   card.append(heading,el('strong',c==='BONUS'?money(data.balances[c]):amount(data.balances[c],c)));if(c===primary)card.append(el('small','Внутренний баланс Pushok','muted'));grid.append(card);
  }
  root.append(grid);if(data.has_debt)root.append(el('p','Есть задолженность после возврата платежа или отмены награды. Новые оплаты с баланса приостановлены до погашения. Бонусная задолженность погашается новыми бонусами; вопросы — в поддержку.','wallet-warning'));
 }
 // Остатки берём только из журнала Pushok. Запоздавший ответ другой сессии не рисуем.
 async function overview(node){
  const version=++overviewEpoch,session=token,uid=me?.id;
  const current=()=>version===overviewEpoch&&session===token&&uid===me?.id&&node.isConnected;
  let panel=node.querySelector('.wallet-overview');
  if(!panel){panel=el('section',undefined,'wallet-overview');panel.append(el('h2','Баланс Pushok'),el('strong','—','wallet-total'),el('dl',undefined,'wallet-secondary'));const a=el('a','','ui-button');a.href='#/wallet';window.pushokViews.iconAction(a,'chevron','Открыть баланс');panel.append(a);node.replaceChildren(panel);}
  panel.setAttribute('aria-busy','true');
  try{
   const data=await api('/wallet');if(!current())return;
   panel.querySelector('h2').textContent='Баланс Pushok';
   const currency='RUB',total=panel.querySelector('.wallet-total');total.dataset.currency=currency;
   const signature=JSON.stringify([data.balances,data.enabled,data.has_debt]);
   if(panel.dataset.snapshot!==signature){
    total.textContent=amount(data.balances[currency],currency);
    const other=panel.querySelector('.wallet-secondary');other.replaceChildren();for(const c of ['BONUS',...(data.balances.XTR?['XTR']:[])])other.append(el('dt',names[c]),el('dd',c==='BONUS'?money(data.balances[c]):amount(data.balances[c],c)));
    panel.querySelectorAll('.wallet-warning,.wallet-disabled').forEach(p=>p.remove());
    if(data.enabled===false)panel.append(el('p','Внутренний баланс выключен','muted wallet-disabled'));
    if(data.has_debt)panel.append(el('p','Проверьте задолженность в истории операций.','wallet-warning'));
    panel.dataset.snapshot=signature;
   }
   panel.removeAttribute('title');
  }catch(e){if(current()){panel.querySelector('h2').textContent=panel.dataset.snapshot?'Баланс · не обновлён':'Баланс · нет связи';panel.title='Не удалось обновить баланс. Повторите обновление страницы.';}}
  finally{if(current())panel.setAttribute('aria-busy','false');}
 }
 async function openInvoice(t){if(t.currency==='XTR'&&miniAppMode)await window.pushokMiniApp.payInvoice(t.invoice_url);else window.open(t.invoice_url,'_blank','noopener,noreferrer');}
 function topupForm(root,data){
  if(miniAppMode||data.stars_only)return;
  const p=panel('Пополнить баланс'),form=el('form');p.classList.add('wallet-topup');
  const a=field(form,'Сумма пополнения, ₽','text','');a.required=true;a.inputMode='decimal';a.placeholder='Например, 300';
  form.append(el('p','Через СБП: 1 ₽ = 1 ₽ на балансе.','muted'));
  const b=submit(form,'Пополнить через СБП',async()=>{await remembered('/wallet/topups',{currency:'RUB',amount:minor(a.value,'RUB')});notice('Счёт готов в списке пополнений.',true);});
  b.disabled=!data.sbp_available;if(!data.sbp_available)form.append(el('p','Пополнение через СБП пока не подключено.','muted'));p.append(form);root.append(p);
 }
 function topupList(root,data){
  const p=panel('Мои пополнения');p.classList.add('wallet-invoices');p.append(el('p','Последние 30 счетов. После оплаты обновите статус; сверка СБП также выполняется автоматически.','muted'));
  if(!data.topups.length)p.append(el('p','Пополнений пока нет.'));
  data.topups.forEach(t=>{const row=el('article',undefined,'card');row.append(el('h3',amount(t.amount,t.currency)),el('p',(statuses[t.status]||t.status)+(t.test_mode?' · ТЕСТОВЫЙ, без зачисления':'')),el('p',date(t.created_at)+' · #'+t.id.slice(0,8),'muted'));
   if(t.refunded_amount)row.append(el('p','Возвращено провайдером: '+amount(t.refunded_amount,t.currency)));
   if(t.review_reason)row.append(el('p','Сверка задерживается. Администратор может проверить этот счёт.','muted'));
   if(t.invoice_url&&!(t.currency==='RUB'&&(miniAppMode||data.stars_only)))row.append(button('Открыть счёт',()=>openInvoice(t)));
   if(data.enabled&&t.status==='created')row.append(button('Продолжить выпуск счёта',async()=>{await api('/wallet/topups/'+t.id+'/resume',{method:'POST'});await load(target);}));
   if(['pending','succeeded'].includes(t.status))row.append(button('Проверить пополнение',async()=>{await api('/wallet/topups/'+t.id+'/verify',{method:'POST'});await load(target);}));p.append(row);
  });root.append(p);
 }
 function autopay(root,data){
  if(miniAppMode||data.stars_only)return;
  const p=panel('Автоматическая оплата'),f=el('form'),prefs=data.preferences;p.classList.add('wallet-autopay');
  const enabled=field(f,'Автоматически оплачивать мои одобренные заявки','checkbox');enabled.checked=prefs.auto_pay;enabled.setAttribute('role','switch');enabled.disabled=!data.enabled;
  const c={value:'RUB'};
  const limit=field(f,'Максимум за одну заявку','text',value(prefs.per_campaign_limit,c.value));limit.inputMode='decimal';limit.required=true;
  const bonus=field(f,'Сначала расходовать бонусные рубли (для RUB)','checkbox');bonus.checked=prefs.use_bonus;
  f.append(el('p','Согласие действует для всех ваших одобренных заявок, в том числе созданных ранее. Списывается цена заявки после скидки в пределах указанного лимита. При недостатке средств заявка ждёт пополнения. Уже выставленный внешний счёт сначала нужно завершить. Проверка выполняется раз в минуту.','muted'));
  submit(f,'Сохранить автосписание',async()=>{await api('/wallet/preferences',{method:'PUT',body:{auto_pay:enabled.checked,currency:c.value,per_campaign_limit:limit.value?minor(limit.value,c.value):0,use_bonus:bonus.checked}});notice(enabled.checked?'Автосписание включено в указанных пределах.':'Автосписание выключено.',true);await load(target);});
  p.append(f);root.append(p);
 }
 function entries(root,data,admin=false,onPage){
  const p=panel(admin?'Журнал пользователя':'История операций');p.classList.add('wallet-journal');if(!data.entries.length)p.append(el('p','Операций пока нет.'));
  data.entries.forEach(e=>{const row=el('article',undefined,'card wallet-entry');row.dataset.direction=e.delta>0?'credit':'debit';
   row.append(el('h3',(e.delta>0?'+':'')+amount(e.delta,e.currency)),el('p',e.kind==='payment'&&e.automatic?'Автооплата размещения':kinds[e.kind]||e.kind));
   const more=window.pushokViews.disclosure(date(e.created_at),[el('p',e.reason),el('p','Остаток после операции: '+amount(e.balance_after,e.currency),'muted'),el('p','Проводка №'+e.entry_number,'muted')],'wallet-entry-details');row.append(more);
   if(e.actor_telegram_id)more.append(el('p','Автор: '+(e.actor_username?'@'+e.actor_username+' · ':'')+e.actor_telegram_id,'muted'));
   if(e.campaign_id&&(admin||!['referral_reward','referral_reversal'].includes(e.kind))){const a=el('a','Открыть заявку →');a.href='#/'+(admin?'moderation/':'create/')+e.campaign_id;more.append(a);}
   if(admin&&['manual_credit','manual_debit'].includes(e.kind)&&!e.reversed){const f=el('form'),reason=field(f,'Причина отмены корректировки','text');reason.required=true;reason.minLength=3;reason.maxLength=500;submit(f,'Отменить корректировку',async()=>{await api('/admin/wallet/entries/'+e.id+'/reverse',{method:'POST',body:{reason:reason.value}});await adminLookup();});row.append(f);}
   if(e.reversed)row.append(el('p',e.kind==='referral_reward'?'Награда отменена отдельной записью.':'Корректировка отменена отдельной записью.','muted'));p.append(row);
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
  const adjust=panel('Ручная операция'),f=el('form'),c=currencies(f,'Счёт','',true),direction=field(f,'Операция','select');opt(direction,'Начислить','credit');opt(direction,'Списать','debit');if(data.balances.XTR<0)opt(c,'Погасить прежнюю задолженность Stars','XTR');
  const a=field(f,'Сумма операции','text');a.required=true;a.inputMode='decimal';
  const reason=field(f,'Основание (видно пользователю)','text');reason.required=true;reason.minLength=3;reason.maxLength=500;
  f.append(el('p','Для подарка выберите «Бонусные рубли». Начисления RUB означают подтверждённые средства; укажите основание. Прежняя задолженность Stars корректируется только до нуля по подтверждённому основанию. Записи нельзя удалить: ошибку отменяйте отдельной корректировкой.','muted'));
  submit(f,'Провести операцию',async()=>{await remembered('/admin/wallet/users/'+data.user.telegram_user_id+'/adjust',{currency:c.value,direction:direction.value,amount:minor(a.value,c.value),reason:reason.value});notice('Операция сохранена в журнале.',true);});adjust.append(f);node.append(adjust);
  node.append(button('Скачать CSV (последние 10 000 операций)',async()=>{const response=await fetch('/api/v1/admin/wallet/users/'+data.user.telegram_user_id+'/export',{headers:{Authorization:'Bearer '+token}});if(!response.ok)throw Error('Не удалось получить журнал.');const url=URL.createObjectURL(await response.blob()),a=el('a');a.href=url;a.download='wallet-'+data.user.telegram_user_id+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
  entries(node,data,true,async page=>{adminPage=page;await adminLookup();});
  const payments=panel('Оплаты с баланса');data.payments.forEach(pay=>{const row=el('article',undefined,'card');row.append(el('h3','Заявка #'+pay.campaign_id.slice(0,8)),el('p',amount(pay.cash_amount,pay.currency)+(pay.bonus_amount?' + '+amount(pay.bonus_amount,'BONUS'):'')),el('p',pay.status==='refunded'?'Возвращено на баланс':pay.automatic?'Автосписание':'Оплачено пользователем'));
   if(pay.status==='paid'){const f=el('form'),reason=field(f,'Основание возврата','text');reason.required=true;reason.minLength=3;reason.maxLength=500;f.append(el('p','Вернётся та же сумма в тех же валютах. Запланированное размещение отменится. Активное сначала нужно остановить. Это возврат на внутренний баланс.','muted'));submit(f,'Вернуть на баланс',async()=>{await api('/admin/wallet/campaigns/'+pay.campaign_id+'/refund',{method:'POST',body:{reason:reason.value}});await adminLookup();});row.append(f);}payments.append(row);});node.append(payments);
 }
 async function load(node){
  if(!node||!me)return;node.querySelectorAll('[data-wallet-section]').forEach(d=>expanded[d.dataset.walletSection]=d.open);target=node;const version=++epoch,session=token,uid=me.id;node.replaceChildren(el('p','Загружаем баланс…'));adminTarget=null;adminSeq++;
  try{
   const [data,journal]=await Promise.all([api('/wallet'),api('/wallet/entries?page='+historyPage)]);if(version!==epoch||session!==token||uid!==me?.id)return;
   const root=el('div',undefined,'wallet-page');balances(root,data);
   const tools=el('nav',undefined,'wallet-tools');tools.setAttribute('aria-label','Действия баланса');tools.append(window.pushokViews.iconAction(button('',()=>load(node)),'refresh','Обновить баланс'));
   const payments=el('a','Мои оплаты','ui-button');payments.href='#/payments';tools.append(payments);root.append(tools);
   const request=pending();if(request){const p=panel('Незавершённая операция');p.append(el('p','Ответ не получен. Повтор отправит тот же запрос и не создаст вторую операцию.'),button('Повторить незавершённую операцию',()=>replay(request)));root.append(p);}
   if(data.enabled){topupForm(root,data);redeemGift(root);}else root.append(el('p','Внутренний баланс выключен. Остатки и история сохранены, новые пополнения и списания остановлены. Ранее выставленные счета продолжают проверяться. Заявки можно оплачивать напрямую.','wallet-warning'));
   topupList(root,data);entries(root,journal,false,async page=>{historyPage=page;await load(node);});autopay(root,data);telegramStars(root);
   if(me.role==='admin'){
    const admin=panel('Управление балансами'),f=el('form'),tg=field(f,'Telegram ID пользователя','text',adminTelegram);tg.required=true;tg.inputMode='numeric';tg.pattern='[1-9][0-9]{0,15}';
    submit(f,'Найти пользователя',async()=>{adminTelegram=tg.value;adminPage=0;await adminLookup();});admin.append(f);adminTarget=el('div');admin.append(adminTarget);root.append(admin);
    const review=panel('Сверка пополнений');review.append(button('Показать последние 30 пополнений',async()=>{const rows=await api('/admin/wallet/topups');if(session!==token||uid!==me?.id||version!==epoch)return;let list=review.querySelector('.wallet-review');if(list)list.remove();list=el('div',undefined,'wallet-review');rows.forEach(t=>{const r=el('article',undefined,'card');r.append(el('p','Telegram ID '+t.telegram_user_id+' · '+amount(t.amount,t.currency)),el('p',(statuses[t.status]||t.status)+(t.test_mode?' · Тест':'')),el('p','#'+t.id,'growth-code'));if(t.review_reason)r.append(el('p','Нужна сверка: '+t.review_reason));list.append(r);});const unmatched=await api('/admin/wallet/unmatched-stars');if(session!==token||uid!==me?.id||version!==epoch)return;
     unmatched.receipts.forEach(r=>{const card=el('article',undefined,'card');card.append(el('h3','Неразобранное поступление Stars'),el('p','Telegram ID '+r.payer_telegram_user_id+' · '+r.total_amount+' Stars'),el('p','Запись #'+r.id+' · '+r.reason_code),el('p','Сверьте поступление и плательщика в Telegram. Не начисляйте средства повторно без проверки.','muted'));list.append(card);});
     unmatched.refunds.forEach(r=>{const card=el('article',undefined,'card');card.append(el('h3','Неразобранный возврат Stars'),el('p','Telegram ID '+r.telegram_user_id+' · '+r.amount+' Stars'),el('p',r.charge_id,'growth-code'));list.append(card);});
     review.append(list);}));root.append(review);
   }
   if(me.role==='admin'){const gifts=await api('/admin/wallet/gifts?page='+giftPage);if(version!==epoch||session!==token||uid!==me?.id)return;adminGifts(root,gifts);}
   foldPanels(root,data);node.replaceChildren(root);if(adminTelegram&&adminTarget)await adminLookup();
  }catch(e){if(version===epoch&&session===token)node.replaceChildren(el('p',e.message),button('Повторить',()=>load(node)));}
 }
 function telegramStars(root){
  const p=panel('Мои Stars в Telegram');p.append(el('p','Это личный баланс вашего аккаунта Telegram. Бот не получает его сумму: здесь показаны только Stars, ранее оплаченные для puShok.','muted'));
  const link=el('a','Открыть мои Stars в Telegram ↗');link.href='tg://stars';p.append(link,el('p','Если ссылка не открылась: Telegram → Настройки → Мои звёзды. Кампанию также можно оплатить напрямую Stars, без предварительного пополнения puShok.','muted'));root.append(p);
 }
 function redeemGift(root){
  const p=panel('Активировать подарочный код'),f=el('form'),code=field(f,'Подарочный код на баланс','text');code.required=true;code.maxLength=64;code.placeholder='GIFT-…';code.autocomplete='off';
  p.append(el('p','Код начисляет бонусные рубли один раз на пользователя. Ими можно оплатить рублёвую заявку в веб-кабинете; в Mini App оплата проходит Stars. Обмена бонусов на Stars или деньги нет.','muted'));
  submit(f,'Получить подарок',async()=>{const result=await api('/wallet/gifts/redeem',{method:'POST',body:{code:code.value.trim().toUpperCase()}});notice(result.already_redeemed?'Этот подарок уже зачислен. Повторного начисления нет.':'Начислено '+amount(result.amount,'BONUS'),true);await load(target);});p.append(f);root.append(p);
 }
 function giftStatus(g){return 'До '+date(g.expires_at)+' · '+(!g.active?'Выключен':g.expired?'Срок истёк':g.used>=g.max_uses?'Лимит исчерпан':'Действует');}
 function adminGifts(root,data){
  const p=panel('Подарочные коды на баланс'),f=el('form');
  p.append(el('p','Создайте код и передайте получателю. Каждый пользователь активирует его один раз в пределах общего лимита. Подарок начисляется бонусными рублями; отключение кода останавливает новые активации и сохраняет уже выданные подарки.','muted'));
  const name=field(f,'Название подарка','text');name.required=true;name.minLength=3;name.maxLength=100;
  const a=field(f,'Подарок, бонусных ₽','text');a.required=true;a.inputMode='decimal';
  const limit=field(f,'Количество получателей','number','1');limit.required=true;limit.min='1';limit.max='10000';limit.step='1';
  const personal=field(f,'Только одному пользователю Telegram','checkbox');
  const recipient=field(f,'Telegram ID получателя подарка','text');recipient.inputMode='numeric';recipient.pattern='[1-9][0-9]{0,15}';recipient.maxLength=16;
  const recipientHint=el('p','Укажите числовой Telegram ID, а не @username. Получатель увидит свой ID в профиле кабинета. Код сможет активировать только этот аккаунт, даже если ссылку или код перешлют.','muted');f.append(recipientHint);
  function recipientMode(){recipient.required=personal.checked;recipient.disabled=!personal.checked;recipient.parentElement.hidden=!personal.checked;recipientHint.hidden=!personal.checked;limit.disabled=personal.checked;if(personal.checked)limit.value='1';}
  personal.addEventListener('change',recipientMode);recipientMode();
  const expires=field(f,'Действует до (ваше время)','datetime-local');expires.required=true;
  submit(f,'Создать подарочный код',async()=>{await remembered('/admin/wallet/gifts',{name:name.value,amount:minor(a.value,'BONUS'),max_uses:Number(limit.value),expires_at:new Date(expires.value).toISOString(),recipient_telegram_id:personal.checked?Number(recipient.value):null});notice('Код создан. Скопируйте его из списка ниже.',true);});p.append(f);
  if(!data.items.length)p.append(el('p','Подарочных кодов пока нет.'));
  for(const g of data.items){const row=el('article',undefined,'card wallet-gift');row.append(el('h3',g.name),el('p',g.code,'growth-code'),el('p',amount(g.amount,'BONUS')+' · активаций '+g.used+' из '+g.max_uses,'wallet-gift-summary'),el('p',giftStatus(g),'wallet-gift-status muted'));
   row.append(el('p',g.recipient_telegram_id?'Только Telegram ID '+g.recipient_telegram_id:'Любой получатель кода','muted'));
   const toggle=button(g.active?'Выключить код':'Включить код',async()=>{await api('/admin/wallet/gifts/'+g.id,{method:'PUT',body:{active:!g.active}});await load(target);});toggle.classList.add('wallet-gift-toggle');
   row.append(button('Скопировать код',async()=>{await navigator.clipboard.writeText(g.code);notice('Код скопирован.',true);}),toggle);giftReportControls(row,g);p.append(row);}
  const nav=el('div',undefined,'inline-actions');if(data.page>0)nav.append(button('Предыдущие коды',async()=>{giftPage--;await load(target);}));if((data.page+1)*data.page_size<data.total)nav.append(button('Следующие коды',async()=>{giftPage++;await load(target);}));p.append(nav);root.append(p);
 }
 function giftReportControls(row,g){
  const box=el('div',undefined,'wallet-gift-report');box.hidden=true;let seq=0;
  async function show(page=0){
   const current=++seq,session=token,uid=me?.id;box.hidden=false;box.replaceChildren(el('p','Загружаем активации…'));
   const valid=()=>current===seq&&session===token&&uid===me?.id&&box.isConnected&&!box.hidden;
   try{const data=await api('/admin/wallet/gifts/'+g.id+'/redemptions?page='+page);if(!valid())return;
    Object.assign(g,data.gift,{used:data.total});row.querySelector('.wallet-gift-summary').textContent=amount(g.amount,'BONUS')+' · активаций '+g.used+' из '+g.max_uses;row.querySelector('.wallet-gift-status').textContent=giftStatus(g);row.querySelector('.wallet-gift-toggle').textContent=g.active?'Выключить код':'Включить код';
    box.replaceChildren(el('h4','Активации подарка'),el('p','Получателей: '+data.total+' · начислено '+amount(data.credited,'BONUS')),
     el('p','Это начисления по коду. Последующие расходы и корректировки смотрите в журнале пользователя.','muted'));
    if(!data.items.length)box.append(el('p','Этот код ещё никто не активировал.'));
    for(const e of data.items){const item=el('article',undefined,'card');item.append(el('strong','Telegram ID '+e.telegram_user_id),el('p',(e.username?'@'+e.username+' · ':'')+(e.first_name||'Пользователь')),el('p','+'+amount(e.amount,'BONUS')),el('p',date(e.created_at)+' · проводка №'+e.entry_number,'muted'));box.append(item);}
    const nav=el('div',undefined,'inline-actions');if(data.page>0)nav.append(button('Предыдущие активации',()=>show(data.page-1)));if((data.page+1)*data.page_size<data.total)nav.append(button('Следующие активации',()=>show(data.page+1)));
    nav.append(button('Обновить активации',()=>show(data.page)),button('Скачать активации CSV',async()=>{
     const response=await fetch('/api/v1/admin/wallet/gifts/'+g.id+'/export',{headers:{Authorization:'Bearer '+session}});if(!response.ok)throw Error('Не удалось выгрузить активации.');
     const blob=await response.blob();if(!valid())return;const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download='gift-'+g.id+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }));box.append(nav);
   }catch(e){if(valid())box.replaceChildren(el('p',e.message),button('Повторить загрузку активаций',()=>show(page)));}
  }
  row.append(button('История активаций',()=>{if(box.hidden)return show();seq++;box.hidden=true;}),box);
 }
 async function walletSettings(){
  const node=$('wallet-settings'),session=token,uid=me?.id,seq=++settingsEpoch;if(!node||me?.role!=='admin')return;
  node.replaceChildren(el('p','Загружаем настройки баланса…'));
  try{const data=await api('/admin/wallet/settings');if(seq!==settingsEpoch||session!==token||uid!==me?.id)return;
   const p=panel('Внутренний баланс'),f=el('form'),enabled=field(f,'Включить внутренний баланс','checkbox');enabled.checked=data.enabled;
   const state=el('p',data.enabled?'Баланс включён':'Баланс выключен','wallet-settings-state');p.append(state);enabled.addEventListener('change',()=>{state.textContent=enabled.checked===data.enabled?(data.enabled?'Баланс включён':'Баланс выключен'):(enabled.checked?'Будет включён после сохранения':'Будет выключен после сохранения');});
   f.append(el('p','Выключение останавливает новые пополнения, оплату с баланса и активацию подарочных кодов. Остатки, история, возвраты и подтверждения уже выставленных счетов сохраняются. Автосписания выключатся у всех: после включения каждый пользователь разрешает их заново. Прямая оплата заявок продолжает работать.','muted'));
   submit(f,'Сохранить настройку баланса',async()=>{await api('/admin/wallet/settings',{method:'PUT',body:{enabled:enabled.checked}});notice('Настройка баланса сохранена.',true);await walletSettings();});p.append(f);node.replaceChildren(p);
  }catch(e){if(seq===settingsEpoch&&session===token)node.replaceChildren(el('p',e.message),button('Повторить настройку баланса',walletSettings));}
 }
 async function payment(c,container){
  const session=token,uid=me?.id;container.hidden=false;container.replaceChildren(el('p','Проверяем возможность оплаты с баланса…'));
  try{
   const data=await api('/wallet/campaigns/'+c.id);if(!container.isConnected||session!==token||uid!==me?.id)return;container.replaceChildren();
   if(data.payment){container.append(el('h3',data.payment.status==='refunded'?'Оплата возвращена на баланс':'Оплачено с баланса'),el('p',amount(data.payment.cash_amount,data.payment.currency)+(data.payment.bonus_amount?' + '+amount(data.payment.bonus_amount,'BONUS'):'')));return;}
   if(['verified','refunded'].includes(c.payment_status)){container.hidden=true;return;}
   if(!data.enabled){container.append(el('p','Внутренний баланс выключен администратором. Используйте прямую оплату заявки.','muted'));return;}
   if(!data.payable){container.append(el('p','Оплата с баланса станет доступна после одобрения заявки, если по ней нет открытого внешнего счёта.','muted'));return;}
   if((miniAppMode||data.stars_only)&&!data.balances.XTR){container.append(el('p','В Telegram — прямая оплата Stars. Рублёвый баланс используется в веб-кабинете.','muted'));return;}
   container.append(el('h3',(miniAppMode||data.stars_only)?'Использовать прежний остаток Stars':'Оплатить с баланса'));const form=el('form'),curr=currencies(form,'Списать со счёта',miniAppMode||data.stars_only),bonus=field(form,'Сначала использовать бонусные рубли','checkbox');bonus.checked=true;if(!miniAppMode&&!data.stars_only&&data.balances.XTR>0)opt(curr,names.XTR,'XTR');
   const total=el('p');form.append(total);const b=submit(form,'Оплатить с баланса',async()=>{const currency=curr.value;await api('/wallet/campaigns/'+c.id+'/pay',{method:'POST',body:{currency,use_bonus:bonus.checked,expected_amount:currency==='RUB'?data.amount_rub:data.amount_xtr}});notice('Размещение оплачено с баланса.',true);await refresh();});
   function state(){const currency=curr.value,price=currency==='RUB'?data.amount_rub:data.amount_xtr,available=data.balances[currency]+(currency==='RUB'&&bonus.checked?data.balances.BONUS:0);bonus.parentElement.hidden=currency!=='RUB';total.textContent=price?'К оплате '+amount(price,currency)+' · доступно '+amount(available,currency):'Цена Stars не настроена для этой заявки.';b.disabled=data.has_debt||!price||available<price;}
   curr.addEventListener('change',state);bonus.addEventListener('change',state);state();container.append(form);const link=el('a','Открыть баланс и пополнить →');link.href='#/wallet';container.append(link);
  }catch(e){if(container.isConnected&&session===token)container.replaceChildren(el('p',e.message));}
 }
 return {load,payment,minor,overview,settings:walletSettings,reset(){expanded={};epoch++;overviewEpoch++;settingsEpoch++;$('wallet-settings')?.replaceChildren();adminSeq++;if(target)target.replaceChildren();target=null;adminTarget=null;adminTelegram='';historyPage=0;adminPage=0;giftPage=0;}};
})();
