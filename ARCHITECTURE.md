# WHM Analytics - Detailed Architecture

> **Версия:** 1.3.0
> **Последнее обновление:** 18 января 2026

## 1. Collector Service

### 1.1 Request Flow

```
Browser/Server                     Collector                           Matomo
     │                                 │                                  │
     │  POST /collect                  │                                  │
     │  {event, url, ...}              │                                  │
     ├────────────────────────────────►│                                  │
     │                                 │                                  │
     │                                 │ 1. Validate request              │
     │                                 │ 2. Extract IP from headers       │
     │                                 │ 3. GeoIP lookup                  │
     │                                 │ 4. Parse User-Agent              │
     │                                 │ 5. Visitor Store (SQLite)        │
     │                                 │    - Load stored dimensions      │
     │                                 │    - Merge with incoming         │
     │                                 │    - Save First/Last Touch       │
     │                                 │ 6. Enrich event data             │
     │                                 │                                  │
     │                                 │  POST /matomo.php                │
     │                                 │  (Tracking API)                  │
     │                                 ├─────────────────────────────────►│
     │                                 │                                  │
     │                                 │◄─────────────────────────────────┤
     │                                 │  200 OK                          │
     │◄────────────────────────────────┤                                  │
     │  200 OK                         │                                  │
     │                                 │                                  │
```

### 1.2 Event Schema

```python
class BaseEvent:
    # Required
    site_id: int                    # Matomo site ID
    event_type: str                 # page_view, click, scroll, etc.
    url: str                        # Current page URL
    timestamp: int                  # Unix timestamp ms
    
    # Auto-filled by collector
    ip: str                         # From X-Real-IP header
    user_agent: str                 # From User-Agent header
    visitor_id: str                 # From cookie or generated
    session_id: str                 # From cookie or generated
    
    # Optional from JS
    referrer: str | None
    title: str | None
    screen_width: int | None
    screen_height: int | None
    viewport_width: int | None
    viewport_height: int | None
    pixel_ratio: float | None
    timezone: str | None
    language: str | None

class PageViewEvent(BaseEvent):
    event_type: Literal["page_view"]
    
class ScrollEvent(BaseEvent):
    event_type: Literal["scroll"]
    scroll_depth: int               # 25, 50, 75, 90, 100
    
class ClickEvent(BaseEvent):
    event_type: Literal["click"]
    element_tag: str                # a, button, div, etc.
    element_id: str | None
    element_class: str | None
    element_text: str | None        # First 100 chars
    href: str | None                # For links
    is_outbound: bool               # External link?
    
class FormEvent(BaseEvent):
    event_type: Literal["form_start", "form_submit"]
    form_id: str | None
    form_name: str | None
    form_action: str | None
    
class EngagementEvent(BaseEvent):
    event_type: Literal["user_engagement"]
    engagement_time_msec: int       # Time on page
    
class EcommerceEvent(BaseEvent):
    event_type: Literal["view_item", "add_to_cart", "purchase", "trial_started"]
    transaction_id: str | None
    value: float | None
    currency: str | None
    items: list[dict] | None
```

### 1.3 Matomo Tracking API Mapping

```python
# Event → Matomo Tracking API params
MATOMO_MAPPING = {
    "page_view": {
        "action_name": "{title}",
        "url": "{url}",
        "urlref": "{referrer}",
        "res": "{screen_width}x{screen_height}",
        "_cvar": {"1": ["visitor_id", "{visitor_id}"]},
    },
    "scroll": {
        "e_c": "Scroll",
        "e_a": "scroll_depth",
        "e_n": "{scroll_depth}%",
        "e_v": "{scroll_depth}",
    },
    "click": {
        "e_c": "Click",
        "e_a": "{element_tag}",
        "e_n": "{element_text}",
        "link": "{href}",  # if outbound
    },
    "form_submit": {
        "e_c": "Form",
        "e_a": "submit",
        "e_n": "{form_id}",
    },
    "purchase": {
        "idgoal": 0,
        "ec_id": "{transaction_id}",
        "revenue": "{value}",
        "ec_items": "{items_json}",
    },
}
```

## 2. AdBlock Bypass Architecture

### 2.1 Problem

AdBlock/uBlock/Brave блокируют запросы к доменам содержащим `analytics`, `tracking`, `pixel` и т.д.

### 2.2 Solution: 1st Party Proxy

```
Браузер → yoursite.com/t/collect → (.htaccess proxy) → analytics.webhostmost.com → Collector
```

**Преимущества:**
- Запросы идут на тот же домен (1st party)
- Нет cross-origin ограничений
- Не попадает под фильтры AdBlock
- Cookies ставятся как 1st party

### 2.3 Implementation

