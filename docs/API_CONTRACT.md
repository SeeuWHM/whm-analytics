# WHM Analytics - API Contract

> **Версия:** 1.2.0
> **Последнее обновление:** 16 января 2026

## Base URL

**Production (через proxy):**
```
https://{your-domain}/t/collect
```

**Direct (если нужно):**
```
https://analytics.webhostmost.com/collect
```

> **Важно:** Рекомендуется использовать proxy через `/t/` на своём домене для обхода AdBlocker.
> Пример: `https://analytics.ignat.best/t/collect`

---

## Proxy Setup (.htaccess)

Для обхода блокировщиков рекламы добавьте в `.htaccess` вашего сайта:

```apache
# WHM Analytics Proxy - DO NOT REMOVE
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteCond %{REQUEST_URI} ^/t/
RewriteRule ^t/(.*)$ https://analytics.webhostmost.com/t/$1 [P,L,QSA]
</IfModule>
# END WHM Analytics Proxy
```

---

## Endpoints

### POST /collect

Основной endpoint для сбора событий.

**Request:**
```json
{
  "event_type": "pageview|event|ecommerce|goal",
  "site_id": 3,
  "visitor_id": "abcd1234abcd1234",
  "url": "https://example.com/page",
  "title": "Page Title",
  "referrer": "https://google.com",
  "user_id": "client_123",
  
  "event_category": "Button",
  "event_action": "click",
  "event_name": "CTA",
  "event_value": 42,
  
  "order_id": "ORD-123",
  "revenue": 99.99,
  
  "goal_id": 1,
  
  "dimension1": "fb.1.xxx",
  "dimension2": "fb.1.yyy",
  "dimension3": "GCLID123",
  "dimension4": "YCLID456",
  "dimension5": "sha256_email",
  "dimension6": "sha256_phone",
  "dimension7": "user_id",
  "dimension8": "google",
  "dimension9": "cpc",
  
  "screen_width": 1920,
  "screen_height": 1080
}
```

**Required fields:**
- `site_id` (int, 1-999) - ID сайта в Matomo
- `visitor_id` (string, 16-32 hex chars) - Уникальный ID посетителя
- `url` (string, max 2048) - URL страницы

**Response (success):**
```json
{
  "status": "ok",
  "visitor_id": "abcd1234"
}
```

**Response (error):**
```json
{
  "status": "error",
  "code": "site_not_allowed",
  "message": "Site ID not allowed: 999"
}
```

**Response (skipped):**
```json
{
  "status": "skipped",
  "code": "bot",
  "message": "Bot detected"
}
```

**Error codes:**
| Code | Description |
|------|-------------|
| `site_not_allowed` | Site ID не разрешён |
| `validation_error` | Ошибка валидации данных |
| `rate_limited` | Превышен лимит запросов |
| `bot` | Обнаружен бот |

