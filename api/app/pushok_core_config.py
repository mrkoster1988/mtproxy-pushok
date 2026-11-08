"""Pushok Core: offline generation of a staging TOML template for upstream Telemt 3.5.17.

No remote operations, runtime management, or advertising changes. Secrets must remain local.
"""
import re
import secrets
from ipaddress import ip_address

_VALID_DNS = re.compile(r"^(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_HEX = re.compile(r"^[0-9a-f]{32}$")
_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


def validate_domain(host: str) -> str:
    if not isinstance(host, str):
        raise ValueError("invalid_tls_domain")
    domain = host.strip().lower()
    if not _VALID_DNS.fullmatch(domain) or domain.endswith("."):
        raise ValueError("invalid_tls_domain")
    try:
        ip_address(domain)
    except ValueError:
        return domain
    raise ValueError("invalid_tls_domain")


def validate_hex(value: str, label: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value.lower()) or value.lower() == "0" * 32:
        raise ValueError(f"invalid_{label}")
    return value.lower()


def render_staging_config(
    *, tls_domain: str, port: int = 8443, username: str = "pushok",
    secret: str | None = None, ad_tag: str | None = None,
) -> str:
    """Create a staging-only configuration bound to 127.0.0.1.

    Return value includes a client secret. NEVER log it, expose via API or commit it.
    Upstream compatibility still requires an actual binary smoke test.
    """
    domain = validate_domain(tls_domain)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid_port")
    if not isinstance(username, str) or not _USERNAME.fullmatch(username):
        raise ValueError("invalid_username")
    user_secret = validate_hex(secret or secrets.token_hex(16), "secret")
    tag = validate_hex(ad_tag, "ad_tag") if ad_tag is not None else None
    adline = f'ad_tag = "{tag}"\n' if tag else ""
    return (
        "# Pushok Core: LOCAL TEST CONFIG ONLY. Do not publish or commit this file.\n"
        "[general]\n"
        "use_middle_proxy = true\n"
        "log_level = \"normal\"\n"
        + adline +
        "\n[general.modes]\n"
        "classic = false\n"
        "secure = false\n"
        "tls = true\n"
        "\n[server]\n"
        f"port = {port}\n"
        "\n[server.api]\n"
        "enabled = false\n"
        'listen = "127.0.0.1:9091"\n'
        "\n[[server.listeners]]\n"
        'ip = "127.0.0.1"\n'
        "\n[censorship]\n"
        f'tls_domain = "{domain}"\n'
        "mask = true\n"
        "tls_emulation = true\n"
        "\n[access.users]\n"
        f'{username} = "{user_secret}"\n'
    )
