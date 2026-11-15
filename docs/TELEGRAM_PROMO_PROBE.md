# Проверка ответа Telegram о спонсоре

Когда MTProxy передаёт данные, запущен с правильным `-P`, а канал не появляется,
`scripts/check_telegram_promo.py` позволяет запросить `help.getPromoData` через
настроенный прокси. Запрос доступен **пользовательским аккаунтам**, не ботам.
Это отдельная ручная диагностика: она не включается в агента, API или установщик.

## Запуск на VPS

1. Получить собственные `api_id` и `api_hash` в https://my.telegram.org/apps.
   Не отправлять их, телефон, код входа или пароль 2FA в чат, issue или журнал.
2. Создать отдельное окружение и установить проверенную версию библиотеки:

   ```bash
   python3 -m venv /root/pushok-promo-check-venv
   /root/pushok-promo-check-venv/bin/python -m pip install 'Telethon==1.45.0'
   ```

   Если Ubuntu сообщает об отсутствии `ensurepip`, установить `python3-venv`
   через apt и повторить создание окружения. Системные Python-пакеты кабинета
   скрипт не меняет.

3. Из каталога проверенного исходного кода запустить сначала проверку без сети:

   ```bash
   /root/pushok-promo-check-venv/bin/python scripts/check_telegram_promo.py --expected-channel push_canal --check-only
   /root/pushok-promo-check-venv/bin/python scripts/check_telegram_promo.py --expected-channel push_canal
   ```

Скрипт читает адрес, порт и секрет из `/etc/mtproxy-pushok/mtproxy.env` без исполнения
файла. Нужен обычный файл текущего владельца с правами 0600 или 0400. Поддерживаются
секреты Classic/`dd`; Fake TLS/`ee` не поддерживается, прямого обхода прокси нет.
Все поля авторизации вводятся скрыто в терминале, а не в аргументах команд.

Создаётся новая сессия **Pushok Sponsor Check** в `MemorySession`, без `.session`
на диске. Скрипт не запрашивает историю чатов, не подписывается на канал и не
отправляет сообщения. После запроса выполняется выход именно из этой временной
сессии. При `temporary_session_logout=false` или аварийном завершении проверить
Telegram → Настройки → Устройства и завершить сессию Pushok Sponsor Check.

## Результат

| outcome | Значение |
|---|---|
| `proxy_promo_expected` | Telegram вернул ожидаемый публичный username с флагом proxy |
| `proxy_promo_other` | Telegram вернул спонсора с другим публичным username |
| `no_proxy_promo` | В этом ответе для этого аккаунта нет спонсора MTProxy; PSA не считается спонсором |
| `proxy_promo_unresolved` | Промо есть, но по ответу нельзя сопоставить его с публичным username |

В отчёте также есть срок актуальности ответа в UTC и доступные флаги выбранного
канала `left`, `kicked`, `restricted`. Не выводятся сырой ответ, access_hash,
API hash, телефон, код, пароль, proxy secret и данные других сущностей.

`display_verified` всегда false: полученный ответ не доказывает отображение
на Android. Новый клиент на VPS использует другой сетевой путь до прокси и новую
сессию; результат относится к этой проверке. Пустой ответ не доказывает отсутствие
показа у всех аккаунтов и не устанавливает причину (привязка, состояние аккаунта,
обновление данных Telegram и прочее).

Проверки без живой авторизации: сценарии ответа, 2FA, выход после ошибки, защита
вывода и чтение приватного env. Реальный показ принимается отдельно в Telegram.

Источники:
- https://core.telegram.org/method/help.getPromoData
- https://core.telegram.org/api/config#mtproxy-sponsor
- https://docs.telethon.dev/en/stable/basic/signing-in.html#using-mtproto-proxies
- https://docs.telethon.dev/en/stable/concepts/sessions.html
