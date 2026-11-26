'use strict';
window.pushokVpnRenew = (() => {
  const orderKey = () => 'pushok-vpn-order:'+me.id;
  function save(order) { try { sessionStorage.setItem(orderKey(),JSON.stringify(order)); } catch {} }
  function saved() { try { return JSON.parse(sessionStorage.getItem(orderKey())); } catch { return null; } }
  function clear() { try { sessionStorage.removeItem(orderKey()); } catch {} }
  async function show(target) {
    const session=token, box=el('article',undefined,'campaign-tile vpn-renew');
    target.replaceChildren(box);
    const visible=()=>token===session && box.isConnected && location.hash==='#/vpn/renew';
    const note=el('p','Загружаем тарифы…','muted'); note.setAttribute('role','status'); box.append(note);
    let revision=0;
    async function receipt(id,attempt=0) {
      const turn=++revision;
      if (!visible()||turn!==revision) return;
      let order;
      try { order=await api('/vpn/orders/'+encodeURIComponent(id)); }
      catch(e) {
        if (!visible()||turn!==revision) return;
        if (e.status===404) {clear(); return show(target);}
        box.replaceChildren(el('p',e.message),button('Проверить заказ',()=>receipt(id))); return;
      }
      if (!visible()||turn!==revision) return;
      const done=order.status==='active',paid=order.status==='paid',canceled=order.status==='canceled';
      const message=done?'Подписка продлена':paid?'Оплата получена':canceled?'Заказ отменён':'Ожидаем подтверждение оплаты';
      box.replaceChildren(el('h2',message),el('p',order.amount+' ⭐ · Заказ № '+id.slice(0,8),'muted'));
      if (done || canceled) {
        clear();window.pushokVpn.reset();
        box.append(button(done?'Моя подписка':'К тарифам',()=>done?window.cabinet.go('vpn'):show(target)));
        return;
      }
      if (paid) box.append(el('p','Магазин обновляет срок подписки.'));
      const previous=saved();
      if(order.status==='pending' && (order.payment_url || previous?.order===id && previous.url) && window.pushokMiniApp?.canPay()) {
        box.append(button('Открыть счёт',async()=>{
          if(turn!==revision)return;
          ++revision;
          try { await window.pushokMiniApp.payInvoice(order.payment_url||previous.url); }
          catch(e) { if(visible())box.append(el('p',e.message)); }
          if(visible())await receipt(id);
        }));
      }
      box.append(button('Проверить оплату',()=>receipt(id)));
      if (!paid) box.append(button('К тарифам',()=>{clear();return show(target);}));
      if (attempt<8) setTimeout(()=>{if(visible()&&turn===revision)void receipt(id,attempt+1);},3000);
    }
    const prior=saved();
    if (prior?.order && /^[a-f0-9-]{36}$/.test(prior.order)) return receipt(prior.order);
    let catalog;
    try { catalog=await api('/vpn/plans'); }
    catch(e) { if(visible())box.replaceChildren(el('p',e.message),button('Повторить',()=>show(target)));return; }
    if(!visible())return;
    if(catalog.premium){box.replaceChildren(el('h2','Ваша подписка'),el('p','Продление сейчас не требуется. Подробности — в карточке VPN.'),button('Моя подписка',()=>window.cabinet.go('vpn')));return;}
    const choices=el('fieldset',undefined,'vpn-plans'); choices.append(el('legend','Срок продления'));
    const terms=el('details',undefined,'vpn-terms');
    terms.append(el('summary','Условия оплаты'),el('p',catalog.terms||''));
    const consent=el('label',undefined,'vpn-consent'),check=el('input');check.type='checkbox';
    consent.append(check,document.createTextNode('Принимаю условия оплаты'));
    let selected=catalog.plans.find(p=>p.id===catalog.preferred_plan)||catalog.plans[0],busy=false,requestId=prior?.request_id||crypto.randomUUID();
    if (prior?.plan) selected=catalog.plans.find(p=>p.id===prior.plan)||selected;
    const pay=el('button',undefined,'campaign-pay-action');pay.type='button';note.textContent='';
    function update(){pay.textContent=selected?'Оплатить '+selected.amount+' ⭐':'Оплатить';pay.disabled=busy||!selected||!check.checked||!catalog.available;}
    for(const plan of catalog.plans){
      const label=el('label',undefined,'vpn-plan'),radio=el('input'),copy=el('span');
      radio.type='radio';radio.name='vpn-plan';radio.checked=plan===selected;
      radio.onchange=()=>{selected=plan;requestId=crypto.randomUUID();update();};
      copy.append(el('strong',plan.name),el('small',plan.days+' дн.'));label.append(radio,copy,el('b',plan.amount+' ⭐'));choices.append(label);
    }
    check.onchange=update;update();
    pay.onclick=async()=>{
      if(busy||!selected||!check.checked||!visible())return;
      if(!window.pushokMiniApp?.canPay()) { note.textContent='Для оплаты откройте эту вкладку в Mini App Telegram.';return; }
      busy=true;choices.disabled=check.disabled=true;update();note.textContent='Готовим счёт…';
      save({plan:selected.id,request_id:requestId});
      try {
        const order=await api('/vpn/checkout',{method:'POST',body:{plan:selected.id,plan_version:selected.version,amount:selected.amount,
          policy_version:catalog.policy_version,accept_terms:true,request_id:requestId}});
        if(!visible())return;
        if(['active','paid','checkout'].includes(order.status)){save({order:order.id});return receipt(order.id);}
        if(order.status==='canceled'){requestId=crypto.randomUUID();throw Error('Этот заказ отменён. Выберите тариф ещё раз.');}
        save({order:order.id,url:order.payment_url});
        const result=await window.pushokMiniApp.payInvoice(order.payment_url);
        if(!visible())return;
        if(result==='cancelled'||result==='failed'){await receipt(order.id);return;}
        await receipt(order.id);
      } catch(e) { if(visible()){note.textContent=e.message;if(e.code==='vpn_offer_changed')box.append(button('Обновить тарифы',()=>{clear();return show(target);}));} }
      finally {busy=false;choices.disabled=check.disabled=false;update();}
    };
    box.replaceChildren(choices);
    if(!catalog.plans.length || !catalog.available) { box.append(el('p',catalog.unavailable_reason||'Платное продление сейчас недоступно.','muted'));return; }
    box.append(terms,consent,pay,note);
  }
  return {show};
})();
