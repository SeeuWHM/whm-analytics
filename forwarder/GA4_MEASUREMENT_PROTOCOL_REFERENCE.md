# GA4 Measurement Protocol - Полная справка
## Официальная документация Google

> Источник: https://developers.google.com/analytics/devguides/collection/protocol/ga4/reference
> Дата обновления: 2026-01-17

---

## 📡 Транспорт

### Endpoint
```
POST https://www.google-analytics.com/mp/collect?measurement_id=G-XXXXXX&api_secret=XXXXX
```

### EU Endpoint (для данных в ЕС)
```
POST https://region1.google-analytics.com/mp/collect
```

### Для валидации (debug)
```
POST https://www.google-analytics.com/debug/mp/collect
```

---

## 📦 Структура Payload

### Query Parameters
| Параметр | Описание |
|----------|----------|
| `api_secret` | **Required.** API Secret из GA4 UI |
| `measurement_id` | **Required.** G-XXXXXXX для Web streams |

### JSON Body - Top Level
| Ключ | Тип | Описание |
|------|-----|----------|
| `client_id` | string | **Required.** Уникальный ID клиента |
| `user_id` | string | Optional. ID пользователя для cross-platform |
| `timestamp_micros` | number | Optional. Unix timestamp в микросекундах |
| `user_properties` | object | Optional. Свойства пользователя |
| `consent` | object | Optional. Настройки согласия |
| `user_location` | object | Optional. Гео-данные (структурированно) |
| `ip_override` | string | Optional. IP для определения гео |
| `device` | object | Optional. Данные устройства |
| `non_personalized_ads` | boolean | Optional. Отключить персонализацию рекламы |
| `validation_behavior` | string | Optional. `RELAXED` или `ENFORCE_RECOMMENDATIONS` |
| `events[]` | array | **Required.** Массив событий (до 25) |

---

## 🌍 Geographic Information (user_location)

**Приоритет:** `user_location` > `ip_override` > `client_id` geo

### Структура user_location
```json
{
  "user_location": {
    "city": "Mountain View",
    "region_id": "US-CA",
    "country_id": "US",
    "subcontinent_id": "021",
    "continent_id": "019"
  }
}
```

### Поля
| Ключ | Тип | Формат | Пример |
|------|-----|--------|--------|
| `city` | string | Название города | `"Moscow"`, `"Mountain View"` |
| `region_id` | string | ISO 3166-2 | `"RU-MOW"`, `"US-CA"`, `"GB-LND"` |
| `country_id` | string | ISO 3166-1 alpha-2 | `"RU"`, `"US"`, `"GB"` |
| `subcontinent_id` | string | UN M49 | `"151"` (Eastern Europe) |
| `continent_id` | string | UN M49 | `"150"` (Europe) |

### Коды континентов (UN M49)
| Код | Континент |
|-----|-----------|
| 002 | Africa |
| 019 | Americas |
| 142 | Asia |
| 150 | Europe |
| 009 | Oceania |

### Коды субконтинентов (UN M49)
| Код | Субконтинент |
|-----|--------------|
| 151 | Eastern Europe |
| 154 | Northern Europe |
| 155 | Western Europe |
| 039 | Southern Europe |
| 021 | Northern America |
| 419 | Latin America |
| 030 | Eastern Asia |
| 034 | Southern Asia |
| 035 | South-Eastern Asia |
| 145 | Western Asia |

### Примеры region_id для России
| Регион | Код |
|--------|-----|
| Москва | RU-MOW |
| Санкт-Петербург | RU-SPE |
| Московская область | RU-MOS |
| Свердловская область | RU-SVE |
| Краснодарский край | RU-KDA |
| Татарстан | RU-TA |
| Новосибирская область | RU-NVS |

### Альтернатива: ip_override
```json
{
  "ip_override": "185.86.151.11"
}
```
Google сам определит гео по IP.

---

## 📱 Device Information

### Структура device
```json
{
  "device": {
    "category": "mobile",
    "language": "en",
    "screen_resolution": "1280x2856",
    "operating_system": "Android",
    "operating_system_version": "14",
    "model": "Pixel 9 Pro",
    "brand": "Google",
    "browser": "Chrome",
    "browser_version": "136.0.7103.60"
  }
}
```

