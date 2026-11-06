# MTProxy Pushok

**Версия проекта: v0.004.**

Отдельный проект для установки MTProxy и управления спонсорским каналом. Он развивается параллельно VPN-магазину; интеграцию с магазином выполним позже.

## Что есть

- Установщик MTProxy на официальном исходном коде Telegram.
- systemd-служба MTProxy и ежедневное обновление конфигурации Telegram.
- Спецификация MVP кабинета спонсора: `docs/MVP.md`.
- FastAPI и PostgreSQL-схема: роли, пользователи, каналы, тарифы, кампании и сессии.
- Telegram Login Widget, список тарифов, создание каналов и черновиков кампаний, отправка на модерацию, решение оператора.

## API и PostgreSQL

Это локальная среда разработки, не production-развёртывание. Порт API привязан к `127.0.0.1:8000`, PostgreSQL не публикует порт на хосте.

Требуется Docker Engine с Compose plugin. Подготовьте настройки:

~~~bash
cp .env.example .env
openssl rand -hex 32
~~~

Сгенерируйте разные случайные строки для `POSTGRES_PASSWORD` и `SESSION_SIGNING_KEY`, затем заполните `TELEGRAM_BOT_TOKEN`. В @BotFather настройте домен сайта через `/setdomain` для бота авторизации. Не добавляйте реальные значения в Git.

Запустите сервисы:

~~~bash
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
curl -fsS http://127.0.0.1:8000/api/v1/tariffs
~~~

Для интерфейса Telegram Login Widget отправляйте полученные от Telegram поля JSON-запросом на `POST /api/v1/auth/telegram`. Успешный ответ содержит bearer-токен; передавайте его как `Authorization: Bearer <token>`. Входная подпись проверяется по токену бота, устаревшие и повторно использованные данные отклоняются. Токен действует 12 часов и отзывается через `POST /api/v1/auth/logout`.

Основные маршруты:

- `GET /api/v1/tariffs` — активные тарифы;
- `POST /api/v1/channels` — добавить свой канал;
- `POST /api/v1/campaigns` — создать черновик по собственному каналу и активному тарифу;
- `GET /api/v1/campaigns` — свои кампании;
- `POST /api/v1/campaigns/{id}/submit` — отправить черновик на модерацию;
- `POST /api/v1/operator/campaigns/{id}/moderation` — решение оператора; одобрение открывает шаг оплаты.

Для проверки защищённых маршрутов сначала получите токен через Telegram Login Widget и передавайте его в заголовке Authorization. Роль оператора назначается администратором напрямую в БД; публичного API для повышения роли нет.

На чистой базе SQL-файлы в `database/` выполняются при первом запуске Compose. Для существующего локального тома v0.003 примените добавленную миграцию:

~~~bash
docker compose exec -T db psql -U mtproxy_pushok -d mtproxy_pushok < database/002_telegram_auth.sql
~~~

Сброс локальной базы командой `docker compose down -v` удаляет все данные в томе.

## Установка MTProxy

Требуется Ubuntu/Debian с systemd и доступом root, домен или IPv4 и свободный TCP-порт. Для домена `mtproxy.pushok.shop` создайте DNS A-запись на IPv4 VPS. По умолчанию используется TCP 8443.

~~~bash
sudo bash install.sh
~~~

Проверьте службы:

~~~bash
systemctl is-active mtproxy-pushok
systemctl is-active mtproxy-pushok-update-config.timer
ss -lntp | grep ':8443'
journalctl -u mtproxy-pushok -n 100 --no-pager
~~~

Обычный HTTP reverse proxy (Nginx/Caddy) не проксирует MTProxy как HTTP-сайт. Межсетевой экран должен пропускать выбранный TCP-порт.

## Ограничения

- Telegram-аутентификация и API-маршруты опубликованы в исходниках, но их запуск и интеграционные сценарии ещё не проверены.
- Перед публичным размещением нужны HTTPS, ограничения частоты запросов и проверка параметров развёртывания.
- Платежи подтверждаются вручную; автоматическая смена спонсорского тега не реализована.
- Секрет MTProxy находится в `/etc/mtproxy-pushok/mtproxy.env` с правами 0600. Не публикуйте его.
- MTProxy Pushok остаётся отдельным от VPN-магазина; объединение планируется позже.

## Версии

Подробная история изменений находится в [CHANGELOG.md](CHANGELOG.md). MVP-спецификация — [docs/MVP.md](docs/MVP.md).
