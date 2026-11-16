"""Предохранитель для фактического приёма Stars до приёмки.

Смена тарифа XTR сама по себе не разрешает выставлять настоящие счета.
Отключение не блокирует обработку уже состоявшихся successful_payment.
"""
import os
from .control import flag


def stars_invoices_enabled() -> bool:
    return flag("stars", os.getenv("PUSHOK_STARS_PAYMENTS_ENABLED", "0") == "1")



def sbp_reconciliation_enabled() -> bool:
    # После включения приёма сверка ранее выпущенных счетов работает и при выключении новых оплат.
    return flag('sbp_reconciliation', os.getenv('PUSHOK_SBP_RECONCILIATION_ENABLED', '0') == '1')
