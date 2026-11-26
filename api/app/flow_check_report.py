"""Decode the small, user-shared Flow result. Never accept URLs or network data."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo

PATTERN = r'flowcheck_(ok|dns|https|unknown)_([0-9]{10})'
LABELS = {
    'ru': ('Проверка из Flow', 'DNS и HTTPS работают, example.com ответил.', 'DNS не вернул адрес. HTTPS не проверялся.',
           'DNS вернул адрес. example.com по HTTPS не ответил успешно.', 'Flow не подтвердил маршрут через свой туннель.'),
    'en': ('Check shared from Flow', 'DNS and HTTPS worked for example.com.', 'DNS returned no address. HTTPS was not tested.',
           'DNS worked. The HTTPS check to example.com failed.', 'Flow could not confirm its tunnel route.'),
    'es': ('Prueba compartida desde Flow', 'DNS y HTTPS funcionaron para example.com.', 'DNS no devolvió una dirección. HTTPS no se probó.',
           'DNS funcionó. Falló la prueba HTTPS de example.com.', 'Flow no pudo confirmar la ruta del túnel.')
}


def describe(parameter, locale):
    match=re.fullmatch(PATTERN,parameter)
    if not match:return None
    label,*reasons=LABELS[locale]
    when=datetime.fromtimestamp(int(match[2]),ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
    return f'{label} · {when} MSK\n'+reasons[('ok','dns','https','unknown').index(match[1])]