---

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": 1768353050
}
```

---

### GET /health/ready

Readiness check (включает проверку Matomo).

**Response:**
```json
{
  "status": "ready",
  "matomo": "ok",
  "timestamp": 1768353050
}
```

---

### GET /whm.js

JS трекер (минифицированный, ~4KB gzip).

**Headers:**
```
Content-Type: application/javascript
Cache-Control: public, max-age=3600
```

---

### GET /docs

Swagger UI с интерактивной документацией API.

---

### GET /openapi.json

OpenAPI 3.0 спецификация в JSON формате.

---

## Event Types

### pageview
Просмотр страницы. Отправляется автоматически при загрузке.

```json
{
  "event_type": "pageview",
  "site_id": 3,
  "visitor_id": "abcd1234abcd1234",
  "url": "https://example.com/page",
  "title": "Page Title"
}
```

### event
Произвольное событие (клик, скролл, форма).

```json
{
  "event_type": "event",
  "site_id": 3,
  "visitor_id": "abcd1234abcd1234",
  "url": "https://example.com/page",
  "event_category": "Button",
  "event_action": "click",
  "event_name": "CTA Button",
  "event_value": 42
}
```

### ecommerce
E-commerce событие (покупка, корзина).

```json
{
  "event_type": "ecommerce",
  "site_id": 3,
  "visitor_id": "abcd1234abcd1234",
  "url": "https://example.com/checkout",
  "order_id": "ORD-12345",
  "revenue": 99.99,
  "items": "[{\"sku\":\"SKU-1\",\"name\":\"Product\",\"price\":99.99,\"quantity\":1}]"
}
```

### goal
Конверсия (цель в Matomo).

```json
{
  "event_type": "goal",
  "site_id": 3,
  "visitor_id": "abcd1234abcd1234",
  "url": "https://example.com/thank-you",
  "goal_id": 1,
  "revenue": 50
}
```

---

## Custom Dimensions

### Visit Scope (Attribution) - dimensions 1-9

| Dimension | Field | Description |
|-----------|-------|-------------|
| dimension1 | fbc | Facebook Click ID |
| dimension2 | fbp | Facebook Browser ID |
| dimension3 | gclid | Google Click ID |
| dimension4 | yclid | Yandex Click ID |
| dimension5 | email_hash | SHA-256 хеш email |
| dimension6 | phone_hash | SHA-256 хеш телефона |
| dimension7 | user_id | ID клиента WHMCS |
| dimension8 | utm_source | UTM Source |
| dimension9 | utm_medium | UTM Medium |

### Action Scope - dimensions 10-14

| Dimension | Field | Description |
|-----------|-------|-------------|
| dimension10 | utm_campaign | UTM Campaign |
| dimension11 | utm_content | UTM Content |
| dimension12 | utm_term | UTM Term |
| dimension13 | page_type | Тип страницы |
| dimension14 | scroll_depth | Глубина скролла % |

### WHMCS E-commerce - dimensions 15-18

| Dimension | Field | Description |
|-----------|-------|-------------|
| dimension15 | plan_type | `free` / `trial` / `paid` |
| dimension16 | is_renewal | `yes` / `no` |
| dimension17 | client_domain | Домен клиента |
| dimension18 | product_name | Название продукта |

---

## Visitor Store (Cross-Domain Attribution)

Collector использует SQLite для сохранения attribution dimensions по visitor_id.

**Логика:**
1. Visitor приходит на Front с `?gclid=XXX&utm_source=google`
2. Dimensions сохраняются в SQLite (First Touch + Last Touch)
3. Visitor переходит на WHMCS с `?_whm_vid=abc123`
4. Collector видит visitor_id, достаёт сохранённые dimensions
5. Dimensions добавляются к событию перед отправкой в Matomo

**First Touch:** Сохраняется при первом визите, никогда не меняется
**Last Touch:** Обновляется при каждом визите с новыми params

---

## Validation Rules

| Field | Rule |
|-------|------|
| `site_id` | Integer 1-999, must be whitelisted |
| `visitor_id` | 16-32 hex characters (0-9, a-f) |
| `url` | Max 2048 characters |
| `user_id` | Max 200 characters |
| `referrer` | Max 2048 characters |
| `title` | Max 500 characters |
| `event_category` | Max 255 characters |
| `event_action` | Max 255 characters |
| `event_name` | Max 255 characters |
| `order_id` | Max 100 characters |
| `revenue` | Float >= 0 |
| `goal_id` | Integer >= 0 |
| `dimensionN` | Max 255 characters |

---

## Rate Limiting

- **Limit:** 100 requests per second per IP
- **Burst:** 50 requests
- **Response on limit:** HTTP 429 Too Many Requests

---

## CORS

CORS заголовки:
- `Access-Control-Allow-Origin: *`
- `Access-Control-Allow-Methods: POST, OPTIONS`
- `Access-Control-Allow-Headers: Content-Type`

---

## Cross-Domain Tracking

Для связи визитов между доменами (front → WHMCS) используется параметр `_whm_vid` в URL.

**Пример:**
```
https://staging.whmtest.com/cart.php?pid=1&_whm_vid=abcd1234abcd1234
```

JS трекер автоматически:
1. Добавляет `_whm_vid` к ссылкам на `linkDomains`
2. Читает `_whm_vid` из URL при загрузке страницы
3. Использует тот же visitor_id для связи визитов

WHMCS PHP hook:
1. Читает `_whm_vid` из cookie (JS трекер ставит его)
2. Отправляет тот же visitor_id при покупке
3. Collector восстанавливает dimensions из SQLite

---

## Authentication

Публичный API, аутентификация не требуется.
Защита через whitelist site_id и rate limiting.

---

## Versioning

- **Collector:** v1.0.0
- **JS Tracker:** v1.2.0
- **WHMCS Hook:** v2.0