**На каждом сайте (.htaccess):**
```apache
# WHM Analytics Proxy - DO NOT REMOVE
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteCond %{REQUEST_URI} ^/t/
RewriteRule ^t/(.*)$ https://analytics.webhostmost.com/t/$1 [P,L,QSA]
</IfModule>
```

**Nginx на analytics.webhostmost.com:**
```nginx
server {
    listen 443 ssl http2;
    server_name analytics.webhostmost.com;
    
    location / {
        proxy_pass http://127.0.0.1:9100;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # CORS
        add_header Access-Control-Allow-Origin * always;
        add_header Access-Control-Allow-Methods "GET, POST, OPTIONS" always;
    }
}
```

---

## 3. JS Tracker

### 3.1 Core Features

```typescript
// Initialization
window.WHM_SITE_ID = 1;
window.WHM_CONFIG = {
    endpoint: '/t/collect',  // Uses proxy, not direct URL
    trackPageview: true,
    trackScroll: true,
    trackClicks: true,
    trackForms: true,
    trackEngagement: true,
    scrollThresholds: [25, 50, 75, 90],
    clickSelector: 'a, button, [data-track]',
    debug: false,
};

// Auto-tracking
// - Page view on load
// - Scroll depth on scroll (debounced)
// - Click on elements
// - Form interactions
// - Time on page (beacon on unload)

// Manual tracking
whm.track('custom_event', { key: 'value' });
whm.trackPageView();
whm.identify('user@email.com');
```

### 2.2 Cross-Domain Linking

```typescript
// When user clicks link to staging.whmtest.com
// Append _whm_id parameter to preserve visitor identity

const LINKED_DOMAINS = [
    'webhostmost.com',
    'staging.whmtest.com',
    // Regional domains
];

function decorateLink(url: string): string {
    const visitorId = getVisitorId();
    const sessionId = getSessionId();
    const u = new URL(url);
    u.searchParams.set('_whm_id', visitorId);
    u.searchParams.set('_whm_sess', sessionId);
    return u.toString();
}
```

### 2.3 Ad Click ID Capture

```typescript
// On page load, capture click IDs from URL
const AD_PARAMS = {
    'fbclid': '_fbc',      // Facebook
    'gclid': '_gclid',     // Google Ads
    'yclid': '_yclid',     // Yandex
    'msclkid': '_msclkid', // Microsoft
    'ttclid': '_ttclid',   // TikTok
};

function captureClickIds() {
    const params = new URLSearchParams(location.search);
    for (const [param, cookie] of Object.entries(AD_PARAMS)) {
        const value = params.get(param);
        if (value) {
            // Store in cookie (1st party)
            setCookie(cookie, formatClickId(param, value), 90); // 90 days
        }
    }
}

function formatClickId(type: string, value: string): string {
    // Format: {type}.{version}.{timestamp}.{value}
    return `${type}.1.${Date.now()}.${value}`;
}
```

## 3. Forwarders Architecture

### 3.1 Base Forwarder

```python
from abc import ABC, abstractmethod
from typing import Generator

class BaseForwarder(ABC):
    """Base class for all platform forwarders"""
    
    name: str
    batch_size: int = 100
    
    @abstractmethod
    def get_new_events(self, conn) -> Generator[dict, None, None]:
        """Fetch events not yet sent to this platform"""
        pass
    
    @abstractmethod
    def transform_event(self, event: dict) -> dict:
        """Transform Matomo event to platform format"""
        pass
    
    @abstractmethod
    def send_batch(self, events: list[dict]) -> bool:
        """Send batch of events to platform"""
        pass
    
    @abstractmethod
    def mark_sent(self, conn, event_ids: list[str]) -> None:
        """Mark events as sent"""
        pass
    
    def run(self):
        """Main execution loop"""
        conn = get_db_connection()
        
        batch = []
        for event in self.get_new_events(conn):
            transformed = self.transform_event(event)
            if transformed:
                batch.append(transformed)
            
            if len(batch) >= self.batch_size:
                if self.send_batch(batch):
                    self.mark_sent(conn, [e['id'] for e in batch])
                batch = []
        
        # Send remaining
        if batch:
            if self.send_batch(batch):
                self.mark_sent(conn, [e['id'] for e in batch])
```

### 3.2 Event Type Mapping

```python
# Matomo event → Platform events mapping

EVENT_MAPPING = {
    "ga4": {
        "page_view": "page_view",
        "scroll": "scroll",
        "click": "click",
        "form_submit": "form_submit",
        "purchase": "purchase",
        "trial_started": "generate_lead",
        "sign_up": "sign_up",
        "begin_checkout": "begin_checkout",
    },
    "meta": {
        "page_view": "PageView",
        "click": None,  # Don't send clicks to Meta
        "purchase": "Purchase",
        "trial_started": "StartTrial",
        "sign_up": "CompleteRegistration",
        "add_to_cart": "AddToCart",
        "begin_checkout": "InitiateCheckout",
    },
    "yandex": {
        "page_view": None,  # YM tracks via JS
        "purchase": "purchase",
        "trial_started": "trial",
    },
}
```

