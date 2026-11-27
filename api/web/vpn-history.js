'use strict';
window.pushokVpnHistory=(()=>{
  const labels={sent:'Telegram принял сообщение',waiting_chat:'Бот пока не получил доступ к чату',blocked:'Пользователь заблокировал бота или закрыл аккаунт',
    failed:'Ошибка отправки',retry:'Ожидает повторной отправки',paused:'Рассылка выключена',queued:'В очереди',unknown:'Подтверждение не сохранено',in_app:'В личном чате'};
  const kinds={premium:'Подарочный Premium',renewal_plan:'Смена тарифа',changed:'Изменение подписки',created:'Новая подписка',
    removed:'Отзыв доступа',order:'Заказ',refund:'Возврат',device:'Профиль устройства',link:'Ссылка подключения',reminder:'Напоминание'};
  function status(value){return labels[value]||labels.unknown;}
  function show(target,subject){
    target.replaceChildren();
    if(!/^[a-f0-9-]{36}$/i.test(subject)||(!hasDeveloperAccess(me)&&me?.role!=='admin'&&subject!==me?.id)){
      target.append(el('p','История недоступна.'));return;
    }
    const session=token,box=el('section',undefined,'panel vpn-history'),tabs=el('div',undefined,'vpn-history-tabs');
    const items=el('div'),feedback=el('p',undefined,'muted'),more=el('button','Показать ещё','labeled-action');
    more.type='button';feedback.setAttribute('role','status');
    box.append(tabs,items,feedback,more);target.append(box);
    let revision=0,source='',offset=0;
    async function load(){
      const version=revision;more.disabled=true;feedback.textContent='Загрузка…';
      try{
        const data=await api('/vpn/users/'+subject+'/messages?source='+source+'&offset='+offset);
        if(!box.isConnected||token!==session||version!==revision)return;
        for(const msg of data.items){
          const card=el('article',undefined,'vpn-history-entry'),head=el('header');
          head.append(el('strong',msg.title||kinds[msg.kind]||'VPN'),el('small',new Date(msg.created*1000).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'})+' мск'));
          const state=el('small',status(msg.delivery),'vpn-delivery');state.dataset.delivery=msg.delivery;
          card.append(head,el('p',msg.text),state);
          if(subject===me.id&&['blocked','waiting_chat'].includes(msg.delivery)){
            const link=el('a','Открыть бота уведомлений');link.href='https://t.me/mtproxy_pushok_bot?start=notifications';link.target='_blank';link.rel='noopener noreferrer';card.append(link);
          }
          items.append(card);
        }
        feedback.textContent=items.childElementCount?'':'Сообщений пока нет.';
        offset=data.next;more.hidden=offset==null;more.textContent='Показать ещё';
      }catch(error){if(version===revision&&token===session){
        feedback.textContent='Не удалось загрузить историю.';more.hidden=false;more.textContent='Повторить';
      }}finally{if(version===revision)more.disabled=false;}
    }
    more.onclick=load;
    for(const [key,title] of [['vpn','Подписка'],['telegram','Уведомления'],['support','Поддержка']]){
      const tab=el('button',title,'labeled-action');tab.type='button';tabs.append(tab);
      tab.onclick=()=>{revision++;source=key;offset=0;items.replaceChildren();more.hidden=true;
        for(const item of tabs.children)item.setAttribute('aria-pressed',String(item===tab));
        void load();};
    }
    tabs.firstElementChild.click();
  }
  return {status,show};
})();
