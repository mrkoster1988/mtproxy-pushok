'use strict';
window.pushokHall=(()=>{
 const kinds={month:'Месяц',year:'Год'},titles={invited:'Приглашённые',paid_referrals:'Платящие приглашённые',spend:'Расходы на рекламу',paid_campaigns:'Оплаченные кампании'};
 const date=d=>new Date(d).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'});
 const fmt=n=>Number(n).toLocaleString('ru-RU'),month=d=>new Date(d).toLocaleDateString('ru-RU',{month:'long',year:'numeric',timeZone:'Europe/Moscow'});
 function frame(node,rank){if(!node||!rank)return;node.classList.add('hall-frame',rank===1?'hall-gold':rank===2?'hall-silver':rank===3?'hall-bronze':'hall-teal');node.title='Место '+rank;}
 function medals(target,data){
  if(!data.medals?.length)return;const box=el('details',undefined,'hall-medals'),head=el('summary','Медали · '+data.medals.length);box.append(head);
  for(const m of data.medals){const row=el('div',undefined,'hall-medal');row.append(el('span',['','🥇','🥈','🥉'][m.rank]),el('span',month(m.starts_at)+' · '+(titles[m.metric]||m.metric)),el('strong','#'+m.rank));box.append(row);}target.append(box);
 }
 async function load(target){
  const session=token;let kind='month',period='',serial=0;
  const controls=el('div',undefined,'hall-controls'),tabs=el('nav',undefined,'hall-tabs'),history=el('select'),body=el('section',undefined,'hall-board'),admin=el('section');
  history.setAttribute('aria-label','Период рейтинга');
  for(const [key,title] of Object.entries(kinds)){const b=button(title,()=>{kind=key;period='';void refresh();});b.dataset.kind=key;tabs.append(b);}
  history.onchange=()=>{period=history.value;void refresh();};controls.append(tabs,history);target.replaceChildren(controls,body,admin);window.pushokVpn.leaderboard(target);
  const help=el('details',undefined,'context-help');help.append(el('summary','ⓘ Как считаем и награждаем'),el('p','Учёт с регистрации первого пользователя. Участвуют пользователи и сотрудники, кроме разработчика и заблокированных аккаунтов. Расходы Stars учитываются по рублёвой цене заявки, рублёвые — по фактически оплаченной сумме без бонусов. Тестовые, ручные и возвращённые оплаты не учитываются. Периоды по Москве. До окончания периода места могут меняться. Итоги и бонусы фиксируются после завершения; повторных начислений нет. Исторические периоды до запуска доски восстанавливаются без бонусов. При равенстве выше первая учитываемая активность, затем ID.'));target.append(help);
  function valid(){return session===token&&target.isConnected&&location.hash.startsWith('#/hall');}
  function periodTitle(d){return new Date(d.starts_at).toLocaleDateString('ru-RU',{year:'numeric',...(kind==='month'?{month:'long'}:{}),timeZone:'Europe/Moscow'});}
  function person(row,metric,podium=false){
   const line=el('article',undefined,podium?'hall-podium-person hall-place-'+row.rank:'hall-row'),avatar=window.pushokViews.userAvatar(row.user_id,row.first_name||row.username||'',true),copy=el('div',undefined,'hall-person');frame(avatar,row.rank<=10?row.rank:null);avatar.dataset.hallRank=String(row.rank);
   const name=[row.first_name,row.last_name].filter(Boolean).join(' ')||row.username||'Участник';copy.append(el('strong',name));window.pushokVpn.badge(copy.firstChild,row.user_id);if(!podium){
    const handle=window.pushokProfiles.telegramHandle(row);
    if(handle)copy.append(handle);
   }
   const value=el('div',undefined,'hall-value');value.append(el('strong',['spend','spend_rub'].includes(metric)?fmt(row.score/100)+' ₽':fmt(row.score)+(metric==='spend_xtr'?' Stars':'')));if(row.bonus_amount)value.append(el('small','+'+fmt(row.bonus_amount/100)+' бонусных ₽'));
   if(!podium)line.append(el('span',String(row.rank),'hall-rank'));line.append(avatar,copy,value);return line;
  }
  async function refresh(){
   const request=++serial;body.setAttribute('aria-busy','true');[...tabs.children].forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.kind===kind)));
   try{const all=await Promise.all(Object.keys(titles).map(metric=>api('/hall?kind='+kind+'&metric='+metric+(period?'&period_id='+period:''))));if(!valid()||request!==serial)return;
    const first=all[0];history.replaceChildren();const current=el('option',periodTitle({starts_at:new Date(),ends_at:new Date()}));current.value='';history.append(current);
    if(!period)current.textContent=periodTitle(first);for(const p of first.periods){const o=el('option',periodTitle(p));o.value=p.id;history.append(o);}history.value=period;
    const heading=el('div',undefined,'hall-heading');heading.append(el('small',first.closed?'Итоги сохранены':'Места обновляются до конца периода','muted'));body.replaceChildren(heading);
    Object.keys(titles).forEach((metric,i)=>{const data=all[i],section=el('section',undefined,'hall-category');section.append(el('h2',titles[metric]));const podium=el('div',undefined,'hall-podium');for(const rank of [2,1,3]){const row=data.rows.find(r=>r.rank===rank);if(row)podium.append(person(row,metric,true));}section.append(podium);
     if(Object.keys(data.legacy_spend||{}).length){section.append(el('p','Архив сохранён в прежних номинациях, без пересчёта валют.','muted'));for(const [old,rows] of Object.entries(data.legacy_spend)){section.append(el('h3',old==='spend_rub'?'Расходы в рублях':'Расходы в Stars'));rows.forEach(row=>section.append(person(row,old)));}body.append(section);return;}
     if(!data.rows.length)section.append(el('p','Первое место пока свободно','muted'));
     const rest=el('details',undefined,'hall-rest'),own=data.own;rest.append(el('summary','Все участники · топ-'+data.top_size+' · '+(own?'Ваше место: '+own.rank:'У вас пока нет результата')));
     for(const row of data.rows.filter(r=>r.rank>3))rest.append(person(row,metric));if(own&&own.rank>data.top_size)rest.append(person(own,metric));
     if(!data.rows.some(r=>r.rank>3))rest.append(el('p','Других участников пока нет','muted'));section.append(rest);
     if(data.rewards.some(n=>n>0))section.append(el('small','Призы: '+data.rewards.map((n,j)=>n?'#'+(j+1)+' · '+fmt(n/100)+' ₽':null).filter(Boolean).join(' / '),'hall-prizes'));body.append(section);
    });
   }catch(e){if(valid()&&request===serial)body.replaceChildren(el('p',e.message,'empty'),button('Повторить',refresh));}finally{if(request===serial)body.removeAttribute('aria-busy');}
  }
  await refresh();if(me?.role==='admin'&&valid())await adminPanel(admin,valid);
 }
 async function adminPanel(target,valid){
  try{const data=await api('/hall/admin');if(!valid())return;const box=el('details',undefined,'hall-admin panel');box.append(el('summary','Управление и статистика'));target.replaceChildren(box);
   box.append(el('p','Победителей: '+fmt(data.totals.winners)+' · Призовых мест: '+fmt(data.totals.awards)+' · Начислено: '+fmt(data.totals.bonus/100)+' бонусных ₽'));
   const activity=el('details');activity.append(el('summary','Статистика текущего месяца'));for(const [key,item] of Object.entries(data.activity)){activity.append(el('p',titles[key]+' · участников: '+fmt(item.participants)+' · всего: '+fmt(key==='spend'?item.total/100:item.total)));}box.append(activity);
   const form=el('form'),size=el('input');size.type='number';size.min=3;size.max=200;size.value=data.settings.top_size;const sizeLabel=el('label','Размер топа');sizeLabel.append(size);form.append(sizeLabel);
   const period=el('select'),metric=el('select');period.setAttribute('aria-label','Период наград');metric.setAttribute('aria-label','Номинация наград');for(const [k,t] of Object.entries(kinds)){const o=el('option',t);o.value=k;period.append(o);}for(const [k,t] of Object.entries(titles)){const o=el('option',t);o.value=k;metric.append(o);}
   const rewards=structuredClone(data.settings.rewards),grid=el('div',undefined,'hall-prize-grid');form.append(period,metric,grid);let previousKind=period.value,previousMetric=metric.value;
   function persist(){rewards[previousKind]??={};rewards[previousKind][previousMetric]=[...grid.querySelectorAll('input')].map(i=>Math.round(Number(i.value)*100));}
   function paint(){previousKind=period.value;previousMetric=metric.value;grid.replaceChildren();for(let i=0;i<10;i++){const label=el('label','Место '+(i+1)),input=el('input');input.type='number';input.min=0;input.max=previousMetric==='spend'?2000000:1000000;input.step='0.01';input.value=(rewards[previousKind]?.[previousMetric]?.[i]||0)/100;label.append(input);grid.append(label);}}
   period.onchange=metric.onchange=()=>{persist();paint();};paint();const submit=window.pushokViews.iconAction(el('button',undefined,'primary'),'save','Сохранить настройки');submit.type='submit';const status=el('p',undefined,'muted');status.setAttribute('role','status');
   form.append(el('small','Награды — в бонусных рублях. 0 означает без награды. Новые правила действуют со следующего месяца или года; правила текущих периодов сохраняются.','muted'),submit,status);
   form.onsubmit=async e=>{e.preventDefault();persist();submit.disabled=true;try{await api('/hall/admin/settings',{method:'PUT',body:{top_size:Number(size.value),rewards}});if(valid())status.textContent='Сохранено для следующих периодов.';}catch(err){if(valid())status.textContent=err.message;}finally{submit.disabled=false;}};box.append(form);
   const history=el('details');history.append(el('summary','История итогов и начислений'));for(const p of data.history)history.append(el('p',kinds[p.kind]+' · '+date(p.starts_at)+' · '+(p.closed_at?'Закрыт':'Текущий')+' · '+p.places+' мест · '+fmt(p.bonus/100)+' бонусных ₽'));box.append(history);
  }catch(e){if(valid())target.replaceChildren(el('p',e.message,'muted'),button('Повторить',()=>adminPanel(target,valid)));}
 }
 return {load,medals,frame};
})();
