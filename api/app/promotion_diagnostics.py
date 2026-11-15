"""Пояснения к плану и ограниченный отчёт агента. Не подтверждают показ рекламы."""
from datetime import datetime, timezone
from .promotion_report import read_report
from .promotion_runtime import confirmation, node_plan


# Только фиксированные тексты: исключения, команды и рекламные теги не публикуются.
MESSAGES = {
    'ready': ('Готово к применению', 'Условия кампании выполнены. Подтверждения применения текущего плана пока нет.'),
    'tag_applied': ('Агент подтвердил применение тега', 'Проверьте фактический показ канала в Telegram.'),
    'idle': ('Автоматического размещения сейчас нет', 'Проверьте подготовку канала и расписание кампании.'),
    'node_not_found': ('Агент не нашёл узел', 'Сверьте адрес и порт узла с адресом в отчёте агента.'),
    'node_ambiguous': ('Адрес соответствует двум узлам', 'Устраните дублирование домена и IP в реестре, сохранив нужные кампании.'),
    'node_paused': ('Узел на паузе', 'Возобновите узел в реестре после проверки его готовности.'),
    'node_archived': ('Узел в архиве', 'Восстановите нужный узел в разделе «Удалённые».'),
    'engine_unsupported': ('Движок не поддерживается агентом', 'Автоприменение работает с локальным MTProxy Classic.'),
    'manual_placement_active': ('Действует ручное размещение', 'Автоматика ожидает завершения размещения, которым управляет оператор.'),
    'conflicting_campaigns': ('Пересекаются кампании', 'Исправьте расписание: на узле должен быть один текущий спонсор.'),
    'binding_missing': ('Не подготовлена связь канала с тегом', 'В блоке «Автоприменение» выберите канал и сохраните его подтверждённый тег.'),
    'binding_changed': ('Связь канала с тегом устарела', 'После изменения канала, адреса или порта проверьте и сохраните связь заново.'),
    'channel_archived': ('Канал в архиве', 'Восстановите канал или отмените его размещение.'),
    'moderation_pending': ('Нет одобрения модератора', 'Проверьте результат модерации кампании.'),
    'payment_pending': ('Оплата не подтверждена', 'Завершите проверку оплаты или оформление бесплатного тарифа.'),
    'waiting_start': ('Ожидается начало периода', 'Тег будет применяться в назначенный период после ответа агента.'),
    'period_ended': ('Период размещения закончился', 'Проверьте возврат исходного тега и завершите кампанию.'),
    'no_current_campaign': ('Нет кампании для текущего периода', 'Проверьте назначение узла и расписание одобренной оплаченной кампании.'),
    'waiting_update': ('Агент ожидает обновления', 'После завершения обновления проверка повторится автоматически.'),
    'agent_busy': ('Предыдущая проверка агента ещё выполняется', 'Обновите диагностику после завершения проверки.'),
    'env_missing': ('Не найдена конфигурация MTProxy', 'Проверьте установку локального MTProxy Classic на сервере кабинета.'),
    'config_error': ('Ошибка чтения конфигурации', 'Требуется проверить конфигурацию MTProxy на сервере.'),
    'unsafe_config': ('Конфигурация имеет неверные права', 'Требуется проверить владельца и права файла конфигурации на сервере.'),
    'permission_error': ('Недостаточно прав у агента', 'Проверьте установку системной службы агента.'),
    'runtime_unavailable': ('Агент не получил план из кабинета', 'Проверьте доступность API и базы данных; агент повторит запрос.'),
    'invalid_plan': ('Агент отклонил план', 'Обновите диагностику; при повторении ошибки проверьте версии агента и API.'),
    'apply_failed': ('Не удалось применить тег', 'Проверьте состояние MTProxy на сервере. Фактическое применение не подтверждено.'),
    'restore_failed': ('Не подтверждён возврат исходного тега', 'Требуется проверить MTProxy на сервере; не считайте размещение завершённым.'),
    'ack_unavailable': ('Результат не подтверждён кабинетом', 'Агент повторит проверку. Тег мог быть применён, но подтверждения пока нет.'),
    'plan_changed': ('План изменился во время применения', 'Агент вернул исходный тег и повторит проверку расписания.'),
    'restored': ('Агент вернул исходный тег', 'Проверьте результат в Telegram.'),
    'agent_error': ('Проверка агента завершилась ошибкой', 'Обновите диагностику; при повторении требуется проверка службы на сервере.'),
}


def describe(code):
    if code not in MESSAGES:
        code = 'agent_error'
    title, action = MESSAGES[code]
    return {'code': code, 'title': title, 'next_action': action}


def agent_report(now=None):
    return describe_report(read_report(now))


def describe_report(data):
    empty = {'status': 'unknown', 'recent': False, 'checked_at': None,
             'title': 'Нет отчёта агента',
             'next_action': 'Обновите диагностику. Установка и работа агента пока не подтверждены.'}
    if not data or data['code'] not in MESSAGES:
        return empty
    recent = data['recent']
    result = {'status': 'recent' if recent else 'stale', 'recent': recent,
              'checked_at': data['checked_at'].isoformat(), **describe(data['code'])}
    # Ревизия нужна только для внутренней сверки и не публикуется в API.
    for key in ('host', 'port'):
        if key in data:
            result[key] = data[key]
    if not recent:
        result['last_title'] = result['title']
        result['title'] = 'Отчёт агента устарел'
        result['next_action'] = 'Обновите диагностику. Если свежий отчёт не появился, проверьте службу агента на сервере.'
    return result


def diagnostics(conn):
    now = datetime.now(timezone.utc)
    report = read_report(now)
    nodes = conn.execute('''SELECT n.id,n.name,n.host,n.port,n.status,n.archived_at,n.target_engine,
        r.campaign_id,r.state,r.checked_at,r.plan_revision
        FROM proxy_nodes n LEFT JOIN promotion_runtime r ON r.proxy_node_id=n.id
        WHERE n.archived_at IS NULL ORDER BY n.name,n.id''').fetchall()
    items = []
    for node in nodes:
        plan = node_plan(conn, node)
        confirmed = confirmation(conn, node, node, now=now, plan=plan, report=report)
        code = 'tag_applied' if confirmed['config_applied'] else plan['reason']
        item = {'node_id': str(node['id']), 'name': node['name'], 'host': node['host'],
                'port': node['port'], 'plan_title': describe(plan['reason'])['title'],
                **describe(code), **confirmed}
        if plan.get('next_start_at'):
            item['next_start_at'] = plan['next_start_at']
        items.append(item)
    return {'agent': describe_report(report), 'nodes': items, 'display_verified': False}
