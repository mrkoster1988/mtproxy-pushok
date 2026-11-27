'use strict';
window.pushokVpnRenew = (() => {
  const orderKey = () => 'pushok-vpn-order:'+me.id;
  function save(order) {
    try { sessionStorage.setItem(orderKey(),JSON.stringify(order)); } catch {}
    if(order.order)try { localStorage.setItem(orderKey(),order.order); } catch {}
  }
  function saved() {
    let current,id;
    try { current=JSON.parse(sessionStorage.getItem(orderKey())); } catch {}
    try { id=localStorage.getItem(orderKey()); } catch {}
    return id && current?.order!==id ? {order:id} : current;
  }
  function clear() {
    try { sessionStorage.removeItem(orderKey()); } catch {}
    try { localStorage.removeItem(orderKey()); } catch {}
  }
  async function show(target,embedded=false) {
    const session=token, box=el('article',undefined,'campaign-tile vpn-renew');
    target.replaceChildren(box);
    const route=location.hash;
    const visible=()=>token===session && box.isConnected && location.hash===route;
    const again=()=>show(target,embedded);
    const note=el('p','Загружаем тарифы…','muted'); note.setAttribute('role','status'); box.append(note);
    let revision=0;
    async function openOrder(id,url) {
      ++revision;
      save({order:id,url});
      let result;
      try { result=await window.pushokMiniApp.payInvoice(url); }
      catch { result='failed'; }
      if(!visible())return;
      save({order:id,url,result});
      return receipt(id);
    }
    async function receipt(id,attempt=0) {
      const turn=++revision;
      if (!visible()||turn!==revision) return;
      let order;
      try { order=await api('/vpn/orders/'+encodeURIComponent(id)); }
      catch(e) {
        if (!visible()||turn!==revision) return;
        if (e.status===404) {clear(); return again();}
        box.replaceChildren(el('p',e.message),button('Проверить заказ',()=>receipt(id))); return;
      }
      if (!visible()||turn!==revision) return;
      const done=order.status==='active',paid=order.status==='paid',canceled=order.status==='canceled';
      const previous=saved(),result=previous?.order===id?previous.result:null;
      const confirming=order.status==='checkout'||result==='pending'||result==='paid';
      const message=done?'Подписка продлена':paid?'Оплата получена':canceled?'Заказ отменён':confirming?'Проверяем оплату':'Счёт готов к оплате';
      box.replaceChildren(el('h2',message),el('p',order.amount+' ⭐ · Заказ № '+id.slice(0,8),'muted'));
      if (done || canceled) {
        clear();window.pushokVpn.reset();
        box.append(button(done?'Вернуться в VPN':'К тарифам',()=>done?window.cabinet.go('vpn'):again()));
        return;
      }
      if (paid) box.append(el('p','Магазин обновляет срок подписки.'));
      const url=order.payment_url||(previous?.order===id && previous.url);
      if(order.status==='pending' && !confirming) {
        if(result==='failed')box.append(el('p','Telegram не завершил оплату. Этот счёт можно открыть повторно.'));
        if(result==='cancelled')box.append(el('p','Вы закрыли форму. Счёт сохранён.'));
        if(url && window.pushokMiniApp?.canPay())box.append(button('Открыть счёт',()=>turn===revision && openOrder(id,url)));
      }
      box.append(button('Проверить оплату',()=>receipt(id)));
      if(confirming && !paid && attempt>=8)box.append(el('p','Подтверждение задерживается. Проверьте этот заказ позже.','muted'));
      if (!paid && !confirming) box.append(button('К тарифам',()=>{clear();return again();}));
      if ((paid||confirming) && attempt<8) setTimeout(()=>{if(visible()&&turn===revision)void receipt(id,attempt+1);},3000);
    }
    const prior=saved();
    if (prior?.order && /^[a-f0-9-]{36}$/.test(prior.order)) return receipt(prior.order);
    let catalog;
    try { catalog=await api('/vpn/plans'); }
    catch(e) { if(visible())box.replaceChildren(el('p',e.message),button('Повторить',again));return; }
    if(!visible())return;
    if(catalog.premium&&embedded){target.replaceChildren();return;}
    if(catalog.premium){box.replaceChildren(el('h2','Ваша подписка'),el('p','Продление сейчас не требуется. Подробности — в карточке VPN.'),button('Вернуться в VPN',()=>window.cabinet.go('vpn')));return;}
    const choices=el('fieldset',undefined,'vpn-plans'),group='vpn-plan-'+crypto.randomUUID();
    choices.append(el('legend',embedded?'Тарифы VPN':'Срок продления'));
    const terms=el('details',undefined,'vpn-terms');
    terms.append(el('summary','Условия оплаты'),el('p',catalog.terms||''));
    const consent=el('label',undefined,'vpn-consent'),check=el('input');check.type='checkbox';
    consent.append(check,document.createTextNode('Принимаю условия оплаты'));
    let selected=catalog.plans.find(p=>p.id===catalog.preferred_plan)||catalog.plans[0],busy=false,requestId=prior?.request_id||crypto.randomUUID();
    if (prior?.plan) selected=catalog.plans.find(p=>p.id===prior.plan)||selected;
    const pay=el('button',undefined,'campaign-pay-action labeled-action');pay.type='button';note.textContent='';
    function update(){pay.textContent=selected?'Оплатить '+selected.amount+' ⭐':'Оплатить';pay.disabled=busy||!selected||!catalog.available;}
    for(const plan of catalog.plans){
      const label=el('label',undefined,'vpn-plan'),radio=el('input'),copy=el('span');
      radio.type='radio';radio.name=group;radio.checked=plan===selected;
      radio.onchange=()=>{selected=plan;requestId=crypto.randomUUID();update();};
      label.classList.add('flow-tariff');
      if(plan.days>=90)label.classList.add('long-term');
      copy.append(el('strong',plan.name),el('b',plan.amount+' ⭐'),el('small',plan.days+' дн.'));
      if(plan.days>30)copy.append(el('small',(plan.amount*30/plan.days).toLocaleString('ru-RU',{maximumFractionDigits:2})+' ⭐ за 30 дней'));
      label.append(radio,copy);choices.append(label);
    }
    check.onchange=()=>{note.textContent='';};update();
    pay.onclick=async()=>{
      if(busy||!selected||!catalog.available||!visible())return;
      if(!check.checked){
        note.textContent='Прочитайте условия и отметьте согласие. Затем нажмите «Оплатить».';
        terms.open=true;check.focus();return;
      }
      if(!window.pushokMiniApp?.canPay()) { note.textContent='Для оплаты откройте эту вкладку в Mini App Telegram.';return; }
      busy=true;choices.disabled=check.disabled=true;update();note.textContent='Готовим счёт…';
      save({plan:selected.id,request_id:requestId});
      try {
        const order=await api('/vpn/checkout',{method:'POST',body:{plan:selected.id,plan_version:selected.version,amount:selected.amount,
          policy_version:catalog.policy_version,accept_terms:true,request_id:requestId}});
        if(session!==token)return;
        if(order.id&&order.status!=='canceled')save({order:order.id,url:order.payment_url});
        if(!visible())return;
        if(['active','paid','checkout'].includes(order.status))return receipt(order.id);
        if(order.status==='canceled'){requestId=crypto.randomUUID();throw Error('Этот заказ отменён. Выберите тариф ещё раз.');}
        await openOrder(order.id,order.payment_url);
      } catch(e) { if(visible()){note.textContent=e.message;if(e.code==='vpn_offer_changed')box.append(button('Обновить тарифы',()=>{clear();return again();}));} }
      finally {busy=false;choices.disabled=check.disabled=false;update();}
    };
    box.replaceChildren(choices);
    if(!catalog.plans.length || !catalog.available) { box.append(el('p',catalog.unavailable_reason||'Платное продление сейчас недоступно.','muted'));return; }
    box.append(pay,consent,terms,note);
  }
  return {show};
})();
