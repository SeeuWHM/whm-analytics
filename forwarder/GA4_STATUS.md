# GA4 Forwarder Status Report

## Текущее состояние (2026-01-17)

### ✅ Что работает:

1. **Отправка событий в GA4**
   - Events доходят до GA4 (видны в Realtime)
   - Validation API подтверждает валидность payload
   - Custom dimensions (source, medium, campaign, country, city) успешно отправляются

2. **Custom Dimensions зарегистрированы:**
   - `source` - Traffic Source (EVENT scope)
   - `medium` - Traffic Medium (EVENT scope)
   - `campaign` - Campaign Name (EVENT scope)
   - `country` - Country Code (EVENT scope)
   - `city` - City Name (EVENT scope)

3. **Инфраструктура:**
   - Service Account работает
   - GA4 Admin API и Data API доступны
   - Matomo DB читается корректно

### ⏳ Что требует времени:

1. **Custom Dimensions в отчётах**
   - GA4 Data API показывает пустые значения
   - Это НОРМАЛЬНО - данные появляются с задержкой 24-48 часов
   - Custom Dimensions начинают собирать данные только ПОСЛЕ создания

2. **Geo в отчётах**
   - Встроенные country/city GA4 НЕ РАБОТАЮТ с MP (нет IP)
   - Используем custom dimensions для передачи geo из Matomo

### ❌ Ограничения GA4 Measurement Protocol:

1. **session_start** - reserved event, нельзя отправлять через MP
2. **Geo на карте** - НЕ РАБОТАЕТ (IP не передаётся)
3. **Traffic Source attribution** - требует Custom Dimensions
4. **gclid как custom dim** - reserved parameter, используем traffic_gclid

### 📊 Как проверить что данные приходят:

\`\`\`bash
# 1. Realtime (события за последние 30 минут)
# Идёт в GA4 UI -> Reports -> Realtime

# 2. Через API (данные за сегодня, с задержкой)
cd /opt/whm-analytics/forwarder && source venv/bin/activate && python3 << 'SCRIPT'
import os
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = '/opt/whm-analytics/analytics-test-484616-1bedccc703e0.json'
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Dimension, Metric

client = BetaAnalyticsDataClient()
request = RunReportRequest(
    property="properties/520394907",
    dimensions=[
        Dimension(name="eventName"),
        Dimension(name="customEvent:source"),
        Dimension(name="customEvent:medium"),
    ],
    metrics=[Metric(name="eventCount")],
    date_ranges=[DateRange(start_date="today", end_date="today")],
)
response = client.run_report(request)
for row in response.rows:
    print(row.dimension_values, row.metric_values)
SCRIPT
\`\`\`

### 🔧 Следующие шаги:

1. **Подождать 24-48 часов** для появления Custom Dimensions в отчётах
2. **Убрать session_start** из events (reserved)
3. **Добавить полное логирование** в файл для отладки
4. **Настроить alerting** если события не уходят

### 📁 Конфигурация:

- Config: \`/opt/whm-analytics/forwarder/sites.yaml\`
- Script: \`/opt/whm-analytics/forwarder/universal_forwarder.py\`
- Service Account: \`/opt/whm-analytics/analytics-test-484616-1bedccc703e0.json\`
- Dedup DB: \`/opt/whm-analytics/data/forwarder_sent.db\`
- Logs: \`/opt/whm-analytics/logs/forwarder.log\`

