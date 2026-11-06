# MTProxy Pushok

Версия проекта: v0.005.

Отдельный проект для установки MTProxy и управления спонсорским каналом. Он развивается параллельно VPN-магазину; интеграцию с магазином выполним позже.

## Что готово

- Установщик MTProxy, systemd-служба и ежедневное обновление конфигурации Telegram.
- Спецификация MVP кабинета: docs/MVP.md.
- FastAPI, PostgreSQL и вход через Telegram Login Widget.
- Личные каналы и кампании, отправка на модерацию.
- Ручная проверка оплаты, закрытая загрузка чеков, назначение слота и фиксация размещения оператором.

## Локальный запуск API

Требуется Docker Engine с Compose plugin. Подготовьте .env:

~~~bash
cp .env.example .env
openssl rand -hex 32
~~~

Сгенерируйте разные случайные значения для POSTGRES_PASSWORD и SESSION_SIGNING_KEY, затем задайте токен бота в TELEGRAM_BOT_TOKEN. В @BotFather настройте домен сайта через /setdomain. Не добавляйте реальные секреты в Git.

~~~bash
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
curl -fsS http://127.0.0.1:8000/api/v1/tariffs
~~~

API привязан к 127.0.0.1:8000; PostgreSQL и хранилище чеков не публикуются. Чеки ограничены 5 МБ и принимаются как JPEG, PNG или PDF. Оператор скачивает чек через защищённый маршрут GET /api/v1/operator/campaigns/{campaign_id}/payment-proofs/{proof_id}.

## Сценарии рекламодателя

1. Получить данные входа через Telegram Login Widget и отправить на POST /api/v1/auth/telegram.
2. Передавать выданный токен в заголовке Authorization: Bearer <token>.
3. Добавить канал через POST /api/v1/channels.
4. Создать кампанию через POST /api/v1/campaigns и отправить на модерацию через POST /api/v1/campaigns/{id}/submit.
5. После одобрения загрузить чек через POST /api/v1/campaigns/{id}/payment-proofs.

Список тарифов: GET /api/v1/tariffs. Свои кампании и комментарии по чекам: GET /api/v1/campaigns. Выход с отзывом сессии: POST /api/v1/auth/logout.

## Сценарии оператора

- Очередь: GET /api/v1/operator/campaigns?moderation_status=pending или payment_status=proof_submitted.
- Модерация: POST /api/v1/operator/campaigns/{id}/moderation.
- Проверка оплаты: POST /api/v1/operator/campaigns/{id}/payment-review; отказ требует комментарий.
- Назначение размещения: POST /api/v1/operator/campaigns/{id}/schedule; время окончания вычисляется по длительности тарифа, PostgreSQL блокирует пересечения на одном узле.
- Фиксация ручного включения: POST /api/v1/operator/campaigns/{id}/activate.
- Фиксация выключения и завершения: POST /api/v1/operator/campaigns/{id}/complete.

Решение оператора об активации означает, что тег был вручную применён в конфигурации MTProxy. API не хранит тег и не переключает его автоматически. Прокси-узлы пока добавляются администратором напрямую в БД; публичного API управления инфраструктурой нет.

Для чистой БД Compose выполняет SQL-файлы из database/ при первом создании тома. При существующей базе новые SQL-миграции применяются вручную. Команда docker compose down -v удаляет локальные данные БД и загруженные чеки.

## MTProxy

Для mtproxy.pushok.shop создайте DNS A-запись на IPv4 VPS. Установщик запускается от root на Ubuntu/Debian:

~~~bash
sudo bash install.sh
~~~

По умолчанию используется TCP-порт 8443. Обычный HTTP reverse proxy не проксирует MTProxy как HTTP-сайт. Регистрация прокси и получение спонсорского тега выполняются через официальный @MTProxybot.

## Ограничения

- v0.005 не развёрнут на VPS и не проверен с реальным Telegram-ботом или PostgreSQL.
- Перед публичным размещением нужны HTTPS, ограничения частоты запросов, резервное копирование БД и файлов, а также операционные проверки.
- Подтверждение оплаты и применение MTProxy-тега выполняются оператором вручную.
- MTProxy Pushok остаётся отдельным от VPN-магазина; объединение планируется позже.

Версии: CHANGELOG.md.
