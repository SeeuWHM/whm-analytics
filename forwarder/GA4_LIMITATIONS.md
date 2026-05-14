# GA4 Measurement Protocol - Что работает, что нет

## ✅ ЧТО РАБОТАЕТ через MP:

### Events
- `page_view` - просмотры страниц
- `session_start` - начало сессии
- `purchase` - покупки с items
- Любые custom events

### Event Parameters (передаём в params)
- `page_location` - URL страницы
- `page_title` - заголовок
- `page_referrer` - откуда пришёл
- `engagement_time_msec` - время на странице
- `screen_resolution` - разрешение экрана
- `language` - язык браузера

### Custom Dimensions (нужно создать в GA4 Admin!)
Мы создали:
- `source` - Traffic Source
- `medium` - Traffic Medium  
- `campaign` - Campaign Name
- `country` - Country Code
- `city` - City Name

❗ `gclid` - RESERVED, нельзя как custom dimension

### User Properties
- Любые кастомные: `whm_source`, `whm_country`, `whm_city`
- Появляются в User Explorer и можно использовать в сегментах

### User ID
- `user_id` - связывает сессии одного юзера

---

## ❌ ЧТО НЕ РАБОТАЕТ через MP:

### Геолокация на карте
- MP НЕ передаёт IP клиента
- Google определяет geo по IP только для gtag.js
- **Решение**: передаём country/city как custom dimensions

### Built-in Traffic Source Attribution
- `firstUserSource`, `sessionSource` и т.д. - НЕ заполняются через MP
- Это ограничение Google - атрибуция работает только с gtag.js
- **Решение**: используем custom dimensions `source`, `medium`, `campaign`

### Session автоматика
- `sessionId` нужно генерировать самим
- `ga_session_id` - reserved, но можно передать

### Realtime показывает не всё
- Realtime API ограничен в dimensions
- Данные в основных отчётах - через 24-48 часов

---

## 📊 Как смотреть наши данные в GA4:

### Сразу (DebugView)
Admin → DebugView → выбрать событие → Parameters

### Через 24-48 часов (Reports)
1. Reports → Engagement → Events
2. Добавить secondary dimension: Traffic Source, Traffic Medium, etc.

### Explore
1. Explore → Free form
2. Добавить dimensions: source, medium, campaign, country, city
3. Добавить metrics: Event count, Active users

---

## 🔧 Наша архитектура:

```
[Браузер] → [whm.js] → [Collector] → [Matomo]
                                         ↓
                                   [Forwarder]
                                         ↓
                              [GA4 Measurement Protocol]
                                         ↓
                                  [GA4 Reports]
```

Все данные собираем сами, передаём по API. Никаких gtag.js!
