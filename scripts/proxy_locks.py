"""Обновление исключает всю работу хоста; диагностика совместима с автоматикой."""
from contextlib import contextmanager, ExitStack
import fcntl
import time


class RuntimeBusy(BlockingIOError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@contextmanager
def runtime_lock(*, wait_seconds=0):
    """Сериализация изменений прокси без блокировки read-only диагностики.

    Порядок у всех агентов одинаков: shared host-lock, exclusive proxy-operation-lock.
    Обновлятор по-прежнему берёт exclusive host-lock. При ожидании занятой
    автоматики обе блокировки освобождаются, чтобы не задерживать обновление.
    """
    from updater import lock_file
    if not 0 <= wait_seconds <= 30:
        raise ValueError('invalid_lock_wait')
    deadline = time.monotonic() + wait_seconds
    while True:
        with ExitStack() as stack:
            code = 'waiting_update'
            try:
                gate = stack.enter_context(lock_file('host-lock'))
                fcntl.flock(gate, fcntl.LOCK_SH | fcntl.LOCK_NB)
                code = 'agent_busy'
                operation = stack.enter_context(lock_file('proxy-operation-lock'))
                fcntl.flock(operation, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                # Исключение внутри операции не должно повторно исполнять её.
                yield
                return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeBusy(code)
        time.sleep(min(0.2, remaining))
