'use strict';
window.pushokGrowth=(()=>{
 let epoch=0,target=null,overviewEpoch=0,offersDeleted=false;
 Object.assign(errors,{
  growth_offer_not_found:'Предложение не найдено или удалено.',growth_kind_locked:'Тип существующего предложения изменить нельзя.',growth_used_identity_locked:'Использованный код и получателя менять нельзя.',growth_limit_used:'Лимит не может быть меньше уже использованного.',growth_referral_terms_locked:'Условия заработанного купона зафиксированы. Можно менять название и срок.',
  growth_free_tariff:'Для бесплатного тарифа код не нужен. Выберите платный тариф или уберите код.',
  growth_code_unavailable:'Код недоступен: проверьте тариф, срок действия и лимит использований.',
  growth_code_exists:'Такой код уже существует. Выберите другой.',growth_quote_required:'Сначала рассчитайте стоимость.',
  growth_quote_changed:'Условия предложения изменились. Рассчитайте стоимость ещё раз.',
  growth_tariff_locked:'В этой заявке уже использован бонус. Её тариф изменить нельзя; дату и канал можно исправить.',
  growth_referrals_disabled:'Реферальная программа сейчас выключена.',growth_invitation_unavailable:'Это приглашение недоступно.',
  growth_invitation_already_bound:'Для вашего аккаунта уже сохранено другое приглашение.',
  growth_invitation_before_order:'Приглашение можно принять только до первой заявки.',
  growth_link_unavailable:'Не удалось подготовить ссылку. Повторите чуть позже.'
 });
 function field(form,label,type,value='',required=false){
  const box=el('label',label),input=el(type==='select'?'select':'input');
  if(type!=='select')input.type=type;input.value=value;input.required=required;input.setAttribute('aria-label',label);box.append(input);form.append(box);return input;
 }
 function option(select,label,value){const o=el('option',label);o.value=value;select.append(o);}
 function panel(title){const p=el('section',undefined,'panel');p.append(el('h2',title));return p;}
 function bind(form,fn){form.addEventListener('submit',async e=>{e.preventDefault();const b=form.querySelector('button[type="submit"]');b.disabled=true;try{await fn();notice('Сохранено.',true);await load(target);}catch(err){notice(err.message);}finally{b.disabled=false;}});}
 function save(form,text){const b=el('button',text,'primary');b.type='submit';form.append(b);}
 function referralPanel(settings,reload){
  const referral=panel('Реферальная программа'),rf=el('form');
  const signup=field(rf,'Бонус каждому за регистрацию, ₽','number',((settings.registration_bonus_kopecks||0)/100).toFixed(2),true);
  signup.min='0';signup.max='10000';signup.step='0.01';
  rf.append(el('small','Начисляется один раз, в том числе уже зарегистрированным. 0 отключает начисление.','muted'));
  const enabled=field(rf,'Принимать новые приглашения','checkbox');enabled.checked=settings.referrals_enabled;
  const rewardMode=field(rf,'Награда за приглашённого','select');option(rewardMode,'Купон на скидку','coupon');option(rewardMode,'На бонусный баланс','bonus');rewardMode.value=settings.reward_mode;
  const percent=field(rf,'Скидка за приглашённого, %','number',settings.reward_percent,true);percent.min=1;percent.max=100;
  const days=field(rf,'Срок купона, дней','number',settings.reward_days,true);days.min=1;days.max=365;
  const bonus=field(rf,'Награда на бонусный баланс, ₽','number',(settings.reward_bonus_kopecks/100).toFixed(2),true);bonus.min='0.01';bonus.max='10000';bonus.step='0.01';
  function rewardFields(){const isBonus=rewardMode.value==='bonus';for(const input of [percent,days]){input.disabled=isBonus;input.parentElement.hidden=isBonus;}bonus.disabled=!isBonus;bonus.parentElement.hidden=!isBonus;}
  rewardMode.addEventListener('change',rewardFields);rewardFields();
  const conditions=el('details',undefined,'personal-help');conditions.append(el('summary','Условия начисления'));rf.append(conditions);
  conditions.append(el('p','Награда выдаётся один раз после первого оплаченного и завершённого размещения приглашённого. Тип, сумма или условия купона фиксируются при принятии приглашения. Изменения действуют только для новых приглашений. Отключение программы закрывает новые приглашения; уже принятые сохраняют условия.','muted'));
  conditions.append(el('p','Бонусная награда — фиксированная сумма, не процент и не обмен Stars. Бесплатные, тестовые и полностью бонусные оплаты не дают награду. При возврате оплаты награда отменяется; потраченные бонусы образуют задолженность. Бонусы без срока, для рублёвых размещений на сайте; без вывода и перевода другому пользователю.','muted'));
  save(rf,'Сохранить условия');bindReferral(rf,()=>api('/admin/growth/settings',{method:'PUT',body:{registration_bonus_kopecks:Math.round(Number(signup.value)*100),referrals_enabled:enabled.checked,reward_percent:Number(percent.value),reward_days:Number(days.value),reward_mode:rewardMode.value,reward_bonus_kopecks:Math.round(Number(bonus.value)*100)}}),reload);referral.append(rf);return referral;
 }
 async function bindReferral(form,write,reload){form.addEventListener('submit',async e=>{e.preventDefault();const b=form.querySelector('[type=submit]');if(b.disabled)return;b.disabled=true;try{await write();notice('Условия сохранены.',true);if(reload)await reload();}catch(e){notice(e.message);}finally{b.disabled=false;}});}
 async function referralSettings(){if(me?.role!=='admin')return;const s=token;const data=await api('/admin/growth');if(s!==token)return;let host=$('referral-settings');if(!host){host=el('div');host.id='referral-settings';$('settings-system').append(host);}if(host.contains(document.activeElement)||window.pushokControls?.uses(host))return;host.replaceChildren(referralPanel(data.settings));}
 function adminPanels(root,data,available){
  root.append(referralPanel(data.settings,()=>load(target)));
  if(!offersDeleted){const create=panel('Создать предложение');offerForm(create,available);root.append(create);}
  const list=panel('Предложения и выданные бонусы'),tabs=el('nav',undefined,'bonus-tabs');
  for(const [value,title] of [[false,'Предложения'],[true,'Удалённые предложения']]){const b=button(title,()=>{offersDeleted=value;return load(target);});b.setAttribute('aria-pressed',String(offersDeleted===value));tabs.append(b);}list.append(tabs);
  if(!data.offers.length)list.append(el('p',offersDeleted?'Удалённых предложений нет.':'Предложений пока нет.'));
  data.offers.forEach(o=>{const row=el('article',undefined,'card bonus-offer');const expired=o.ends_at&&new Date(o.ends_at)<=new Date();row.append(el('h3',o.name),el('p',({promo:'Промокод',discount:'Скидка',gift:'Подарок',referral:'Реферальный бонус'})[o.kind]+' · '+o.percent+'% · '+(o.tariff_name||'Все тарифы')),el('p','Использовано: '+o.uses+(o.total_limit?' / '+o.total_limit:'')+' · '+(o.deleted_at?'Удалено':!o.active?'Выключено':expired?'Срок истёк':'Включено')));
   if(o.code)row.append(el('p',o.code,'growth-code'));if(o.ends_at)row.append(el('p','До '+date(o.ends_at),'muted'));
   if(o.recipient_user_id)row.append(el('p','Получатель: '+(o.recipient_username?'@'+o.recipient_username:'ID '+o.recipient_telegram_id),'muted'));
   const actions=el('div',undefined,'bonus-actions');row.append(actions);function action(text,icon,fn){const b=button(text,fn);b.prepend(window.pushokViews.icon(icon));actions.append(b);}
   if(o.deleted_at)action('Восстановить','refresh',async()=>{await api('/admin/growth/offers/'+o.id+'/restore',{method:'POST'});await load(target);});
   else{
    actions.append(window.pushokLinks.share('offer_'+o.id.replaceAll('-',''),'🎁 '+o.name+' — '+o.percent+'% на размещение в puShok. Откройте приложение, чтобы применить предложение.'+(o.recipient_user_id?' Персональный купон.':'')));
    if(o.code)action('Скопировать код','copy',async()=>{await navigator.clipboard.writeText(o.code);notice('Код скопирован.',true);});
    action('Изменить','edit',()=>{row.querySelector('.offer-editor')?.remove();offerForm(row,available,o).scrollIntoView({block:'center'});});
    action('Удалить','trash',async()=>{if(!confirm('Удалить «'+o.name+'»? Новые применения прекратятся. История заявок и начислений сохранится.'))return;await api('/admin/growth/offers/'+o.id,{method:'DELETE'});await load(target);});
    if(o.kind!=='referral'||o.active)action(o.active?'Выключить':'Включить','status',async()=>{await api('/admin/growth/offers/'+o.id,{method:'PATCH',body:{active:!o.active}});await load(target);});
   }list.append(row);
  });root.append(list);
 }
 function offerForm(parent,available,o=null){
  const f=el('form',undefined,'offer-editor'),referral=o?.kind==='referral',used=Boolean(o?.uses);
  const kind=field(f,'Тип предложения','select');[['Промокод','promo'],['Автоматическая скидка','discount'],['Подарочное размещение','gift'],...(referral?[['Реферальный купон','referral']]:[])].forEach(([l,v])=>option(kind,l,v));if(o)kind.value=o.kind;kind.disabled=Boolean(o);
  const name=field(f,'Название','text',o?.name||'',true);name.maxLength=100;
  const code=field(f,'Промокод','text',o?.code||'PUSHOK-'+crypto.randomUUID().slice(0,8).toUpperCase(),true);code.maxLength=48;code.pattern='[A-Za-z0-9_-]{3,48}';
  const pct=field(f,'Скидка, %','number',o?.percent||10,true);pct.min=1;pct.max=100;
  const tariff=field(f,'Тариф','select');
  const hours=field(f,'Минимальная длительность размещения, часов','number',o?o.min_duration_seconds/3600:0,true);hours.min=0;hours.max=8760;
  const local=value=>value?new Date(new Date(value)-new Date(value).getTimezoneOffset()*60000).toISOString().slice(0,16):'';
  const start=field(f,'Начало (ваше местное время; пусто — сейчас)','datetime-local',local(o?.starts_at));
  const end=field(f,'Окончание (ваше местное время; пусто — без срока)','datetime-local',local(o?.ends_at));
  const limit=field(f,'Всего заявок (пусто — без ограничения)','number',o?.total_limit||'');limit.min=Math.max(1,o?.uses||0);limit.max=1000000;
  const own=field(f,'Заявок на одного пользователя','number',o?.per_user_limit||1,true);own.min=1;own.max=1000000;
  const personal=field(f,'Персональное предложение','checkbox');personal.checked=Boolean(o?.recipient_user_id);personal.disabled=used||referral;
  const recipient=window.pushokUsers.picker(f,{label:'Получатель предложения: @никнейм или ID',value:o?.recipient_telegram_id?String(o.recipient_telegram_id):o?.recipient_user_id||''});
  function recipientMode(){recipient.disable(!personal.checked||used||referral);}personal.addEventListener('change',recipientMode);recipientMode();
  function mode(){const gift=kind.value==='gift',selected=tariff.value||o?.tariff_id;tariff.replaceChildren();option(tariff,gift?'Выберите платный тариф':'Все тарифы','');available.filter(t=>!t.admin_only&&(!gift||t.price_kopecks>0)).forEach(t=>option(tariff,t.name,t.id));if(o?.tariff_id&&![...tariff.options].some(t=>t.value===o.tariff_id))option(tariff,o.tariff_name||'Сохранённый тариф',o.tariff_id);tariff.value=[...tariff.options].some(t=>t.value===selected)?selected:'';code.parentElement.hidden=kind.value!=='promo';code.required=kind.value==='promo';code.disabled=kind.value!=='promo'||used;pct.disabled=gift||referral;if(gift)pct.value=100;tariff.required=gift;limit.disabled=gift||referral;if(gift)limit.value=1;own.disabled=gift||referral;if(gift)own.value=1;for(const input of [tariff,hours,start])input.disabled=referral;}
  kind.addEventListener('change',mode);mode();
  f.append(el('p',referral?'Условия заработанного купона зафиксированы. Можно изменить название и срок либо выключить купон.':o?'Изменения действуют на новые заявки. Уже созданные заявки сохранят свою цену; использованный код и получатель зафиксированы.':'Применяется одна, самая выгодная скидка. Лимит расходуется при отправке заявки. Персональное предложение доступно только выбранному пользователю.','muted'));
  save(f,o?'Сохранить предложение':'Создать предложение');bind(f,async()=>{
   const user=personal.checked&&!used&&!referral?await recipient.resolve():null;
   const body={kind:kind.value,name:name.value,code:kind.value==='promo'?code.value:o?.code||null,percent:Number(pct.value),tariff_id:tariff.value||null,min_duration_seconds:Math.round(Number(hours.value)*3600),starts_at:o&&start.value===local(o.starts_at)?o.starts_at:start.value?new Date(start.value).toISOString():null,ends_at:o&&end.value===local(o.ends_at)?o.ends_at:end.value?new Date(end.value).toISOString():null,total_limit:limit.value?Number(limit.value):null,per_user_limit:Number(own.value),recipient_user_id:used||referral?o.recipient_user_id:personal.checked?user?.id:null};
   if(personal.checked&&!body.recipient_user_id)throw Error('Выберите получателя предложения.');
   return api('/admin/growth/offers'+(o?'/'+o.id:''),{method:o?'PUT':'POST',body});
  });if(o)f.append(button('Отмена',()=>f.remove()));parent.append(f);return f;
 }
 function rewardDescription(r){return r.reward_mode==='bonus'?money(r.reward_bonus_kopecks)+' на бонусный баланс':'купон на скидку '+r.reward_percent+'%, срок '+r.reward_days+' дней';}
 function invitationList(root,mine){
  const p=panel('Мои приглашения');p.append(el('p','Начислено на бонусный баланс: '+money(mine.bonus_rewards.credited)),el('p','Отменено при возвратах: '+money(mine.bonus_rewards.reversed),'muted'));
  const wallet=el('a','Баланс и история операций →');wallet.href='#/wallet';p.append(wallet);
  if(!mine.invitations.length)p.append(el('p','Принятых приглашений пока нет.'));
  else p.append(el('p','Последние 100 приглашений. Для каждого сохранены условия на момент принятия.','muted'));
  mine.invitations.forEach(r=>{const row=el('article',undefined,'card');row.append(el('h3',({previous_inviter:'Награда была выдана до переназначения',waiting:'Ожидается оплаченное и завершённое размещение',rewarded:'Награда выдана',reversed:'Награда отменена после возврата'})[r.status]),el('p',rewardDescription(r)),el('p','Приглашение принято: '+date(r.created_at),'muted'));p.append(row);});root.append(p);
 }
 async function claimProxyInvite(){
  const key='__Host-pushok_invite';
  const read=()=>document.cookie.split('; ').find(pair=>pair.startsWith(key+'='))?.slice(key.length+1);
  let code;
  try{code=read();}catch{return;}
  if(!/^[A-HJ-NP-Z2-9]{8}$/.test(code||''))return;
  try{await api('/growth/referral',{method:'POST',body:{code}});}
  catch(error){if(error.status!==409)return;}
  try{if(read()===code)document.cookie=key+'=; Max-Age=0; Path=/; Secure; SameSite=Lax';}catch{/* Хранилище может быть отключено в WebView. */}
 }
 function myInvitation(){
  const query=location.protocol==='https:'?'?invite_origin='+encodeURIComponent(location.origin):'';
  return api('/growth/me'+query);
 }
 function referralLink(root,mine){
  if(!mine.referrals_enabled||!mine.link)return;
  const row=el('div',undefined,'referral-link-row'),link=el('a',mine.link.replace(/^https:\/\//,''),'referral-link');
  link.href=mine.link;link.target='_blank';link.rel='noopener noreferrer';link.setAttribute('aria-label','Ваша ссылка для друзей');
  const actions=el('div',undefined,'referral-actions');
  const copy=window.pushokViews.iconAction(button('',async()=>{await navigator.clipboard.writeText(mine.link);notice('Ссылка скопирована.',true);}), 'copy','Скопировать реферальную ссылку');
  const share=el('a','','ui-button');share.href='https://t.me/share/url?url='+encodeURIComponent(mine.link)+'&text='+encodeURIComponent(mine.proxy_invite?'Подключайся к прокси puShok. Код приглашения в кабинет: '+mine.code:'Приглашаю в рекламный кабинет puShok.');share.target='_blank';share.rel='noopener noreferrer';
  window.pushokViews.iconAction(share,'share','Поделиться реферальной ссылкой');
  share.addEventListener('click',event=>{if(miniAppMode&&typeof window.Telegram?.WebApp?.openTelegramLink==='function'){event.preventDefault();window.Telegram.WebApp.openTelegramLink(share.href);}});
  actions.append(copy,share);row.append(link,actions);root.append(row);
 }
 async function overview(node){
  if(!node||!me)return;const version=++overviewEpoch,session=token,uid=me.id;
  let card=node.querySelector('.referral-overview');
  if(!card){card=el('section',undefined,'referral-overview');card.append(el('h2','Приглашайте друзей'),el('p','Загружаем приглашение…','muted'));node.replaceChildren(card);}
  card.setAttribute('aria-busy','true');
  try{
   const mine=await myInvitation();if(version!==overviewEpoch||session!==token||uid!==me?.id)return;
   const signature=JSON.stringify([mine.referrals_enabled,mine.link,mine.proxy_invite,mine.invited,mine.reward_mode,mine.reward_bonus_kopecks,mine.reward_percent,mine.reward_days]);
   if(card.dataset.snapshot===signature){card.querySelector('h2').lastChild.textContent='Приглашайте друзей';card.removeAttribute('title');card.setAttribute('aria-busy','false');return;}
   const expanded=card.dataset.inviteLink===mine.link && card.querySelector('.invite-toggle')?.getAttribute('aria-expanded')==='true';
   const head=el('div',undefined,'section-heading'),title=el('h2','Приглашайте друзей');title.prepend(window.pushokViews.icon('users'));head.append(title);card.replaceChildren(head);
   card.append(el('p',mine.referrals_enabled?'За первое оплаченное рублями и завершённое размещение друга — '+rewardDescription(mine)+'.':'Новые приглашения пока отключены.','muted'));
   if(mine.referrals_enabled && mine.link){
    card.dataset.inviteLink=mine.link;
    const invite=el('div',undefined,'invite-details');
    invite.id='home-invite-details';
    invite.hidden=!expanded;
    if(mine.invite_qr){
     const qr=el('img');qr.src=mine.invite_qr;qr.alt='QR-код с вашей реферальной ссылкой';invite.append(qr);
    }
    referralLink(invite,mine);
    const toggle=button('Пригласить друга',()=>{invite.hidden=!invite.hidden;toggle.setAttribute('aria-expanded',String(!invite.hidden));});
    toggle.classList.add('invite-toggle');toggle.setAttribute('aria-controls',invite.id);toggle.setAttribute('aria-expanded',String(!invite.hidden));card.append(toggle,invite);
   }else{delete card.dataset.inviteLink;}
   const info=el('div',undefined,'referral-meta'),more=el('a','Бонусы и условия');more.href='#/growth';info.append(el('span','Приглашено: '+mine.invited),more);card.append(info);card.dataset.snapshot=signature;card.removeAttribute('title');card.setAttribute('aria-busy','false');
  }catch(e){if(version===overviewEpoch&&session===token&&uid===me?.id){card.querySelector('h2').lastChild.textContent='Приглашения · нет связи';card.title='Не удалось обновить приглашение. Повторим при следующем обновлении.';card.setAttribute('aria-busy','false');}}
 }
 async function load(node){
  if(!node||!me)return;target=node;const version=++epoch,session=token,uid=me.id;node.replaceChildren(el('p','Загружаем бонусы…'));
  try{
   const mine=await myInvitation();if(version!==epoch||session!==token||uid!==me?.id)return;
   const root=el('div',undefined,'growth-page'),p=panel('Пригласить друга');
   root.append(window.pushokViews.mascot(mine.referrals_enabled?'Делитесь приглашением и получайте награду за первое оплаченное рублями и завершённое размещение друга.':'Здесь собраны ваши приглашения, купоны и подарочные размещения.'));
   p.append(el('p',mine.referrals_enabled?'Для новых приглашений: за первое оплаченное рублями и завершённое размещение друга — '+rewardDescription(mine)+'.':'Новые приглашения пока отключены администратором.'),el('p','Приглашено: '+mine.invited));
   if(mine.reward_mode==='bonus')p.append(el('p','Бонусами можно оплатить рублёвое размещение на сайте. Они не обмениваются на Stars и не выводятся. Бесплатные, тестовые и полностью бонусные оплаты друга не дают награду. При возврате оплаты награда отменяется, даже если бонусы уже потрачены.','muted'));
   referralLink(p,mine);if(mine.referrals_enabled&&mine.link)p.append(el('p',mine.proxy_invite?'Ссылка открывает подключение к прокси в Telegram. При входе в кабинет из того же браузера приглашение зачтётся автоматически. В другом браузере друг может ввести код '+mine.code+' до первой заявки.':'Откройте приглашение в боте и нажмите «Запустить» до создания первой заявки.','muted'));
   if(!mine.bound&&mine.referrals_enabled){const form=el('form'),code=field(form,'Код пригласившего вас пользователя','text','',true);code.maxLength=48;save(form,'Принять приглашение');bind(form,()=>api('/growth/referral',{method:'POST',body:{code:code.value}}));p.append(form);}
   root.append(p);invitationList(root,mine);
   const rewards=panel('Мои купоны');if(!mine.coupons.length)rewards.append(el('p','Бонусных купонов пока нет.'));
   mine.coupons.forEach(c=>{const row=el('article',undefined,'card'),expired=c.ends_at&&new Date(c.ends_at)<=new Date();row.append(el('h3',c.name+' · '+c.percent+'%'),el('p',c.used?'Использован':!c.active?'Отменён':expired?'Срок истёк':'Доступен'),el('p',c.code,'growth-code'));if(c.ends_at)row.append(el('p','До '+date(c.ends_at),'muted'));row.append(window.pushokLinks.share('offer_'+c.id.replaceAll('-',''),'🎁 Персональный купон puShok: '+c.percent+'%. Доступен только владельцу купона.'));rewards.append(row);});
   rewards.append(el('p','Введите код купона, подарка или промокод при создании заявки. Он применяется к одной заявке; скидки не суммируются. Это скидка на размещение puShok, не денежный баланс.','muted'));const order=el('a','Создать размещение →');order.href='#/create';rewards.append(order);root.append(rewards);
   if(me.role==='admin'){const [data,available]=await Promise.all([api('/admin/growth?deleted='+offersDeleted),api('/admin/tariffs')]);if(version!==epoch||session!==token||uid!==me?.id)return;adminPanels(root,data,available);}
   root.querySelectorAll('.panel').forEach(panel=>{const notes=[...panel.querySelectorAll(':scope > p.muted')];if(notes.length)panel.append(window.pushokViews.disclosure('Условия и подробности',notes));});
   node.replaceChildren(root);
  }catch(e){if(version===epoch&&session===token)node.replaceChildren(el('p',e.message),button('Повторить',()=>load(node)));}
 }
 return {load,overview,referralSettings,claimProxyInvite,reset(){offersDeleted=false;epoch++;overviewEpoch++;if(target)target.replaceChildren();target=null;}};
})();
