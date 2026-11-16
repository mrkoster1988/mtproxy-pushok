'use strict';
window.pushokCampaignPricing=(()=>{
 const messages={campaign_pricing_locked:'Условия можно менять только до оплаты и назначения размещения.',
  campaign_pricing_proof_pending:'Сначала проверьте загруженный чек.',
  campaign_pricing_invoice_open:'По заявке уже открыт счёт или обрабатывается платёж. Дождитесь подтверждённой отмены либо завершения оплаты.',
  campaign_pricing_changed:'Условия уже изменились. Закройте и снова откройте «Тариф и скидка».',
  campaign_pricing_same_tariff:'Выберите другой тариф. Для изменения цены используйте скидку.',
  campaign_pricing_tariff_unavailable:'Текущий тариф отключён. Выберите другой тариф.',
  campaign_pricing_stars_required:'Для заявки из Mini App нужен тариф с ценой в Stars.'};
 Object.assign(errors,messages);
 function amount(rub,stars){return money(rub)+(stars===null?'':' · '+stars+' XTR');}
 function field(parent,title,node){node.setAttribute('aria-label',title);const label=el('label',title);label.append(node);parent.append(label);return node;}
 function reduced(value,percent,rub=false){if(value===null)return null;if(percent===100||value===0)return 0;const n=Math.ceil(value*(100-percent)/100);return rub?Math.max(Math.min(value,100),n):n;}
 function create(c){
  const root=el('details',undefined,'campaign-pricing'),summary=el('summary','Тариф и скидка'),content=el('div');root.append(summary,content);
  const session=token;let loading=false;
  async function load(){
   if(!root.open||loading)return;loading=true;content.replaceChildren(el('p','Загрузка условий…','muted'));
   try{
    const state=await api('/admin/campaigns/'+c.id+'/pricing');
    if(!root.isConnected||session!==token||me?.role!=='admin')return;
    render(state);
   }catch(e){if(root.isConnected&&session===token)content.replaceChildren(el('p',e.message),button('Повторить',load));}
   finally{loading=false;}
  }
  root.addEventListener('toggle',load);
  function render(state){
   content.replaceChildren();const current=state.current;
   content.append(el('p',current.name+' · '+amount(current.price_kopecks,current.stars_price_xtr),'pricing-current'));
   if(!state.can_edit)content.append(el('p',messages[state.blocked_reason]||messages.campaign_pricing_locked,'muted'));
   else{
    const form=el('form'),fields=el('fieldset'),grid=el('div',undefined,'pricing-fields');fields.append(grid);form.append(fields);content.append(form);
    const kind=field(grid,'Изменить условия',el('select'));option(kind,'discount','Скидка');option(kind,'tariff','Тариф');
    const percent=field(grid,'Скидка, %',el('input'));percent.type='number';percent.min='0';percent.max='100';percent.step='1';percent.required=true;percent.value=String(current.admin_discount_percent??0);
    const tariff=field(grid,'Новый тариф',el('select'));option(tariff,'','Выберите тариф');
    for(const t of state.tariffs.filter(t=>t.id!==current.tariff_id))option(tariff,t.id,t.name+' · '+amount(t.price_kopecks,t.stars_price_xtr));
    const preview=el('p',undefined,'pricing-preview');preview.setAttribute('role','status');fields.append(preview);
    const hint=el('p',undefined,'muted');fields.append(hint);
    const reason=field(fields,'Причина изменения',el('input'));reason.required=true;reason.minLength=3;reason.maxLength=500;reason.placeholder='Например: скидка постоянному клиенту';
    const submit=el('button','Сохранить','primary');submit.type='submit';fields.append(submit);
    const feedback=el('p',undefined,'pricing-feedback');feedback.setAttribute('role','status');form.append(feedback);
    function previewPrice(){
     const discount=kind.value==='discount';percent.closest('label').hidden=!discount;percent.disabled=!discount;tariff.closest('label').hidden=discount;tariff.disabled=discount;tariff.required=!discount;
     const next=discount?(percent.validity.valid?{price_kopecks:reduced(current.base_price_kopecks,Number(percent.value),true),stars_price_xtr:reduced(current.base_stars_price_xtr,Number(percent.value))}:null):state.tariffs.find(t=>t.id===tariff.value);
     preview.textContent=next?'Итого: '+amount(next.price_kopecks,next.stars_price_xtr):(discount?'Введите скидку от 0 до 100%':'Выберите новый тариф');
     hint.textContent=discount?'Только эта заявка. 0% — убрать скидку, 100% — бесплатно. Заменяет прежнюю скидку; ₽ и Stars считаются отдельно.':'Цена и длительность — по новому тарифу. Прежняя скидка и бронь времени будут сняты.';
    }
    for(const input of [kind,percent,tariff])input.addEventListener('input',previewPrice);previewPrice();
    form.addEventListener('submit',async event=>{
     event.preventDefault();if(fields.disabled||!form.reportValidity())return;
     const body={revision:state.revision,reason:reason.value.trim(),...(kind.value==='discount'?{discount_percent:Number(percent.value)}:{tariff_id:tariff.value})};
     fields.disabled=true;feedback.textContent='Сохраняем…';
     try{
      if(session!==token||me?.role!=='admin')return;
      await api('/admin/campaigns/'+c.id+'/pricing',{method:'PATCH',body});
      if(session!==token||me?.role!=='admin')return;
      try{await refresh();notice('Условия заявки обновлены.',true);}
      catch{content.replaceChildren(el('p','Условия сохранены. Обновите данные перед следующей правкой.'),button('Обновить',load));}
     }catch(e){if(session===token&&root.isConnected)feedback.textContent=e.message;}
     finally{fields.disabled=false;}
    });
   }
   if(state.history?.length){
    const history=el('details',undefined,'pricing-history');history.append(el('summary','История изменений'));
    for(const row of state.history){const data=row.details_json,item=el('p');item.append(el('strong',date(row.created_at)),document.createTextNode(' · '+data.before.name+' → '+data.after.name+' · '+amount(data.before.price_kopecks,data.before.stars_price_xtr)+' → '+amount(data.after.price_kopecks,data.after.stars_price_xtr)),el('span',data.reason,'muted'));history.append(item);}content.append(history);
   }
  }
  return root;
 }
 return {create};
})();
