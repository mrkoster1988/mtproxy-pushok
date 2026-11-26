"""Shared customer-support languages and verified troubleshooting replies."""
import re
from typing import Literal

Preference = Literal['auto', 'ru', 'en', 'es']
NAMES = {'ru': 'Русский', 'en': 'English', 'es': 'Español'}
MODEL_LANGUAGE = {'ru': 'Russian', 'en': 'English', 'es': 'Spanish'}


def effective(preference, hint):
    if preference in NAMES:
        return preference
    code = (hint or '').lower().replace('_', '-').partition('-')[0]
    return code if code in NAMES else 'en' if code else 'ru'


def get(db, uid):
    row = db.execute('SELECT preference FROM support_language_preferences WHERE telegram_id=%s', (uid,)).fetchone()
    return row['preference'] if row else 'auto'


def save(db, uid, code):
    db.execute('''INSERT INTO support_language_preferences VALUES(%s,%s)
                  ON CONFLICT (telegram_id) DO UPDATE SET preference=excluded.preference''', (uid, code))


# Russian bot appearance remains administrator-editable. Translations share media and tone.
APPEARANCE = {
    'ru': {},
    'en': dict(title='Hi, I’m Pushok!', welcome='Need a hand with **Pushok Flow** or the **Mini App**? Ask me a question, share an idea or talk to the team.',
               case_title='Conversation', staff_title='Reply from the Pushok team', question='Ask a question', idea='Share an idea', human='Talk to a person'),
    'es': dict(title='¡Hola! Soy Pushok', welcome='¿Necesitas ayuda con **Pushok Flow** o la **Mini App**? Haz una pregunta, comparte una idea o habla con el equipo.',
               case_title='Consulta', staff_title='Respuesta del equipo Pushok', question='Hacer una pregunta', idea='Sugerir una idea', human='Hablar con el equipo'),
}
COPY = {
    'handoff': ('Передал вопрос команде. Сотрудник увидит переписку и ответит здесь.', 'I’ve passed this conversation to the team. A person will reply here with the chat history available.', 'He pasado la conversación al equipo. Una persona podrá leer el historial y responderte aquí.'),
    'queued': ('Добавил сообщение к обращению. Команда увидит его вместе с перепиской.', 'I’ve added your message to the conversation for the team.', 'He añadido tu mensaje a la conversación para el equipo.'),
    'idea': ('Спасибо за идею! Команда увидит ваше предложение. Когда эта функция пригодилась бы вам больше всего?', 'Thanks for the idea! The team will see it. When would this feature help you most?', '¡Gracias por la idea! El equipo la recibirá. ¿Cuándo te sería más útil esta función?'),
    'idea_ai': ('Какую задачу вы бы первой поручили Пушку, если бы такой чат уже был доступен?', 'If that chat were available today, what is the first task you would give Pushok?', 'Si ese chat estuviera disponible hoy, ¿qué tarea le pedirías primero a Pushok?'),
    'idea_speed': ('Понял, важны и скорость, и качество. Для длинного ответа можно показывать первые строки сразу, пока Пушок дописывает остальные. Вам подошёл бы такой вариант?', 'You want a useful answer without the wait. For longer replies, Pushok could show the first lines as it writes the rest. Would that help?', 'Buscas una respuesta útil y rápida. Para respuestas largas, Pushok podría mostrarte las primeras líneas mientras escribe el resto. ¿Te serviría?'),
    'idea_first': ('Как вы представляете это в работе? Можно на примере одного действия в приложении.', 'How would this work for you? One example of something you would do in the app would help.', '¿Cómo te gustaría que funcionara? Un ejemplo de algo que harías en la aplicación sería útil.'),
    'idea_example': ('Какой результат вы хотели бы получить в этом примере?', 'What result would you like to get in that example?', '¿Qué resultado te gustaría obtener en ese ejemplo?'),
    'idea_offline': ('Сейчас генерация ответов недоступна. Ваше уточнение сохранено вместе с идеей. Если хотите обсудить её с командой, выберите «Позвать сотрудника».', 'I can’t generate a full reply right now. Your detail is saved with the idea. Choose “Talk to a person” if you’d like to discuss it with the team.', 'Ahora mismo no puedo generar una respuesta completa. Tu aclaración queda junto a la idea. Elige «Hablar con el equipo» si quieres comentarla con una persona.'),
    'hello': ('Здравствуйте! Чем помочь с Pushok Flow или Mini App?', 'Hello! What can I help you with in Pushok Flow or the Mini App?', '¡Hola! ¿En qué puedo ayudarte con Pushok Flow o la Mini App?'),
    'fallback': ('На каком шаге возникла проблема и что написано на экране? Команда увидит вопрос и сможет ответить здесь.', 'Which step were you on, and what did the screen say? I’ve sent your question to the team so they can reply here.', '¿En qué paso estabas y qué decía la pantalla? He enviado la consulta al equipo para que pueda responderte aquí.'),
    'topup': ('Если баланс Stars равен нулю, Telegram открывает покупку звёзд. При **PROVIDER_ACCOUNT_INVALID** можно попробовать официальный @PremiumBot в Telegram Desktop или Web. После пополнения вернитесь в **VPN → Продлить**.', 'With a zero Stars balance, Telegram opens a Stars purchase. For **PROVIDER_ACCOUNT_INVALID**, try the official @PremiumBot in Telegram Desktop or Web. After topping up, return to **VPN → Продлить** (Renew).', 'Con saldo cero, Telegram abre la compra de Stars. Si aparece **PROVIDER_ACCOUNT_INVALID**, prueba el bot oficial @PremiumBot en Telegram Desktop o Web. Tras recargar, vuelve a **VPN → Продлить** (Renovar).'),
    'paid': ('Если Stars уже списались, повторная оплата не нужна. Пришлите номер заказа, если он есть. Сотрудник проверит платёж в этом обращении.', 'If Stars have already been charged, avoid paying again. Share the order number if you have it. A team member will check the payment here.', 'Si ya se han cobrado las Stars, evita pagar otra vez. Comparte el número de pedido si lo tienes. El equipo revisará el pago en esta conversación.'),
    'revoked': ('У отозванной подписки отключён прежний ключ. Передаю обращение сотруднику для проверки доступа и выдачи нового профиля, если доступ восстановлен.', 'A revoked subscription has its previous key disabled. I’ve asked the team to check access and issue a new profile if access is restored.', 'La clave anterior se desactiva al revocar una suscripción. He pedido al equipo que revise el acceso y emita un perfil nuevo si se restablece.'),
    'traffic': ('В **Pushok Flow → Настройки → IP и трафик** можно посмотреть расход по приложениям своего телефона. Общий расход по подписке находится во вкладке **VPN** в Mini App.', 'In Android Flow, open **Настройки → IP и трафик** (Settings → IP and traffic) for usage by app on your phone. The **VPN** tab of the Mini App shows subscription totals.', 'En Flow para Android abre **Настройки → IP и трафик** (Ajustes → IP y tráfico) para ver el consumo por aplicación de tu teléfono. La pestaña **VPN** de la Mini App muestra el total de la suscripción.'),
    'connect': ('На главной Mini App под кнопкой подключения можно скачать **Pushok Flow для Android**. Откройте Flow, нажмите кнопку подключения и следуйте подсказкам входа.', 'On the Mini App home screen, use the Android download below the connect button. Open **Pushok Flow**, tap connect and follow the sign-in prompts.', 'En el inicio de la Mini App, descarga **Pushok Flow para Android** bajo el botón de conexión. Abre Flow, pulsa conectar y sigue las indicaciones para iniciar sesión.'),
    'proxy': ('В Flow нажмите **Включить MTProxy**. Подтвердите подключение в Telegram и вернитесь в Flow. Отметьте «Прокси подключён», когда Telegram покажет подключение.', 'Tap **Включить MTProxy** in Flow. Confirm the proxy in Telegram and return to Flow. Select “Proxy connected” once Telegram shows it is connected.', 'Pulsa **Включить MTProxy** en Flow. Confirma el proxy en Telegram y vuelve a Flow. Marca que está conectado cuando Telegram confirme la conexión.'),
    'premium': ('**Premium** — бессрочный тариф в подарок от администратора. Продлевать или оплачивать его не нужно.', '**Premium** is a lifetime plan gifted by an administrator. No renewal or payment is required.', '**Premium** es un plan de por vida que regala un administrador. No hace falta renovarlo ni pagarlo.'),
    'new': ('Новое обращение','New conversation','Nueva consulta'),
    'question': ('Что подсказать про Flow или Mini App?', 'What would you like to know about Flow or the Mini App?', '¿Qué te gustaría saber sobre Flow o la Mini App?'),
    'probe': ('В Flow на Android откройте диагностику и нажмите **Проверить интернет через туннель**. Раскройте результат и нажмите **Обсудить результат с Пушком**.', 'In Flow for Android, open Diagnostics and run the internet check through the tunnel. Expand the result and share it with Pushok.', 'En Flow para Android, abre el diagnóstico y comprueba internet a través del túnel. Abre el resultado y compártelo con Pushok.'),
    'probe_failed': ('Вижу результат проверки с телефона. Передаю его команде. Подскажите, это было через Wi-Fi или мобильную сеть?', 'I’ve added the device result for the team. Were you using Wi-Fi or mobile data?', 'He pasado el resultado del dispositivo al equipo. ¿Usabas wifi o datos móviles?'),
    'probe_ok': ('При этой проверке телефон получил ответ сайта. Если другое приложение по-прежнему недоступно, напишите его название.', 'The site responded to this check. If another app still fails, tell me which one.', 'El sitio respondió en esta prueba. Si otra aplicación sigue fallando, dime cuál.'),
    'problem': ('На каком шаге возникла проблема?', 'Which step are you having trouble with?', '¿En qué paso surgió el problema?'),
    'idea_prompt': ('Какой функции вам не хватает?', 'Which feature would you like to see?', '¿Qué función te gustaría tener?'),
    'human': ('Расскажите, что случилось. Сотрудник ответит здесь.', 'Tell us what happened. A person will reply here.', 'Cuéntanos qué ha pasado. Una persona te responderá aquí.'),
    'rate': ('Попробуйте написать через пять минут.', 'Please try again in five minutes.', 'Vuelve a intentarlo en cinco minutos.'),
    'lang': ('Язык ответов', 'Reply language', 'Idioma de respuesta'),
    'auto': ('Как в Telegram или браузере', 'Match Telegram or browser', 'Usar idioma de Telegram o del navegador'),
    'commands': ('/question — задать вопрос\n/idea — предложить идею\n/human — позвать сотрудника\n/new — начать заново', '/question — ask a question\n/idea — share an idea\n/human — talk to a person\n/new — new conversation', '/question — preguntar\n/idea — sugerir una idea\n/human — hablar con el equipo\n/new — nueva consulta'),
}


