'use strict';
window.pushokHall=(()=>{
 const kinds={week:'Неделя',month:'Месяц',year:'Год'},titles={invited:'Приглашённые',paid_referrals:'Платящие приглашённые',spend_rub:'Расходы · ₽',spend_xtr:'Расходы · Stars',paid_campaigns:'Оплаченные кампании'};
 const fmt=n=>Number(n).toLocaleString('ru-RU'),month=d=>new Date(d).toLocaleDateString('ru-RU',{month:'long',year:'numeric',timeZone:'Europe/Moscow'});
 function frame(node,rank){if(!node||!rank)return;node.classList.add('hall-frame',rank===1?'hall-gold':rank===2?'hall-silver':rank===3?'hall-bronze':'hall-teal');node.title='Место '+rank;}
 function medals(target,data){
  if(!data.medals?.length)return;const box=el('details',undefined,'hall-medals'),head=el('summary','Медали · '+data.medals.length);box.append(head);
  for(const m of data.medals){const row=el('div',undefined,'hall-medal');row.append(el('span',['','🥇','🥈','🥉'][m.rank]),el('span',month(m.starts_at)+' · '+(titles[m.metric]||m.metric)),el('strong','#'+m.rank));box.append(row);}target.append(box);
 }
 async function load(target){
  const session=token;let kind='month',metric='invited',period='',serial=0;
  target.replaceChildren();const controls=el('div',undefined,'hall-controls'),tabs=el('nav',undefined,'hall-tabs'),select=el('select'),history=el('select'),body=el('section',undefined,'hall-board'),admin=el('section');
  select.setAttribute('aria-label','Номинация');history.setAttribute('aria-label','Период рейтинга');
  for(const [key,title] of Object.entries(titles)){const o=el('option',title);o.value=key;select.append(o);}select.value=metric;
  for(const [key,title] of Object.entries(kinds)){const b=button(title,()=>{kind=key;period='';void refresh();});b.dataset.kind=key;tabs.append(b);}
  select.onchange=()=>{metric=select.value;void refresh();};history.onchange=()=>{period=history.value;void refresh();};controls.append(tabs,select,history);target.append(controls,body,admin);
  const help=el('details',undefined,'context-help');help.append(el('summary','ⓘ Как считаем и награждаем'),el('p','Календарные периоды по Москве; неделя — с понедельника. Учитываются зарегистрированные приглашённые и реальные оплаты рекламы. Бонусные, ручные, тестовые и возвращённые оплаты не участвуют. Платящий приглашённый учитывается в период первой действующей оплаты. Сотрудники и заблокированные пользователи не участвуют. При равенстве баллов выше тот, у кого раньше первая учитываемая активность; затем порядок по ID. Итоги и бонусы фиксируются после закрытия периода, последующие возвраты не меняют уже закрытые итоги. Награды за разные номинации и периоды суммируются. Рамка профиля — лучший результат последнего закрытого месяца; медали топ-3 сохраняются в истории.'));target.append(help);
  function valid(){return session===token&&target.isConnected&&location.hash.startsWith('#/hall');}
  async function refresh(){
   const request=++serial;body.replaceChildren(el('p','Загрузка…','muted'));[...tabs.children].forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.kind===kind)));
   try{const data=await api('/hall?kind='+kind+'&metric='+metric+(period?'&period_id='+period:''));if(!valid()||request!==serial)return;
    history.replaceChildren();const current=el('option','Текущий период');current.value='';history.append(current);for(const p of data.periods){const o=el('option',date(p.starts_at)+' — '+date(p.ends_at));o.value=p.id;history.append(o);}history.value=period;
    const heading=el('div',undefined,'hall-heading');heading.append(el('h2','Топ-'+data.top_size),el('span',data.closed?'Итоги':'Предварительные результаты','muted'));body.replaceChildren(heading,el('small',date(data.starts_at)+' — '+date(data.ends_at),'muted'));
    if(data.rewards.some(n=>n>0))body.append(el('p','Призы: '+data.rewards.map((n,i)=>n?'#'+(i+1)+' · '+fmt(n/100)+' бонусных ₽':null).filter(Boolean).join(' / '),'hall-prizes'));
    if(!data.rows.length)body.append(el('p','Пока нет участников с результатом. Учёт начат '+date(data.started_at)+'.','empty'));
    for(const row of data.rows){const line=el('article',undefined,'hall-row'),avatar=window.pushokViews.userAvatar(row.user_id,row.first_name||row.username||'',true),copy=el('div',undefined,'hall-person');frame(avatar,row.rank<=10?row.rank:null);
     const name=[row.first_name,row.last_name].filter(Boolean).join(' ')||row.username||'Участник';copy.append(el('strong',name));if(row.username&&/^[A-Za-z0-9_]+$/.test(row.username)){const a=el('a','@'+row.username);a.href='https://t.me/'+row.username;a.target='_blank';a.rel='noopener noreferrer';copy.append(a);}
     const value=el('div',undefined,'hall-value');value.append(el('strong',metric==='spend_rub'?fmt(row.score/100)+' ₽':fmt(row.score)+(metric==='spend_xtr'?' XTR':'')));if(row.bonus_amount)value.append(el('small','+'+fmt(row.bonus_amount/100)+' бонусных ₽'));
     line.append(el('span',String(row.rank),'hall-rank'),avatar,copy,value);body.append(line);
    }
   }catch(e){if(valid()&&request===serial)body.replaceChildren(el('p',e.message,'empty'),button('Повторить',refresh));}
  }
  await refresh();if(me?.role==='admin'&&valid())await adminPanel(admin,valid);
 }
 async function adminPanel(target,valid){
  try{const data=await api('/hall/admin');if(!valid())return;const box=el('details',undefined,'hall-admin panel');box.append(el('summary','Управление и статистика'));target.replaceChildren(box);
   box.append(el('p','Победителей: '+fmt(data.totals.winners)+' · Призовых мест: '+fmt(data.totals.awards)+' · Начислено: '+fmt(data.totals.bonus/100)+' бонусных ₽'));
   const activity=el('details');activity.append(el('summary','Статистика текущего месяца'));for(const [key,item] of Object.entries(data.activity)){activity.append(el('p',titles[key]+' · участников: '+fmt(item.participants)+' · всего: '+fmt(key==='spend_rub'?item.total/100:item.total)));}box.append(activity);
   const form=el('form'),size=el('input');size.type='number';size.min=3;size.max=200;size.value=data.settings.top_size;const sizeLabel=el('label','Размер топа');sizeLabel.append(size);form.append(sizeLabel);
   const period=el('select'),metric=el('select');period.setAttribute('aria-label','Период наград');metric.setAttribute('aria-label','Номинация наград');for(const [k,t] of Object.entries(kinds)){const o=el('option',t);o.value=k;period.append(o);}for(const [k,t] of Object.entries(titles)){const o=el('option',t);o.value=k;metric.append(o);}
   const rewards=structuredClone(data.settings.rewards),grid=el('div',undefined,'hall-prize-grid');form.append(period,metric,grid);let previousKind=period.value,previousMetric=metric.value;
   function persist(){rewards[previousKind]??={};rewards[previousKind][previousMetric]=[...grid.querySelectorAll('input')].map(i=>Math.round(Number(i.value)*100));}
   function paint(){previousKind=period.value;previousMetric=metric.value;grid.replaceChildren();for(let i=0;i<10;i++){const label=el('label','Место '+(i+1)),input=el('input');input.type='number';input.min=0;input.max=1000000;input.step='0.01';input.value=(rewards[previousKind]?.[previousMetric]?.[i]||0)/100;label.append(input);grid.append(label);}}
   period.onchange=metric.onchange=()=>{persist();paint();};paint();const submit=el('button','Сохранить настройки','primary');submit.type='submit';const status=el('p',undefined,'muted');status.setAttribute('role','status');
   form.append(el('small','Награды — в бонусных рублях. 0 означает без награды. Новые правила действуют со следующей недели, месяца или года; правила текущих периодов сохраняются.','muted'),submit,status);
   form.onsubmit=async e=>{e.preventDefault();persist();submit.disabled=true;try{await api('/hall/admin/settings',{method:'PUT',body:JSON.stringify({top_size:Number(size.value),rewards})});if(valid())status.textContent='Сохранено для следующих периодов.';}catch(err){if(valid())status.textContent=err.message;}finally{submit.disabled=false;}};box.append(form);
   const history=el('details');history.append(el('summary','История итогов и начислений'));for(const p of data.history)history.append(el('p',kinds[p.kind]+' · '+date(p.starts_at)+' · '+(p.closed_at?'Закрыт':'Текущий')+' · '+p.places+' мест · '+fmt(p.bonus/100)+' бонусных ₽'));box.append(history);
  }catch(e){if(valid())target.replaceChildren(el('p',e.message,'muted'),button('Повторить',()=>adminPanel(target,valid)));}
 }
 return {load,medals,frame};
})();
