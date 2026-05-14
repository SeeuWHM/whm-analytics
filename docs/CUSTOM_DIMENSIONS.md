# Custom Dimensions - WHM Analytics

> **Версия:** 1.2.0
> **Последнее обновление:** 16 января 2026
> **Сайты:** 3 (analytics.ignat.best), 4 (staging.whmtest.com)

## Visit Scope Dimensions (Attribution)

Эти dimensions сохраняются в SQLite visitor_store и автоматически восстанавливаются при cross-domain переходах.

| CD# | Имя | Описание | Источник |
|-----|-----|----------|----------|
| 1 | fbc | Facebook Click ID | Cookie `_fbc` / URL `fbclid` |
| 2 | fbp | Facebook Browser ID | Cookie `_fbp` |
| 3 | gclid | Google Click ID | URL param `gclid` |
| 4 | yclid | Yandex Click ID | URL param `yclid` |
| 27 | msclkid | Microsoft Click ID | URL param `msclkid` / Cookie `_msclkid` |
| 5 | email_hash | SHA256 хеш email | `whm('set', 'email', ...)` или WHMCS hook |
| 6 | phone_hash | SHA256 хеш телефона | `whm('set', 'phone', ...)` или WHMCS hook |
| 7 | user_id | ID клиента WHMCS | `whm('set', 'userId', ...)` или WHMCS hook |
| 8 | utm_source | UTM Source | URL param |
| 9 | utm_medium | UTM Medium | URL param |
| 15 | plan_type | Тип плана | `free` / `trial` / `paid` |
| 16 | is_renewal | Продление? | `yes` / `no` |
| 17 | client_domain | Домен клиента | Из WHMCS (tblhosting.domain) |
| 18 | product_name | Название продукта | Чистое из tblproducts.name |

## Action Scope Dimensions

| CD# | Имя | Описание |
|-----|-----|----------|
| 10 | utm_campaign | UTM Campaign |
| 11 | utm_content | UTM Content |
| 12 | utm_term | UTM Term |
| 13 | page_type | Тип страницы |
| 14 | scroll_depth | Глубина скролла (%) |

---

## Типы планов (plan_type)

| Значение | Описание | Как определить |
|----------|----------|----------------|
| `free` | Бесплатный план | `tblproductgroups.name LIKE '%Free Plan%'` |
| `trial` | Триал (14 дней) | `tblproductgroups.name LIKE '%Trial%'` |
| `paid` | Платный план | Всё остальное |

---

## Renewal vs First Purchase (is_renewal)

```sql
CASE
  WHEN h.regdate = DATE(i.date) THEN 'no'   -- первая покупка
  WHEN h.regdate < DATE(i.date) THEN 'yes'  -- продление
END
```

---

## Использование в Tracking API

### JS Tracker
```javascript
whm('set', 'dimension15', 'paid');
whm('set', 'dimension16', 'no');
whm('set', 'dimension17', 'example.com');
whm('set', 'dimension18', '[USA] Holiday Pro');
```

### PHP (WHMCS Hook)
```php
$params['dimension15'] = 'paid';
$params['dimension16'] = 'no';
$params['dimension17'] = $hosting->domain;
$params['dimension18'] = $product->name;
```

---

## Важно для Forwarders

**is_renewal = 'yes'** → НЕ отправлять в рекламные аналитики (GA4, Meta CAPI)!

Причина: Renewal не является новой конверсией с рекламы. Если отправить - завысит ROI рекламы.

```python
# В forwarder
if event.get('dimension16') == 'yes':
    log("Skipping renewal - not sending to ad platforms")
    continue
```

---

## Cross-Domain Attribution (Visitor Store)

Dimensions 1-9 и 27 сохраняются в SQLite базе `/opt/whm-analytics/data/visitors.db` и автоматически восстанавливаются при cross-domain переходах.

### Логика работы:

1. Visitor приходит на Front с `?gclid=XXX&utm_source=google`
2. JS трекер отправляет событие → Collector сохраняет в SQLite
3. Visitor переходит на WHMCS с `?_whm_vid=abc123`
4. WHMCS hook отправляет событие с тем же visitor_id (БЕЗ dimensions)
5. Collector достаёт dimensions из SQLite по visitor_id
6. Событие уходит в Matomo С dimensions!

### First Touch vs Last Touch:

- **First Touch** — сохраняется при первом визите, никогда не меняется
- **Last Touch** — обновляется при каждом визите с новыми params

Пример:
```
Визит 1: gclid=AAA, utm_source=google
Визит 2: (без params)
Визит 3: utm_source=facebook

First Touch: gclid=AAA, utm_source=google
Last Touch:  gclid=AAA, utm_source=facebook (gclid сохранён!)
```

### SQLite Schema:

```sql
CREATE TABLE visitor_dimensions (
    visitor_id TEXT PRIMARY KEY,
    first_dimensions TEXT NOT NULL,  -- JSON
    first_seen INTEGER NOT NULL,
    last_dimensions TEXT NOT NULL,   -- JSON
    last_seen INTEGER NOT NULL,
    user_id TEXT,
    hit_count INTEGER DEFAULT 1
);
```

### TTL:

Записи автоматически удаляются через 90 дней неактивности.

### Проверка:

```bash
docker exec whm-collector python3 -c "
import sqlite3, json
conn = sqlite3.connect('/opt/whm-analytics/data/visitors.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute('SELECT * FROM visitor_dimensions ORDER BY last_seen DESC LIMIT 3')
for row in c.fetchall():
    print('Visitor:', row['visitor_id'][:8])
    print('  First:', json.loads(row['first_dimensions']))
    print('  Last:', json.loads(row['last_dimensions']))
    print('  Hits:', row['hit_count'])
    print()
"
```

