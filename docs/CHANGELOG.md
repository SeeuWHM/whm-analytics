# WHM Analytics Changelog

## 2026-01-18 - Major Fixes Session

### whm.js v1.7.0 - SPA Navigation Tracking
**File:** `/opt/whm-analytics/collector/static/whm.js`

**Problem:** На SPA сайтах (analytics.ignat.best - Next.js) переходы между страницами 
не трекались, потому что URL менялся через `history.pushState()` без перезагрузки страницы.

**Solution:** Добавлен перехват History API:
- `history.pushState()` - перехватывается
- `history.replaceState()` - перехватывается  
- `popstate` event - слушается (кнопки назад/вперёд)

При изменении URL (кроме hash-only изменений) автоматически отправляется `pageview`.

**Code location:** Lines 524-581 в whm.js
```javascript
function setupSpaTracking() {
  var originalPushState = history.pushState;
  history.pushState = function() {
    originalPushState.apply(this, arguments);
    setTimeout(trackSpaNavigation, 10);
  };
  // ... replaceState и popstate аналогично
}
```

**Verified:** Meta Events Manager показывает PageViews с разными URL при SPA навигации.

---

### universal_forwarder.py - Dedup Fix for GA4

**Problem:** GA4 отправлял одни и те же события повторно каждые 30 секунд,
потому что проверка `is_sent()` делалась только для Meta, не для GA4.

**Solution:** Добавлена фильтрация через dedup ДО обработки:
```python
# Page Views
page_views = self.fetcher.fetch_page_views(...)
new_page_views = [e for e in page_views if not self.dedup_db.is_sent(e['event_hash'], 'ga4')]

# Conversions
new_conversions = [e for e in conversions if not self.dedup_db.is_sent(e['event_hash'], 'ga4')]

# Begin Checkout
new_checkouts = [e for e in begin_checkouts if not self.dedup_db.is_sent(e['event_hash'], 'ga4')]
```

**Logs now show:**
```
📄 Found 0 new page_views (of 8 total)
```
вместо "Found 8 new page_views" каждый цикл.

---

### universal_forwarder.py - Revenue-Based Event Detection

**Problem:** Заказ с revenue=$90 отправлялся как StartTrial потому что 
`plan_type=trial` в Matomo (пользователь переключался между планами в разных табах).

**Solution:** Revenue > 0 = Purchase, независимо от plan_type:
```python
# ГЛАВНОЕ ПРАВИЛО: revenue > 0 = Purchase
if revenue > 0:
    event_name = 'purchase'
    success = sender.send_purchase(event, items)
elif plan_type == 'trial':
    event_name = 'start_trial'
    success = sender.send_start_trial(event, items)
elif plan_type == 'free':
    # GA4 gets it, Meta skips
    ...
```

---

### DedupDB Schema Fix

**Problem:** PRIMARY KEY был только `event_hash`, что не позволяло 
отправить одно событие в GA4 И Meta (второй INSERT падал с UNIQUE violation).

**Solution:** Composite primary key:
```sql
PRIMARY KEY (event_hash, destination)
```

Теперь одно событие может быть записано для разных destinations.

---

## Configuration Reference

### Sites
- Site 3: analytics.ignat.best (front)
- Site 4: staging.whmtest.com (WHMCS staging)

### Matomo Custom Dimensions
| ID | Name | Scope |
|----|------|-------|
| 1 | fbc | visit |
| 2 | fbp | visit |
| 3 | gclid | visit |
| 4 | yclid | visit |
| 5 | email_hash | visit |
| 6 | phone_hash | visit |
| 7 | user_id | visit |
| 8 | utm_source | visit |
| 9 | utm_medium | visit |
| 15 | plan_type | action |
| 16 | is_renewal | action |
| 17 | client_domain | action |
| 18 | product_name | action |

### Meta CAPI
- Pixel ID: 3778444585786828
- Test Event Code: TEST82264

### Files
- Forwarder: `/opt/whm-analytics/forwarder/universal_forwarder.py`
- Tracker JS: `/opt/whm-analytics/collector/static/whm.js`
- Dedup DB: `/opt/whm-analytics/data/forwarder_sent.db`
- Sites config: `/opt/whm-analytics/forwarder/sites.yaml`
- WHMCS Hook (staging): `/opt/whm-analytics/whmcs_hook_staging.php`
