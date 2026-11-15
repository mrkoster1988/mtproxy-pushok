"""События жизненного цикла отправляются отдельными Telegram-сообщениями."""
from uuid import UUID
from .advertiser_notices import deliver_advertiser_notices

TEXTS={
 'started':'✅ Рекламная кампания запущена. Подробности размещения — в карточке заявки.',
 'completed':'🏁 Рекламная кампания завершена. Кампания перенесена в архив. Отчёт сохранён.',
 'failed':'⚠️ Зафиксирован технический сбой размещения. Заявка сохранена, система повторит проверку. Подробности доступны администратору в диагностике.',
}
REASONS={
 'apply_failed':'Не удалось применить тег', 'restore_failed':'Не подтверждено снятие/восстановление тега',
 'ack_unavailable':'Не удалось сохранить подтверждение агента','runtime_unavailable':'План кабинета недоступен агенту',
 'binding_missing':'Не подготовлен тег канала','binding_changed':'Привязка тега устарела',
 'default_binding_missing':'Не подготовлен тег канала по умолчанию','conflicting_campaigns':'Пересекаются периоды кампаний',
}

def render_lifecycle_notice(item):
    event=item.get('event_type')
    if event not in TEXTS:
        raise ValueError('invalid_lifecycle_event')
    lines=['🐱 MTProxy Pushok',TEXTS[event]]
    title=item.get('channel_title')
    if isinstance(title,str):lines.append('Канал: '+' '.join(title.split())[:160])
    if item.get('campaign_id'):lines.append('Заявка: '+str(UUID(str(item['campaign_id'])))[:8])
    if item.get('audience')=='staff' and item.get('reason') in REASONS:lines.append('Причина: '+REASONS[item['reason']])
    return '\n\n'.join(lines)


def deliver_lifecycle_notices(api,sender,miniapp_url=None):
    def mapped_api(route,payload):
        return api(route.replace('advertisers/outbox/','lifecycle/outbox/'),payload)
    def keyboard(item):
        cid=str(UUID(str(item['campaign_id']))) if item.get('campaign_id') else None
        staff=item.get('audience')=='staff'
        if miniapp_url:
            route=(('archive/' if item.get('event_type')=='completed' else 'moderation/' if staff else 'campaign/')+cid) if cid else 'sponsors'
            button={'text':'Открыть заявку' if cid else 'Узлы и диагностика','web_app':{'url':miniapp_url+'#/'+route}}
        else:
            button={'text':'Мои кампании' if cid and not staff else 'Модерация','callback_data':'campaigns' if cid and not staff else 'moderation'}
        return {'inline_keyboard':[[button]]}
    deliver_advertiser_notices(mapped_api,sender,renderer=render_lifecycle_notice,keyboard_builder=keyboard)
