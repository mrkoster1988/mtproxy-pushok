'use strict';
window.pushokVpnPlans=(()=>{
  function label(title,input){
    const node=el('label',title,'vpn-field');node.append(input);return node;
  }
  function option(value,text){
    const node=el('option',text);node.value=value;return node;
  }
  function catalog(host,data){
    const plans=data.plans,form=el('form',null,'vpn-policy-card'),select=el('select');
    const fields={},enabled=el('input'),renew=el('input');
    let current=null;
    form.append(el('h2','Тарифы VPN'),label('Тариф',select));
    select.append(...plans.map(p=>option(p.id,p.name)),option('','＋ Создать тариф'));
    const grid=el('div',null,'vpn-rule-grid');
    const specs=[
      ['id','Код','text',32],['name','Название','text',80],['days','Срок, дней','number',3650],
      ['price','Цена, ₽','number',1000000],['stars','Цена, Stars','number',100000],
      ['device_limit','Дополнительные профили','number',20],['free_activation_limit','Лимит бесплатных активаций','number',1000000],
      ['free_traffic_gb','Квота бесплатного тарифа, ГБ','number',10000],['free_max_renewals','Количество бесплатных продлений','number',12]
    ];
    for(const [key,title,type,max] of specs){
      const input=el('input');fields[key]=input;input.type=type;input.required=true;
      if(type==='number'){input.min=key==='days'?1:0;input.max=max;input.step=1;}
      else input.maxLength=max;
      if(key==='id')input.pattern='[a-z][a-z0-9_\\-]*';
      grid.append(label(title,input));
    }
    enabled.type=renew.type='checkbox';
    const renewLabel=label('Автопродление бесплатного тарифа',renew),enabledLabel=label('Доступен для выбора',enabled);
    enabledLabel.classList.add('vpn-switch');renewLabel.classList.add('vpn-switch');
    const freeFields=['free_activation_limit','free_traffic_gb','free_max_renewals'];
    function paintFree(){
      const free=Number(fields.price.value)===0&&Number(fields.stars.value)===0;
      for(const key of freeFields){fields[key].disabled=!free;fields[key].parentElement.hidden=!free;}
      renewLabel.hidden=!free;renew.disabled=!free;
    }
    const save=el('button','Сохранить тариф','ui-button labeled-action'),note=el('small',null,'muted');save.type='submit';note.setAttribute('role','status');
    function paint(){
      current=plans.find(p=>p.id===select.value);
      for(const [key,,type] of specs)fields[key].value=current?.[key]??({days:30,device_limit:4}[key]??(type==='number'?0:''));
      fields.id.disabled=!!current;enabled.checked=!!current?.enabled;renew.checked=!!current?.free_auto_renew;
      note.textContent='';paintFree();
    }
    form.append(grid,enabledLabel,renewLabel,save,note);select.onchange=paint;fields.price.oninput=fields.stars.oninput=paintFree;
    form.onsubmit=async e=>{
      e.preventDefault();if(save.disabled||!form.reportValidity())return;
      const body={enabled:enabled.checked,free_auto_renew:!renew.disabled&&renew.checked};
      for(const [key,,type] of specs)body[key]=type==='number'?(fields[key].disabled?0:Number(fields[key].value)):fields[key].value.trim();
      if(current)body.version=current.version;
      save.disabled=true;select.disabled=true;
      try{
        const result=await api('/vpn/settings/plans/'+(current?'update':'create'),{method:'POST',body});
        if(current){plans[plans.indexOf(current)]=result;select.selectedOptions[0].textContent=result.name;}
        else{plans.push(result);select.insertBefore(option(result.id,result.name),select.lastElementChild);}
        select.value=result.id;paint();note.textContent='Тариф сохранён.';
      }catch(error){note.textContent=error.message;}finally{save.disabled=select.disabled=false;}
    };
    host.replaceChildren(form);paint();
  }
  function assignment(row,subject,reload){
    const section=el('details',null,'vpn-rule'),content=el('div',null,'vpn-rule-body');
    section.append(el('summary','Изменить тариф'),content);
    let loaded=false;
    section.ontoggle=async()=>{
      if(!section.open||loaded)return;loaded=true;content.textContent='Загружаем тарифы…';
      try{
        const data=await api('/vpn/settings/plans'),plans=data.plans.filter(p=>p.enabled&&!p.free_traffic_gb);
        if(data.premium)plans.unshift(data.premium);
        if(row.tariff_retry){
          const choice=row.tariff_retry;
          plans.splice(0,plans.length,{id:choice.plan_id,name:choice.name,version:choice.plan_version,retry:true});
        }
        if(!plans.length){content.textContent='В каталоге пока нет доступных тарифов WireGuard.';return;}
        const select=el('select');select.append(option('','Выберите тариф'),...plans.map(p=>option(p.id,p.name)));
        const terms=el('small',null,'muted'),save=el('button','Назначить тариф','ui-button labeled-action'),note=el('small',null,'muted');
        save.type='button';save.disabled=true;note.setAttribute('role','status');
        select.onchange=()=>{const plan=plans.find(p=>p.id===select.value);save.disabled=!plan;
          terms.textContent=plan?.retry?'Повторим начатое изменение.':plan?.lifetime?'Подарок за счёт заведения · без срока и оплаты':plan?plan.days+' дн. · '+plan.price+' ₽ / '+plan.stars+' ⭐ · доп. профилей '+plan.device_limit:'';};
        save.onclick=async()=>{const p=plans.find(p=>p.id===select.value);if(!p||save.disabled)return;
          save.disabled=select.disabled=true;
          try{await api('/vpn/settings/plans/choice',{method:'POST',body:{subject,subscription_id:row.subscription_id,
            plan_id:p.id,plan_version:p.version,version:row.renewal_version||0,before:row.expires_at||0}});
            notice('Тариф изменён.',true);await reload();}
          catch(error){note.textContent=error.message;}finally{select.disabled=false;save.disabled=false;}};
        const hint=el('small',null,'muted');
        const update=select.onchange;select.onchange=()=>{update();const p=plans.find(p=>p.id===select.value);
          hint.textContent=!p||p.retry?'':p.lifetime?'Пользователь получит поздравление от Пушка.':row.is_premium?'Бессрочный доступ сменится выбранным тарифом. Новый срок начнётся сегодня.':'Дата окончания сохранится, списания не будет.';};
        content.replaceChildren(label('Новый тариф',select),terms,hint,save,note);
        if(row.tariff_retry){select.value=plans[0].id;select.onchange();save.textContent='Повторить назначение';}
      }catch(error){content.textContent=error.message;loaded=false;}
    };
    return section;
  }
  return {catalog,assignment};
})();
