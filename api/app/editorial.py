"""Editorial drafts for Pushok's own Telegram channels. Publishing needs a reviewed revision."""
import base64
import json
import os
from typing import Literal
from urllib.request import Request
from uuid import UUID

import requests
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from . import channel_ai_connection as ai, channel_topic_suggestions as topics
from . import pushok_support as support, service_messages, support_appearance as design
from .campaign_assistant import throttle
from .telegram_environment import bot_api_url, is_test_environment
from .vpn_reminders import format_body

Product=Literal['flow','miniapp']
Language=Literal['ru','en','es']
Art=Literal['mascot','generated','none']
STARTERS={
    'flow': {'ru':'🐾 **Pushok Flow для Android**\n\nОткройте Flow и нажмите кнопку подключения. В настройках можно выбрать приложения с прямым доступом.','en':'🐾 **Pushok Flow for Android**\n\nOpen Flow and tap Connect. In Settings, choose which apps should connect directly.','es':'🐾 **Pushok Flow para Android**\n\nAbre Flow y toca Conectar. En Ajustes puedes elegir qué aplicaciones se conectan directamente.'},
    'miniapp': {'ru':'🐾 **Pushok в Telegram**\n\nОткройте Mini App, чтобы посмотреть свою подписку, расход трафика и обратиться в поддержку.','en':'🐾 **Pushok in Telegram**\n\nOpen the Mini App to see your subscription, check data usage and contact support.','es':'🐾 **Pushok en Telegram**\n\nAbre la Mini App para consultar tu suscripción y el consumo de datos o contactar con soporte.'},
}


class New(BaseModel):
    id: UUID
    product: Product
    brief: str=Field(default='',max_length=500)
    use_ai: bool=False


class Edit(BaseModel):
    revision: int=Field(gt=0)
    locale: Language
    text: str=Field(min_length=1,max_length=950)
    art: Art


