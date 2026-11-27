"""Административный текст главной; атомарное сохранение в постоянном control volume."""
import json
import os
import tempfile
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict

FILE = Path('/var/lib/mtproxy-pushok/control/home-banner.json')
class Banner(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    title: str = Field(default='Добро пожаловать в puShok', min_length=1, max_length=100)
    subtitle: str = Field(default='Подключайтесь к MTProxy', max_length=180)
    connect_label: str = Field(default='Подключить', min_length=1, max_length=24)
    connect_caption: str = Field(default='трафик', max_length=24)

def read():
    try:
        return Banner.model_validate_json(FILE.read_text()).model_dump()
    except (OSError, ValueError):
        return Banner().model_dump()

def save(data):
    # Старые версии кабинета сохраняли только заголовок и подпись баннера.
    # Не сбрасываем пользовательскую надпись кнопки при таком запросе.
    missing = {'connect_label', 'connect_caption'} - data.model_fields_set
    if missing:
        current = read()
        data = data.model_copy(update={key: current[key] for key in missing})
    FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=FILE.parent, prefix='home-banner-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data.model_dump(), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, FILE)
    finally:
        if os.path.exists(name): os.unlink(name)
    return data.model_dump()
