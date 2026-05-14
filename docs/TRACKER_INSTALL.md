# WHM Analytics Tracker v1.2.0 - Инструкция по установке

> **Последнее обновление:** 16 января 2026

## Шаг 1: Настройка Proxy (.htaccess)

Добавьте в `.htaccess` вашего сайта (в корне `public_html/`):

```apache
# WHM Analytics Proxy - DO NOT REMOVE
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteCond %{REQUEST_URI} ^/t/
RewriteRule ^t/(.*)$ https://analytics.webhostmost.com/t/$1 [P,L,QSA]
</IfModule>
# END WHM Analytics Proxy
```

> **Важно:** Proxy обязателен для обхода блокировщиков рекламы!

---

## Шаг 2: Добавление JS трекера

### Вариант A: Обычный сайт (HTML)

Добавьте в `<head>`:

```html
<script src="/t/whm.js"></script>
<script>
whm('init', {
  siteId: YOUR_SITE_ID,
  collectorUrl: '/t/collect',
  linkDomains: ['staging.whmtest.com']  // для cross-domain на WHMCS
});
</script>
```

### Вариант B: Next.js (React)

В `_app.tsx` или layout:

```tsx
import Script from 'next/script';

<Script src="/t/whm.js" strategy="afterInteractive" />
<Script id="whm-analytics" strategy="afterInteractive">
  {`whm('init', {
    siteId: ${YOUR_SITE_ID},
    collectorUrl: '/t/collect',
    linkDomains: ['staging.whmtest.com']
  });`}
</Script>
```

### Вариант C: WHMCS

В WHMCS Admin → Setup → General Settings → Other → Global Header:

```html
<script src="/t/whm.js"></script>
<script>
whm('init', { 
  siteId: YOUR_SITE_ID,
  collectorUrl: '/t/collect'
});
</script>
```

---

## Site IDs (TEST)

| Site ID | Домен | Тип |
|---------|-------|-----|
| 3 | analytics.ignat.best | Test Front |
| 4 | staging.whmtest.com | Test WHMCS |

> **Note:** Site 1 и 2 - production (webhostmost.com, client.webhostmost.com)

---

## Cross-Domain Tracking

Для отслеживания посетителей между front → WHMCS:

### Автоматически через linkDomains

На фронте (analytics.ignat.best):
```javascript
whm('init', {
  siteId: 3,
  collectorUrl: '/t/collect',
  linkDomains: ['staging.whmtest.com']  // WHMCS тестовый
});
```

Трекер автоматически добавит `?_whm_vid=...` к ссылкам на указанные домены.

### Вручную через CSS класс

Добавьте класс `whm-cross` к ссылке:

```html
<a href="https://staging.whmtest.com/cart.php" class="whm-cross">
  Заказать
</a>
```

### Программно через API

```javascript
// Получить URL с visitor_id
var url = whm('getUrl', 'https://staging.whmtest.com/cart.php?pid=1');
// Результат: https://staging.whmtest.com/cart.php?pid=1&_whm_vid=abcd1234
```

Для React/Next.js:
```tsx
const handleClick = (url: string) => {
  const finalUrl = typeof window.whm === 'function' 
    ? window.whm('getUrl', url) || url 
    : url;
  window.location.href = finalUrl;
};
```

---

## Опции инициализации

```javascript
whm('init', {
  siteId: 3,                      // ID сайта в Matomo (обязательно)
  collectorUrl: '/t/collect',     // URL для отправки (рекомендуется proxy)
  debug: false,                   // Логи в консоль
  autoPageview: true,             // Авто pageview при загрузке
  respectDNT: false,              // Уважать Do Not Track
  linkDomains: []                 // Домены для cross-domain tracking
});
```

---

## API Reference

### Инициализация
```javascript
whm('init', { siteId: 3, collectorUrl: '/t/collect', ... })
```

### Pageview (авто или ручной)
```javascript
whm('pageview')
```

