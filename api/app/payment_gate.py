"""Предохранитель для фактического приёма Stars до приёмки.

Смена тарифа XTR сама по себе не разрешает выставлять настоящие счета.
Отключение не блокирует обработку уже состоявшихся successful_payment.
"""
import os
from .control import flag


def stars_invoices_enabled() -> bool:
    return flag("stars", os.getenv("PUSHOK_STARS_PAYMENTS_ENABLED", "0") == "1")
