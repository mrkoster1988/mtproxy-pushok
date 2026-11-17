"""Необязательная отраслевая тематика. Подсказка не является модерацией."""
from enum import Enum
import re


class Topic(str, Enum):
    technology = 'technology'
    education = 'education'
    business = 'business'
    finance = 'finance'
    marketing = 'marketing'
    career = 'career'
    news = 'news'
    entertainment = 'entertainment'
    gaming = 'gaming'
    culture = 'culture'
    health = 'health'
    beauty = 'beauty'
    sports = 'sports'
    travel = 'travel'
    food = 'food'
    real_estate = 'real_estate'
    auto = 'auto'
    family = 'family'
    pets = 'pets'
    science = 'science'
    industry = 'industry'
    agriculture = 'agriculture'
    shopping = 'shopping'
    lifestyle = 'lifestyle'
    other = 'other'


# Стабильные идентификаторы сохраняются в заявке; подписи можно менять отдельно.
CATALOG = [
    ('technology', 'IT и технологии', 'программирован python javascript разработк сервер linux нейросет искусственный интеллект гаджет софт vpn mtproxy'),
    ('education', 'Образование', 'обучени образован урок курс репетитор экзамен школьн студент excel эксель егэ'),
    ('business', 'Бизнес', 'бизнес предпринимат стартап франшиз управлени компани продаж'),
    ('finance', 'Финансы и инвестиции', 'финанс инвестиц акци облигац банков вклад бирж криптовалют биткоин'),
    ('marketing', 'Маркетинг и реклама', 'маркетинг реклам таргет smm seo продвижен копирайт'),
    ('career', 'Работа и карьера', 'ваканси карьер трудоустрой резюме рекрутинг собеседован работодат'),
    ('news', 'Новости и общество', 'новост политик общество происшеств парламент выборы региональн'),
    ('entertainment', 'Юмор и развлечения', 'юмор мем анекдот шутк смешн развлечен'),
    ('gaming', 'Игры', 'видеоигр гейминг киберспорт steam playstation xbox геймер'),
    ('culture', 'Культура и искусство', 'искусств культур театр кино музык литератур живопис книг'),
    ('health', 'Здоровье и медицина', 'медицин здоровь врач лечени клиник психолог терапи'),
    ('beauty', 'Красота и мода', 'красот мод макияж косметик маникюр стилист бьюти'),
    ('sports', 'Спорт и фитнес', 'спорт фитнес футбол хоккей тренировк йога атлетик'),
    ('travel', 'Путешествия и туризм', 'путешеств туризм турист авиабилет отел экскурси'),
    ('food', 'Еда и кулинария', 'кулинар рецепт готовим выпечк ресторан гастроном'),
    ('real_estate', 'Недвижимость и строительство', 'недвижимост квартир ипотек риелтор строительств новостройк'),
    ('auto', 'Авто и транспорт', 'автомобил автосервис автолюбит транспорт мотоцикл шин'),
    ('family', 'Семья и дети', 'родител воспитани детск малыш материнств беременност'),
    ('pets', 'Животные', 'животн питом собак кошк котят ветеринар'),
    ('science', 'Наука', 'наук физик астроном космос биологи химия исследован'),
    ('industry', 'Промышленность и производство', 'промышлен производств завод станк металлообработ оборудован'),
    ('agriculture', 'Сельское хозяйство', 'сельск фермер агро урожай растениевод животновод'),
    ('shopping', 'Покупки и товары', 'покупк товар скидк распродаж маркетплейс wildberries ozon'),
    ('lifestyle', 'Личный блог и образ жизни', 'личный блог повседневн моя жизнь образ жизни саморазвити'),
    ('other', 'Другое', ''),
]
LABELS = {key: label for key, label, _ in CATALOG}


def label(topic):
    return LABELS.get(topic)


def keywords(title, description, posts):
    """Консервативная подсказка без ИИ: две разные тематические подсказки минимум."""
    texts = [title, description, *posts]
    normalized = [re.sub(r'[^\w]+', ' ', text.lower().replace('ё', 'е')) for text in texts]
    scores = []
    for key, _, roots in CATALOG:
        # Отдельные фразы поддержаны явно, а не разбиты на общие слова.
        terms = roots.split()
        if key == 'lifestyle': terms = ['личный блог', 'повседневн', 'моя жизнь', 'образ жизни', 'саморазвити']
        if key == 'technology': terms = [t for t in terms if t not in ('искусственный', 'интеллект')] + ['искусственный интеллект']
        matched = {term for term in terms if any(re.search(r'\b' + re.escape(term), text) for text in normalized)}
        if len(matched) < 2:
            continue
        # Повтор одного слова или спам в одном посте не увеличивает вес.
        score = sum(min(3, sum(bool(re.search(r'\b' + re.escape(term), text)) for term in matched))
                    * (2 if i < 2 else 1) for i, text in enumerate(normalized))
        scores.append((score, key))
    scores.sort(reverse=True)
    if not scores or len(scores) > 1 and scores[0][0] < scores[1][0] + 2:
        return None
    return scores[0][1]