### Событие
```javascript
whm('event', 'Category', 'Action', 'Name', Value)
// или
whm('track', 'event', {
  category: 'Button',
  action: 'click',
  name: 'CTA',
  value: 42
})
```

### E-commerce
```javascript
whm('track', 'ecommerce', {
  orderId: 'ORD-123',
  revenue: 99.99,
  items: [{ sku: 'SKU-1', name: 'Product', price: 99.99, quantity: 1 }]
})
```

### Goal/Conversion
```javascript
whm('goal', { goalId: 1, revenue: 50 })
// или
whm('conversion', { goalId: 1, revenue: 50 })
```

### User Data
```javascript
whm('set', 'userId', 'client_123')      // ID клиента
whm('set', 'email', 'user@example.com') // Хешируется SHA256
whm('set', 'phone', '+79001234567')     // Хешируется SHA256
```

### Cross-Domain
```javascript
whm('getVisitorId')                     // Получить visitor ID
whm('getUrl', url)                      // URL с visitor ID
whm('linkDomains', ['domain.com'])      // Добавить домены
```

### Debug
```javascript
whm('set', 'debug', true)               // Включить логи
```

---

## Custom Dimensions

Трекер автоматически собирает:

| Dimension | Описание | Источник |
|-----------|----------|----------|
| dimension1 | fbc | Facebook Click ID |
| dimension2 | fbp | Facebook Browser ID |
| dimension3 | gclid | Google Click ID (из URL) |
| dimension4 | yclid | Yandex Click ID (из URL) |
| dimension5 | email_hash | SHA-256 (через `whm('set', 'email', ...)`) |
| dimension6 | phone_hash | SHA-256 (через `whm('set', 'phone', ...)`) |
| dimension7 | user_id | Через `whm('set', 'userId', ...)` |
| dimension8 | utm_source | Из URL |
| dimension9 | utm_medium | Из URL |

**Cross-Domain Attribution:**
Dimensions 1-9 сохраняются в SQLite на сервере collector и автоматически восстанавливаются при переходе на WHMCS (если visitor_id совпадает через `_whm_vid`).

---

## Debug режим

Включите debug в консоли:

```javascript
whm('set', 'debug', true);
```

Увидите:
```
[WHM] WHM Tracker v1.2.0
[WHM] Initialized site 3 visitor: abcd1234abcd1234
[WHM] Sending: {site_id: 3, visitor_id: "abcd1234", ...}
[WHM] Sent (beacon)
```

---

## Проверка работы

1. **Откройте сайт с трекером**
2. **F12 → Network** → фильтр по `/t/collect`
3. **Должен быть POST запрос со статусом 200**
4. **F12 → Console** → проверьте нет ли ошибок

Или в терминале:
```bash
curl -s "https://your-site.com/t/health"
# Должен вернуть: {"status":"healthy","timestamp":...}
```

---

## Troubleshooting

### "whm is not defined"
Скрипт не загрузился. Проверьте:
- Путь `/t/whm.js` доступен
- Proxy настроен в .htaccess

### Запрос на /t/collect возвращает 404
Proxy не работает. Проверьте:
- mod_rewrite включён
- .htaccess в правильной папке
- `curl https://your-site.com/t/health` работает

### Данные не появляются в Matomo
- Проверьте site_id (должен быть разрешён)
- Проверьте логи: `docker logs whm-collector --tail 50`
- Убедитесь что POST на `/t/collect` возвращает 200

### Dimensions не передаются на WHMCS
- Проверьте что visitor_id совпадает (параметр `_whm_vid`)
- Проверьте SQLite: `docker exec whm-collector python3 -c "import sqlite3; ..."`
- Убедитесь что cookie `_whm_vid` установлена

---

## WHMCS Hook

На WHMCS работает PHP hook (`analytics_hook.php`) который:
1. Читает visitor_id из cookie `_whm_vid`
2. Отправляет событие `ecommerce` при оплате invoice
3. Collector восстанавливает dimensions из SQLite

Путь к hook:
```
/home/USER/domains/DOMAIN/public_html/includes/hooks/analytics_hook.php
```
