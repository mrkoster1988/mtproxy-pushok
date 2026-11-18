"""Понятные ошибки лицензии установки для API и бота."""
from fastapi import HTTPException

def explain(exc):
    code=getattr(getattr(exc,'diag',None),'message_primary',None)
    if getattr(exc, 'sqlstate', None) == 'PCH01':
        raise HTTPException(409, 'channel_moderation_blocked') from None
    if code in {'application_license_required','application_license_limit_max_nodes','application_license_limit_max_admins','application_license_limit_max_campaigns'}:
        raise HTTPException(409,code) from None