### 3.3 GA4 Event Details

#### begin_checkout

Событие срабатывает когда посетитель кликает "Buy Now" / "Try Now" и переходит на `/store/*` страницу.

**Источник данных:** Matomo dimension6 (custom variable 6), куда JS трекер записывает путь продукта.

**Формат event_name в Matomo:**
```
{category}/{plan_name}
```

**Примеры:**
- `usa-managed-hosting/usa-holiday-pro`
- `trial-managed-hosting-usa/14-days-trial-holiday-pro-usa`
- `domains/domain_register`

**Преобразование для GA4:**

| Поле GA4 | Источник | Пример |
|----------|----------|--------|
| `page_title` | Последняя часть пути (после `/`) | `usa-holiday-pro` |
| `items[].item_name` | Последняя часть пути | `usa-holiday-pro` |
| `items[].item_id` | Последняя часть пути | `usa-holiday-pro` |
| `items[].item_category` | Первая часть пути | `usa-managed-hosting` |
| `currency` | Статически | `USD` |
| `source` / `medium` | UTM параметры или referer | `google / cpc` |

**Примечание:** `value` не отправляется для begin_checkout, так как:
- Пользователь ещё не выбрал биллинг-цикл (monthly/yearly)
- Купоны не применены
- Реальная цена известна только в момент purchase

**Пример payload для GA4:**
```json
{
  "client_id": "visitor_abc123",
  "events": [{
    "name": "begin_checkout",
    "params": {
      "engagement_time_msec": 100,
      "currency": "USD",
      "session_id": "12345",
      "page_location": "https://staging.whmtest.com/store/...",
      "page_title": "usa-holiday-pro",
      "source": "google",
      "medium": "cpc",
      "items": [{
        "item_id": "usa-holiday-pro",
        "item_name": "usa-holiday-pro",
        "item_category": "usa-managed-hosting",
        "quantity": 1,
        "price": 0
      }]
    }
  }]
}
```

#### purchase

Событие приходит из WHMCS hook при завершении оплаты.

**Содержит:**
- `transaction_id` - номер инвойса
- `value` - сумма покупки
- `currency` - валюта (USD)
- `items` - список продуктов

### 3.4 Sent Events Tracking

```sql
-- Unified table for tracking sent events
CREATE TABLE whm_events_sent (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    platform ENUM('ga4', 'meta', 'yandex', 'google_ads') NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_id VARCHAR(100) NOT NULL,  -- Matomo event identifier
    matomo_idvisit BIGINT,
    matomo_idaction BIGINT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    response_code INT,
    response_body TEXT,
    
    UNIQUE KEY uk_platform_event (platform, event_id),
    INDEX idx_sent_at (sent_at),
    INDEX idx_matomo_visit (matomo_idvisit)
) ENGINE=InnoDB;
```

## 4. GeoIP Integration

### 4.1 MaxMind GeoIP2

```python
import geoip2.database

class GeoIPService:
    def __init__(self, db_path: str = '/opt/whm-analytics/data/GeoLite2-City.mmdb'):
        self.reader = geoip2.database.Reader(db_path)
    
    def lookup(self, ip: str) -> dict:
        try:
            response = self.reader.city(ip)
            return {
                'country_code': response.country.iso_code,
                'country_name': response.country.name,
                'region_code': response.subdivisions.most_specific.iso_code,
                'region_name': response.subdivisions.most_specific.name,
                'city': response.city.name,
                'postal_code': response.postal.code,
                'latitude': response.location.latitude,
                'longitude': response.location.longitude,
                'timezone': response.location.time_zone,
            }
        except Exception:
            return {}
```

### 4.2 Auto-Update GeoIP Database

```bash
#!/bin/bash
# /opt/whm-analytics/scripts/update-geoip.sh
# Run weekly via cron

ACCOUNT_ID="your_account_id"
LICENSE_KEY="your_license_key"
DB_DIR="/opt/whm-analytics/data"

curl -o "$DB_DIR/GeoLite2-City.mmdb.gz" \
    "https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-City&license_key=$LICENSE_KEY&suffix=tar.gz"

gunzip -f "$DB_DIR/GeoLite2-City.mmdb.gz"
```

## 5. Real-time Data Flow

### 5.1 Event Processing Timeline

```
T+0ms     JS event triggered
T+5ms     Beacon sent to /collect
T+10ms    Collector receives request
T+15ms    GeoIP lookup, UA parsing
T+20ms    Matomo Tracking API called
T+50ms    Event in Matomo database
T+100ms   Response to browser

T+5min    Forwarder picks up event
T+5min    Events sent to GA4/Meta/etc.
```

### 5.2 Matomo Real-time Updates

