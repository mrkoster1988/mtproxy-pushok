# Pushok Icons — иконки кабинета и Telegram

Веб-иконки уже подключены к навигации кабинета и Mini App. Сервер выдаёт SVG через `/assets/pushok-icons.svg`. Для нативного меню бота добавлена опциональная настройка Custom Emoji; она выключена до регистрации набора и проверки реальных ID.

Семейство: линейные пиктограммы 24×24, бирюзовый `#56e2d7`, янтарный `#ff9f66`, прозрачный фон. В меню размер 20×20; подписи остаются текстом, декоративный SVG скрыт от экранного диктора.

## Реальная навигация

| Маршрут | Подпись | Символ SVG |
|---|---|---|
| `dashboard` | Главная | `pi-home` |
| `channels` | Мои каналы | `pi-channel` |
| `campaigns` | Кампании | `pi-campaign` |
| `payments` | Оплата | `pi-wallet` |
| `calendar` | Календарь | `pi-calendar` |
| `reports` | Отчёты | `pi-chart` |
| `moderation` | Модерация | `pi-moderation` |
| `sponsors` | Узлы | `pi-server` |
| `settings` | Настройки | `pi-settings` |
| `help` | Помощь | `pi-support` |
| `bot` | Telegram-бот | `pi-channel` |

Остальные символы в спрайте — запас для будущих элементов. Наличие `pi-star`, `pi-qr` или `pi-sponsor` само по себе не означает подключённую оплату или активного спонсора.

## Выдача и доступность

```html
<svg class="pushok-nav-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
  <use href="/assets/pushok-icons.svg#pi-campaign"></use>
</svg>
```

Путь соответствует `StaticFiles` в `api/app/main.py`, одинаков для `/` и `/miniapp`. Установщик сейчас использует отдельный домен кабинета. Размещение всего кабинета под `/mtproxy` требует отдельной настройки маршрутов и не подтверждено этой проверкой.

Браузерный сценарий `scripts/check_cabinet_pages.py` проверяет HTTP 200, MIME SVG, соответствие всех 11 ссылок символам, непустую отрисовку `<use>`, доступные текстовые названия, мобильное меню 320/390 px и Mini App в светлой/тёмной теме. Внешние запросы в тесте запрещены; API и временная PostgreSQL настоящие, оболочка Telegram SDK тестовая.

Скриншоты находятся в артефакте `pushok-cabinet-pages` конкретного успешного CI. Для мобильного меню: `20-mobile-menu-320.png`, `20-mobile-menu-390.png`; для Mini App: `21-miniapp-menu-light.png`, `22-miniapp-menu-dark.png`. Результат Chromium не заменяет проверку Safari и настоящих клиентов Telegram.

## Telegram Custom Emoji

[Инструкция настройки и отключения](PUSHOK_TELEGRAM_EMOJI.md).

Workflow `.github/workflows/pushok-icons.yml` экспортирует PNG 100×100 и `manifest.json` с SHA-256, проверяет файлы и сохраняет `pushok-custom-emoji-png` на 14 дней. Он запускается для связанных изменений в PR, после попадания в `main` и вручную. Артефакт доступен по правилам доступа GitHub-репозитория, не является закрытым хранилищем и не содержит токенов/реальных настроек бота.

Workflow ничего не публикует в Telegram. Владелец сообщил о публикации PushokIcons; реальные ID и право использования конкретным ботом пока не подтверждены. Проверка формата ID в Python не проверяет его существование.
