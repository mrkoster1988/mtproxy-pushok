"""Уведомления БД ускоряют обработку; периодическая проверка страхует разрыв связи."""
import psycopg


def listen(dsn, stopped, schedule, moderation):
    while not stopped.is_set():
        try:
            with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as connection:
                connection.execute('LISTEN pushok_campaign_work')
                # После подключения перечитываем очередь, включая пропущенные события.
                schedule.set()
                for wakeup in moderation:
                    wakeup.set()
                while not stopped.is_set():
                    for notification in connection.notifies(timeout=1, stop_after=100):
                        if notification.payload == 'schedule':
                            schedule.set()
                        elif notification.payload == 'moderation':
                            for wakeup in moderation:
                                wakeup.set()
        except psycopg.Error:
            stopped.wait(5)
