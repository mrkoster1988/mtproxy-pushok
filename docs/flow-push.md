# Уведомления аккаунта Pushok Flow на Android

В Firebase Console зарегистрируйте Android-приложение с именем пакета `shop.pushok.flow`. Включите Firebase Cloud Messaging HTTP v1. Для API задайте в серверном `.env` публичные клиентские параметры из `google-services.json`.

| Параметр окружения | Значение из JSON |
|---|---|
| FLOW_FCM_PROJECT_ID | project_info.project_id |
| FLOW_FCM_SENDER_ID | project_info.project_number |
| FLOW_FCM_ANDROID_APP_ID | client_info.mobilesdk_app_id клиента shop.pushok.flow |
| FLOW_FCM_ANDROID_API_KEY | api_key.current_key этого клиента |

Выдайте серверному аккаунту разрешение отправлять сообщения в этом проекте. Задайте `FLOW_FCM_CREDENTIAL_FILE` как локальный путь к JSON этого аккаунта. Храните файл вне каталога проекта с доступом для UID 10001. Запустите штатные миграции приложения до включения доставки. Примените параметры к API и запустите отправитель отдельным сервисом.

```sh
docker compose -f compose.yaml -f compose.flow-push.yaml up -d --build api flow-push
```

В Flow откройте настройки аккаунта, нажмите «Уведомления аккаунта» и разрешите уведомления. Событие кошелька или кампании отправляет общее сообщение. Для доставки нужна действующая сессия Flow. Суммы, реквизиты, названия кампаний и данные Telegram в сообщение не включаются. Отключение удаляет регистрацию этой сессии и её очередь.
