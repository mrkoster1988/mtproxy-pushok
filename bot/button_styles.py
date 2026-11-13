"""Цвета Telegram-кнопок по действию; подписи, ссылки и callback не меняются."""

_ACTIONS = {
    "advertise": "success", "draft:confirm": "success",
    "start": "primary", "campaigns": "primary", "tariffs": "primary",
    "moderation": "primary", "delivery:refresh": "primary",
    "draft:cancel": "danger", "review:cancel": "danger",
}
_PREFIXES = (
    ("stars:", "success"), ("submit:", "success"),
    ("review:approve:", "success"), ("review:confirm:", "success"),
    ("review:reject:", "danger"), ("review:changes:", "primary"),
    ("delivery:retry:", "primary"),
)


def _style(button):
    action = button.get("callback_data")
    if isinstance(action, str):
        if action in _ACTIONS:
            return _ACTIONS[action]
        for prefix, style in _PREFIXES:
            if action.startswith(prefix):
                return style
    if "web_app" in button or button.get("url") == "https://t.me/pushok_proxy":
        return "primary"
    return None


def with_button_styles(payload):
    """Копия сообщения с нативными цветами. Помощь и неизвестные действия нейтральны."""
    markup = payload.get("reply_markup")
    if not isinstance(markup, dict) or not isinstance(markup.get("inline_keyboard"), list):
        return payload
    rows = markup["inline_keyboard"]
    if any(not isinstance(row, list) or any(not isinstance(b, dict) for b in row) for row in rows):
        return payload
    colored = []
    for row in rows:
        buttons = []
        for button in row:
            style = _style(button)
            buttons.append({**button, "style": style} if style and "style" not in button else dict(button))
        colored.append(buttons)
    return {**payload, "reply_markup": {**markup, "inline_keyboard": colored}}