### Поля
| Ключ | Тип | Описание | Примеры |
|------|-----|----------|---------|
| `category` | string | Категория устройства | `desktop`, `mobile`, `tablet`, `smart TV` |
| `language` | string | Язык (ISO 639-1) | `en`, `ru`, `en-US` |
| `screen_resolution` | string | Разрешение WIDTHxHEIGHT | `1920x1080`, `1280x720` |
| `operating_system` | string | ОС | `Windows`, `MacOS`, `Android`, `iOS`, `Linux` |
| `operating_system_version` | string | Версия ОС | `10`, `14`, `13.5` |
| `model` | string | Модель устройства | `Pixel 9 Pro`, `iPhone 15` |
| `brand` | string | Бренд | `Google`, `Samsung`, `Apple` |
| `browser` | string | Браузер | `Chrome`, `Firefox`, `Safari`, `Edge` |
| `browser_version` | string | Версия браузера | `136.0.7103.60` |

---

## ✅ Consent (Согласие)

### Структура consent
```json
{
  "consent": {
    "ad_user_data": "GRANTED",
    "ad_personalization": "GRANTED"
  }
}
```

### Значения
| Ключ | Значения | Описание |
|------|----------|----------|
| `ad_user_data` | `GRANTED` / `DENIED` | Согласие на использование данных для рекламы |
| `ad_personalization` | `GRANTED` / `DENIED` | Согласие на персонализацию рекламы |

---

## 📊 Общие параметры событий

| Параметр | Тип | Описание |
|----------|-----|----------|
| `session_id` | string | ID сессии (нужен для Realtime!) |
| `engagement_time_msec` | number | Время вовлечения в мс (нужен для Realtime!) |
| `timestamp_micros` | number | Время события (микросекунды) |

> ⚠️ **ВАЖНО:** Для отображения в Realtime ОБЯЗАТЕЛЬНЫ `session_id` и `engagement_time_msec`!

---

## 🛒 Рекомендуемые события E-commerce

### Воронка покупки
| Событие | Описание |
|---------|----------|
| `view_item` | Просмотр товара |
| `add_to_cart` | Добавление в корзину |
| `view_cart` | Просмотр корзины |
| `begin_checkout` | Начало оформления |
| `add_shipping_info` | Добавление адреса доставки |
| `add_payment_info` | Добавление способа оплаты |
| `purchase` | Покупка |
| `refund` | Возврат |

### Другие E-commerce события
| Событие | Описание |
|---------|----------|
| `remove_from_cart` | Удаление из корзины |
| `add_to_wishlist` | Добавление в избранное |
| `view_item_list` | Просмотр списка товаров |
| `select_item` | Выбор товара из списка |
| `view_promotion` | Просмотр промо |
| `select_promotion` | Клик по промо |

### Параметры purchase
```json
{
  "name": "purchase",
  "params": {
    "currency": "USD",
    "transaction_id": "T_12345",
    "value": 30.03,
    "coupon": "SUMMER_FUN",
    "shipping": 3.33,
    "tax": 1.11,
    "customer_type": "new",
    "items": [
      {
        "item_id": "SKU_12345",
        "item_name": "Product Name",
        "price": 10.01,
        "quantity": 3,
        "item_brand": "Brand",
        "item_category": "Category1",
        "item_category2": "Category2",
        "item_variant": "green"
      }
    ]
  }
}
```

### Параметры товара (items)
| Параметр | Тип | Required | Описание |
|----------|-----|----------|----------|
| `item_id` | string | Yes* | ID товара |
| `item_name` | string | Yes* | Название товара |
| `price` | number | No | Цена за единицу |
| `quantity` | number | No | Количество (default: 1) |
| `item_brand` | string | No | Бренд |
| `item_category` | string | No | Категория 1 |
| `item_category2-5` | string | No | Категории 2-5 |
| `item_variant` | string | No | Вариант товара |
| `affiliation` | string | No | Магазин/партнёр |
| `coupon` | string | No | Купон |
| `discount` | number | No | Скидка |
| `index` | number | No | Позиция в списке |
| `location_id` | string | No | Google Place ID |

> *Нужен `item_id` ИЛИ `item_name`

---

## 🎯 Атрибуция трафика (campaign_details)

```json
{
  "name": "campaign_details",
  "params": {
    "campaign_id": "google_1234",
    "campaign": "Summer_fun",
    "source": "google",
    "medium": "cpc",
    "term": "summer+travel",
    "content": "logolink"
  }
}
```

| Параметр | Описание |
|----------|----------|
| `campaign_id` | ID кампании |
| `campaign` | Название кампании (utm_campaign) |
| `source` | Источник трафика (utm_source) |
| `medium` | Канал (utm_medium) |
| `term` | Ключевые слова (utm_term) |
| `content` | Контент для A/B тестов (utm_content) |

