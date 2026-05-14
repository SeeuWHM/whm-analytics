# WHM Analytics - Контекст проекта

> **Версия:** 1.2.0
> **Последнее обновление:** 16 января 2026
> **Статус:** ✅ Тестирование завершено, готово к production

---

## 📋 КРАТКОЕ ОПИСАНИЕ

**WHM Analytics** — server-side аналитика для WebHostMost.

**Цель:** Один лёгкий JS скрипт (~4KB) вместо 10+ тегов, отправка в наш Collector → Matomo → форвардеры (GA4, Meta, Yandex).

**Преимущества:**
- 0% потерь от AdBlock (1st party proxy)
- Один скрипт вместо кучи тегов
- Все данные в Matomo
- Добавить сайт = .htaccess + 2 строки JS
- Cross-domain attribution (gclid, utm сохраняются между доменами)

---

## 🧪 ТЕСТОВЫЕ САЙТЫ

| Site ID | Домен | Тип | Статус |
|---------|-------|-----|--------|
| 3 | analytics.ignat.best | Test Front | ✅ Работает |
| 4 | staging.whmtest.com | Test WHMCS | ✅ Работает |

---

## 🏗️ АРХИТЕКТУРА

```
┌─────────────────────────┐    ┌─────────────────────────┐
│  analytics.ignat.best   │    │  staging.whmtest.com    │
│  (Site 3 - Test Front)  │    │  (Site 4 - Test WHMCS)  │
│                         │    │                         │
│  whm.js v1.2.0          │    │  whm.js + PHP hook v2.0 │
│  /t/ proxy → collector  │    │  /t/ proxy → collector  │
└───────────┬─────────────┘    └───────────┬─────────────┘
            │                              │
            │  POST /t/collect             │  POST /t/collect
            │  (gclid, utm_source...)      │  (visitor_id, order...)
            └──────────────┬───────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│              analytics.webhostmost.com                  │
│                   (Nginx proxy)                         │
│                                                         │
│  /t/collect → localhost:9100/collect                    │
│  /t/whm.js  → localhost:9100/whm.js                     │
│  /t/health  → localhost:9100/health                     │
│  /          → Matomo (port 9000)                        │
└───────────────────────┬─────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────┐
│                    COLLECTOR                            │
│              Docker: whm-collector:9100                 │
│                                                         │
│  • Валидация site_id (whitelist)                        │
│  • Обогащение: GeoIP, User-Agent parsing                │
│  • Хеширование: email, phone → SHA256                   │
│  • Rate limit: 100 req/sec/IP                           │
│  • SQLite Visitor Store (First/Last Touch)              │
│                                                         │
│  → Matomo Tracking API                                  │
└───────────────────────┬─────────────────────────────────┘
                        │
        ┌───────────────┴───────────────┐
        ▼                               ▼
┌───────────────────┐         ┌───────────────────┐
│   SQLite Store    │         │      MATOMO       │
│  visitors.db      │         │  Docker port 9000 │
│                   │         │                   │
│ • First Touch     │         │ • Все события     │
│ • Last Touch      │         │ • Custom Dims 1-18│
│ • Attribution     │         │ • E-commerce      │
└───────────────────┘         └───────────────────┘
```

---

## 📁 СТРУКТУРА ПРОЕКТА

```
/opt/whm-analytics/
├── collector/
│   ├── app/
│   │   ├── main.py           # FastAPI endpoints
│   │   ├── schemas.py        # Pydantic models
│   │   ├── matomo.py         # Matomo Tracking API
│   │   ├── visitor_store.py  # SQLite First/Last Touch
│   │   ├── enricher.py       # IP → GeoIP, UA → Device
│   │   ├── validator.py      # Site whitelist
│   │   ├── geoip.py          # MaxMind lookup
│   │   ├── ua_parser.py      # User-Agent parsing
│   │   ├── limiter.py        # Rate limiting
│   │   └── config.py         # Settings
│   ├── static/
│   │   └── whm.js            # JS трекер v1.2.0
│   ├── Dockerfile
│   └── requirements.txt
├── config/
│   ├── sites.yaml            # Whitelist site_id
│   └── dimensions.yaml       # Custom Dimensions config
├── data/
│   ├── visitors.db           # SQLite visitor store
│   └── GeoLite2-City.mmdb    # GeoIP database
├── docs/
│   ├── API_CONTRACT.md       # API документация
│   ├── CONTEXT.md            # ← этот файл
│   ├── TRACKER_INSTALL.md    # Установка трекера
│   └── CUSTOM_DIMENSIONS.md  # Описание dimensions
├── .env                      # MATOMO_TOKEN
├── docker-compose.yml
├── README.md
├── ROADMAP.md
└── ARCHITECTURE.md
```

---

## 🔧 УСТАНОВКА ТРЕКЕРА

### 1. Proxy в .htaccess

```apache
# WHM Analytics Proxy - DO NOT REMOVE
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteCond %{REQUEST_URI} ^/t/
RewriteRule ^t/(.*)$ https://analytics.webhostmost.com/t/$1 [P,L,QSA]
</IfModule>
# END WHM Analytics Proxy
```

### 2. JS трекер

