"""Публичная карточка, присутствие и закрытый список приглашённых."""
from uuid import UUID
import calendar
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter,Depends,HTTPException,Query,Response
from .verified_profiles import today, Birthday, private_profile
from .telegram_environment import environment_name
from .hall_of_fame import scores,bounds,METRICS
from datetime import datetime,timezone
from pydantic import BaseModel, ConfigDict, Field
from .channel_logos import user_avatar


def require_verified(conn,uid):
    conn.execute('SELECT id FROM users WHERE id=%s FOR SHARE',(uid,)).fetchone()
    if not conn.execute('SELECT 1 FROM verified_profiles WHERE user_id=%s AND telegram_environment=%s AND phone_verified_at IS NOT NULL',(uid,environment_name())).fetchone():
        raise HTTPException(403,'phone_verification_required')


def person(conn,uid):
    row=conn.execute('''SELECT id,first_name,last_name,username,role,is_owner,
       CASE WHEN is_owner THEN NULL ELSE coalesce(last_seen_at>now()-interval '2 minutes',false) END AS online
       FROM users WHERE id=%s AND blocked_at IS NULL''',(uid,)).fetchone()
    if not row:raise HTTPException(404,'user_profile_not_found')
    d=today();birthday=conn.execute('SELECT birth_day,birth_month FROM verified_profiles WHERE user_id=%s AND telegram_environment=%s AND phone_verified_at IS NOT NULL',(uid,environment_name())).fetchone()
    row['phone_verified']=bool(birthday)
    row['birthday_today']=bool(birthday and birthday['birth_month']==d.month and min(birthday['birth_day'] or 32,calendar.monthrange(d.year,d.month)[1])==d.day)
    start,end=bounds('month',datetime.now(timezone.utc));ranks=[]
    if not row['is_owner']:
        for metric in METRICS:
            ranks.extend(r['rank'] for r in scores(conn,metric,start,end,0,uid))
    row['frame_rank']=min(ranks) if ranks and min(ranks)<=10 else None
    return row


class PersonalEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    phone_number:str|None=Field(default=None,pattern=r'^\+[0-9]{7,15}$')
    birthday:Birthday|None=None

class PersonalTransfer(BaseModel):
    model_config=ConfigDict(extra='forbid')
    target_telegram_id:int=Field(gt=0)


