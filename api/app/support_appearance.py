"""Message templates and welcome artwork for the dedicated support bot."""
import base64
from hashlib import sha256
from io import BytesIO
from typing import Literal

from fastapi import HTTPException, Response
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from . import control
from .gift_sharing import public_origin
from .vpn_reminders import format_body

FILE = 'support-bot-appearance.json'
MASCOT = '/assets/pushok-pencil.png'
COMMANDS = '/question — задать вопрос\n/idea — предложить идею\n/human — позвать сотрудника\n/new — начать заново'
ACTIONS = {
    'question': ('💬', 'Я весь внимание! Что подсказать про Flow или Mini App?'),
    'idea': ('💡', 'Люблю такие разговоры! Какой функции вам не хватает и когда она бы пригодилась?'),
    'human': ('👤', 'Подключим команду. Расскажите, что случилось, и сотрудник ответит здесь.'),
}


class Appearance(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(default='Привет! Это Пушок', min_length=1, max_length=64)
    welcome: str = Field(default='Рад вас видеть! Одной лапой помогу разобраться с **Pushok Flow**, другой — с **Mini App**.\n\nЧто у вас сегодня — вопрос, хитрая неполадка или идея, которой пора дать ход?', min_length=1, max_length=500)
    case_title: str = Field(default='Обращение', min_length=1, max_length=48)
    staff_title: str = Field(default='Ответ команды Pushok', min_length=1, max_length=64)
    question: str = Field(default='Задать вопрос', min_length=1, max_length=24)
    idea: str = Field(default='Предложить идею', min_length=1, max_length=24)
    human: str = Field(default='Позвать сотрудника', min_length=1, max_length=24)
    tone: Literal['playful', 'calm'] = 'playful'
    emoji: bool = True
    buttons: bool = True
    image_mode: Literal['mascot', 'custom', 'none'] = 'mascot'


class AppearanceEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    settings: Appearance
    revision: str
    image_base64: str | None = Field(default=None, max_length=4_194_304)


def read():
    state = control.read(FILE, {}) or {}
    if not state or state.get('greeting_version', 0) >= 2:
        return state
    with control.locked():
        state = control.read(FILE, {}) or {}
        if state and state.get('greeting_version', 0) < 2:
            settings = dict(state.get('settings', {}))
            for field in ('title', 'welcome'):
                settings.pop(field, None)
            state.update(settings=settings, greeting_version=2, revision=control.now())
            control.write(FILE, state)
        return state


def image_path(state):
    return '/ai-support/images/' + state['image_id'] + '.jpg' if state.get('image_id') else None


def public(state):
    return dict(settings=Appearance(**state.get('settings', {})).model_dump(),
                revision=state.get('revision', ''), image_path=image_path(state), mascot_path=MASCOT)


def picture(encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(raw), formats=('JPEG', 'PNG', 'WEBP')) as img:
            if img.width * img.height > 20_000_000 or max(img.size) > 20 * min(img.size):
                raise ValueError()
            img.thumbnail((1280, 1280))
            image = ImageOps.exif_transpose(img).convert('RGBA')
            canvas = Image.new('RGB', image.size, '#102635')
            canvas.paste(image, mask=image.getchannel('A'))
            output = BytesIO()
            canvas.save(output, format='JPEG', quality=86)
        return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, 'support_bot_bad_image') from None


def save(edit):
    image = picture(edit.image_base64) if edit.image_base64 is not None else None
    read()  # Apply the greeting update before checking the editor's revision.
    with control.locked():
        state = control.read(FILE, {}) or {}
        if edit.revision != state.get('revision', ''):
            raise HTTPException(409, 'support_bot_settings_changed')
        if image is not None:
            state.update(image_id=sha256(image).hexdigest(), photo=base64.b64encode(image).decode())
        if edit.settings.image_mode == 'custom' and not state.get('image_id'):
            raise HTTPException(422, 'support_bot_bad_image')
        state.update(settings=edit.settings.model_dump(), greeting_version=2, revision=control.now())
        control.write(FILE, state)
        return public(state)


def photo(image_id):
    state = read()
    if image_id != state.get('image_id') or state.get('settings', {}).get('image_mode') != 'custom':
        raise HTTPException(404)
    return Response(base64.b64decode(state['photo']), media_type='image/jpeg',
                    headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


def icon(style, glyph):
    return glyph + ' ' if style.emoji else ''


def heading(style, glyph, title):
    return icon(style, glyph) + '**' + title + '**'


def welcome_payload(state, uid, help_command=False, locale="ru"):
    from . import support_languages as lang
    style = Appearance(**state.get('settings', {}))
    style = style.model_copy(update=lang.APPEARANCE[locale])
    text = heading(style, '🐾', style.title) + '\n\n' + style.welcome
    fields = {'chat_id': uid, 'parse_mode': 'HTML'}
    if style.buttons:
        fields['reply_markup'] = {'inline_keyboard': [
            [{'text': icon(style, glyph) + getattr(style, action), 'callback_data': 'support:' + action}]
            for action, (glyph, _) in ACTIONS.items()
        ]}
    if help_command or not style.buttons:
        text += '\n\n' + lang.tr(locale, 'commands')
    text += '\n\n/language · ' + lang.tr(locale, 'lang')
    fields['text'] = format_body(text)
    path = {'mascot': MASCOT, 'custom': image_path(state), 'none': None}[style.image_mode]
    return fields, path


def photo_url(path):
    try:
        return public_origin() + path
    except HTTPException:
        return 'https://pushok.shop' + path
