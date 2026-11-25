"""Look up a country locally. No client address is sent to a third party."""
import ipaddress
from functools import lru_cache

@lru_cache(maxsize=1)
def database():
    import maxminddb
    return maxminddb.open_database('/usr/share/pushok/dbip-country.mmdb')

@lru_cache(maxsize=2048)
def country(address):
    if not address:return None
    try:
        ip=ipaddress.ip_address(address)
        if not ip.is_global:return None
        record=database().get(str(ip)) or {}
        code=record.get('country_code')
        return code if isinstance(code,str) and len(code)==2 and code.isalpha() else None
    except (ImportError,OSError,ValueError):return None
