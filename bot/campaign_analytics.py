"""Whitelist событий Telegram для аналитики приглашений."""
import re

def member_payload(update):
    """Чистый whitelist, только переход из неучастника в участника по invite."""
    value=update.get('chat_member')
    if not isinstance(value,dict):return None
    old,new=value.get('old_chat_member',{}),value.get('new_chat_member',{})
    def inside(member):return member.get('status') in ('creator','administrator','member') or (member.get('status')=='restricted' and member.get('is_member') is True)
    if not isinstance(old,dict) or not isinstance(new,dict):return None
    user=new.get('user',{})
    if not isinstance(user,dict):return None
    if inside(old) or not inside(new) or user.get('is_bot') is not False:return None
    invite=value.get('invite_link')
    if not isinstance(invite,dict):return None
    link=invite.get('invite_link')
    if not isinstance(link,str) or not re.fullmatch(r'https://t\.me/\+[A-Za-z0-9_-]{8,128}',link):return None
    return dict(chat_id=value.get('chat',{}).get('id'),telegram_user_id=user.get('id'),invite_link=link,date=value.get('date'))

