'use strict';
window.pushokWallet=(()=>{
 let epoch=0,overviewEpoch=0,target=null,adminTelegram='',historyPage=0,adminPage=0,giftPage=0,giftDeleted=false,giftArchived=false,settingsEpoch=0;
 const names={RUB:'Рубли',XTR:'Остаток Stars прежнего кошелька',BONUS:'Бонусные рубли'};
 const kinds={gift_credit:'Подарочный код',topup:'Пополнение',payment:'Оплата размещения',refund:'Возврат на баланс',manual_credit:'Ручное начисление',manual_debit:'Ручное списание',reversal:'Отмена корректировки',provider_refund:'Возврат провайдером',referral_reward:'Реферальная награда',referral_reversal:'Отмена реферальной награды'};
 const statuses={created:'Выпуск счёта не завершён',pending:'Ожидает оплаты',succeeded:'Подтверждено',failed:'Счёт закрыт',refunded:'Возвращено провайдером',review:'Требуется сверка'};
 Object.assign(errors,{wallet_bonus_disabled:'Оплата бонусами выключена. Обновите расчёт, чтобы оплатить рублями.',wallet_topup_not_closed:'Счёт ещё ожидает оплаты. Обновите его статус перед удалением.',wallet_stars_topup_retired:'Stars оплачиваются напрямую из Telegram. Новые пополнения внутреннего кошелька Stars закрыты.',
  gift_share_bot_unavailable:'Укажите имя Telegram-бота в настройках, чтобы отправить сертификат.',gift_share_rate_limit:'Слишком много запросов. Повторите через минуту.',
  wallet_disabled:'Внутренний баланс выключен администратором. Остатки и история сохранены; можно оплатить заявку напрямую.',
  wallet_gift_terms_used:'После активации нельзя менять сумму или получателя. Создайте отдельный подарок.',wallet_gift_limit_used:'Лимит не может быть меньше числа активаций.',
  wallet_gift_not_found:'Подарочный код не найден. Попросите отправителя поделиться ссылкой из карточки нужного подарка.',
  wallet_gift_wrong_recipient:'Этот подарок предназначен другому аккаунту Telegram. Откройте ссылку из аккаунта получателя.',
  wallet_gift_deleted:'Отправитель удалил этот подарок. Новая активация недоступна.',
  wallet_gift_disabled:'Отправитель выключил этот подарок. Для активации его нужно включить.',
  wallet_gift_expired_code:'Срок действия подарка закончился. Отправитель может продлить его.',
  wallet_gift_limit_reached:'Этот подарок уже получили: лимит активаций исчерпан.',
  wallet_gift_unavailable:'Код недоступен: проверьте получателя, срок действия и лимит активаций.',wallet_gift_expired:'Укажите срок действия в будущем.',
  wallet_request_conflict:'Этот запрос уже использован с другими данными. Обновите журнал и проверьте операцию.',
  wallet_insufficient_funds:'Недостаточно средств. Пополните баланс или уменьшите сумму.',
  wallet_limit_exceeded:'Превышен предел остатка на балансе.',wallet_stars_only_in_telegram:'В Telegram выберите Stars или оплатите весь тариф подарочными бонусами. Для оплаты с рублёвого баланса откройте веб-кабинет.',
  wallet_campaign_not_payable:'Заявка ещё не готова к оплате или уже имеет открытый счёт. Проверьте её статус.',
  wallet_price_changed:'Цена изменилась. Обновите карточку перед оплатой.',wallet_provider_refund_debt:'Есть задолженность после возврата платежа или отмены награды. Проверьте журнал и обратитесь в поддержку.',
  wallet_payment_refunded:'Эта оплата уже возвращена. Для нового размещения создайте заявку.',
  wallet_stop_placement_first:'Время размещения уже началось или есть наблюдения о показе. Сначала подтвердите его остановку; при задержке ответа агента дождитесь обновления статуса.',
  wallet_payment_not_found:'Заявка не оплачивалась с внутреннего баланса.',wallet_use_payment_refund:'Используйте возврат оплаты по заявке. Эта операция не является ручной корректировкой.',
  wallet_user_not_found:'Пользователь не найден в базе puShok. Проверьте Telegram ID или попросите получателя открыть кабинет. Подарочный код можно выдать и до первого входа.',wallet_entry_not_found:'Операция не найдена.',
  wallet_topup_in_progress:'Уже есть незавершённое пополнение этой валюты. Откройте его ниже.',wallet_topup_not_found:'Пополнение не найдено.',
  wallet_provider_unavailable:'Ответ платёжной системы пока не получен. Запрос сохранён: повторите его, не создавая новый.',
  wallet_provider_mismatch:'Платёж требует сверки администратором. Не оплачивайте его повторно.',wallet_resume_topup:'Нажмите «Продолжить выпуск счёта».',
  wallet_topup_limit:'Превышен предел суммы пополнения.'
 });
 let expanded={},deletedTopups=false;
 function amount(n,c){return c==='XTR'?new Intl.NumberFormat('ru-RU').format(n)+' Stars':money(n)+(c==='BONUS'?' бонусами':'');}
 function minor(value,c){const s=String(value).trim().replace(',','.');if(!(c==='XTR'?/^\d{1,8}$/:/^\d{1,8}(?:\.\d{1,2})?$/).test(s))throw Error('Введите положительную сумму: Stars целыми числами, рубли — до двух знаков после запятой.');const [a,b='']=s.split('.');const n=c==='XTR'?Number(a):Number(a)*100+Number(b.padEnd(2,'0'));if(!Number.isSafeInteger(n)||n<0||n>100000000)throw Error('Сумма слишком велика.');return n;}
 function value(n,c){return c==='XTR'?String(n):(n/100).toFixed(2);}
 function panel(title){const p=el('section',undefined,'panel');p.append(el('h2',title));return p;}
 function foldPanels(root,data){
  const folds={'История операций':['history','История операций','chart'],'Пополнить баланс':['topup','Пополнить','plus'],'Активировать подарочный код':['gift','Подарочный код','gift'],'Автоматическая оплата':['autopay','Автооплата · '+(data.preferences.auto_pay?'включена':'выключена'),'refresh'],'Мои Stars в Telegram':['telegram','Личные Stars в Telegram','star'],'Мои пополнения':['topups','Счета на пополнение','wallet'],'Управление балансами':['admin','Управление балансами','users'],'Сверка пополнений':['review','Сверка пополнений','check'],'Подарочные коды на баланс':['gifts','Выдать подарок','gift']};
  for(const p of [...root.children]){const h=p.querySelector(':scope > h2'),entry=folds[h?.textContent];if(!entry)continue;
   const [key,title,name]=entry,d=el('details',undefined,'disclosure-panel wallet-disclosure'),summary=el('summary',title);summary.prepend(window.pushokViews.icon(name));d.dataset.walletSection=key;
   d.open=Boolean(expanded[key]||(key==='topups'&&data.topups.some(t=>['created','pending','review'].includes(t.status))));
   if(key==='topup')d.classList.add('wallet-topup-disclosure');
   d.addEventListener('toggle',()=>{if(d.isConnected)expanded[key]=d.open;if(d.open&&p.loadReview&&!p.reviewLoaded){p.reviewLoaded=true;void p.loadReview().catch(()=>{p.reviewLoaded=false;p.replaceChildren(el('p','Не удалось загрузить платежи.'),button('Повторить',()=>p.loadReview()));});}});p.before(d);h.remove();d.append(summary,p);
  }
  for(const [title,keys] of [['Мой кошелёк',['topup','topups','autopay','telegram','history']],['Подарки',['gift']],['Администрирование',['admin','review','gifts']]]){
   const sections=keys.map(key=>root.querySelector('[data-wallet-section="'+key+'"]')).filter(Boolean);
   if(!sections.length)continue;
   const group=el('section',undefined,'wallet-group');group.append(el('h2',title),...sections);root.append(group);
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
 function balances(root,data,personal=false){
  const grid=el('div',undefined,'wallet-balances'),primary='RUB';
  for(const c of [primary,'BONUS',...(data.balances.XTR?['XTR']:[])]){
   const card=el('article',undefined,'card');card.dataset.currency=c;card.classList.toggle('wallet-primary',c===primary);
   const heading=el('span',c==='RUB'?'Баланс':c==='BONUS'?'Бонусы':'Прежние Stars','wallet-currency');heading.prepend(window.pushokViews.icon(c==='BONUS'?'gift':'wallet'));
   card.append(heading,el('strong',c==='BONUS'?money(data.balances[c]):amount(data.balances[c],c)));
   if(personal&&c==='BONUS'&&data.enabled&&data.bonus_spending_enabled&&!data.has_debt&&data.balances.BONUS>0){
    const spend=el('a','Использовать бонусы','wallet-bonus-shortcut');spend.href='#/payments';card.append(spend);
   }
   grid.append(card);
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
  const p=panel('Мои пополнения');p.classList.add('wallet-invoices');p.append(button(deletedTopups?'Текущие счета':'Удалённые счета',()=>{deletedTopups=!deletedTopups;return load(target);}));
  if(!data.topups.length)p.append(el('p',deletedTopups?'Удалённых счетов нет.':'Счетов пока нет.','empty'));
  data.topups.forEach(t=>{const row=el('article',undefined,'card wallet-invoice'),head=el('div',undefined,'panel-heading');head.append(el('h3',amount(t.amount,t.currency)));
   if(t.hidden_at)head.append(window.pushokViews.iconAction(button('',async()=>{await api('/wallet/topups/'+t.id+'/restore',{method:'POST'});await load(target);}),'refresh','Восстановить счёт'));
   else if(['failed','succeeded','refunded'].includes(t.status))head.append(window.pushokViews.iconAction(button('',async()=>{if(!confirm('Удалить счёт из списка? Записи об оплате, история и возвраты сохранятся.'))return;await api('/wallet/topups/'+t.id,{method:'DELETE'});await load(target);}),'close','Удалить счёт'));
   head.querySelector('[aria-label="Удалить счёт"]')?.classList.add('danger-text');
   row.append(head,el('p',(statuses[t.status]||t.status)+(t.test_mode?' · ТЕСТОВЫЙ, без зачисления':'')),el('p',date(t.created_at)+' · #'+t.id.slice(0,8),'muted'));
   if(t.refunded_amount)row.append(el('p','Возвращено провайдером: '+amount(t.refunded_amount,t.currency)));
   if(t.review_reason)row.append(el('p','Сверка задерживается. Администратор может проверить этот счёт.','muted'));
   if(t.invoice_url&&!t.hidden_at&&!(t.currency==='RUB'&&(miniAppMode||data.stars_only)))row.append(button('Открыть счёт',()=>openInvoice(t)));
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
  const bonus=field(f,'Сначала расходовать бонусные рубли (для RUB)','checkbox');bonus.checked=prefs.use_bonus;bonus.disabled=!data.bonus_spending_enabled;bonus.parentElement.hidden=!data.bonus_spending_enabled;
  f.append(el('p','Согласие действует для всех ваших одобренных заявок, в том числе созданных ранее. Списывается цена заявки после скидки в пределах указанного лимита. При недостатке средств заявка ждёт пополнения. Уже выставленный внешний счёт сначала нужно завершить. Проверка выполняется раз в минуту.','muted block-note'));
  submit(f,'Сохранить автосписание',async()=>{await api('/wallet/preferences',{method:'PUT',body:{auto_pay:enabled.checked,currency:c.value,per_campaign_limit:limit.value?minor(limit.value,c.value):0,use_bonus:bonus.checked}});notice(enabled.checked?'Автосписание включено в указанных пределах.':'Автосписание выключено.',true);await load(target);});
  p.append(f);root.append(p);
 }
 function entries(root,data,admin=false,onPage){
  const p=panel(admin?'Журнал пользователя':'История операций');p.classList.add('wallet-journal');if(!data.entries.length)p.append(el('p','Движений средств пока нет. Неоплаченные и закрытые счета показаны выше.','empty'));
  data.entries.forEach(e=>{const row=el('article',undefined,'card wallet-entry');row.dataset.direction=e.delta>0?'credit':'debit';
   const direct=e.source==='direct';row.append(el('h3',(e.delta>0?'+':'')+amount(e.delta,e.currency)),el('p',direct?'Оплата кампании · '+(e.provider==='telegram_stars'?'Telegram Stars':'СБП')+(e.payment_status==='refunded'?' · Возвращена':''):e.kind==='payment'&&e.automatic?'Автооплата размещения':kinds[e.kind]||e.kind));
   const at=el('time',date(e.created_at));at.dateTime=e.created_at;row.append(at);const more=window.pushokViews.disclosure('Детали',[el('p',e.reason)],'wallet-entry-details');row.append(more);
   if(direct)more.append(el('p','Прямая оплата. Внутренний баланс не изменялся.','muted'));else more.append(el('p','Остаток после операции: '+amount(e.balance_after,e.currency),'muted'),el('p','Проводка №'+e.entry_number,'muted'));
   if(e.actor_telegram_id)more.append(el('p','Автор: '+(e.actor_username?'@'+e.actor_username+' · ':'')+e.actor_telegram_id,'muted'));
   if(e.campaign_id&&(admin||!['referral_reward','referral_reversal'].includes(e.kind))){const a=el('a','Открыть заявку →');a.href='#/'+(e.archived_at?'archive/':admin?'moderation/':'create/')+e.campaign_id;more.append(a);}
   if(admin&&['manual_credit','manual_debit'].includes(e.kind)&&!e.reversed){const f=el('form'),reason=field(f,'Причина отмены корректировки','text');reason.required=true;reason.minLength=3;reason.maxLength=500;submit(f,'Отменить корректировку',async()=>{await api('/admin/wallet/entries/'+e.id+'/reverse',{method:'POST',body:{reason:reason.value}});await adminLookup();});row.append(f);}
   if(e.reversed)row.append(el('p',e.kind==='referral_reward'?'Награда отменена отдельной записью.':'Корректировка отменена отдельной записью.','muted'));p.append(row);
  });
  const nav=el('div',undefined,'inline-actions');if(data.page>0)nav.append(button('Предыдущие',()=>onPage(data.page-1)));if((data.page+1)*data.page_size<data.total)nav.append(button('Следующие',()=>onPage(data.page+1)));p.append(el('p','Операций: '+data.total,'muted'),nav);root.append(p);
 }
 let adminTarget=null,adminSeq=0;
 async function adminLookup(){
  if(!adminTarget||!adminTelegram)return;const node=adminTarget,seq=++adminSeq,session=token,uid=me.id;
  const current=()=>seq===adminSeq&&session===token&&uid===me?.id&&node===adminTarget&&node.isConnected;
  node.replaceChildren(el('p','Ищем пользователя…','muted'));
  let data;
  if(!/^[1-9][0-9]{0,15}$/.test(adminTelegram)){
   try{const users=await api('/admin/wallet/users?query='+encodeURIComponent(adminTelegram));if(!current())return;
    node.replaceChildren(el('p',users.length?'Выберите пользователя:':'Пользователь не найден. Попросите его открыть кабинет и скопировать ID из профиля.','muted'));
    for(const user of users){const row=el('article',undefined,'card wallet-search-user');row.append(window.pushokProfiles.compact(user),button('Открыть баланс',async()=>{adminTelegram=String(user.telegram_user_id);adminPage=0;await adminLookup();}));node.append(row);}
   }catch(e){if(current())node.replaceChildren(el('p',e.message,'wallet-lookup-error'));}return;
  }
  try{data=await api('/admin/wallet/users/'+encodeURIComponent(adminTelegram)+'?page='+adminPage);}
  catch(e){if(current()){const error=el('p',e.message,'wallet-lookup-error');error.setAttribute('role','status');node.replaceChildren(error,button('Повторить поиск',adminLookup));}return;}
  if(seq!==adminSeq||session!==token||uid!==me?.id||node!==adminTarget)return;
  node.replaceChildren(window.pushokProfiles.compact(data.user));balances(node,data);
  node.append(el('p',data.reconciled?'Сверка: остатки совпадают с журналом.':'Ошибка сверки остатков: проверьте данные перед новыми операциями.',data.reconciled?'muted':'wallet-warning'));
  const totals=el('div');data.totals.forEach(r=>totals.append(el('p',names[r.currency]+': начислено '+amount(r.credited,r.currency)+', списано '+amount(r.debited,r.currency))));node.append(totals);
  const adjust=panel('Ручная операция'),f=el('form'),c=currencies(f,'Счёт','',true),direction=field(f,'Операция','select');opt(direction,'Начислить','credit');opt(direction,'Списать','debit');if(data.balances.XTR<0)opt(c,'Погасить прежнюю задолженность Stars','XTR');
  const a=field(f,'Сумма операции','text');a.required=true;a.inputMode='decimal';
  const reason=field(f,'Основание (видно пользователю)','text');reason.required=true;reason.minLength=3;reason.maxLength=500;
  f.append(el('p','Для подарка выберите «Бонусные рубли». Начисления RUB означают подтверждённые средства; укажите основание. Прежняя задолженность Stars корректируется только до нуля по подтверждённому основанию. Записи нельзя удалить: ошибку отменяйте отдельной корректировкой.','muted block-note'));
  submit(f,'Провести операцию',async()=>{await remembered('/admin/wallet/users/'+data.user.telegram_user_id+'/adjust',{currency:c.value,direction:direction.value,amount:minor(a.value,c.value),reason:reason.value});notice('Операция сохранена в журнале.',true);});adjust.append(f);node.append(adjust);
  node.append(button('Скачать CSV (последние 10 000 операций)',async()=>{const response=await fetch('/api/v1/admin/wallet/users/'+data.user.telegram_user_id+'/export',{headers:{Authorization:'Bearer '+token}});if(!response.ok)throw Error('Не удалось получить журнал.');const url=URL.createObjectURL(await response.blob()),a=el('a');a.href=url;a.download='wallet-'+data.user.telegram_user_id+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
  entries(node,data,true,async page=>{adminPage=page;await adminLookup();});
  const payments=panel('Оплаты с баланса');data.payments.forEach(pay=>{const row=el('article',undefined,'card');row.append(el('h3','Заявка #'+pay.campaign_id.slice(0,8)),el('p',amount(pay.cash_amount,pay.currency)+(pay.bonus_amount?' + '+amount(pay.bonus_amount,'BONUS'):'')),el('p',pay.status==='refunded'?'Возвращено на баланс':pay.automatic?'Автосписание':'Оплачено пользователем'));
   if(pay.status==='paid'){const f=el('form'),reason=field(f,'Основание возврата','text');reason.required=true;reason.minLength=3;reason.maxLength=500;f.append(el('p','Вернётся та же сумма в тех же валютах. Запланированное размещение отменится. Активное сначала нужно остановить. Это возврат на внутренний баланс.','muted'));submit(f,'Вернуть на баланс',async()=>{await api('/admin/wallet/campaigns/'+pay.campaign_id+'/refund',{method:'POST',body:{reason:reason.value}});await adminLookup();});row.append(f);}payments.append(row);});node.append(payments);
 }
 async function load(node){
  if(!node||!me)return;node.querySelectorAll('[data-wallet-section]').forEach(d=>expanded[d.dataset.walletSection]=d.open);target=node;const version=++epoch,session=token,uid=me.id;node.replaceChildren(el('p','Загружаем баланс…'));adminTarget=null;adminSeq++;
  try{
   const [data,journal]=await Promise.all([api('/wallet'+(deletedTopups?'?deleted_topups=true':'')),api('/wallet/entries?page='+historyPage)]);if(version!==epoch||session!==token||uid!==me?.id)return;
   const root=el('div',undefined,'wallet-page');balances(root,data,true);
   const tools=el('nav',undefined,'wallet-tools');tools.setAttribute('aria-label','Действия баланса');tools.append(window.pushokViews.iconAction(button('',()=>load(node)),'refresh','Обновить баланс'));
   const payments=el('a','Мои оплаты','ui-button');payments.href='#/payments';tools.append(payments);root.append(tools);
   const request=pending();if(request){const p=panel('Незавершённая операция');p.append(el('p','Ответ не получен. Повтор отправит тот же запрос и не создаст вторую операцию.'),button('Повторить незавершённую операцию',()=>replay(request)));root.append(p);}
   if(data.enabled){topupForm(root,data);redeemGift(root);}else root.append(el('p','Внутренний баланс выключен. Остатки и история сохранены, новые пополнения и списания остановлены. Ранее выставленные счета продолжают проверяться. Заявки можно оплачивать напрямую.','wallet-warning'));
   topupList(root,data);autopay(root,data);telegramStars(root);
   if(me.role==='admin'){
    const admin=panel('Управление балансами'),f=el('form'),tg=field(f,'Пользователь: @никнейм или ID','text',adminTelegram);tg.required=true;tg.placeholder='Telegram ID, @username или имя';tg.maxLength=100;f.prepend(Object.assign(el('p','Поиск по Telegram ID, @username или имени. Пользователь появляется после входа в puShok.','muted'),{className:'muted block-note'}));
    submit(f,'Найти пользователя',async()=>{adminTelegram=tg.value.trim();adminPage=0;await adminLookup();});f.append(button('К моему балансу',()=>{adminSeq++;adminTelegram='';adminPage=0;tg.value='';adminTarget?.replaceChildren();const section=f.closest('details');if(section)section.open=false;expanded.admin=false;node.scrollIntoView({block:'start'});}));admin.append(f);adminTarget=el('div',undefined,'wallet-admin-result');admin.append(adminTarget);root.append(admin);
    const review=panel('Сверка пополнений');review.loadReview=async()=>{const rows=await api('/admin/wallet/topups');if(session!==token||uid!==me?.id||version!==epoch)return;let list=review.querySelector('.wallet-review');if(list)list.remove();list=el('div',undefined,'wallet-review');rows.forEach(t=>{const r=el('article',undefined,'card wallet-topup-review'),head=el('div',undefined,'wallet-topup-head');head.append(window.pushokProfiles.compact(t),el('strong',amount(t.amount,t.currency)));r.append(head,el('p',(statuses[t.status]||t.status)+(t.test_mode?' · Тест':'')+' · '+date(t.created_at),'muted'));const more=el('div',undefined,'wallet-payment-details');more.setAttribute('aria-label','Подробности платежа');more.append(el('p','Счёт #'+t.id,'growth-code'));if(t.review_reason)more.append(el('p','Нужна сверка: '+t.review_reason));r.append(more);list.append(r);});const unmatched=await api('/admin/wallet/unmatched-stars');if(session!==token||uid!==me?.id||version!==epoch)return;
     unmatched.receipts.forEach(r=>{const card=el('article',undefined,'card');card.append(el('h3','Неразобранное поступление Stars'),el('p','Telegram ID '+r.payer_telegram_user_id+' · '+r.total_amount+' Stars'),el('p','Запись #'+r.id+' · '+r.reason_code),el('p','Сверьте поступление и плательщика в Telegram. Не начисляйте средства повторно без проверки.','muted'));list.append(card);});
     unmatched.refunds.forEach(r=>{const card=el('article',undefined,'card');card.append(el('h3','Неразобранный возврат Stars'),el('p','Telegram ID '+r.telegram_user_id+' · '+r.amount+' Stars'),el('p',r.charge_id,'growth-code'));list.append(card);});
     if(!rows.length&&!unmatched.receipts.length&&!unmatched.refunds.length)list.append(el('p','Пополнений для сверки нет.','wallet-review-empty'));review.replaceChildren(list);};root.append(review);
   }
   if(me.role==='admin'){const gifts=await api('/admin/wallet/gifts?page='+giftPage+'&deleted='+giftDeleted+(giftDeleted?'':'&archived='+giftArchived));if(version!==epoch||session!==token||uid!==me?.id)return;adminGifts(root,gifts);}
   entries(root,journal,false,async page=>{historyPage=page;await load(node);});
   foldPanels(root,data);root.querySelectorAll('.block-note').forEach(n=>n.setAttribute('data-note',''));node.replaceChildren(root);if(adminTelegram&&adminTarget)await adminLookup();
  }catch(e){if(version===epoch&&session===token)node.replaceChildren(el('p',e.message),button('Повторить',()=>load(node)));}
 }
 function telegramStars(root){
  const p=panel('Мои Stars в Telegram'),link=el('a','Открыть мои Stars в Telegram ↗','ui-button');link.href='tg://settings/stars';
  const note=el('p','Telegram → Настройки → Мои звёзды. Личный баланс и вся история Stars находятся в Telegram. Здесь показаны только операции puShok.','muted block-note');p.append(link,note);root.append(p);
 }
 function redeemGift(root){
  const p=panel('Активировать подарочный код'),f=el('form'),code=field(f,'Подарочный код на баланс','text');code.required=true;code.maxLength=64;code.placeholder='GIFT-…';code.autocomplete='off';
  p.append(Object.assign(el('p','Код начисляет бонусные рубли один раз на пользователя. Ими можно оплатить рублёвую заявку в веб-кабинете; в Mini App оплата проходит Stars. Обмена бонусов на Stars или деньги нет.','muted block-note'),{className:'muted block-note'}));
  submit(f,'Получить подарок',async()=>{const result=await api('/wallet/gifts/redeem',{method:'POST',body:{code:code.value.trim().toUpperCase()}});notice(result.already_redeemed?'Этот подарок уже зачислен. Повторного начисления нет.':'Начислено '+amount(result.amount,'BONUS'),true);await load(target);});p.append(f);root.append(p);
 }
 function giftStatus(g){return 'До '+date(g.expires_at)+' · '+(g.deleted_at?'Удалён':!g.active?'Выключен':g.expired?'Срок истёк':g.used>=g.max_uses?'Лимит исчерпан':'Действует');}
 function syncGiftActions(row,g){
  const share=row.querySelector('.gift-share');if(share){share.disabled=Boolean(g.deleted_at||!g.active||g.expired||g.used>=g.max_uses);share.title=share.disabled?giftStatus(g):'Поделиться сертификатом';}
  const toggle=row.querySelector('.wallet-gift-toggle');if(toggle){window.pushokViews.iconAction(toggle,'power',g.active?'Выключить код':'Включить код');toggle.setAttribute('aria-pressed',String(g.active));}
 }
 function giftForm(parent,g=null){
  const f=el('form',undefined,'gift-editor'),used=Boolean(g?.used),name=field(f,'Название подарка','text',g?.name||'');name.required=true;name.minLength=3;name.maxLength=100;
  const a=field(f,'Подарок, бонусных ₽','text',g?(g.amount/100).toFixed(2):'');a.required=true;a.inputMode='decimal';a.disabled=used;
  const limit=field(f,'Количество получателей','number',g?.max_uses||'1');limit.required=true;limit.min=String(Math.max(1,g?.used||0));limit.max='10000';limit.step='1';
  const personal=field(f,'Только одному пользователю Telegram','checkbox');personal.checked=Boolean(g?.recipient_telegram_id);personal.disabled=used;
  const picker=window.pushokUsers.picker(f,{label:'Получатель подарка: @никнейм или ID',allowUnknownId:true,value:g?.recipient_telegram_id?String(g.recipient_telegram_id):''});
  const recipientHint=el('p','Выберите пользователя по @никнейму. До первого входа можно указать его точный Telegram ID. Пересылка ссылки не меняет получателя.','muted');f.append(recipientHint);
  function recipientMode(){picker.disable(!personal.checked||used);recipientHint.hidden=!personal.checked;limit.disabled=personal.checked;if(personal.checked)limit.value='1';}
  personal.addEventListener('change',recipientMode);recipientMode();
  if(used)f.append(el('p','Подарок уже получали. Сумма и получатель зафиксированы; название, срок и общий лимит можно изменить.','muted'));
  const originalDate=g?new Date(new Date(g.expires_at)-new Date(g.expires_at).getTimezoneOffset()*60000).toISOString().slice(0,16):'';
  const expires=field(f,'Действует до (ваше время)','datetime-local',originalDate);expires.required=true;
  submit(f,g?'Сохранить подарок':'Создать подарочный код',async()=>{
   const user=personal.checked&&!used?await picker.resolve():null;
   const body={name:name.value,amount:minor(a.value,'BONUS'),max_uses:Number(limit.value),expires_at:g&&expires.value===originalDate?g.expires_at:new Date(expires.value).toISOString(),recipient_telegram_id:used?g.recipient_telegram_id:personal.checked?user?.telegram_user_id:null};
   if(personal.checked&&!body.recipient_telegram_id)throw Error('Выберите получателя подарка.');
   if(g){await api('/admin/wallet/gifts/'+g.id,{method:'PATCH',body});await load(target);}else await remembered('/admin/wallet/gifts',body);
   notice(g?'Подарок изменён. Уже начисленные бонусы сохранены.':'Код создан. Нажмите «Поделиться» в карточке подарка.',true);
  });if(g)f.append(button('Отмена',()=>f.remove()));parent.append(f);return f;
 }
 function adminGifts(root,data){
  const p=panel('Подарочные коды на баланс');
  p.append(el('p','Удалённые коды закрыты для новых активаций. История и уже начисленные бонусы сохраняются.','muted'));
  const tabs=el('nav',undefined,'bonus-tabs');for(const [value,title] of [['active','Коды'],['archive','Архив'],['deleted','Удалённые коды']]){const b=button(title,()=>{giftDeleted=value==='deleted';giftArchived=value==='archive';giftPage=0;return load(target);});b.setAttribute('aria-pressed',String(value===(giftDeleted?'deleted':giftArchived?'archive':'active')));tabs.append(b);}p.append(tabs);
  if(!giftDeleted&&!giftArchived)giftForm(p);
  if(!data.items.length)p.append(el('p',giftDeleted?'Удалённых кодов нет.':giftArchived?'Активированных подарков пока нет.':'Подарочных кодов пока нет.'));
  for(const g of data.items){const row=el('article',undefined,'card wallet-gift'),head=el('div',undefined,'gift-heading'),name=el('h3',g.name);name.prepend(window.pushokViews.icon('gift'));head.append(name);row.append(head,el('p',amount(g.amount,'BONUS')+' · активаций '+g.used+' из '+g.max_uses,'wallet-gift-summary'),el('p',g.code,'growth-code'),el('p',giftStatus(g),'wallet-gift-status muted'));
   row.append(el('p',g.recipient_telegram_id?'Получатель: '+(g.recipient_username?'@'+g.recipient_username:'Telegram ID '+g.recipient_telegram_id):'Любой получатель кода','muted'));
   const actions=el('div',undefined,'gift-actions');row.append(actions);
   function action(text,icon,fn){const b=window.pushokViews.iconAction(button('',fn),icon,text);actions.append(b);return b;}
   if(g.deleted_at)action('Восстановить','restore',async()=>{await api('/admin/wallet/gifts/'+g.id+'/restore',{method:'POST'});await load(target);});
   else{
    actions.append(window.pushokLinks.giftShare(g,row));
    action('Скопировать код','copy',async()=>{await navigator.clipboard.writeText(g.code);notice('Код скопирован.',true);});
    action('Изменить','edit',()=>{row.querySelector('.gift-editor')?.remove();giftForm(row,g).scrollIntoView({block:'center'});});
    action('Удалить','trash',async()=>{if(!confirm('Удалить код «'+g.name+'»? Новые активации прекратятся. Начисленные бонусы и история сохранятся.'))return;await api('/admin/wallet/gifts/'+g.id,{method:'DELETE'});await load(target);});
    const toggle=action(g.active?'Выключить код':'Включить код','power',async()=>{await api('/admin/wallet/gifts/'+g.id,{method:'PUT',body:{active:!g.active}});await load(target);});toggle.classList.add('wallet-gift-toggle');head.append(toggle);
   }
   giftReportControls(row,g,actions);syncGiftActions(row,g);p.append(row);}
  const nav=el('div',undefined,'inline-actions');if(data.page>0)nav.append(button('Предыдущие коды',async()=>{giftPage--;await load(target);}));if((data.page+1)*data.page_size<data.total)nav.append(button('Следующие коды',async()=>{giftPage++;await load(target);}));p.append(nav);root.append(p);
 }
 function giftReportControls(row,g,actions=row){
  const box=el('div',undefined,'wallet-gift-report');box.hidden=true;let seq=0;
  async function show(page=0){
   const current=++seq,session=token,uid=me?.id;box.hidden=false;box.replaceChildren(el('p','Загружаем активации…'));
   const valid=()=>current===seq&&session===token&&uid===me?.id&&box.isConnected&&!box.hidden;
   try{const data=await api('/admin/wallet/gifts/'+g.id+'/redemptions?page='+page);if(!valid())return;
    Object.assign(g,data.gift,{used:data.total});row.querySelector('.wallet-gift-summary').textContent=amount(g.amount,'BONUS')+' · активаций '+g.used+' из '+g.max_uses;row.querySelector('.wallet-gift-status').textContent=giftStatus(g);syncGiftActions(row,g);
    box.replaceChildren(el('h4','Активации подарка'),el('p','Получателей: '+data.total+' · начислено '+amount(data.credited,'BONUS')),
     el('p','Это начисления по коду. Последующие расходы и корректировки смотрите в журнале пользователя.','muted block-note'));
    if(!data.items.length)box.append(el('p','Этот код ещё никто не активировал.'));
    for(const e of data.items){const item=el('article',undefined,'card');item.append(el('strong','Telegram ID '+e.telegram_user_id),el('p',(e.username?'@'+e.username+' · ':'')+(e.first_name||'Пользователь')),el('p','+'+amount(e.amount,'BONUS')),el('p',date(e.created_at)+' · проводка №'+e.entry_number,'muted'));box.append(item);}
    const nav=el('div',undefined,'inline-actions');if(data.page>0)nav.append(button('Предыдущие активации',()=>show(data.page-1)));if((data.page+1)*data.page_size<data.total)nav.append(button('Следующие активации',()=>show(data.page+1)));
    nav.append(button('Обновить активации',()=>show(data.page)),button('Скачать активации CSV',async()=>{
     const response=await fetch('/api/v1/admin/wallet/gifts/'+g.id+'/export',{headers:{Authorization:'Bearer '+session}});if(!response.ok)throw Error('Не удалось выгрузить активации.');
     const blob=await response.blob();if(!valid())return;const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download='gift-'+g.id+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }));box.append(nav);
   }catch(e){if(valid())box.replaceChildren(el('p',e.message),button('Повторить загрузку активаций',()=>show(page)));}
  }
  const history=window.pushokViews.iconAction(button('',()=>{if(box.hidden)return show();seq++;box.hidden=true;}),'chart','История активаций');actions.append(history);row.append(box);
 }
 async function walletSettings(){
  const node=$('wallet-settings'),session=token,uid=me?.id;
  if(!node||me?.role!=='admin')return;
  const editing=()=>Boolean(node.querySelector('form[data-dirty],form[aria-busy="true"]'));
  if(editing())return;
  const seq=++settingsEpoch;
  const info=$('wallet-settings-info'),help=$('wallet-settings-help');
  info.onclick=()=>{help.hidden=!help.hidden;info.setAttribute('aria-expanded',String(!help.hidden));};
  if(!node.querySelector('form'))node.replaceChildren(el('p','Загружаем настройки баланса…'));
  try{const data=await api('/admin/wallet/settings');if(seq!==settingsEpoch||session!==token||uid!==me?.id||editing())return;
   const form=el('form'),row=el('label',undefined,'payment-method'),symbol=el('span',undefined,'payment-symbol payment-wallet');
   symbol.append(window.pushokViews.icon('wallet'));
   const copy=el('span',undefined,'payment-copy');copy.append(el('strong','Оплата с баланса'),el('small','Пополнения и подарочные коды'));
   const choice=el('span',undefined,'payment-choice'),enabled=el('input'),state=el('small',data.enabled?'Баланс включён':'Баланс выключен','wallet-settings-state');
   enabled.type='checkbox';enabled.checked=data.enabled;enabled.setAttribute('aria-label','Включить внутренний баланс');
   choice.append(enabled,state);row.append(symbol,copy,choice);form.append(row);
   const bonusRow=el('label',undefined,'payment-method'),bonusIcon=el('span',undefined,'payment-symbol payment-stars');
   bonusIcon.append(window.pushokViews.icon('gift'));
   const bonusCopy=el('span',undefined,'payment-copy'),bonusChoice=el('span',undefined,'payment-choice');
   bonusCopy.append(el('strong','Оплата бонусами'),el('small','Бонусные рубли покрывают часть или всю стоимость размещения в веб-кабинете.'));
   const bonus=el('input'),bonusState=el('small');
   bonus.type='checkbox';bonus.setAttribute('aria-label','Разрешить оплату бонусами');bonus.checked=data.bonus_spending_enabled;
   bonusChoice.append(bonus,bonusState);bonusRow.append(bonusIcon,bonusCopy,bonusChoice);form.append(bonusRow);
   const feedback=el('p',undefined,'wallet-setting-feedback');feedback.setAttribute('role','status');
   function paint(){
    form.toggleAttribute('data-dirty',enabled.checked!==data.enabled||bonus.checked!==data.bonus_spending_enabled);
    bonusState.textContent=bonus.checked!==data.bonus_spending_enabled?'Не сохранено':bonus.checked?'Разрешена':'Выключена';
    state.textContent=enabled.checked===data.enabled?(data.enabled?'Баланс включён':'Баланс выключен'):'Не сохранено';
    feedback.textContent=enabled.checked===data.enabled?'':enabled.checked?'Баланс станет доступен после сохранения.':'После сохранения пополнения и оплата с баланса остановятся. Автооплату пользователям потребуется включить заново.';
   }
   enabled.addEventListener('change',paint);bonus.addEventListener('change',paint);paint();
   form.append(feedback);
   const save=submit(form,'Сохранить настройку баланса',async()=>{
    form.setAttribute('aria-busy','true');enabled.disabled=true;bonus.disabled=true;
    try{
     const saved=await api('/admin/wallet/settings',{method:'PUT',body:{enabled:enabled.checked,bonus_spending_enabled:bonus.checked}});
     if(session!==token||uid!==me?.id||!form.isConnected)return;
     Object.assign(data,saved);paint();
     notice('Настройка баланса сохранена.',true);
    }finally{enabled.disabled=false;bonus.disabled=false;form.removeAttribute('aria-busy');}
   });
   save.className='payment-save secondary-button';const footer=el('div',undefined,'payment-form-footer');footer.append(save);form.append(footer);
   node.replaceChildren(form);
  }catch(e){if(seq===settingsEpoch&&session===token&&!editing())node.replaceChildren(el('p',e.message),button('Повторить настройку баланса',walletSettings));}
 }
 async function payment(c,container){
  const session=token,uid=me?.id;container.hidden=false;container.replaceChildren(el('p','Проверяем возможность оплаты с баланса…'));
  try{
   const data=await api('/wallet/campaigns/'+c.id);if(!container.isConnected||session!==token||uid!==me?.id)return;container.replaceChildren();
   if(data.payment){
    const paid=data.payment,parts=[];
    if(paid.cash_amount)parts.push(amount(paid.cash_amount,paid.currency));
    if(paid.bonus_amount)parts.push(amount(paid.bonus_amount,'BONUS'));
    container.append(el('h3',paid.status==='refunded'?'Оплата возвращена на баланс':'Оплачено с баланса'),el('p',parts.join(' + ')));return;
   }
   if(['verified','refunded'].includes(c.payment_status)){container.hidden=true;return;}
   if(!data.enabled){container.append(el('p','Внутренний баланс выключен администратором. Используйте прямую оплату заявки.','muted'));return;}
   if(!data.payable){container.append(el('p','Оплата с баланса станет доступна после одобрения заявки, если по ней нет открытого внешнего счёта.','muted'));return;}
   const telegram=miniAppMode||data.stars_only,available=data.balances;
   const heading=el('div',undefined,'bonus-checkout-heading');
   heading.append(window.pushokViews.icon('gift'),el('h3','Бонусы'));
   container.append(heading);
   const bonusPrice=data.amount_rub,bonusBalance=Math.max(0,available.BONUS);
   const canRedeem=data.bonus_spending_enabled&&!data.has_debt&&bonusPrice>0&&bonusBalance>=bonusPrice;
   async function pay(currency,useBonus,expected){
    await api('/wallet/campaigns/'+c.id+'/pay',{method:'POST',body:{currency,use_bonus:useBonus,expected_amount:expected}});
    if(session!==token||uid!==me?.id)return;
    notice('Оплата подтверждена.',true);await refresh();
   }
   function payButton(label,fn){
    const control=button(label,fn);control.classList.add('action-positive','labeled-action');return control;
   }
   container.append(el('p','На бонусном балансе '+money(bonusBalance),'bonus-available'));
   if(canRedeem){
    const use=payButton('Оплатить бонусами · '+money(bonusPrice),()=>pay('BONUS',true,bonusPrice));
    use.prepend(window.pushokViews.icon('gift'));container.append(use);
   }else{
    const missing=Math.max(0,bonusPrice-bonusBalance);
    container.append(el('p',!data.bonus_spending_enabled?'Оплата бонусами выключена в настройках.':data.has_debt?'Оплата с баланса приостановлена из-за задолженности.':'Для полной оплаты бонусами не хватает '+money(missing)+'.','muted'));
   }
   if(!telegram){
    const part=data.bonus_spending_enabled&&!canRedeem?Math.min(bonusBalance,bonusPrice):0,cash=bonusPrice-part;
    const cashButton=payButton(part?'Оплатить · '+money(part)+' бонусами + '+money(cash)+' с баланса':'Оплатить с баланса',()=>pay('RUB',part>0,bonusPrice));
    cashButton.disabled=data.has_debt||!bonusPrice||available.RUB<cash;
    container.append(cashButton);
   }
   if(available.XTR>0&&data.amount_xtr>0){
    const stars=payButton('Использовать прежние Stars · '+data.amount_xtr,()=>pay('XTR',false,data.amount_xtr));
    stars.disabled=data.has_debt||available.XTR<data.amount_xtr;container.append(stars);
   }
  }catch(e){if(container.isConnected&&session===token)container.replaceChildren(el('p',e.message));}
 }
 return {load,payment,minor,overview,selectUser(tg){adminTelegram=String(tg);adminPage=0;expanded.admin=true;},settings:walletSettings,reset(){expanded={};deletedTopups=false;epoch++;overviewEpoch++;settingsEpoch++;$('wallet-settings')?.replaceChildren();adminSeq++;if(target)target.replaceChildren();target=null;adminTarget=null;adminTelegram='';historyPage=0;adminPage=0;giftPage=0;giftDeleted=false;giftArchived=false;}};
})();
