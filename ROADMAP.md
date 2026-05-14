# WHM Analytics - ROADMAP

> **Статус:** 🟢 Основной функционал готов, тестирование завершено
> **Начало:** 13 января 2026
> **Последнее обновление:** 16 января 2026

---

## ✅ Фаза 0: Планирование (ГОТОВО)
- [x] Анализ текущей системы
- [x] Определение архитектуры
- [x] Создание структуры папок
- [x] Документация (CONTEXT.md)
- [x] Конфигурация (sites.yaml, dimensions.yaml)

---

## ✅ Фаза 1: Collector (ГОТОВО)

**Цель:** API сервис для приёма событий аналитики

- [x] `requirements.txt` — зависимости
- [x] `app/main.py` — FastAPI приложение, endpoints
- [x] `app/schemas.py` — Pydantic модели валидации
- [x] `app/validator.py` — валидация входных данных
- [x] `app/enricher.py` — обогащение данных (IP, Geo, UA)
- [x] `app/geoip.py` — MaxMind GeoIP2 lookup
- [x] `app/ua_parser.py` — парсинг браузера, ОС, устройства
- [x] `app/matomo.py` — отправка в Matomo Tracking API
- [x] `app/limiter.py` — rate limiting (100 req/sec на IP)
- [x] `app/visitor_store.py` — SQLite хранилище для cross-domain attribution
- [x] `Dockerfile` + `docker-compose.yml`
- [x] Health check endpoints (/health, /health/ready)

---

## ✅ Фаза 2: JS Tracker (ГОТОВО)

**Цель:** Лёгкий скрипт для сбора данных в браузере

- [x] `static/whm.js` — трекер v1.2.0
- [x] Pageview автоматический
- [x] Events (event, ecommerce, goal)
- [x] Cross-domain tracking через `_whm_vid`
- [x] linkDomains конфигурация
- [x] Сбор UTM, gclid, fbclid, yclid
- [x] SHA256 хеширование email/phone
- [x] Debug режим

---

## ✅ Фаза 3: AdBlock Bypass (ГОТОВО)

**Цель:** Обход блокировщиков рекламы

- [x] Домен `analytics.webhostmost.com` (вместо analytics.*)
- [x] Nginx reverse proxy → localhost:9100
- [x] SSL сертификат
- [x] `.htaccess` proxy на сайтах (`/t/` → analytics.webhostmost.com)
- [x] Тестирование с разными блокировщиками

---

## ✅ Фаза 4: Тестовые сайты (ГОТОВО)

**Цель:** Создать чистые сайты для тестирования

- [x] Site 3: analytics.ignat.best (Test Front)
- [x] Site 4: staging.whmtest.com (Test WHMCS)
- [x] Custom Dimensions настроены (18 dimensions)
- [x] JS трекер установлен на обоих сайтах
- [x] Cross-domain связка работает
- [x] Данные появляются в Matomo

---

## ✅ Фаза 5: WHMCS Hooks (ГОТОВО)

**Цель:** Server-side события из WHMCS

- [x] Hook: `InvoicePaid` → purchase event
- [x] Передача visitor_id из cookie `_whm_vid`
- [x] Custom Dimensions 1-9 (attribution)
- [x] Custom Dimensions 15-18 (plan_type, is_renewal, client_domain, product_name)
- [x] E-commerce tracking (order_id, revenue, items)
- [x] SHA256 хеширование email/phone
- [x] Тест на staging.whmtest.com ✅

**Путь к hook:**
```
/home/rjaazbmd/domains/staging.whmtest.com/public_html/includes/hooks/analytics_hook.php
```

---

## ✅ Фаза 6: Cross-Domain Attribution (ГОТОВО)

**Цель:** Сохранение attribution данных между доменами

- [x] SQLite visitor_store.py
- [x] First Touch (никогда не меняется)
- [x] Last Touch (обновляется с новыми params)
- [x] Автоматическое восстановление dimensions на WHMCS
- [x] E2E тест: Front (gclid) → WHMCS (order) → gclid сохранён! ✅

**Тест 16 января 2026:**
- Visitor `9cb467f15206b712` пришёл на front с `gclid=REAL_TEST_GCLID_123`
- Перешёл на WHMCS, зарегистрировался (user_id=68139)
- В Matomo на Site 4: gclid, utm_source, utm_medium сохранены!

---

## ⏳ Фаза 7: Форвардеры (TODO)

**Цель:** Отправка данных в внешние платформы

- [ ] Рефакторинг существующих форвардеров в единую структуру
- [ ] GA4 Forwarder (Measurement Protocol)
- [ ] Meta CAPI Forwarder
- [ ] Yandex Metrika Forwarder
- [ ] APScheduler вместо cron
- [ ] Docker контейнер для форвардеров

**Ожидаемое время:** 2-3 дня

---

## ⏳ Фаза 8: Production (TODO)

**Цель:** Миграция рабочих сайтов

### 8.1 Фронты
- [ ] webhostmost.com
- [ ] Региональные домены (28 сайтов)
- [ ] Добавить .htaccess proxy на каждый
- [ ] Интегрировать whm.js в Next.js билд

### 8.2 WHMCS
- [ ] client.webhostmost.com
- [ ] Установить новые hooks
- [ ] Добавить .htaccess proxy
- [ ] Добавить JS трекер в header

### 8.3 Мониторинг
- [ ] Alerting при падении collector
- [ ] Grafana dashboard
- [ ] Log rotation

**Ожидаемое время:** 3-5 дней

---

## 📊 ПРОГРЕСС

| Фаза | Статус | Готовность |
|------|--------|------------|
| Планирование | ✅ Готово | 100% |
| Collector | ✅ Готово | 100% |
| JS Tracker | ✅ Готово | 100% |
| AdBlock Bypass | ✅ Готово | 100% |
| Тестовые сайты | ✅ Готово | 100% |
| WHMCS Hooks | ✅ Готово | 100% |
| Cross-Domain Attribution | ✅ Готово | 100% |
| Форвардеры | ⏳ Ожидает | 0% |
| Production | ⏳ Ожидает | 0% |

**Общий прогресс: ~80%**

---

## 📝 ЗАМЕТКИ

### Что работает:
- Collector стабилен (Docker healthy)
- Cross-domain tracking через URL параметр `_whm_vid` надёжен
- Attribution dimensions сохраняются в SQLite и восстанавливаются на WHMCS
- Proxy через .htaccess обходит AdBlock
- WHMCS hook v2.0 отправляет все 18 dimensions + e-commerce

### Технические детали:
- SQLite база: `/opt/whm-analytics/data/visitors.db`
- TTL записей: 90 дней (автоочистка)
- First Touch vs Last Touch attribution

### Приоритеты:
1. ~~Закончить WHMCS hooks~~ ✅
2. ~~E2E тест с реальной покупкой~~ ✅
3. Форвардеры и production
