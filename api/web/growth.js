'use strict';
window.pushokGrowth=(()=>{
 let epoch=0,target=null;
 Object.assign(errors,{
  growth_free_tariff:'Для бесплатного тарифа код не нужен. Выберите платный тариф или уберите код.',
  growth_code_unavailable:'Код недоступен: проверьте тариф, срок действия и лимит использований.',
  growth_code_exists:'Такой код уже существует. Выберите другой.',growth_quote_required:'Сначала рассчитайте стоимость.',
  growth_quote_changed:'Условия предложения изменились. Рассчитайте стоимость ещё раз.',
  growth_tariff_locked:'В этой заявке уже использован бонус. Её тариф изменить нельзя; дату и канал можно исправить.',
  growth_referrals_disabled:'Реферальная программа сейчас выключена.',growth_invitation_unavailable:'Это приглашение недоступно.',
  growth_invitation_already_bound:'Для вашего аккаунта уже сохранено другое приглашение.',
  growth_invitation_before_order:'Приглашение можно принять только до первой заявки.'
 });
 function field(form,label,type,value='',required=false){
  const box=el('label',label),input=el(type==='select'?'select':'input');
  if(type!=='select')input.type=type;input.value=value;input.required=required;box.append(input);form.append(box);return input;
 }
 function option(select,label,value){const o=el('option',label);o.value=value;select.append(o);}
 function panel(title){const p=el('section',undefined,'panel');p.append(el('h2',title));return p;}
 function bind(form,fn){form.addEventListener('submit',async e=>{e.preventDefault();const b=form.querySelector('button[type="submit"]');b.disabled=true;try{await fn();notice('Сохранено.',true);await load(target);}catch(err){notice(err.message);}finally{b.disabled=false;}});}
 function save(form,text){const b=el('button',text,'primary');b.type='submit';form.append(b);}
 function adminPanels(root,data,available){
  const referral=panel('Реферальная программа'),rf=el('form');
  const enabled=field(rf,'Принимать новые приглашения','checkbox');enabled.checked=data.settings.referrals_enabled;
  const percent=field(rf,'Скидка за приглашённого, %','number',data.settings.reward_percent,true);percent.min=1;percent.max=100;
  const days=field(rf,'Срок купона, дней','number',data.settings.reward_days,true);days.min=1;days.max=365;
  rf.append(el('p','Награда — одноразовая скидка после первого оплаченного и завершённого размещения приглашённого. Процент и срок фиксируются при принятии приглашения. Отключение программы закрывает новые приглашения; уже принятые сохраняют условия.','muted'));
  save(rf,'Сохранить условия');bind(rf,()=>api('/admin/growth/settings',{method:'PUT',body:{referrals_enabled:enabled.checked,reward_percent:Number(percent.value),reward_days:Number(days.value)}}));referral.append(rf);root.append(referral);
  const create=panel('Создать предложение'),f=el('form');
  const kind=field(f,'Тип предложения','select');[['Промокод','promo'],['Автоматическая скидка','discount'],['Подарочное размещение','gift']].forEach(([l,v])=>option(kind,l,v));
  const name=field(f,'Название','text','',true);name.maxLength=100;
  const code=field(f,'Промокод','text','',true);code.maxLength=48;code.pattern='[A-Za-z0-9_-]{3,48}';
  const pct=field(f,'Скидка, %','number','10',true);pct.min=1;pct.max=100;
  const tariff=field(f,'Тариф','select');option(tariff,'Все тарифы','');available.filter(t=>!t.admin_only).forEach(t=>option(tariff,t.name,t.id));
  const hours=field(f,'Минимальная длительность размещения, часов','number','0',true);hours.min=0;hours.max=8760;
  const start=field(f,'Начало (ваше местное время; пусто — сейчас)','datetime-local');
  const end=field(f,'Окончание (ваше местное время; пусто — без срока)','datetime-local');
  const limit=field(f,'Всего заявок (пусто — без ограничения)','number');limit.min=1;limit.max=1000000;
  const own=field(f,'Заявок на одного пользователя','number','1',true);own.min=1;own.max=1000000;
  function mode(){const gift=kind.value==='gift',selected=tariff.value;tariff.replaceChildren();option(tariff,gift?'Выберите платный тариф':'Все тарифы','');available.filter(t=>!t.admin_only&&(!gift||t.price_kopecks>0)).forEach(t=>option(tariff,t.name,t.id));tariff.value=[...tariff.options].some(o=>o.value===selected)?selected:'';code.parentElement.hidden=kind.value!=='promo';code.required=kind.value==='promo';code.disabled=kind.value!=='promo';pct.disabled=gift;if(gift)pct.value=100;tariff.required=gift;limit.disabled=gift;if(gift)limit.value=1;own.disabled=gift;if(gift)own.value=1;}
  kind.addEventListener('change',mode);mode();
  f.append(el('p','Применяется одна, самая выгодная скидка. Лимит расходуется при отправке заявки, даже если её потом отзовут или отклонят. Подарок — код на одно бесплатное размещение выбранного тарифа; модерация остаётся обязательной. Условия созданных предложений не редактируются: выключите старое и создайте новое.','muted'));
  save(f,'Создать предложение');bind(f,()=>api('/admin/growth/offers',{method:'POST',body:{kind:kind.value,name:name.value,code:kind.value==='promo'?code.value:null,percent:Number(pct.value),tariff_id:tariff.value||null,min_duration_seconds:Math.round(Number(hours.value)*3600),starts_at:start.value?new Date(start.value).toISOString():null,ends_at:end.value?new Date(end.value).toISOString():null,total_limit:limit.value?Number(limit.value):null,per_user_limit:Number(own.value)}}));create.append(f);root.append(create);
  const list=panel('Предложения и выданные бонусы');list.append(el('p','Последние 200 предложений. Код подарка передавайте получателю: воспользоваться им сможет первый предъявитель.','muted'));
  if(!data.offers.length)list.append(el('p','Предложений пока нет.'));
  data.offers.forEach(o=>{const row=el('article',undefined,'card');const expired=o.ends_at&&new Date(o.ends_at)<=new Date();row.append(el('h3',o.name),el('p',({promo:'Промокод',discount:'Скидка',gift:'Подарок',referral:'Реферальный бонус'})[o.kind]+' · '+o.percent+'% · '+(o.tariff_name||'Все тарифы')),el('p','Использовано: '+o.uses+(o.total_limit?' / '+o.total_limit:'')+' · '+(!o.active?'Выключено':expired?'Срок истёк':'Включено')));
   if(o.code)row.append(el('p',o.code,'growth-code'));if(o.ends_at)row.append(el('p','До '+date(o.ends_at),'muted'));
   if(o.kind!=='referral')row.append(button(o.active?'Выключить':'Включить',async()=>{await api('/admin/growth/offers/'+o.id,{method:'PATCH',body:{active:!o.active}});await load(target);}));list.append(row);
  });root.append(list);
 }
 async function load(node){
  if(!node||!me)return;target=node;const version=++epoch,session=token,uid=me.id;node.replaceChildren(el('p','Загружаем бонусы…'));
  try{
   const mine=await api('/growth/me');if(version!==epoch||session!==token||uid!==me?.id)return;
   const root=el('div'),p=panel('Пригласить рекламодателя');
   p.append(el('p',mine.referrals_enabled?'За первое оплаченное и завершённое размещение приглашённого вы получите одноразовый купон на скидку '+mine.reward_percent+'%, действующий '+mine.reward_days+' дней.':'Новые приглашения пока отключены администратором.'),el('p','Приглашено: '+mine.invited));
   if(mine.referrals_enabled&&mine.link){const input=field(p,'Ваша ссылка для друзей','text',mine.link);input.readOnly=true;input.addEventListener('click',()=>input.select());p.append(el('p','Друг должен открыть эту ссылку и нажать «Запустить» до создания первой заявки. Это приглашение в рекламный кабинет.','muted'));}
   if(!mine.bound&&mine.referrals_enabled){const form=el('form'),code=field(form,'Код пригласившего вас пользователя','text','',true);code.maxLength=48;save(form,'Принять приглашение');bind(form,()=>api('/growth/referral',{method:'POST',body:{code:code.value}}));p.append(form);}
   root.append(p);
   const rewards=panel('Мои купоны');if(!mine.coupons.length)rewards.append(el('p','Бонусных купонов пока нет.'));
   mine.coupons.forEach(c=>{const row=el('article',undefined,'card'),expired=c.ends_at&&new Date(c.ends_at)<=new Date();row.append(el('h3',c.name+' · '+c.percent+'%'),el('p',c.used?'Использован':!c.active?'Отменён':expired?'Срок истёк':'Доступен'),el('p',c.code,'growth-code'));if(c.ends_at)row.append(el('p','До '+date(c.ends_at),'muted'));rewards.append(row);});
   rewards.append(el('p','Введите код купона, подарка или промокод при создании заявки. Он применяется к одной заявке; скидки не суммируются. Это скидка на размещение puShok, не денежный баланс.','muted'));const order=el('a','Создать размещение →');order.href='#/create';rewards.append(order);root.append(rewards);
   if(me.role==='admin'){const [data,available]=await Promise.all([api('/admin/growth'),api('/admin/tariffs')]);if(version!==epoch||session!==token||uid!==me?.id)return;adminPanels(root,data,available);}
   node.replaceChildren(root);
  }catch(e){if(version===epoch&&session===token)node.replaceChildren(el('p',e.message),button('Повторить',()=>load(node)));}
 }
 return {load,reset(){epoch++;if(target)target.replaceChildren();target=null;}};
})();
