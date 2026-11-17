'use strict';
// Общие действия и разделы: обработчики форм остаются у исходных элементов.
window.pushokInterface=(()=>{
 const v=()=>window.pushokViews;
 // Эти разделы открываются прямо из блока «Управление» в профиле.
 const managementSections=[
  {key:'payments',title:'Платежи',icon:'wallet',pane:'payments'},
  {key:'tariffs',title:'Тарифы',icon:'calendar',pane:'tariffs'},
  {key:'design',title:'Оформление',icon:'edit',pane:'system'},
  {key:'people',title:'Пользователи и награды',icon:'gift',pane:'system'},
  {key:'automation',title:'ИИ и прокси',icon:'server',pane:'system'},
  {key:'technical',title:'Диагностика',icon:'status',pane:'system'}
 ];
 function managementSection(key){return managementSections.find(item=>item.key===key)||managementSections[0];}
 function showManagement(key){
  const selected=managementSection(key);$('settings').dataset.managementSection=selected.key;
  for(const pane of ['payments','tariffs','system'])$('settings-'+pane).hidden=pane!==selected.pane;
  for(const group of document.querySelectorAll('#settings-system > [data-settings-group]')){
   group.hidden=group.dataset.settingsGroup!==selected.key;
   if(!group.hidden)group.open=true;
  }
  return selected;
 }
 function actions(root=document){
  for(const b of root.querySelectorAll('button')){
   if(b.matches('.user-choice,.person-role,[role=tab],[aria-pressed],.settings-nav button,.campaign-scope button,.hall-tabs button,.profile-stats button,.topic-option,.topic-trigger')||b.closest('.mobile-bottom-nav'))continue;
   if(b.classList.contains('icon-action')){if(!b.querySelector('svg')&&b.textContent.trim())v().iconAction(b,b.dataset.actionIcon||'check',b.textContent.trim());continue;}
   const label=b.getAttribute('aria-label')||b.textContent.trim();
   const name=/^(Сохранить|Применить)/.test(label)?'save':/^(Отмена|Отменить ожидание)$/.test(label)?'close':/^(Обновить|Проверить|Повторить)/.test(label)?'refresh':/^Найти/.test(label)?'search':/^(Добавить|Создать)/.test(label)?'plus':/^(Изменить|Редактировать)/.test(label)?'edit':/^Удалить/.test(label)?'trash':/^Скопировать/.test(label)?'copy':/^Поделиться/.test(label)?'share':/^(Восстановить|Стандартный текст)/.test(label)?'restore':/^(Выйти|Выключить|Включить)/.test(label)?'power':/^(Следующ|Ещё)/.test(label)?'chevron':/^Предыдущ/.test(label)?'back':/^(Подтвердить|Одобрить)/.test(label)?'check':/^(Отклонить|Закрыть)/.test(label)?'close':/^Вернуть/.test(label)?'back':/^(Загрузить|Установить)/.test(label)?'download':/^Открыть/.test(label)?'chevron':/^(Оплатить|Купить|Реквизиты|Посмотреть чеки)/.test(label)?'wallet':/^(Отправить|Принять|Активировать|Разрешить|Записать)/.test(label)?'check':/^(Забронировать|Бронь|Назначить|Через 2 минуты)/.test(label)?'calendar':/^(Продолжить|Возобновить)/.test(label)?'refresh':/^(Назад|К моему|К рабочим)/.test(label)?'back':/^(Журнал|История|Отчёт|Показать последние)/.test(label)?'chart':/^Разобрать/.test(label)?'search':/^Исправить/.test(label)?'edit':/^Вставить/.test(label)?'copy':/^(Подключить|Прикрепить)/.test(label)?'plus':/^(Скачать)/.test(label)?'download':/^Отключить/.test(label)?'power':/^(Отменить|Отозвать|Снять|Скрыть)/.test(label)?'close':/^Показать стартовую/.test(label)?'info':null;
   if(name)v().iconAction(b,name,label);
  }
 }
 function group(parent,key,title,icon){let g=parent.querySelector(':scope > [data-settings-group="'+key+'"]');if(g)return g;g=el('details',undefined,'settings-category');g.dataset.settingsGroup=key;const h=el('summary');h.append(v().icon(icon),el('span',title),v().icon('chevron'));g.append(h);parent.append(g);return g;}
 function wrap(node,parent,title){if(!node||node.closest('.settings-section'))return;const d=el('details',undefined,'settings-section'),h=el('summary',title);d.append(h,node);parent.append(d);}
 function settings(){
  const sys=$('settings-system');if(!sys)return;
  const design=group(sys,'design','Оформление','edit'),people=group(sys,'people','Пользователи и награды','users'),automation=group(sys,'automation','ИИ и прокси','server'),technical=group(sys,'technical','Диагностика','status');
  for(const [id,g,title] of [['home-banner-settings',design,'Текст на главной'],['loading-title-form',design,'Экран загрузки'],['icons-form',design,'Иконки Telegram'],['team-form',people,'Права пользователей'],['birthday-settings',people,'Дни рождения'],['referral-settings',people,'Приглашения и награды'],['channel-ai-settings',automation,'Подключение и права ИИ'],['proxy-provisioning-settings',automation,'Дополнительные прокси'],['proxy-gateway-settings',automation,'Единый вход и интересы'],['default-sponsor-form',automation,'Канал по умолчанию'],['launch-refresh',technical,'Проверка запуска']]){
   const source=$(id);if(source)wrap(source.closest('.core-health')||source,g,title);
  }
  for(const node of [...sys.querySelectorAll(':scope > .core-health')])wrap(node,technical,'Испытания ядра');
  const payments=$('settings-payments');for(const [id,title] of [['sbp-connection','Подключение СБП'],['payment-switches','Способы оплаты'],['wallet-settings','Баланс и пополнения']])wrap($(id),payments,title);
  for(const node of [...$('settings-tariffs').children])if(!node.classList.contains('settings-section'))wrap(node,$('settings-tariffs'),'Тарифы и стоимость');
  if(location.hash.startsWith('#/management/'))showManagement(location.hash.split('/')[2]);
  actions();
 }
 function recent(){const box=$('software-updates');if(!box||box.querySelector('.recent-releases'))return;const section=el('details',undefined,'recent-releases');section.open=true;section.append(el('summary','Последние 5 обновлений'));const versions=window.pushokReleaseNotes||[];for(const r of versions.slice(0,5)){const d=el('details',undefined,'release-item');d.append(el('summary','v'+r.version+' · '+r.title));const list=el('ul');for(const item of r.items)list.append(el('li',item));d.append(list);section.append(d);}const a=el('a','Все обновления ↗');a.href='https://github.com/mrkoster1988/mtproxy-pushok/blob/main/CHANGELOG.md';a.target='_blank';a.rel='noopener noreferrer';section.append(a);box.append(section);}
 let queued=false;new MutationObserver(()=>{if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;actions();});}).observe(document.body,{childList:true,subtree:true});
 actions();recent();return {settings,actions,managementSections,managementSection,showManagement};
})();