```html
<script src="/t/whm.js"></script>
<script>
whm('init', {
  siteId: 3,  // или 4 для WHMCS
  collectorUrl: '/t/collect',
  linkDomains: ['staging.whmtest.com']  // cross-domain
});
</script>
```

---

## 🔗 CROSS-DOMAIN TRACKING

### Visitor ID
Фронт → WHMCS сохраняет visitor_id через `?_whm_vid=` параметр.

```javascript
// На фронте
whm('init', {
  siteId: 3,
  collectorUrl: '/t/collect',
  linkDomains: ['staging.whmtest.com']
});

// Ссылка на WHMCS автоматически получит _whm_vid
// <a href="https://staging.whmtest.com/cart.php">
// станет
// <a href="https://staging.whmtest.com/cart.php?_whm_vid=abc123">
```

### Attribution Dimensions
Dimensions (gclid, utm_source, utm_medium и др.) сохраняются в SQLite:

1. Visitor приходит на Front с `?gclid=XXX&utm_source=google`
2. JS трекер отправляет событие с dimensions
3. Collector сохраняет в SQLite (First Touch + Last Touch)
4. Visitor переходит на WHMCS с `?_whm_vid=abc123`
5. WHMCS hook отправляет событие с тем же visitor_id
6. Collector достаёт dimensions из SQLite
7. В Matomo событие приходит С dimensions!

---

## 🛒 WHMCS HOOK

**Путь:** `/home/rjaazbmd/domains/staging.whmtest.com/public_html/includes/hooks/analytics_hook.php`

**Версия:** 2.0

**Функционал:**
- Hook на `InvoicePaid`
- Читает visitor_id из cookie `_whm_vid`
- Отправляет e-commerce событие в Collector
- Custom Dimensions 1-9 (attribution)
- Custom Dimensions 15-18 (plan_type, is_renewal, client_domain, product_name)
- SHA256 хеширование email/phone

---

## ✅ ПРОВЕРЕНО (16 января 2026)

- [x] Collector на analytics.webhostmost.com работает
- [x] JS трекер загружается через /t/whm.js
- [x] События записываются в Matomo
- [x] Cross-domain tracking работает (visitor_id через _whm_vid)
- [x] Attribution dimensions сохраняются в SQLite
- [x] WHMCS hook отправляет e-commerce с dimensions
- [x] AdBlock не блокирует /t/ endpoints
- [x] E2E тест: Front (gclid) → WHMCS (order) → gclid в Matomo ✅

**Тестовые данные:**
- Visitor `9cb467f15206b712`
- Site 3: gclid=REAL_TEST_GCLID_123, utm_source=google, utm_medium=cpc
- Site 4: user_id=68139, email_hash, phone_hash, те же gclid/utm!

---

## 🚀 PRODUCTION (НЕ ТРОГАТЬ)

| Site ID | Домен | Тип |
|---------|-------|-----|
| 1 | client.webhostmost.com | WHMCS Production |
| 2 | webhostmost.com + региональные | Фронты Production |

> ⚠️ Production настраивается ПОСЛЕ полного тестирования!

---

## 🐳 DOCKER КОМАНДЫ

```bash
# Статус
docker ps | grep whm-collector

# Логи
docker logs whm-collector --tail 50

# Rebuild
cd /opt/whm-analytics/collector
docker build -t whm-collector:latest .
docker stop whm-collector && docker rm whm-collector
docker run -d --name whm-collector --network whm-analytics \
  -p 127.0.0.1:9100:9100 \
  -v /opt/whm-analytics/config:/opt/whm-analytics/config:ro \
  -v /opt/whm-analytics/data:/opt/whm-analytics/data \
  -v /opt/whm-analytics/logs:/opt/whm-analytics/logs \
  -v /opt/whm-analytics/.env:/opt/whm-analytics/.env:ro \
  --restart unless-stopped whm-collector:latest

# SQLite проверка
docker exec whm-collector python3 -c "
import sqlite3
conn = sqlite3.connect('/opt/whm-analytics/data/visitors.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute('SELECT COUNT(*) as cnt FROM visitor_dimensions')
print('Visitors:', c.fetchone()['cnt'])
"
```

---

## 📊 CUSTOM DIMENSIONS

### Visit Scope (1-9) - Attribution
| # | Name | Описание |
|---|------|----------|
| 1 | fbc | Facebook Click ID |
| 2 | fbp | Facebook Browser ID |
| 3 | gclid | Google Click ID |
| 4 | yclid | Yandex Click ID |
| 5 | email_hash | SHA256 email |
| 6 | phone_hash | SHA256 phone |
| 7 | user_id | WHMCS User ID |
| 8 | utm_source | UTM Source |
| 9 | utm_medium | UTM Medium |

### Action Scope (10-14)
| # | Name | Описание |
|---|------|----------|
| 10 | utm_campaign | UTM Campaign |
| 11 | utm_content | UTM Content |
| 12 | utm_term | UTM Term |
| 13 | page_type | Тип страницы |
| 14 | scroll_depth | Глубина скролла % |

### WHMCS E-commerce (15-18)
| # | Name | Описание |
|---|------|----------|
| 15 | plan_type | free/trial/paid |
| 16 | is_renewal | yes/no |
| 17 | client_domain | Домен клиента |
| 18 | product_name | Название продукта |
