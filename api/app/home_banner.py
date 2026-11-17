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

def read():
    try:
        return Banner.model_validate_json(FILE.read_text()).model_dump()
    except (OSError, ValueError):
        return Banner().model_dump()

def save(data):
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
