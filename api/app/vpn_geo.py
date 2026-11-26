"""Resolve the last public endpoint using local DB-IP City Lite files."""
from functools import lru_cache
import ipaddress
from pathlib import Path

import maxminddb

DIRECTORY = Path('/usr/share/pushok')

# DB-IP provides an English place name, sometimes followed by a district.
RU_NAMES = {
    'Moscow': 'Москва', 'Saint Petersburg': 'Санкт-Петербург', 'St Petersburg': 'Санкт-Петербург',
    'Novosibirsk': 'Новосибирск', 'Yekaterinburg': 'Екатеринбург', 'Kazan': 'Казань',
    'Krasnoyarsk': 'Красноярск', 'Nizhny Novgorod': 'Нижний Новгород',
    'Chelyabinsk': 'Челябинск', 'Ufa': 'Уфа', 'Krasnodar': 'Краснодар',
    'Samara': 'Самара', 'Rostov-on-Don': 'Ростов-на-Дону', 'Omsk': 'Омск',
    'Voronezh': 'Воронеж', 'Perm': 'Пермь', 'Volgograd': 'Волгоград',
}


@lru_cache(maxsize=2)
def database(version):
    return maxminddb.open_database(str(DIRECTORY / f'dbip-city-ipv{version}.mmdb'))


@lru_cache(maxsize=2048)
def location(address):
    try:
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return None, None
        record = database(ip.version).get(str(ip)) or {}
    except (OSError, ValueError, maxminddb.InvalidDatabaseError):
        return None, None
    country = record.get('country_code')
    if not isinstance(country, str) or len(country) != 2 or not country.isascii() or not country.isalpha():
        return None, None
    country = country.upper()
    raw = record.get('city')
    city = raw.partition(' (')[0].strip() if isinstance(raw, str) else ''
    if country == 'RU':
        city = RU_NAMES.get(city, city)
    return country, city or None
