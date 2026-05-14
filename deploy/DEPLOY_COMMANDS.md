# WHM Analytics - Deploy Commands
# Последнее обновление: 16 января 2026
# Версия: 1.2.0
# 
# ВАЖНО: Эти команды устарели. Актуальная установка:
# - JS трекер: /t/whm.js через proxy
# - WHMCS hook: analytics_hook.php (v2.0)
# - Cross-domain: _whm_vid параметр + SQLite visitor_store
#
# Выполнить на текущем сервере (где коллектор)

# =============================================================================
# СЕРВЕР 6: analytics.ignat.best (Test Front)
# Юзер: bqzrgnkg
# =============================================================================

# 1. Скопировать index.html на сервер 6
scp -P 2323 /opt/whm-analytics/deploy/test-front/index.html \
    root@server6.webhostmost.com:/home/bqzrgnkg/domains/ignat.best/public_html/analytics/index.html

# Или если субдомен настроен отдельно:
# scp -P 2323 /opt/whm-analytics/deploy/test-front/index.html \
#     root@server6.webhostmost.com:/home/bqzrgnkg/domains/analytics.ignat.best/public_html/index.html

# =============================================================================
# СЕРВЕР 8: staging.whmtest.com (Test WHMCS)  
# Юзер: rjaazbmd
# =============================================================================

# 2. Скопировать PHP хук на сервер 8
scp -P 2323 /opt/whm-analytics/deploy/whmcs/whm_analytics.php \
    root@server8.webhostmost.com:/home/rjaazbmd/domains/staging.whmtest.com/public_html/includes/hooks/whm_analytics.php

# 3. Скопировать сниппет для header.tpl (для справки)
scp -P 2323 /opt/whm-analytics/deploy/whmcs/header_snippet.tpl \
    root@server8.webhostmost.com:/home/rjaazbmd/whm_header_snippet.txt

# =============================================================================
# ПОСЛЕ SCP - выполнить на СЕРВЕРЕ 8 через SSH
# =============================================================================

# SSH на сервер 8:
# ssh -p 2323 root@server8.webhostmost.com

# Найти какой шаблон активен:
# ls -la /home/rjaazbmd/domains/staging.whmtest.com/public_html/templates/

# Добавить сниппет в header.tpl (LAGOM):
# Открыть файл:
# nano /home/rjaazbmd/domains/staging.whmtest.com/public_html/templates/lagom/header.tpl
# 
# Найти </head> и ПЕРЕД ним добавить содержимое из /home/rjaazbmd/whm_header_snippet.txt
#
# Или автоматом (если </head> на отдельной строке):
# sed -i '/<\/head>/i \
# <!-- WHM Analytics -->\
# <script src="https://analytics.webhostmost.com/whm.js"></script>\
# <script>\
# whm('\''init'\'', {\
#     siteId: 4,\
#     collectorUrl: '\''https://analytics.webhostmost.com/collect'\'',\
#     linkDomains: ['\''analytics.ignat.best'\''],\
#     debug: true\
# });\
# {if $loggedin}whm('\''set'\'', '\''userId'\'', '\''client_{$clientsdetails.id}'\'');{/if}\
# </script>' /home/rjaazbmd/domains/staging.whmtest.com/public_html/templates/lagom/header.tpl

# Установить права:
# chown rjaazbmd:rjaazbmd /home/rjaazbmd/domains/staging.whmtest.com/public_html/includes/hooks/whm_analytics.php
# chmod 644 /home/rjaazbmd/domains/staging.whmtest.com/public_html/includes/hooks/whm_analytics.php

# Создать папку для логов:
# mkdir -p /home/rjaazbmd/domains/staging.whmtest.com/custom_logs/whm-analytics
# chown rjaazbmd:rjaazbmd /home/rjaazbmd/domains/staging.whmtest.com/custom_logs/whm-analytics

# =============================================================================
# ТЕСТИРОВАНИЕ
# =============================================================================

# 1. Открыть https://analytics.ignat.best/analytics/ (или без /analytics/ если субдомен напрямую)
#    - Должен показать "Status: Active ✓"
#    - Должен показать Visitor ID
#    - Попробовать кнопки "Track Click", "Track Custom"

# 2. Нажать "Go to WHMCS Cart"
#    - URL должен содержать ?_whm_vid=xxxxx
#    - WHMCS должен загрузиться

# 3. На WHMCS проверить консоль браузера (F12)
#    - Должно быть [WHM] init...
#    - Должно быть [WHM] pageview sent

# 4. Проверить логи коллектора:
#    docker logs whm-collector --tail 50

# 5. Проверить Matomo:
#    - https://analytics.webhostmost.com
#    - Site 3 и Site 4 должны показывать визиты
