# Pushok Icons — собственная система пиктограмм v1

Семейство оригинальных лаконичных линейных иконок вместо копирования Crypto Bot. Образ: бирюзовая линия как карандашный штрих, янтарный акцент, без тяжёлых глянцевых квадратов. Рабочая область 24×24, круглые окончания, прозрачный фон.

## Привязка к реальным разделам проекта

- `campaigns` → `pi-campaign`
- `new_campaign` → `pi-megaphone`
- `channels` → `pi-channel`
- `sponsors` → `pi-sponsor`
- `slots` → `pi-calendar`
- `moderation` → `pi-moderation`
- `stars` → `pi-star`
- `sbp` → `pi-qr`
- `payments` → `pi-wallet`
- `proxy_nodes` → `pi-server`
- `report` → `pi-chart`
- `dashboard` → `pi-home`
- `settings` → `pi-settings`
- `calendar` → `pi-calendar`
- `tariffs` → `pi-price`
- `users` → `pi-users`

## Использование в вебе

```html
<svg class="pushok-icon" width="24" height="24" aria-hidden="true"><use href="/assets/pushok-icons.svg#pi-campaign"></use></svg>
```

**Важно:** путь в примере заменить реальным URL выдачи статики (при развёртывании с подпутём проверить base URL). Не подключать sprite до теста маршрута статики.

Для Telegram Bot API на обычных reply/inline-кнопках SVG напрямую **не работает**. Использовать `icon_custom_emoji_id` при выполнении условий Telegram (Premium владельца или дополнительные username, см. официальный Bot API), создать и зарегистрировать отдельный набор custom emoji, а до этого оставить обычные подписи кнопок. Не подставлять случайные ID. Для MAX/VK определить поддерживаемые форматы кнопок платформ отдельно.

## Проверки перед подключением

- [ ] Сравнить пункты меню с текущими командами/API.
- [ ] Проверить SVG и отдачу статики, адаптивность, SVG-спрайт в Safari/Android.
- [ ] Текст остаётся доступным без иконок; декоративные SVG имеют `aria-hidden`.
- [ ] Не увеличивать нагрузку на Mini Apps, не показывать декоративные иконки вместо статуса платежа.
- [ ] В Telegram зарегистрировать собственные Custom Emoji и сохранить реальные IDs.
- [ ] После подключения элементов интерфейса проверить UI-тесты и CHANGELOG.

Статус: дизайн-ассеты опубликованы отдельной веткой, реальные кнопки и серверные настройки **не изменены**.

## Машиночитаемое соответствие

```json
{
  "system": "Pushok Icons",
  "version": "1.0.0",
  "kind": "SVG sprite",
  "asset": "api/web/pushok-icons.svg",
  "colors": {
    "line": "#56e2d7",
    "highlight": "#ff9f66",
    "background": "none"
  },
  "menu": {
    "campaigns": "campaign",
    "new_campaign": "megaphone",
    "channels": "channel",
    "sponsors": "sponsor",
    "slots": "calendar",
    "moderation": "moderation",
    "stars": "star",
    "sbp": "qr",
    "payments": "wallet",
    "proxy_nodes": "server",
    "report": "chart",
    "dashboard": "home",
    "settings": "settings",
    "calendar": "calendar",
    "tariffs": "price",
    "users": "users"
  },
  "telegram": {
    "icon_custom_emoji_ids": {},
    "status": "not-configured",
    "note": "Telegram reply and inline buttons need custom emoji IDs; SVG is for web/Mini Apps only"
  }
}
```
