# WHM Analytics - Server-Side Analytics Platform

> **Цель:** 100% server-side аналитика без клиентских тегов, с форвардингом во все платформы (GA4, Meta, Yandex и др.)
> **Версия:** 1.2.1
> **Последнее обновление:** 17 января 2026

## 🎯 Проблемы которые решаем

1. **Client-side теги тормозят сайт** — Matomo Tag Manager, GA, FB Pixel = +500-1000ms
2. **Блокируются AdBlock/Brave** — теряем 20-40% данных
3. **Куча cookies** — от каждого трекера свои
4. **Сложно масштабировать** — каждый новый сайт = настройка тегов
5. **Cross-domain проблемы** — теряем данные при переходе front → WHMCS

## ✅ Решение

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ФРОНТЕНД (JS трекер ~4KB)                           │
├─────────────────────────────────────────────────────────────────────────────┤
│  • Лёгкий скрипт whm.js v1.2.0                                             │
│  • Отправляет на свой домен /t/collect (1st party, не блокируется!)        │
│  • Cross-domain tracking через _whm_vid параметр                           │
│  • Автоматический сбор UTM, gclid, fbclid, yclid                           │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │ POST /t/collect (proxy)
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      WHM ANALYTICS COLLECTOR (FastAPI)                      │
├─────────────────────────────────────────────────────────────────────────────┤
│  • Pydantic валидация входных данных                                       │
│  • Server-side обогащение: IP → GeoIP, User-Agent → Device                 │
│  • SQLite Visitor Store (First Touch + Last Touch attribution)             │
│  • Rate limiting: 100 req/sec per IP                                       │
│  • Запись в Matomo через Tracking API                                      │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              MATOMO                                         │
│                     (единственный источник правды)                          │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
               GA4 Forward    Meta CAPI      Yandex Metrika
```

## 🚀 Быстрый старт

### 1. Добавить proxy в .htaccess сайта

```apache
# WHM Analytics Proxy
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteCond %{REQUEST_URI} ^/t/
RewriteRule ^t/(.*)$ https://analytics.webhostmost.com/t/$1 [P,L,QSA]
</IfModule>
```

### 2. Добавить JS трекер

```html
<script src="/t/whm.js"></script>
<script>
whm('init', {
  siteId: YOUR_SITE_ID,
  collectorUrl: '/t/collect',
  linkDomains: ['staging.whmtest.com']
});
</script>
```

Готово! Данные пойдут в Matomo.

## 📊 Что собирается

| Данные | Источник |
|--------|----------|
| Pageviews, title, referrer | JS трекер |
| UTM параметры | JS (из URL) |
| gclid, fbclid, yclid | JS (из URL) |
| Geo (страна, город) | Server (GeoIP) |
| Browser, OS, Device | Server (User-Agent) |
| User ID, Email (hashed) | JS или WHMCS hook |

## 🔗 Cross-Domain Tracking

Автоматическая связь визитов между фронтом и WHMCS:

```javascript
// Фронт
whm('init', {
  siteId: 3,
  collectorUrl: '/t/collect',
  linkDomains: ['staging.whmtest.com']
});

// Ссылка https://staging.whmtest.com/cart.php
// Станет https://staging.whmtest.com/cart.php?_whm_vid=abcd1234
```

**Visitor Store (SQLite):** Attribution dimensions (gclid, utm_source, etc.) сохраняются в базе и автоматически восстанавливаются при переходе между доменами.

## 🧪 Тестовые окружения

| Site ID | Домен | Тип | Статус |
|---------|-------|-----|--------|
| 3 | analytics.ignat.best | Test Front | ✅ Работает |
| 4 | staging.whmtest.com | Test WHMCS | ✅ Работает |

## 📁 Структура

```
/opt/whm-analytics/
├── collector/           # FastAPI сервис (Docker)
│   ├── app/
│   │   ├── main.py           # Endpoints
│   │   ├── schemas.py        # Pydantic модели
│   │   ├── matomo.py         # Matomo API
│   │   ├── visitor_store.py  # SQLite First/Last Touch
│   │   ├── enricher.py       # IP → GeoIP, UA → Device
│   │   ├── validator.py      # Валидация site_id
│   │   └── ...
│   └── static/
│       └── whm.js       # JS трекер v1.2.0
├── config/
│   ├── sites.yaml       # Разрешённые сайты
│   └── dimensions.yaml  # Custom Dimensions
├── data/
│   ├── visitors.db      # SQLite visitor store
│   └── GeoLite2-City.mmdb
├── docs/
│   ├── API_CONTRACT.md
│   ├── CONTEXT.md
│   ├── TRACKER_INSTALL.md
│   └── CUSTOM_DIMENSIONS.md
└── docker-compose.yml
```

## 🐳 Docker

```bash
# Запуск
docker-compose up -d

# Логи
docker logs whm-collector --tail 50