def tr(locale, key):
    return COPY[key][('ru','en','es').index(locale)]


def idea_followup(locale, question, history):
    """A small offline interview when generation is disabled or unavailable."""
    earlier = [m['body'] for m in history if m['sender']=='user']
    replies = {m['body'] for m in history if m['sender']=='ai-agent'}
    topic = ' '.join([*earlier, question]).casefold()
    ai_chat = re.search(r'\b(?:ии|ai|ia|chatbot|чатбот)\b|нейросет|чат[- ]бот', topic)
    detail = question
    if history and history[-1]['sender']=='user':
        detail = history[-1]['body'] + ' ' + question
    speed = re.search(r'быстр|тормоз|тупит|скорост|задержк|\b(?:fast|slow|lag|speed|rápid\w*|lent[oa])\b', detail.casefold())
    if ai_chat and speed and replies.isdisjoint(COPY['idea_speed']):
        return tr(locale,'idea_speed')
    if ai_chat and not earlier:
        return tr(locale,'idea_ai')
    if not earlier:
        return tr(locale,'idea_first')
    if replies.isdisjoint((*COPY['idea_example'], *COPY['idea_speed'])):
        return tr(locale,'idea_example')
    return tr(locale,'idea_offline')


def asks_for_staff(text):
    return re.fullmatch(r'(?:(?:позови(?:те)?|пригласи(?:те)?|подключи(?:те)?) (?:оператора|сотрудника|человека)|'
                        r'(?:talk|speak) to a (?:person|human)|hablar con (?:el equipo|una persona))[!. ]*',
                        text.strip().casefold()) is not None