> ⚠️ `campaign_details` НЕ виден в DebugView, но есть в BigQuery export!

---

## 👤 Лиды (Lead Management)

| Событие | Описание |
|---------|----------|
| `generate_lead` | Лид создан |
| `qualify_lead` | Лид квалифицирован |
| `working_lead` | Работа с лидом |
| `disqualify_lead` | Лид дисквалифицирован |
| `close_convert_lead` | Лид конвертирован (покупка) |
| `close_unconvert_lead` | Лид не конвертирован |

---

## 📝 Другие полезные события

| Событие | Описание |
|---------|----------|
| `login` | Вход в аккаунт |
| `sign_up` | Регистрация |
| `search` | Поиск |
| `view_search_results` | Результаты поиска |
| `share` | Поделиться |
| `select_content` | Выбор контента |
| `tutorial_begin` | Начало туториала |
| `tutorial_complete` | Завершение туториала |

---

## 🚫 Зарезервированные имена

### Зарезервированные события (НЕЛЬЗЯ использовать)
- `ad_activeview`, `ad_click`, `ad_exposure`, `ad_query`, `ad_reward`
- `adunit_exposure`
- `app_clear_data`, `app_exception`, `app_install`, `app_remove`, `app_store_refund`, `app_update`, `app_upgrade`
- `dynamic_link_*`
- `error`
- `firebase_*`
- `first_open`, `first_visit`
- `notification_*`
- `os_update`
- `session_start` ⚠️
- `user_engagement`

### Зарезервированные параметры
- `firebase_conversion`
- Начинающиеся с: `_`, `firebase_`, `ga_`, `google_`, `gtag.`

### Зарезервированные user properties
- `first_open_time`, `first_visit_time`
- `last_deep_link_referrer`
- `user_id`
- `first_open_after_install`

---

## 🔧 Валидация

### Режимы валидации
| Режим | Описание |
|-------|----------|
| `RELAXED` | По умолчанию. Принимает данные, игнорирует лишнее |
| `ENFORCE_RECOMMENDATIONS` | Строгая проверка, отклоняет некорректные данные |

### Лимиты
| Параметр | Лимит |
|----------|-------|
| События за запрос | 25 |
| Параметров на событие | 25 |
| User properties | 25 |
| Длина имени события | 40 символов |
| Длина имени параметра | 40 символов |
| Длина значения параметра | 100 символов |
| Backdating событий | до 72 часов |

---

## 📋 Полный пример запроса

```json
{
  "client_id": "123456.7890123",
  "user_id": "user_abc123",
  "user_location": {
    "city": "Moscow",
    "region_id": "RU-MOW",
    "country_id": "RU",
    "subcontinent_id": "151",
    "continent_id": "150"
  },
  "device": {
    "category": "desktop",
    "language": "ru-RU",
    "screen_resolution": "1920x1080",
    "operating_system": "Windows",
    "operating_system_version": "10",
    "browser": "Chrome",
    "browser_version": "120.0.0.0"
  },
  "consent": {
    "ad_user_data": "GRANTED",
    "ad_personalization": "GRANTED"
  },
  "events": [
    {
      "name": "campaign_details",
      "params": {
        "source": "google",
        "medium": "cpc",
        "campaign": "winter_sale"
      }
    },
    {
      "name": "purchase",
      "params": {
        "session_id": "1234567890",
        "engagement_time_msec": 5000,
        "currency": "RUB",
        "transaction_id": "INV-12345",
        "value": 9999.00,
        "items": [
          {
            "item_id": "hosting-12m",
            "item_name": "Хостинг 12 месяцев",
            "price": 9999.00,
            "quantity": 1,
            "item_category": "Hosting"
          }
        ]
      }
    }
  ]
}
```

---

## 🗺️ Маппинг Matomo → GA4

| Matomo поле | GA4 поле |
|-------------|----------|
| `location_country` | `user_location.country_id` |
| `location_city` | `user_location.city` |
| `location_region` | `user_location.region_id` |
| `config_device_type` | `device.category` |
| `config_browser_name` | `device.browser` |
| `config_browser_version` | `device.browser_version` |
| `config_os` | `device.operating_system` |
| `config_os_version` | `device.operating_system_version` |
| `config_resolution` | `device.screen_resolution` |
| `referer_name` (utm_source) | `campaign_details.source` |
| `referer_keyword` (utm_medium) | `campaign_details.medium` |
| `referer_campaign` (utm_campaign) | `campaign_details.campaign` |

---

*Документация создана: 2026-01-17*
*Источник: Google Analytics Measurement Protocol Reference*