# Rebuild
cd /opt/whm-analytics/collector && docker build -t whm-collector:latest . && docker restart whm-collector
```

## 📐 Custom Dimensions

### Visit Scope (Attribution)

| # | Name | Description |
|---|------|-------------|
| 1 | fbc | Facebook Click ID |
| 2 | fbp | Facebook Browser ID |
| 3 | gclid | Google Click ID |
| 4 | yclid | Yandex Click ID |
| 5 | email_hash | SHA256 email |
| 6 | phone_hash | SHA256 phone |
| 7 | user_id | WHMCS User ID |
| 8 | utm_source | UTM Source |
| 9 | utm_medium | UTM Medium |

### WHMCS E-commerce

| # | Name | Description |
|---|------|-------------|
| 15 | plan_type | `free` / `trial` / `paid` |
| 16 | is_renewal | `yes` / `no` |
| 17 | client_domain | Домен клиента |
| 18 | product_name | Название продукта |

## 📚 Документация

- [API Contract](docs/API_CONTRACT.md) - Endpoints, форматы запросов
- [OpenAPI Spec](docs/openapi.yaml) - OpenAPI 3.1 спецификация
- [Context](docs/CONTEXT.md) - Полный контекст проекта
- [Tracker Install](docs/TRACKER_INSTALL.md) - Установка JS трекера
- [Custom Dimensions](docs/CUSTOM_DIMENSIONS.md) - Описание всех dimensions
- [Architecture](ARCHITECTURE.md) - Детальная архитектура
- [Roadmap](ROADMAP.md) - План работ

**Swagger UI:** https://analytics.webhostmost.com/docs
**ReDoc:** https://analytics.webhostmost.com/redoc

## ✅ Текущий статус

- [x] Collector (FastAPI + Pydantic + Docker)
- [x] JS Tracker v1.2.0
- [x] Cross-domain tracking (visitor_id + dimensions)
- [x] SQLite Visitor Store (First Touch + Last Touch)
- [x] AdBlock bypass через proxy
- [x] WHMCS PHP Hook v2.0 (InvoicePaid с всеми dimensions)
- [x] Тестовые окружения работают
- [x] E2E тест: Front → WHMCS → Order (gclid сохранён!)
- [x] **GA4 Forwarder** (Measurement Protocol)
- [x] **Meta CAPI Forwarder** (Conversion API)
- [ ] Yandex Metrika Forwarder
- [ ] Production миграция

---

## 🚀 GA4 Forwarder (Matomo → GA4)

### Конфигурация

| Параметр | Значение |
|----------|----------|
| GA4 Property ID | 520394907 |
| Measurement ID | G-ZYWK432ZT2 |
| API Secret | BJJnpYJSQJGggSNMXOvEtg |
| Endpoint | /mp/collect |

### Что передаётся

| Событие | GA4 Event | Параметры |
|---------|-----------|-----------|
| Purchase (invoice_paid) | `purchase` | transaction_id, value, currency, items[] |
| Free Panel Registration | `sign_up` | method="free_panel", plan_name |
| Trial Start | `begin_checkout` | plan_name, value |
| Page View | `page_view` | page_location, page_title |

### Маппинг items[] для purchase

```json
{
  "item_id": "plan_slug",
  "item_name": "План название",
  "item_category": "free|trial|paid",
  "price": 9.99,
  "quantity": 1
}
```

### Особенности и ограничения

1. **Realtime карта не работает** - ограничение GA4 Measurement Protocol. События приходят, но карта показывает все точки в центре Африки (0,0). Это documented limitation Google.

2. **client_id** - генерируется стабильно из Matomo visitor_id:
   ```python
   def _build_client_id(visitor_id):
       hash_obj = hashlib.md5(visitor_id.encode())
       hash_hex = hash_obj.hexdigest()
       numeric = int(hash_hex[:10], 16) % (10**10)
       timestamp = int(hash_hex[10:18], 16) % (10**10)
       return f"{numeric}.{timestamp}"
   ```

3. **Геолокация** - передаём через `user_location` параметр:
   - Форматы: `US`, `US-CA`, `JP-13` (ISO 3166-2)
   - Работает для отчётов, но НЕ для Realtime карты

### Запуск форвардера

```bash
cd /opt/whm-analytics/forwarder
source venv/bin/activate
python universal_forwarder.py --live
```

### Логи

```bash
tail -f /opt/whm-analytics/forwarder/forwarder.log
```

---

## 📱 Meta CAPI Forwarder (Matomo → Facebook)

### Конфигурация

| Параметр | Значение |
|----------|----------|
| Pixel ID | 1088554satisfying 732422997 |
| Access Token | (в конфиге) |
| Endpoint | /events |

### Что передаётся

| Событие | FB Event | Параметры |
|---------|----------|-----------|
| Purchase | `Purchase` | value, currency, content_ids, fbc, fbp |
| Lead (trial) | `Lead` | content_name, fbc |
| CompleteRegistration | `CompleteRegistration` | content_name |

### User Data для matching

```json
{
  "em": "sha256(email)",
  "ph": "sha256(phone)",
  "client_ip_address": "user_ip",
  "client_user_agent": "user_agent",
  "fbc": "fb.1.timestamp.gclid",
  "fbp": "fb.1.timestamp.random"
}
```

---

## 📞 Контакты

- **Email:** ignat@webhostmost.com
- **Matomo:** https://analytics.webhostmost.com
- **Collector:** https://analytics.webhostmost.com/t/
