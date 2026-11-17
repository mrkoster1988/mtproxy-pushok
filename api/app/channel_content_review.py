"""Признаки 18+ в публичных текстах: подсказка сотруднику, не решение модерации."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SOURCES = ('title', 'description', *(f'post_{n}' for n in range(1, 9)))
LABELS = {'title':'Название канала', 'description':'Описание канала',
          **{f'post_{n}':f'Публикация {n}' for n in range(1, 9)}}
MARKER = re.compile(r'(?<!\w)(?:18\s*\+|\+\s*18|nsfw|порн[а-яё]*|porn(?:o|ography|ographic)?|'
                    r'эротик[а-яё]*|эротическ[а-яё]*|erotic(?:a)?|onlyfans|hentai|хентай[а-яё]*|xxx)(?!\w)|🔞', re.I)
STATUS = {'flagged':'Нужна проверка 18+', 'not_detected':'Признаки 18+ не выявлены',
          'uncertain':'Недостаточно данных для вывода'}
LIMITATION = 'Анализ охватывает только доступные тексты. Фото и видео не анализировались. Это не решение модерации.'


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Literal['title', 'description', 'post_1', 'post_2', 'post_3', 'post_4',
                    'post_5', 'post_6', 'post_7', 'post_8']
    quote: str = Field(strict=True, min_length=3, max_length=200)


class ContentReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    assessment: Literal['flagged', 'not_detected', 'uncertain']
    evidence: list[Evidence] = Field(max_length=3)


def schema():
    return {'type':['object','null'], 'properties':{
        'assessment':{'type':'string','enum':list(STATUS)},
        'evidence':{'type':'array','items':{'type':'object','properties':{
            'source':{'type':'string','enum':list(SOURCES)}, 'quote':{'type':'string'}},
            'required':['source','quote'],'additionalProperties':False}}},
        'required':['assessment','evidence'],'additionalProperties':False}


def sources(channel):
    """Только тот же ограниченный набор текстов, который получает модель."""
    texts = {'title':channel.get('title','')[:255], 'description':channel.get('description','')[:2000]}
    texts.update({f'post_{n}':text[:1200] for n,text in enumerate(channel.get('posts',[])[:8],1)})
    return {key:' '.join(text.split()) for key,text in texts.items() if text.strip()}


def excerpt(source, text, start, end):
    # Возвращаем серверный текст с окружением, а не произвольный текст ответа ИИ.
    left, right = max(0,start-70), min(len(text),end+100)
    return {'source':source, 'source_label':LABELS[source],
            'text':('…' if left else '')+text[left:right]+('…' if right<len(text) else '')}


def assess(channel, public_available, proposal=None):
    """Не выдаём отсутствие маркеров или недоступность Telegram за успешную проверку."""
    texts = sources(channel) if public_available else {}
    posts_count = sum(key.startswith('post_') for key in texts)
    evidence, engine, assessment = [], 'none', 'uncertain'
    detail = 'Публикации недоступны. Откройте канал и проверьте содержание вручную.'
    if proposal is not None:
        model = ContentReview.model_validate(proposal)
        for item in model.evidence:
            text = texts.get(item.source, '')
            quote = ' '.join(item.quote.split())
            found = re.search(re.escape(quote), text, re.I) if len(quote)>=3 else None
            if not found:
                raise ValueError('ungrounded_content_evidence')
            value = excerpt(item.source, text, found.start(), found.end())
            if value not in evidence:
                evidence.append(value)
        if model.assessment == 'flagged' and not evidence:
            raise ValueError('missing_content_evidence')
        engine = 'ai'
        if model.assessment == 'flagged':
            assessment, detail = 'flagged', 'ИИ нашёл возможные признаки 18+. Проверьте приведённые фрагменты и их контекст.'
        elif model.assessment == 'not_detected' and posts_count:
            assessment, detail = 'not_detected', 'В просмотренных текстах ИИ не нашёл признаков 18+. Вывод не распространяется на остальные публикации и медиа.'
        elif posts_count:
            detail = 'ИИ не смог уверенно оценить тексты. Нужна ручная проверка.'
    # Явная маркировка не должна пропадать даже при недоступном или ошибающемся ИИ.
    markers = []
    for source,text in texts.items():
        found = MARKER.search(text)
        if found:
            markers.append(excerpt(source,text,found.start(),found.end()))
            if len(markers) == 3:
                break
    if markers and assessment != 'flagged':
        assessment, engine, evidence = 'flagged', 'rules', markers
        detail = 'Найдена маркировка или упоминания 18+. Они могут встречаться и в новостных, медицинских или образовательных материалах — проверьте контекст.'
    elif proposal is None and posts_count:
        detail = 'ИИ недоступен. Явные маркеры 18+ не найдены, но содержание требует ручной проверки.'
    if not public_available:
        detail = 'Доступность публичного канала не подтверждена. Содержание не проверено.'
    return {'assessment':assessment, 'label':STATUS[assessment], 'detail':detail, 'engine':engine,
            'evidence':evidence, 'posts_count':posts_count, 'media_analyzed':False, 'limitation':LIMITATION}