def guided(text, history):
    words = text.casefold()
    from .flow_check_report import LABELS
    for labels in LABELS.values():
        if text.startswith(labels[0]+' · ') and text.split('\n')[-1] in labels[1:]:
            ok=text.endswith(labels[1])
            return ('probe_ok',False) if ok else ('probe_failed',True)
    rules = [
        ('probe',False,r'(?:не (?:работает интернет|открыва[ею]тся сайт)|сайты не (?:работают|открываются)|no internet|sites? (?:won.t open|unreachable)|sin internet|sitios? no (?:abre|carga))'),
        ('paid', True, r'\bсписа[лн]|уже оплатил|already (?:charged|paid)|charged twice|ya (?:pagué|pagado|se cobr)|cobraron'),
        ('topup', False, r'provider_account_invalid|пополн.{0,18}(stars|зв[её]зд)|top.?up.{0,16}stars|recargar.{0,14}stars'),
        ('revoked', True, r'отозван|revoked|revocad'),
        ('traffic', False, r'расход|трафик|traffic usage|data usage|uso de datos|consumo'),
        ('proxy', False, r'mtproxy'),
        ('premium', False, r'^(?:что такое |what is |qué es (?:el )?)?premium[?!. ]*$'),
        ('connect', False, r'^(?:подключить flow|connect flow|conectar flow)[?!. ]*$'),
    ]
    if history and re.search(r'не помогло|didn.t (?:help|work)|no (?:funcionó|sirvió)', words):
        return 'handoff', True
    return next(((key, human) for key, human, pattern in rules if re.search(pattern, words)), None)


def for_model(text):
    text = re.sub(r'(?im)\b(privatekey|presharedkey|password|token)\s*[:=]\s*\S+', '[redacted]', text)
    return re.sub(r'(?i)(?:https?://|tg://|vless://|vmess://|wireguard://)\S+|\b\d{5,16}:[\w-]{25,}', '[redacted]', text)