```python
# Matomo processes events immediately
# Real-time widget updates every 5 seconds
# Reports archive every hour (cron)

# For GA4 real-time:
# - We send events every 5 minutes (forwarder cron)
# - GA4 shows them in Real-time within 30 seconds of receipt

# For Meta:
# - Events appear in Events Manager within 20 minutes
# - But attribution is retroactive
```

## 6. Configuration Files

### 6.1 sites.yaml

```yaml
# Site configuration
sites:
  # Test environments
  3:
    name: "Test Front"
    domain: "analytics.ignat.best"
    type: "frontend"
    ecommerce: false
    forward_to:
      - ga4
      - meta
    
  4:
    name: "Test WHMCS"
    domain: "staging.whmtest.com"
    type: "whmcs"
    ecommerce: true
    forward_to:
      - ga4
      - meta
  
  # Production
  1:
    name: "WHMCS Client Area"
    domain: "staging.whmtest.com"
    type: "whmcs"
    ecommerce: true
    forward_to:
      - ga4
      - meta
      - yandex
      
  2:
    name: "Front Websites"
    type: "frontend"
    domains:
      - "webhostmost.com"
      - "www.webhostmost.com"
      - "webhostmost.md"
      - "webhostmost.ru"
      # ... all regional domains
    ecommerce: false
    link_to: 1  # Cross-domain with WHMCS
    forward_to:
      - ga4
      - meta
      - yandex
```

### 6.2 forwarders.yaml

```yaml
ga4:
  measurement_id: "${GA_MEASUREMENT_ID}"
  api_secret: "${GA_API_SECRET}"
  batch_size: 25
  interval_seconds: 300  # 5 minutes
  events:
    - page_view
    - scroll
    - click
    - form_submit
    - purchase
    - trial_started
    - sign_up

meta:
  pixel_id: "${FB_PIXEL_ID}"
  access_token: "${FB_ACCESS_TOKEN}"
  batch_size: 100
  interval_seconds: 600  # 10 minutes
  events:
    - page_view
    - purchase
    - trial_started
    - sign_up
    - add_to_cart

yandex:
  counter_id: "${YM_COUNTER_ID}"
  oauth_token: "${YM_OAUTH_TOKEN}"
  batch_size: 100
  interval_seconds: 600
  events:
    - purchase
    - trial_started
```

## 7. Docker Setup

### 7.1 docker-compose.yml

```yaml
version: '3.8'

services:
  collector:
    build: ./collector
    container_name: whm-collector
    restart: unless-stopped
    ports:
      - "127.0.0.1:9100:8000"
    volumes:
      - ./config:/app/config:ro
      - ./data:/app/data:ro
      - ./logs:/app/logs
    environment:
      - MATOMO_URL=http://matomo-app:80
      - MATOMO_TOKEN=${MATOMO_TOKEN}
    depends_on:
      - matomo-app
    networks:
      - analytics

  forwarder:
    build: ./forwarders
    container_name: whm-forwarder
    restart: unless-stopped
    volumes:
      - ./config:/app/config:ro
      - ./logs:/app/logs
    env_file:
      - .env
    depends_on:
      - matomo-db
    networks:
      - analytics

  # Existing services
  matomo-app:
    image: matomo:5-apache
    # ... existing config
    
  matomo-db:
    image: mariadb:10.11
    # ... existing config

networks:
  analytics:
    external: true
```

### 7.2 Nginx Update

```nginx
# Add to analytics.webhostmost.com server block

# New collector endpoint
location /collect {
    proxy_pass http://127.0.0.1:9100;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    
    # CORS for cross-domain tracking
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Access-Control-Allow-Methods "GET, POST, OPTIONS" always;
    add_header Access-Control-Allow-Headers "Content-Type" always;
    
    if ($request_method = OPTIONS) {
        return 204;
    }
}

# JS tracker (cached)
location = /t.js {
    alias /opt/whm-analytics/tracker/dist/whm.js;
    add_header Cache-Control "public, max-age=3600";
    add_header Content-Type "application/javascript";
}
```

## 8. Monitoring & Debugging

### 8.1 Log Format

```python
# Structured JSON logging
{
    "timestamp": "2026-01-12T18:30:00Z",
    "level": "INFO",
    "service": "collector",
    "event": "track_received",
    "site_id": 1,
    "event_type": "page_view",
    "visitor_id": "abc123",
    "ip": "1.2.3.4",
    "country": "US",
    "duration_ms": 45
}
```

### 8.2 Health Endpoints

```python
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "1.0.0",
        "matomo": await check_matomo(),
        "geoip": check_geoip(),
    }

@app.get("/stats")
async def stats():
    return {
        "events_today": get_events_count(today),
        "events_hour": get_events_count(last_hour),
        "avg_latency_ms": get_avg_latency(),
    }
```
