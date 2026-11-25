# Автоматическая публикация и установка MTProxy Pushok

## Публикация

Каждый push в ветку `main` проходит существующий workflow
`API and PostgreSQL checks` (Python/PostgreSQL, Rust, Docker Compose, браузер).
Только если **весь workflow** завершился успешно для **точного текущего
SHA main**, `Publish tested MTProxy stable release` автоматически выполняет
**fast-forward** ветки `release/stable`. Эта ветка — источник обновлений Mini
App. При красном CI, новом main, расхождении истории или смене stable в
момент публикации обновление не выпускается. Для ручного повторного запуска
тоже нужен успешный CI для текущего SHA.

Выпуск новой версии требует изменения номера в README, FastAPI, установщике
и других местах согласно существующему preflight; CI отвергнет несовпадение.
Обходить тесты или принудительно продвигать stable нельзя.

## Один раз на действующем VPS

~~~bash
cd /opt/mtproxy-pushok
git pull --ff-only origin main
python3 scripts/enable_auto_updates.py plan
python3 scripts/enable_auto_updates.py install
~~~

Запуск от root. Команда сохраняет существующий private config и секреты,
включает флаг `.control/auto-update.json` и таймер
`mtproxy-pushok-updater.timer`. Если службы ещё не установлены, штатный
`scripts/install_updater.sh --install-only` устанавливает их. Если установлена
только часть служб или обнаружен чужой updater, операция останавливается.
Затем выполняется одна проверка стабильной ветки.

Штатный updater раз в час проверяет `release/stable`, делает приватную копию
кабинета и PostgreSQL, проверяет миграции и перестраивает контейнеры;
механизмы обновления прокси и Caddy остаются прежними.

~~~bash
systemctl status mtproxy-pushok-updater.timer --no-pager
systemctl status mtproxy-pushok-updater.service --no-pager
journalctl -u mtproxy-pushok-updater.service -n 80 --no-pager
~~~

Отключить автоматическую установку, сохранив все данные:

~~~bash
python3 /opt/mtproxy-pushok/scripts/enable_auto_updates.py disable
~~~

**Не включайте автообновление на нестандартной установке, где git-дерево
содержит локальные изменения, отсутствуют бэкапы или нарушена база.**
Серверный updater намеренно откажет. Публикация GitHub не обновляет VPS, пока
таймер не установлен и явно не включён на хосте.

## Остальные проекты

Shop использует отдельный hourly timer и инструмент edge-обновления, потому
что разделяет VPS с MTProxy и использует собственный постоянный том.
Flow выпускает новый Android APK при повышении версии только с оригинальной
подписью, всеми проверками и манифестом; Android по-прежнему требует
системного согласия пользователя на установку обновления вне Play Store.
