# CI/CD MTProxy Pushok — GitHub

Актуализировано 13 ноября 2026. GitHub — единственный репозиторий публикации и CI по решению владельца. GitLab исключён; зеркалирование и его проверки больше не являются условиями выпуска. Старые записи о блокировке относятся к истории, а не к текущему состоянию.

## Проверки каждого изменения

- `.github/workflows/check.yml`: Python 3.12, временная PostgreSQL 16, полный unittest и pytest-регрессии платежей.
- Изолированный Compose smoke: миграции, backup/restore, перезапуск, API и цикл тестовой кампании.
- Предпусковой сценарий: unit-тесты и настоящий `docker compose config` с временным `.env` и искусственными секретами; без запуска сервисов.
- Сборка Rust-ядра и изолированная TCP-проверка.
- Браузерные сценарии кабинета и Mini App с реальным API и временной БД; снимки — артефакт конкретного запуска.
- Отдельный workflow подготовки emoji запускается по его условиям изменения файлов.

Успешный результат относится к точному SHA. Старый зелёный запуск не заменяет проверку новых изменений. Для базового `main` этого пакета `b4722bc4165baae96924e5db90b19c8763ea666a` подтверждён [run 43337955622](https://github.com/mrkoster1988/mtproxy-pushok/actions/runs/43337955622). Текущий статус смотрите в [Actions](https://github.com/mrkoster1988/mtproxy-pushok/actions).

## Локальные проверки

```bash
python3 scripts/preflight.py
python3 scripts/verify_release.py --mode offline
python3 -m unittest discover -s tests -p test_staging_preflight.py -v
```

На настроенном стенде дополнительно: [staging preflight](STAGING_PREFLIGHT.md).

Полный unittest suite создаёт и удаляет временную БД. Нужны выделенный тестовый PostgreSQL, `TEST_ADMIN_DSN` с базой `postgres`, `PUSHOK_TEST_DB_ALLOWED=1`; для `verify_release.py --mode full` также `--allow-test-db`. Локальный адрес не доказывает изоляцию — порт не должен проксировать рабочую БД. Production credentials не передавать.

## Граница CI и выпуска

Успешный CI подтверждает проверки кода и изолированного стенда, но не живой Telegram, установку на VPS, спонсорский показ или реальный платёж. Коммерческая приёмка — по [MVP_RELEASE_GATE.md](MVP_RELEASE_GATE.md). Тег принятого релиза и публичный деплой требуют закрытия внешних критериев. Не сохранять `.env`, приватные резервные копии или токены в GitHub/артефактах CI.