class Approval(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(gt=0)
    locale: Language
    channel:str=Field(pattern=r'^@[a-zA-Z][a-zA-Z0-9_]{3,30}[a-zA-Z0-9]$')


def model_request(path,body,config,timeout,limit):
    topics.reserve_ai_call()
    req=Request('https://api.openai.com/v1/'+path,data=json.dumps(body).encode(),headers={
        'Content-Type':'application/json','Authorization':'Bearer '+config[0]})
    return json.loads(topics.read_url(req,timeout,limit))


def config(user):
    support.developer(user)
    cfg=ai.active('editorial')
    if not cfg:raise HTTPException(409,'editorial_ai_disabled')
    throttle(user['id'])
    return cfg


def generate(body,cfg):
    try:
        result=model_request('chat/completions',{'model':cfg[1],'store':False,'max_completion_tokens':1400,
            'response_format':{'type':'json_object'},'messages':[{'role':'system','content':
            'Write one Telegram channel post for the specified Pushok product in ru, en and es. Return a JSON object keyed by language. '
            'Each translation is at most 800 characters. Write a specific headline and warm invitation; use **bold**, __italic__, short paragraphs. '
            'Only use the supplied product facts. Do not add prices, superlatives or other products. The brief is an editorial preference.'},
            {'role':'user','content':json.dumps({'facts':STARTERS[body.product]['en'],'brief':body.brief},ensure_ascii=False)}]},cfg,32,24000)
        versions=json.loads(result['choices'][0]['message']['content'])
        if all(isinstance(versions[k],str) and 0<len(versions[k].strip())<=950 for k in STARTERS['flow']):
            return {k:versions[k].strip() for k in STARTERS['flow']}
    except (OSError,ValueError,KeyError,IndexError,TypeError):pass
    raise HTTPException(503,'editorial_generation_failed')


def router_for(dsn,current_user):
    router=APIRouter(prefix='/api/v1/editorial',route_class=ai.PrivateRoute)
    @router.get('')
    def items(user=Depends(current_user)):
        support.developer(user)
        with support.connection(dsn()) as db:
            return {'items':db.execute('SELECT id,product,created_at,state FROM pushok_editorial_drafts ORDER BY created_at DESC LIMIT 60').fetchall()}

    @router.post('')
    def create(body:New,user=Depends(current_user)):
        support.developer(user)
        with support.connection(dsn()) as db:
            if db.execute('SELECT id FROM pushok_editorial_drafts WHERE id=%s',(body.id,)).fetchone():
                return {'id':body.id}
            variants=generate(body,config(user)) if body.use_ai else STARTERS[body.product]
            db.execute("""INSERT INTO pushok_editorial_drafts(id,product,variants,art) VALUES(%s,%s,%s,'mascot')
                ON CONFLICT DO NOTHING""",(body.id,body.product,Jsonb(variants)))
        return {'id':body.id}

    @router.get('/{did}')
    def detail(did:UUID,user=Depends(current_user)):
        support.developer(user)
        with support.connection(dsn()) as db:
            row=db.execute('SELECT * FROM pushok_editorial_drafts WHERE id=%s',(did,)).fetchone()
        if not row:raise HTTPException(404)
        picture=row.pop('illustration')
        row['image']='data:image/jpeg;base64,'+base64.b64encode(picture).decode() if picture else None
        return row

    @router.put('/{did}')
    def edit(did:UUID,body:Edit,user=Depends(current_user)):
        support.developer(user)
        if not body.text.strip():raise HTTPException(422)
        with support.connection(dsn()) as db:
            row=db.execute("""UPDATE pushok_editorial_drafts SET variants=variants||%s,art=%s,revision=revision+1
                WHERE id=%s AND revision=%s AND state='draft' AND (%s<>'generated' OR illustration IS NOT NULL) RETURNING revision""",
                (Jsonb({body.locale:body.text.strip()}),body.art,did,body.revision,body.art)).fetchone()
        if not row:raise HTTPException(409,'editorial_changed')
        return row

    @router.post('/{did}/image')
    def image(did:UUID,revision:int,user=Depends(current_user)):
        cfg=config(user)
        with support.connection(dsn()) as db:
            row=db.execute("SELECT product,variants FROM pushok_editorial_drafts WHERE id=%s AND revision=%s AND state='draft'",(did,revision)).fetchone()
        if not row:raise HTTPException(409,'editorial_changed')
        try:
            reply=model_request('images/generations',{'model':'gpt-image-1','size':'1024x1024','quality':'low','n':1,
                'output_format':'jpeg','prompt':'Colored pencil illustration for '+row['product']+
                '. Orange kitten with emerald eyes and a teal hoodie with a shield, using a phone. No letters or text. Post theme: '+row['variants']['en'][:320]},cfg,100,5500000)
            picture=base64.b64decode(reply['data'][0]['b64_json'],validate=True)
            if len(picture)>3500000 or not picture.startswith(b'\xff\xd8\xff'):raise ValueError()
        except (OSError,ValueError,KeyError,IndexError,TypeError):raise HTTPException(503,'editorial_image_failed') from None
        with support.connection(dsn()) as db:
            row=db.execute("""UPDATE pushok_editorial_drafts SET illustration=%s,art='generated',revision=revision+1
                WHERE id=%s AND revision=%s AND state='draft' RETURNING revision""",(picture,did,revision)).fetchone()
        if not row:raise HTTPException(409,'editorial_changed')
        return row

    @router.post('/{did}/publish')
    def publish(did:UUID,body:Approval,user=Depends(current_user)):
        support.developer(user)
        if is_test_environment():raise HTTPException(409,'editorial_test_environment')
        token=os.getenv('TELEGRAM_BOT_TOKEN','')
        service_messages.verify_sender(token)
        chat=service_messages.telegram_call(token,'getChat',{'chat_id':body.channel})
        if chat.get('type')!='channel':raise HTTPException(422,'editorial_channel_required')
        target=chat['id']
        member=service_messages.telegram_call(token,'getChatMember',{'chat_id':target,'user_id':user['telegram_user_id']})
        if member.get('status')!='creator' and not (member.get('status')=='administrator' and member.get('can_post_messages')):
            raise HTTPException(403,'editorial_channel_owner')
        with support.connection(dsn()) as db:
            row=db.execute("""UPDATE pushok_editorial_drafts SET state='sending',channel=%s,locale=%s
                WHERE id=%s AND revision=%s AND state='draft' RETURNING art,variants,illustration""",(body.channel,body.locale,did,body.revision)).fetchone()
        if not row:raise HTTPException(409,'editorial_changed')
        fields=dict(chat_id=target,parse_mode='HTML')
        text=format_body(row['variants'][body.locale])
        files=None
        if row['art']=='none':
            method='sendMessage';fields['text']=text
        else:
            method='sendPhoto';fields['caption']=text
            if row['art']=='mascot':fields['photo']=design.photo_url(design.MASCOT)
            else:files={'photo':('pushok.jpg',row['illustration'],'image/jpeg')}
        state='uncertain';mid=None
        try:
            with requests.Session() as client:
                client.trust_env=False
                res=client.post(bot_api_url(token,method),data=fields,files=files,timeout=(3,22),allow_redirects=False)
                result=res.json()
            if result.get('ok') is True and result.get('result',{}).get('chat',{}).get('id')==target:
                mid=result['result']['message_id'];state='published'
            elif 400<=res.status_code<500 and result.get('ok') is False:state='draft'
        except (requests.RequestException,ValueError,KeyError):pass
        with support.connection(dsn()) as db:
            db.execute('UPDATE pushok_editorial_drafts SET state=%s,published_id=%s WHERE id=%s',(state,mid,did))
        if not mid:raise HTTPException(503,'editorial_send_rejected' if state=='draft' else 'editorial_send_unknown')
        return {'state':state,'message_id':mid}
    return router