def router_for(dsn,current_user):
    router=APIRouter(prefix='/api/v1/community',tags=['community'])
    def db(action):
        try:
            with psycopg.connect(dsn(),row_factory=dict_row,connect_timeout=3) as conn:return action(conn)
        except psycopg.Error:
            raise HTTPException(503,'database_unavailable') from None
    def owner(conn,user):
        if not conn.execute('SELECT 1 FROM users WHERE id=%s AND is_owner AND blocked_at IS NULL FOR SHARE',(user['id'],)).fetchone():
            raise HTTPException(403,'owner_role_required')
    @router.put('/users/{uid}/personal')
    def edit_personal(uid:UUID,payload:PersonalEdit,user=Depends(current_user)):
        def write(c):
            owner(c,user)
            if not c.execute('SELECT id FROM users WHERE id=%s FOR NO KEY UPDATE',(uid,)).fetchone():raise HTTPException(404,'user_profile_not_found')
            b=payload.birthday
            c.execute("""INSERT INTO verified_profiles(user_id,telegram_environment,phone_number,phone_verified_at,
                birth_day,birth_month,birth_year,birthday_source,birthday_locked)
                VALUES(%s,%s,%s,CASE WHEN %s::text IS NOT NULL THEN now() END,%s,%s,%s,%s,true)
                ON CONFLICT(user_id,telegram_environment) DO UPDATE SET phone_number=EXCLUDED.phone_number,
                phone_verified_at=EXCLUDED.phone_verified_at,birth_day=EXCLUDED.birth_day,birth_month=EXCLUDED.birth_month,
                birth_year=EXCLUDED.birth_year,birthday_source=EXCLUDED.birthday_source,birthday_locked=true,
                transferred_to=NULL,birthday_sync_state='manual',telegram_update_id=NULL,updated_at=now()""",
                (uid,environment_name(),payload.phone_number,payload.phone_number,b.day if b else None,b.month if b else None,b.year if b else None,'manual' if b else None))
            c.execute('DELETE FROM phone_verification_requests WHERE user_id=%s AND telegram_environment=%s',(uid,environment_name()))
            c.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'personal_details_edited',jsonb_build_object('user_id',%s::text))",(user['id'],str(uid)))
            return private_profile(c,uid)
        return db(write)
    @router.post('/users/{uid}/personal/transfer')
    def transfer(uid:UUID,payload:PersonalTransfer,user=Depends(current_user)):
        def write(c):
            owner(c,user)
            target=c.execute('SELECT id FROM users WHERE telegram_user_id=%s AND blocked_at IS NULL',(payload.target_telegram_id,)).fetchone()
            if not target:raise HTTPException(404,'user_profile_not_found')
            dest=target['id']
            if dest==uid:raise HTTPException(409,'personal_transfer_same_user')
            c.execute('SELECT id FROM users WHERE id=ANY(%s) ORDER BY id FOR NO KEY UPDATE',([uid,dest],)).fetchall()
            src=c.execute('SELECT * FROM verified_profiles WHERE user_id=%s AND telegram_environment=%s FOR UPDATE',(uid,environment_name())).fetchone()
            dst=c.execute('SELECT * FROM verified_profiles WHERE user_id=%s AND telegram_environment=%s FOR UPDATE',(dest,environment_name())).fetchone()
            if not src or src['transferred_to'] or (not src['phone_number'] and not src['birth_day']):raise HTTPException(409,'personal_transfer_empty')
            if dst and (dst['phone_number'] or dst['birth_day'] or dst['transferred_to']):raise HTTPException(409,'personal_transfer_target_filled')
            c.execute('DELETE FROM verified_profiles WHERE user_id=%s AND telegram_environment=%s',(dest,environment_name()))
            c.execute('UPDATE verified_profiles SET user_id=%s WHERE user_id=%s AND telegram_environment=%s',(dest,uid,environment_name()))
            c.execute('INSERT INTO verified_profiles(user_id,telegram_environment,birthday_locked,transferred_to) VALUES(%s,%s,true,%s)',(uid,environment_name(),dest))
            # Не переносим деньги и историю, но повторный подарок за уже награждённый год исключён.
            c.execute("""INSERT INTO birthday_rewards(user_id,reward_year,birthday_date,bonus_amount)
                SELECT %s,reward_year,birthday_date,0 FROM birthday_rewards WHERE user_id=%s
                ON CONFLICT(user_id,reward_year) DO NOTHING""",(dest,uid))
            c.execute('DELETE FROM phone_verification_requests WHERE user_id=ANY(%s) AND telegram_environment=%s',([uid,dest],environment_name()))
            c.execute("INSERT INTO audit_events(actor_user_id,event_type,details_json) VALUES(%s,'personal_details_transferred',jsonb_build_object('from',%s::text,'to',%s::text))",(user['id'],str(uid),str(dest)))
            return {'transferred':True,'target_user_id':dest}
        return db(write)
    @router.get('/users/{uid}/avatar')
    def avatar(uid:UUID,user=Depends(current_user)):
        def read(c):
            row=c.execute('SELECT telegram_user_id FROM users WHERE id=%s AND blocked_at IS NULL',(uid,)).fetchone()
            if not row:raise HTTPException(404,'user_profile_not_found')
            return row['telegram_user_id']
        data=user_avatar(db(read))
        return Response(data,status_code=200 if data else 204,media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})
    @router.get('/moderation/{cid}')
    def moderation(cid:UUID,user=Depends(current_user)):
        if user['role'] not in ('admin','operator'):raise HTTPException(403,'operator_role_required')
        from .channel_ai_connection import active
        def read(c):
            campaign=c.execute('SELECT moderation_status,moderator_comment FROM campaigns WHERE id=%s',(cid,)).fetchone()
            if not campaign:raise HTTPException(404,'campaign_not_found')
            row=c.execute('SELECT decision,reason,attempted_at FROM ai_moderation_checks WHERE campaign_id=%s',(cid,)).fetchone()
            if row:
                # Атрибуция по журналу, а не по совпадению текста комментария.
                event=c.execute("SELECT created_at,reason FROM ai_moderation_events WHERE campaign_id=%s AND decision='approved' ORDER BY created_at DESC LIMIT 1",(cid,)).fetchone()
                manual=c.execute("SELECT max(created_at) at FROM audit_events WHERE campaign_id=%s AND event_type='campaign_moderated'",(cid,)).fetchone()
                row['approved_by_agent']=bool(event and campaign['moderation_status']=='approved' and (not manual['at'] or event['created_at']>manual['at']))
                row['agent_profile']='ai-agent'
                if row['approved_by_agent']:row['reason']=event['reason']
            return row
        row=db(read)
        return {'enabled':bool(active('campaign_review')),'check':row}
    @router.post('/presence')
    def presence(user=Depends(current_user)):
        db(lambda c:c.execute("UPDATE users SET last_seen_at=now() WHERE id=%s AND NOT is_owner AND (last_seen_at IS NULL OR last_seen_at<now()-interval '30 seconds')",(user['id'],)))
        return {'ok':True}
    @router.get('/users/{uid}')
    def profile(uid:UUID,response:Response,user=Depends(current_user)):
        response.headers['Cache-Control']='private, no-store'
        return db(lambda c:person(c,uid))
    @router.get('/users/{uid}/invited')
    def invited(uid:UUID,response:Response,page:int=Query(0,ge=0),user=Depends(current_user)):
        if str(user['id'])!=str(uid) and user['role'] not in ('admin','operator'):raise HTTPException(403,'profile_private')
        response.headers['Cache-Control']='private, no-store'
        def read(c):
            rows=c.execute('''SELECT u.id,u.first_name,u.last_name,u.username FROM growth_referrals r JOIN users u ON u.id=r.referred_user_id WHERE r.inviter_user_id=%s ORDER BY r.created_at,u.id LIMIT 21 OFFSET %s''',(uid,page*20)).fetchall()
            return {'items':rows[:20],'more':len(rows)>20,'page':page}
        return db(read)
    return router
