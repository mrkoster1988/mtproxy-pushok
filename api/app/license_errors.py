"""Ошибки квоты одинаковы для кабинета, бота и восстановления из архива."""
from fastapi import HTTPException

CODES={'license_required','license_channels_limit','license_open_campaigns_limit','license_period_limit'}
def explain(exc):
    code=getattr(getattr(exc,'diag',None),'message_primary',None)
    if code in CODES:raise HTTPException(409,code) from None
