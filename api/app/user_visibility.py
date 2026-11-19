"""Доступ к личному имени пользователя Telegram."""
def can_view_username(viewer):
    return viewer.get('role') in ('admin', 'moderator')


def hide_username(person, *, username_key='username', name_prefix=''):
    username = person.get(username_key)
    first = name_prefix + 'first_name'
    last = name_prefix + 'last_name'
    name = ' '.join(filter(None, (person.get(first), person.get(last)))).strip()
    if username and name.lstrip('@').casefold() == username.casefold():
        person[first], person[last] = 'Пользователь', None
    person[username_key] = None
    return person
