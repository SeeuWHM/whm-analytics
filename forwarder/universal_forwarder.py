#!/usr/bin/env python3
"""
WHM Analytics - Universal Forwarder v2.2

Reads events from Matomo DB and forwards to:
  - GA4 (Measurement Protocol)
  - Meta (Conversions API)
  - Microsoft Ads (Offline Conversions API)

Runs as daemon (systemd: whm-forwarder.service)
Sleep 30s between runs.

Usage:
    python3 universal_forwarder.py              # single run
    python3 universal_forwarder.py --daemon     # daemon mode (30s loop)
    python3 universal_forwarder.py --dry-run    # no actual sends
    python3 universal_forwarder.py --validate   # GA4 debug endpoint
    python3 universal_forwarder.py --force-now  # ignore timestamp, use now()

Config: /opt/whm-analytics/forwarder/sites.yaml
Env:    /opt/whm-analytics/forwarder/.env
Dedup:  /opt/whm-analytics/data/forwarder_sent.db
Logs:   /opt/whm-analytics/logs/forwarder.log
"""

import os
import re
import sys
import json
import time
import sqlite3
import logging
import argparse
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

import yaml
import requests
import pymysql
import pymysql.cursors
from pymysql.cursors import DictCursor
from dotenv import load_dotenv


# ============================================================
# CONSTANTS
# ============================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "sites.yaml"
ENV_FILE = SCRIPT_DIR / ".env"

GA4_PROD_URL = "https://www.google-analytics.com/mp/collect"
GA4_DEBUG_URL = "https://www.google-analytics.com/debug/mp/collect"

# Microsoft Ads
MSFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MSFT_API_URL = "https://campaign.api.bingads.microsoft.com/CampaignManagement/v13/OfflineConversions/Apply"
MSFT_OAUTH_SCOPE = "https://ads.microsoft.com/msads.manage offline_access"


# ============================================================
# CONFIG HELPERS
# ============================================================

def substitute_env_vars(obj):
    """Рекурсивно подставляет ${VAR} из os.environ в значения конфига"""
    if isinstance(obj, str):
        def replace(match):
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))
        return re.sub(r'\$\{(\w+)\}', replace, obj)
    elif isinstance(obj, dict):
        return {k: substitute_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [substitute_env_vars(item) for item in obj]
    return obj


def load_config():
    """Загружает sites.yaml, подставляет env переменные"""
    load_dotenv(ENV_FILE)

    with open(CONFIG_FILE, 'r') as f:
        config = yaml.safe_load(f)

    return substitute_env_vars(config)


def setup_logging() -> logging.Logger:
    """Настраивает логирование в stdout (systemd перенаправляет в файл)"""
    logger = logging.getLogger("forwarder")
    logger.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger


# ============================================================
# DATABASE CLASSES
# ============================================================

class MatomoDB:
    """Подключение к Matomo MariaDB"""

    def __init__(self, config):
        self.config = config.get('global', {}).get('matomo_db', config)

    def get_connection(self):
        cfg = self.config
        params = {
            'host': cfg.get('host', '127.0.0.1'),
            'port': int(cfg.get('port', 3306)),
            'user': cfg.get('user', 'matomo'),
            'password': cfg.get('password', ''),
            'database': cfg.get('database', 'matomo'),
            'charset': 'utf8mb4',
            'cursorclass': DictCursor,
        }
        if cfg.get('socket'):
            params['unix_socket'] = cfg['socket']
        return pymysql.connect(**params)


class DedupDB:
    """SQLite дедупликация отправленных событий"""

    def __init__(self, db_path):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sent_events (
                event_hash TEXT NOT NULL,
                site_id INTEGER,
                event_type TEXT,
                destination TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (event_hash, destination)
            )
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_sent_site
            ON sent_events(site_id, sent_at)
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS failed_events (
                event_hash TEXT NOT NULL,
                destination TEXT NOT NULL,
                attempt_count INTEGER DEFAULT 1,
                last_error TEXT,
                first_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (event_hash, destination)
            )
        ''')
        conn.commit()
        conn.close()

    def is_sent(self, event_hash: str, destination: str = None) -> bool:
        """Проверяет, было ли событие уже отправлено.

        Args:
            event_hash: хеш события
            destination: опциональный фильтр по назначению ('ga4', 'meta', etc.)
        """
        conn = sqlite3.connect(self.db_path)
        if destination:
            row = conn.execute(
                'SELECT 1 FROM sent_events WHERE event_hash = ? AND destination = ?',
                (event_hash, destination)
            ).fetchone()
        else:
            row = conn.execute(
                'SELECT 1 FROM sent_events WHERE event_hash = ?',
                (event_hash,)
            ).fetchone()
        conn.close()
        return row is not None

    def mark_sent(self, event_hash: str, site_id: int, event_type: str, destination: str):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT OR IGNORE INTO sent_events \n'
            '               (event_hash, site_id, event_type, destination) \n'
            '               VALUES (?, ?, ?, ?)',
            (event_hash, site_id, event_type, destination)
        )
        conn.commit()
        conn.close()

    def cleanup_old(self, days: int = 90):
        conn = sqlite3.connect(self.db_path)
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn.execute('DELETE FROM sent_events WHERE sent_at < ?', (cutoff,))
        conn.execute('DELETE FROM failed_events WHERE last_attempt < ?', (cutoff,))
        conn.commit()
        conn.close()

    def get_failure_count(self, event_hash: str, destination: str) -> int:
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            'SELECT attempt_count FROM failed_events WHERE event_hash = ? AND destination = ?',
            (event_hash, destination)
        ).fetchone()
        conn.close()
        return row[0] if row else 0

    def record_failure(self, event_hash: str, destination: str, error: str = None):
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            INSERT INTO failed_events (event_hash, destination, last_error)
            VALUES (?, ?, ?)
            ON CONFLICT(event_hash, destination) DO UPDATE SET
                attempt_count = attempt_count + 1,
                last_error = excluded.last_error,
                last_attempt = CURRENT_TIMESTAMP
        ''', (event_hash, destination, error))
        conn.commit()
        conn.close()

    def clear_failures(self, event_hash: str, destination: str):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'DELETE FROM failed_events WHERE event_hash = ? AND destination = ?',
            (event_hash, destination)
        )
        conn.commit()
        conn.close()

    def should_skip_event(self, event_hash: str, destination: str, max_retries: int = 5) -> bool:
        return self.get_failure_count(event_hash, destination) >= max_retries


# ============================================================
# SOURCE / MEDIUM MAPPING
# ============================================================

def map_referer_to_source_medium(referer_type: int, referer_name: str = None) -> tuple:
    """
    Маппит Matomo referer_type в GA4 source/medium.

    Matomo referer_type:
      1 = Direct (набрал URL, закладка)
      2 = Search Engine (Google, Bing и т.д.)
      3 = Website (реферальный)
      6 = Social Network
      7 = Campaign
    """
    name = (referer_name or '').lower()

    if referer_type == 1:
        return ('(direct)', '(none)')
    elif referer_type == 2:
        return (name or 'search', 'organic')
    elif referer_type == 3:
        return (name or 'referral', 'referral')
    elif referer_type == 6:
        return (name or 'social', 'social')
    elif referer_type == 7:
        return (name or 'campaign', 'campaign')
    else:
        return ('(direct)', '(none)')


def resolve_source_medium(event: dict) -> tuple:
    """
    Определяет source/medium с приоритетом:
    1. UTM параметры (utm_source, utm_medium) - если есть
    2. Маппинг из referer_type/referer_name - как fallback
    """
    utm_source = event.get('utm_source')
    utm_medium = event.get('utm_medium')

    if utm_source and utm_source != '(not set)':
        return (utm_source, utm_medium or '(not set)')

    referer_type = event.get('referer_type')
    referer_name = event.get('referer_name')

    if referer_type:
        return map_referer_to_source_medium(int(referer_type), referer_name)

    return ('(direct)', '(none)')


# ============================================================
# EVENT FETCHER
# ============================================================

class EventFetcher:
    """SQL запросы к Matomo DB для получения событий"""

    def __init__(self, matomo_db: MatomoDB, site_id: int, lookback_minutes: int = 10, limit: int = 500):
        self.matomo_db = matomo_db
        self.site_id = site_id
        self.lookback_minutes = lookback_minutes
        self.limit = limit

    def _make_event_hash(self, idvisit, idlink_va) -> str:
        """Уникальный хэш события для дедупликации.

        Используем idlink_va (уникальный ID записи в Matomo) вместо idaction,
        потому что idlink_va гарантированно уникален для каждого события"""
        raw = f"{idvisit}:{idlink_va}"
        return hashlib.md5(raw.encode()).hexdigest()

    def fetch_page_views(self) -> list:
        """Получает page_view события за последние N минут"""
        conn = self.matomo_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                    SELECT
                        v.idvisit,
                        v.idvisitor,
                        HEX(v.idvisitor) as visitor_id,
                        v.user_id,
                        a.idlink_va,
                        a.server_time,
                        UNIX_TIMESTAMP(a.server_time) as event_timestamp,
                        a.time_spent_ref_action,

                        -- URL и title
                        url.name as page_url,
                        url.url_prefix,
                        title.name as page_title,

                        -- Referrer (для source/medium маппинга)
                        v.referer_type,
                        v.referer_url,
                        v.referer_name,

                        -- Custom dimensions (атрибуция)
                        v.custom_dimension_1,
                        v.custom_dimension_2,
                        v.custom_dimension_3 as gclid,
                        v.custom_dimension_4,
                        v.custom_dimension_5,
                        v.custom_dimension_8 as utm_source,
                        v.custom_dimension_9 as utm_medium,
                        v.custom_dimension_10 as utm_campaign,
                        v.custom_dimension_27 as msclkid,

                        -- Device info
                        v.config_browser_name,
                        v.config_browser_version,
                        v.config_os,
                        v.config_os_version,
                        v.config_device_type,
                        v.config_resolution,

                        -- IP Address
                        v.location_ip,

                        -- Geo
                        v.location_country,
                        v.location_city,
                        v.location_region,

                        -- First visit source (for GA4 "First user source")
                        v.visitor_count_visits,
                        fv.referer_type AS first_referer_type,
                        fv.referer_name AS first_referer_name,
                        fv.custom_dimension_8 AS first_utm_source,
                        fv.custom_dimension_9 AS first_utm_medium

                    FROM matomo_log_link_visit_action a
                    JOIN matomo_log_visit v ON a.idvisit = v.idvisit
                    LEFT JOIN matomo_log_action url ON a.idaction_url = url.idaction
                    LEFT JOIN matomo_log_action title ON a.idaction_name = title.idaction
                    LEFT JOIN matomo_log_visit fv ON fv.idvisitor = v.idvisitor
                        AND fv.idsite = v.idsite
                        AND fv.visitor_count_visits = 1

                    WHERE v.idsite = %s
                      AND a.server_time > DATE_SUB(NOW(), INTERVAL %s MINUTE)
                      AND a.idaction_url IS NOT NULL
                      AND url.type = 1  -- ONLY PageView URLs (type=1), NOT events (type=10)

                    ORDER BY a.server_time DESC
                    LIMIT %s
                """, (self.site_id, self.lookback_minutes, self.limit))

            rows = cursor.fetchall()
            events = []
            for row in rows:
                row['event_hash'] = self._make_event_hash(str(row['idvisit']), str(row['idlink_va']))
                row['event_type'] = 'page_view'
                events.append(row)
            return events
        finally:
            conn.close()

    def fetch_conversions(self) -> list:
        """Получает конверсии (purchase) за последние N минут"""
        conn = self.matomo_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                    SELECT
                        c.idvisit,
                        c.idlink_va,
                        c.idgoal,
                        c.server_time,
                        UNIX_TIMESTAMP(c.server_time) as event_timestamp,
                        c.idorder as transaction_id,
                        c.revenue,
                        c.revenue_subtotal,
                        c.revenue_tax,
                        c.revenue_shipping,
                        c.revenue_discount,
                        c.items,

                        v.idvisitor,
                        HEX(v.idvisitor) as visitor_id,
                        v.user_id,

                        -- Referer (для source/medium маппинга)
                        v.referer_type,
                        v.referer_name,

                        -- Custom dimensions (Meta CAPI)
                        v.custom_dimension_1 as fbc,
                        v.custom_dimension_2 as fbp,
                        v.custom_dimension_3 as gclid,
                        v.custom_dimension_5 as email_hash,
                        v.custom_dimension_6 as phone_hash,
                        v.custom_dimension_8 as utm_source,
                        v.custom_dimension_9 as utm_medium,
                        v.custom_dimension_10 as utm_campaign,
                        v.custom_dimension_27 as msclkid,

                        -- Plan info (dimensions 15-18)
                        v.custom_dimension_15 as plan_type,
                        v.custom_dimension_16 as is_renewal,
                        v.custom_dimension_17 as client_domain,
                        v.custom_dimension_18 as product_name,
                        v.custom_dimension_19 as invoice_id,
                        v.custom_dimension_20 as orig_currency,
                        v.custom_dimension_21 as orig_value,
                        v.custom_dimension_22 as product_type,

                        -- PII hashes (dimensions 23-26) for Meta match quality
                        v.custom_dimension_23 as firstname_hash,
                        v.custom_dimension_24 as lastname_hash,
                        v.custom_dimension_25 as state_hash,
                        v.custom_dimension_26 as zip_hash,

                        -- IP Address (for Meta)
                        v.location_ip,

                        -- Geo (для GA4 и Meta)
                        v.location_country,
                        v.location_city,
                        v.location_region,

                        -- Device/Browser info
                        v.config_browser_name,
                        v.config_browser_version,
                        v.config_os,
                        v.config_os_version

                    FROM matomo_log_conversion c
                    JOIN matomo_log_visit v ON c.idvisit = v.idvisit

                    WHERE c.idsite = %s
                      AND c.server_time > DATE_SUB(NOW(), INTERVAL %s MINUTE)
                      AND c.idorder IS NOT NULL
                      AND c.idorder != ''

                    ORDER BY c.server_time DESC
                    LIMIT %s
                """, (self.site_id, self.lookback_minutes, self.limit))

            rows = cursor.fetchall()
            events = []
            for row in rows:
                row['event_hash'] = self._make_event_hash(str(row['idvisit']), str(row.get('idlink_va') or row['transaction_id']))
                row['event_type'] = 'purchase'
                events.append(row)
            return events
        finally:
            conn.close()

    def fetch_ecommerce_items(self, idvisit: int, idorder: str) -> list:
        """Получает items для конкретного заказа"""
        conn = self.matomo_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                    SELECT
                        i.idorder,
                        i.idaction_sku,
                        i.idaction_name,
                        i.idaction_category,
                        i.price,
                        i.quantity,

                        sku.name as item_id,
                        name.name as item_name,
                        cat.name as item_category

                    FROM matomo_log_conversion_item i
                    LEFT JOIN matomo_log_action sku ON i.idaction_sku = sku.idaction
                    LEFT JOIN matomo_log_action name ON i.idaction_name = name.idaction
                    LEFT JOIN matomo_log_action cat ON i.idaction_category = cat.idaction

                    WHERE i.idvisit = %s AND i.idorder = %s
                """, (idvisit, idorder))
            return cursor.fetchall()
        finally:
            conn.close()

    def fetch_begin_checkout_events(self) -> list:
        """
        Получает begin_checkout события за последние N минут.

        begin_checkout определяется по:
        - event_category = 'ecommerce'
        - event_action = 'begin_checkout'

        Это события когда посетитель перешёл на /store/* страницу.
        """
        conn = self.matomo_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                    SELECT
                        a.idlink_va,
                        a.idvisit,
                        a.server_time,
                        UNIX_TIMESTAMP(a.server_time) as event_timestamp,

                        -- Event info
                        cat.name as event_category,
                        act.name as event_action,
                        name.name as event_name,

                        -- URL
                        url.name as page_url,
                        url.url_prefix,

                        -- Visitor info
                        v.idvisitor,
                        HEX(v.idvisitor) as visitor_id,
                        v.user_id,

                        -- Referer (для source/medium маппинга)
                        v.referer_type,
                        v.referer_name,

                        -- Custom dimensions
                        v.custom_dimension_1 as fbc,
                        v.custom_dimension_2 as fbp,
                        v.custom_dimension_3 as gclid,
                        v.custom_dimension_8 as utm_source,
                        v.custom_dimension_9 as utm_medium,
                        v.custom_dimension_10 as utm_campaign,
                        v.custom_dimension_27 as msclkid,

                        -- Device info (for Meta match quality)
                        v.config_browser_name,
                        v.config_browser_version,
                        v.config_os,
                        v.config_os_version,

                        -- IP Address
                        v.location_ip,

                        -- Geo
                        v.location_country,
                        v.location_city,
                        v.location_region

                    FROM matomo_log_link_visit_action a
                    JOIN matomo_log_visit v ON a.idvisit = v.idvisit
                    LEFT JOIN matomo_log_action cat ON a.idaction_event_category = cat.idaction
                    LEFT JOIN matomo_log_action act ON a.idaction_event_action = act.idaction
                    LEFT JOIN matomo_log_action name ON a.idaction_name = name.idaction
                    LEFT JOIN matomo_log_action url ON a.idaction_url = url.idaction

                    WHERE v.idsite = %s
                      AND a.server_time > DATE_SUB(NOW(), INTERVAL %s MINUTE)
                      AND cat.name = 'ecommerce'
                      AND act.name = 'begin_checkout'

                    ORDER BY a.server_time DESC
                    LIMIT %s
                """, (self.site_id, self.lookback_minutes, self.limit))

            rows = cursor.fetchall()
            events = []
            for row in rows:
                row['event_hash'] = self._make_event_hash(str(row['idvisit']), str(row['idlink_va']))
                row['event_type'] = 'begin_checkout'
                events.append(row)
            return events
        finally:
            conn.close()

    def fetch_scroll_events(self) -> list:
        """
        Получает scroll события за последние N минут.

        scroll определяется по:
        - event_category = 'engagement'
        - event_action = 'scroll'
        - event_name = '25%', '50%', '75%', '90%'

        Это события глубины прокрутки страницы.

        ВАЖНО: Для GA4 нужен реальный page_title (не "25%"!).
        Мы получаем его через subquery из последнего pageview с тем же URL.
        """
        conn = self.matomo_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                    SELECT
                        a.idlink_va,
                        a.idvisit,
                        a.server_time,
                        UNIX_TIMESTAMP(a.server_time) as event_timestamp,

                        -- Event info
                        cat.name as event_category,
                        act.name as event_action,
                        name.name as event_name,

                        -- URL (откуда скроллили)
                        url.name as page_url,
                        url.url_prefix,

                        -- REAL page_title: находим из последнего pageview с тем же URL
                        (SELECT t2.name
                         FROM matomo_log_link_visit_action a2
                         JOIN matomo_log_action u2 ON a2.idaction_url = u2.idaction
                         JOIN matomo_log_action t2 ON a2.idaction_name = t2.idaction
                         WHERE a2.idvisit = a.idvisit
                           AND u2.name = url.name
                           AND u2.type = 1  -- only pageview URLs
                           AND t2.type = 4  -- only page titles
                         ORDER BY a2.idlink_va DESC
                         LIMIT 1) as page_title,

                        -- Visitor info
                        v.idvisitor,
                        HEX(v.idvisitor) as visitor_id,
                        v.user_id,

                        -- Referer (для source/medium маппинга)
                        v.referer_type,
                        v.referer_name,

                        -- Custom dimensions
                        v.custom_dimension_1 as fbc,
                        v.custom_dimension_2 as fbp,
                        v.custom_dimension_3 as gclid,
                        v.custom_dimension_8 as utm_source,
                        v.custom_dimension_9 as utm_medium,
                        v.custom_dimension_10 as utm_campaign,

                        -- Geo
                        v.location_country,
                        v.location_city

                    FROM matomo_log_link_visit_action a
                    JOIN matomo_log_visit v ON a.idvisit = v.idvisit
                    LEFT JOIN matomo_log_action cat ON a.idaction_event_category = cat.idaction
                    LEFT JOIN matomo_log_action act ON a.idaction_event_action = act.idaction
                    LEFT JOIN matomo_log_action name ON a.idaction_name = name.idaction
                    LEFT JOIN matomo_log_action url ON a.idaction_url = url.idaction

                    WHERE v.idsite = %s
                      AND a.server_time > DATE_SUB(NOW(), INTERVAL %s MINUTE)
                      AND cat.name = 'engagement'
                      AND act.name = 'scroll'

                    ORDER BY a.server_time DESC
                    LIMIT %s
                """, (self.site_id, self.lookback_minutes, self.limit))

            rows = cursor.fetchall()
            events = []
            for row in rows:
                row['event_hash'] = self._make_event_hash(str(row['idvisit']), str(row['idlink_va']))
                row['event_type'] = 'scroll'
                # Parse percent_scrolled from event_name (e.g. "25%")
                en = row.get('event_name') or ''
                try:
                    row['percent_scrolled'] = int(en.replace('%', '').strip().split()[0])
                except (ValueError, IndexError):
                    row['percent_scrolled'] = -1
                events.append(row)
            return events
        finally:
            conn.close()



    def fetch_custom_events(self, category: str, action: str) -> list:
        """
        Fetches custom Matomo events by category/action.

        Used for site-specific events like:
          - CTA / book_now -> InitiateCheckout
          - Form / contact_submit -> Contact
        """
        conn = self.matomo_db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                    SELECT
                        a.idlink_va,
                        a.idvisit,
                        a.server_time,
                        UNIX_TIMESTAMP(a.server_time) as event_timestamp,

                        -- Event info
                        cat.name as event_category,
                        act.name as event_action,
                        name.name as event_name,

                        -- URL
                        url.name as page_url,
                        url.url_prefix,

                        -- Visitor info
                        v.idvisitor,
                        HEX(v.idvisitor) as visitor_id,
                        v.user_id,

                        -- Custom dimensions
                        v.custom_dimension_1 as fbc,
                        v.custom_dimension_2 as fbp,
                        v.custom_dimension_3 as gclid,
                        v.custom_dimension_5 as email_hash,
                        v.custom_dimension_6 as phone_hash,
                        v.custom_dimension_7 as whmcs_user_id,
                        v.custom_dimension_8 as utm_source,
                        v.custom_dimension_9 as utm_medium,
                        v.custom_dimension_27 as msclkid,

                        -- Device info
                        v.config_browser_name,
                        v.config_browser_version,
                        v.config_os,
                        v.config_os_version,

                        -- IP Address
                        v.location_ip,

                        -- Geo
                        v.location_country,
                        v.location_city,
                        v.location_region

                    FROM matomo_log_link_visit_action a
                    JOIN matomo_log_visit v ON a.idvisit = v.idvisit
                    LEFT JOIN matomo_log_action cat ON a.idaction_event_category = cat.idaction
                    LEFT JOIN matomo_log_action act ON a.idaction_event_action = act.idaction
                    LEFT JOIN matomo_log_action name ON a.idaction_name = name.idaction
                    LEFT JOIN matomo_log_action url ON a.idaction_url = url.idaction

                    WHERE v.idsite = %s
                      AND a.server_time > DATE_SUB(NOW(), INTERVAL %s MINUTE)
                      AND cat.name = %s
                      AND act.name = %s

                    ORDER BY a.server_time DESC
                    LIMIT %s
                """, (self.site_id, self.lookback_minutes, category, action, self.limit))

            rows = cursor.fetchall()
            events = []
            for row in rows:
                row['event_hash'] = self._make_event_hash(str(row['idvisit']), str(row['idlink_va']))
                row['event_type'] = f'custom_{category}_{action}'
                events.append(row)
            return events
        finally:
            conn.close()

# ============================================================
# GA4 SENDER
# ============================================================

class GA4Sender:
    """Отправка событий в GA4 через Measurement Protocol"""

    def __init__(self, config, logger, validate=False, dry_run=False, force_now=False):
        self.measurement_id = config.get('measurement_id')
        self.api_secret = config.get('api_secret')
        self.debug_mode = validate
        self.force_now = force_now
        self.logger = logger
        self.validate = validate
        self.dry_run = dry_run

    def _get_url(self):
        base = GA4_DEBUG_URL if self.validate else GA4_PROD_URL
        return f"{base}?measurement_id={self.measurement_id}&api_secret={self.api_secret}"

    def _build_client_id(self, event):
        """Генерирует СТАБИЛЬНЫЙ client_id из visitor_id для GA4.
        
        Один visitor_id = один client_id навсегда.
        Формат: {number}.{number} (совместим с gtag.js формат GA1.1.xxx.yyy).
        """
        import hashlib
        visitor_id = event.get('visitor_id', '')
        if visitor_id:
            h = hashlib.sha256(visitor_id.encode()).hexdigest()
            ts_part = int(h[:10], 16) % 10000000000
            id_part = int(h[10:20], 16) % 10000000000
            return f"{ts_part}.{id_part}"
        # Fallback: idvisit (менее стабильный, но лучше чем timestamp)
        idvisit = event.get('idvisit', 0)
        h = hashlib.sha256(str(idvisit).encode()).hexdigest()
        return f"{int(h[:10], 16) % 10000000000}.{int(h[10:20], 16) % 10000000000}"


    @staticmethod
    def _resolve_ip(event):
        """Конвертирует location_ip из Matomo (binary/string) в строку"""
        ip = event.get('location_ip')
        if ip:
            if isinstance(ip, (bytes, bytearray)):
                if len(ip) == 4:
                    return '.'.join(str(b) for b in ip)
                elif len(ip) == 16:
                    return ':'.join(f'{ip[i]:02x}{ip[i+1]:02x}' for i in range(0, 16, 2))
            return str(ip)
        return None

    def _add_ip_override(self, payload, event):
        """Добавляет ip_override в GA4 payload для корректной гео-атрибуции"""
        ip = self._resolve_ip(event)
        if ip:
            payload["ip_override"] = ip

    @staticmethod
    def _build_page_url(event):
        """Реконструирует полный URL с протоколом из Matomo url_prefix + name"""
        url = event.get('page_url', '')
        if url and not url.startswith('http'):
            prefix_id = event.get('url_prefix')
            prefix_map = {0: '', 1: 'http://', 2: 'https://', 3: 'http://www.', 4: 'https://www.'}
            prefix = prefix_map.get(prefix_id, 'https://')
            url = prefix + url
        return url

    @staticmethod
    def _get_session_id(event):
        """Получает session_id из idvisit для GA4"""
        return str(event.get('idvisit', ''))

    @staticmethod
    def _resolve_first_source(event):
        """Возвращает (source, medium) первого визита пользователя из Matomo.
        Matomo хранит referer_type/referer_name первого визита (visitor_count_visits=1)
        через LEFT JOIN в SQL запросе."""
        first_src = event.get('first_utm_source')
        first_med = event.get('first_utm_medium')
        if first_src and first_src != '(not set)':
            return (first_src, first_med or '(not set)')

        first_ref_type = event.get('first_referer_type')
        first_ref_name = event.get('first_referer_name')
        if first_ref_type is not None:
            return map_referer_to_source_medium(int(first_ref_type), first_ref_name)

        # Fallback: текущий source/medium
        return resolve_source_medium(event)

    def send_page_view(self, event):
        """
        Отправляет page_view событие в GA4 с полными данными.

        Custom Dimensions (зарегистрированы в GA4):
        - source: Traffic Source
        - medium: Traffic Medium
        - campaign: Campaign Name
        - country: Country Code
        - city: City Name
        """
        ts = int(event.get('event_timestamp') or time.time()) if not self.force_now else int(time.time())

        source, medium = resolve_source_medium(event)

        # Engagement time
        time_spent = max(int(event.get('time_spent_ref_action') or 0), 1) * 1000

        # Screen resolution
        resolution = str(event.get('config_resolution') or '')

        payload = {
            "client_id": self._build_client_id(event),
            "timestamp_micros": ts * 1000000,
            "events": [{
                "name": "page_view",
                "params": {
                    "session_id": self._get_session_id(event),
                    "page_location": self._build_page_url(event),
                    "page_title": event.get('page_title', ''),
                    "page_referrer": event.get('referer_url', ''),
                    "engagement_time_msec": time_spent,
                    "screen_resolution": resolution,
                    # Custom dimensions
                    "source": source,
                    "medium": medium,
                    "campaign": event.get('utm_campaign', ''),
                    "country": (event.get('location_country') or '').upper(),
                    "city": event.get('location_city', ''),
                }
            }],
            "user_properties": {
                "whm_country": {"value": (event.get('location_country') or '').upper()},
                "whm_first_source": {"value": self._resolve_first_source(event)[0]},
                "whm_first_medium": {"value": self._resolve_first_source(event)[1]},
            }
        }

        # Device type mapping
        device_type = event.get('config_device_type')
        if device_type is not None:
            device_map = {0: 'desktop', 1: 'smartphone', 2: 'tablet', 3: 'feature_phone', 4: 'console'}
            payload["events"][0]["params"]["device_category"] = device_map.get(device_type, 'unknown')

        if event.get('user_id'):
            payload["user_id"] = str(event['user_id'])

        if self.debug_mode:
            payload["events"][0]["params"]["debug_mode"] = 1

        self._add_ip_override(payload, event)
        return self._send(payload, 'page_view', event.get('event_hash', ''))

    def send_session_start(self, event, session_id: str):
        """Отправляет session_start событие"""
        ts = int(event.get('event_timestamp') or time.time()) if not self.force_now else int(time.time())

        source, medium = resolve_source_medium(event)

        payload = {
            "client_id": self._build_client_id(event),
            "timestamp_micros": ts * 1000000,
            "events": [{
                "name": "session_start",
                "params": {
                    "session_id": session_id,
                    "ga_session_id": session_id,
                    "page_location": self._build_page_url(event),
                    "source": source,
                    "medium": medium,
                    "campaign": event.get('utm_campaign', ''),
                    "country": (event.get('location_country') or '').upper(),
                    "city": event.get('location_city', ''),
                }
            }]
        }

        if event.get('user_id'):
            payload["user_id"] = str(event['user_id'])

        if self.debug_mode:
            payload["events"][0]["params"]["debug_mode"] = 1

        self._add_ip_override(payload, event)
        return self._send(payload, 'session_start', f"session_{session_id}")

    def send_purchase(self, event, items: list):
        """Отправляет purchase событие"""
        ts = int(event.get('event_timestamp') or time.time()) if not self.force_now else int(time.time())

        # Currency (из orig_currency или USD)
        currency = (event.get('orig_currency') or 'USD').upper()
        revenue = float(event.get('revenue') or 0)

        # Items
        ga4_items = []
        for i, item in enumerate(items):
            ga4_items.append({
                "item_id": str(item.get('item_id') or f"item_{i}"),
                "item_name": item.get('item_name') or 'Unknown Item',
                "item_category": item.get('item_category', ''),
                "price": float(item.get('price') or 0),
                "quantity": int(item.get('quantity') or 1),
            })

        source, medium = resolve_source_medium(event)

        payload = {
            "client_id": self._build_client_id(event),
            "timestamp_micros": ts * 1000000,
            "events": [{
                "name": "purchase",
                "params": {
                    "session_id": self._get_session_id(event),
                    "transaction_id": str(event.get('transaction_id', '')),
                    "value": revenue,
                    "currency": currency,
                    "items": ga4_items,
                    # Custom dims
                    "source": source,
                    "medium": medium,
                    "campaign": event.get('utm_campaign', ''),
                    "country": (event.get('location_country') or '').upper(),
                    "city": event.get('location_city', ''),
                    # Product info
                    "product_name": event.get('product_name', ''),
                    "client_domain": event.get('client_domain', ''),
                }
            }],
            "user_properties": {
                "first_source": {"value": source},
                "first_medium": {"value": medium},
            }
        }

        if revenue:
            payload["events"][0]["params"]["revenue_tax"] = float(event.get('revenue_tax') or 0)
            payload["events"][0]["params"]["revenue_shipping"] = float(event.get('revenue_shipping') or 0)
            payload["events"][0]["params"]["revenue_discount"] = float(event.get('revenue_discount') or 0)

        if event.get('user_id'):
            payload["user_id"] = str(event['user_id'])
            payload["user_properties"]["whmcs_user_id"] = {"value": str(event['user_id'])}

        if self.debug_mode:
            payload["events"][0]["params"]["debug_mode"] = 1

        self._add_ip_override(payload, event)
        return self._send(payload, 'purchase', event.get('event_hash', ''))

    def send_start_trial(self, event, items: list):
        """Отправляет start_trial событие (бесплатный trial)"""
        ts = int(event.get('event_timestamp') or time.time()) if not self.force_now else int(time.time())

        source, medium = resolve_source_medium(event)

        ga4_items = []
        for i, item in enumerate(items):
            ga4_items.append({
                "item_id": str(item.get('item_id') or f"item_{i}"),
                "item_name": item.get('item_name') or 'Unknown Item',
                "item_category": item.get('item_category', ''),
            })

        payload = {
            "client_id": self._build_client_id(event),
            "timestamp_micros": ts * 1000000,
            "events": [{
                "name": "start_trial",
                "params": {
                    "session_id": self._get_session_id(event),
                    "transaction_id": str(event.get('transaction_id', '')),
                    "value": 0,
                    "currency": "USD",
                    "items": ga4_items,
                    "source": source,
                    "medium": medium,
                    "campaign": event.get('utm_campaign', ''),
                    "country": (event.get('location_country') or '').upper(),
                    "city": event.get('location_city', ''),
                }
            }]
        }

        if event.get('user_id'):
            payload["user_id"] = str(event['user_id'])

        if self.debug_mode:
            payload["events"][0]["params"]["debug_mode"] = 1

        self._add_ip_override(payload, event)
        return self._send(payload, 'start_trial', event.get('event_hash', ''))

    def send_start_free(self, event, items: list):
        """Отправляет start_free событие (бесплатный тариф навсегда)"""
        ts = int(event.get('event_timestamp') or time.time()) if not self.force_now else int(time.time())

        source, medium = resolve_source_medium(event)

        ga4_items = []
        for i, item in enumerate(items):
            ga4_items.append({
                "item_id": str(item.get('item_id') or f"item_{i}"),
                "item_name": item.get('item_name') or 'Unknown Item',
            })

        payload = {
            "client_id": self._build_client_id(event),
            "timestamp_micros": ts * 1000000,
            "events": [{
                "name": "start_free",
                "params": {
                    "session_id": self._get_session_id(event),
                    "transaction_id": str(event.get('transaction_id', '')),
                    "value": 0,
                    "currency": "USD",
                    "items": ga4_items,
                    "source": source,
                    "medium": medium,
                    "country": (event.get('location_country') or '').upper(),
                    "city": event.get('location_city', ''),
                }
            }]
        }

        if event.get('user_id'):
            payload["user_id"] = str(event['user_id'])

        self._add_ip_override(payload, event)
        return self._send(payload, 'start_free', event.get('event_hash', ''))

    def send_begin_checkout(self, event):
        """Отправляет begin_checkout событие"""
        ts = int(event.get('event_timestamp') or time.time()) if not self.force_now else int(time.time())

        source, medium = resolve_source_medium(event)

        payload = {
            "client_id": self._build_client_id(event),
            "timestamp_micros": ts * 1000000,
            "events": [{
                "name": "begin_checkout",
                "params": {
                    "session_id": self._get_session_id(event),
                    "page_location": self._build_page_url(event),
                    "source": source,
                    "medium": medium,
                    "campaign": event.get('utm_campaign', ''),
                    "country": (event.get('location_country') or '').upper(),
                    "city": event.get('location_city', ''),
                }
            }]
        }

        if event.get('user_id'):
            payload["user_id"] = str(event['user_id'])

        if self.debug_mode:
            payload["events"][0]["params"]["debug_mode"] = 1

        self._add_ip_override(payload, event)
        return self._send(payload, 'begin_checkout', event.get('event_hash', ''))

    def send_scroll(self, event):
        """Отправляет scroll событие"""
        ts = int(event.get('event_timestamp') or time.time()) if not self.force_now else int(time.time())

        source, medium = resolve_source_medium(event)

        payload = {
            "client_id": self._build_client_id(event),
            "timestamp_micros": ts * 1000000,
            "events": [{
                "name": "scroll",
                "params": {
                    "session_id": self._get_session_id(event),
                    "page_location": self._build_page_url(event),
                    "page_title": event.get('page_title') or event.get('page_name') or 'unknown',
                    "percent_scrolled": event.get('percent_scrolled', 0),
                    "source": source,
                    "medium": medium,
                    "campaign": event.get('utm_campaign', ''),
                    "country": (event.get('location_country') or '').upper(),
                    "city": event.get('location_city', ''),
                }
            }]
        }

        if event.get('user_id'):
            payload["user_id"] = str(event['user_id'])

        self._add_ip_override(payload, event)
        return self._send(payload, 'scroll', event.get('event_hash', ''))

    @staticmethod
    def _clean_none(obj):
        """Рекурсивно заменяет None на '' в payload — GA4 дропает события с null."""
        if isinstance(obj, dict):
            return {k: GA4Sender._clean_none(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [GA4Sender._clean_none(i) for i in obj]
        if obj is None:
            return ''
        return obj

    def _send(self, payload: dict, event_type: str, event_id: str = '') -> bool:
        """Отправляет payload в GA4 с подробным логированием"""
        if self.dry_run:
            self.logger.info(f"[DRY-RUN] Would send {event_type}")
            return True

        # GA4 silently drops events containing null values — clean them
        payload = self._clean_none(payload)

        try:
            url = self._get_url()
            response = requests.post(url, json=payload, timeout=30)

            if self.validate:
                try:
                    val = response.json()
                    msgs = val.get('validationMessages', [])
                    if msgs:
                        for m in msgs:
                            self.logger.warning(f"  ⚠️ GA4 validation: {m.get('description', m)}")
                except Exception as e:
                    self.logger.debug(f"Error parsing validation response: {e}")

            self._log_payload_summary(event_type, payload, response.status_code)
            return response.status_code in (200, 204)

        except Exception as e:
            self.logger.error(f"❌ ERROR sending {event_type}: {e}")
            return False

    def _log_payload_summary(self, event_type: str, payload: dict, status_code: int):
        """Логирует краткую сводку отправленного payload"""
        events = payload.get('events', [{}])
        params = events[0].get('params', {}) if events else {}

        source = params.get('source', '')
        medium = params.get('medium', '')
        country = params.get('country', '')
        city = params.get('city', '')

        url = params.get('page_location', '')
        client_id = payload.get('client_id', '')

        self.logger.info(
            f"✅ SENT {event_type} | {url[:80]}"
        )
        self.logger.info(
            f"         client={client_id} | src={source}/{medium} | geo={country}/{city}"
        )


# ============================================================
# META SENDER
# ============================================================

class MetaSender:
    """Отправка событий в Meta Conversions API (Facebook CAPI)"""

    API_BASE_URL = "https://graph.facebook.com"
    API_VERSION = "v21.0"

    def __init__(self, config, logger, dry_run=False):
        """
        Args:
            config: {
                "pixel_id": "123456789",
                "access_token": "EAAxxxx...",
                "test_event_code": "TEST12345" (опционально, для тестирования)
                "base_url": "https://client.webhostmost.com" (опционально)
            }
        """
        self.pixel_id = config.get('pixel_id')
        self.access_token = config.get('access_token')
        self.test_event_code = config.get('test_event_code')
        self.base_url = config.get('base_url', 'https://client.webhostmost.com')
        self.logger = logger
        self.dry_run = dry_run

        if not self.pixel_id or not self.access_token:
            raise ValueError("Meta config requires pixel_id and access_token")

    def _get_url(self):
        """URL для отправки событий"""
        return f"{self.API_BASE_URL}/{self.API_VERSION}/{self.pixel_id}/events?access_token={self.access_token}"

    def _hash_sha256(self, value: str) -> str:
        """SHA256 хеш для Meta (lowercase, trimmed)"""
        return hashlib.sha256(value.lower().strip().encode()).hexdigest()

    def _hash_phone(self, phone: str) -> str:
        """Хеш телефона: убираем всё кроме цифр, добавляем +"""
        digits = ''.join(c for c in phone if c.isdigit())
        if digits:
            return self._hash_sha256('+' + digits)
        return ''

    def _is_valid_sha256(self, value) -> bool:
        """Проверяет что строка — валидный SHA256 хеш"""
        if not isinstance(value, (str, bytes, bytearray)):
            return False
        if isinstance(value, (bytes, bytearray)):
            value = value.hex()
        return len(value) == 64 and all(c in '0123456789abcdef' for c in value.lower())

    def _build_user_data(self, event: dict, include_pii: bool = False) -> dict:
        """
        Собирает user_data для Meta CAPI.

        Args:
            event: данные события из Matomo
            include_pii: включать ли email/phone (для конверсий - да, для PageView - нет)
        """
        user_data = {}

        # fbc (Facebook Click ID)
        fbc = event.get('custom_dimension_1') or event.get('fbc')
        if fbc:
            user_data['fbc'] = fbc
            self.logger.info(f"  [META] fbc FULL: {fbc}")
            # Validate fbc format for Meta CAPI diagnostics
            if not fbc.startswith('fb.'):
                self.logger.warning(f"  [META] ⚠️ fbc BAD FORMAT (no fb. prefix): {fbc[:50]}")
            else:
                parts = fbc.split('.', 3)
                if len(parts) < 4:
                    self.logger.warning(f"  [META] ⚠️ fbc BAD FORMAT (not enough parts): {fbc[:50]}")
                else:
                    fbclid = parts[3]
                    if fbclid != fbclid.strip():
                        self.logger.warning(f"  [META] ⚠️ fbc has whitespace in fbclid!")
                    if '%' in fbclid:
                        self.logger.warning(f"  [META] ⚠️ fbc has URL-encoded chars: {fbclid[:50]}")

        # fbp (Facebook Browser ID)
        fbp = event.get('custom_dimension_2') or event.get('fbp')
        if fbp:
            user_data['fbp'] = fbp
            self.logger.debug(f"  [META] fbp: {fbp[:20]}...")

        # Client IP
        ip = event.get('location_ip')
        if ip:
            if isinstance(ip, (bytes, bytearray)):
                # Binary IP from Matomo — convert to string
                if len(ip) == 4:
                    ip = '.'.join(str(b) for b in ip)
                elif len(ip) == 16:
                    ip = ':'.join(f'{ip[i]:02x}{ip[i+1]:02x}' for i in range(0, 16, 2))
            user_data['client_ip_address'] = str(ip)
            self.logger.debug(f"  [META] ip: {ip}")

        # User Agent (собираем из компонент)
        ua = event.get('client_user_agent')
        if not ua:
            browser = event.get('config_browser_name', '')
            browser_ver = event.get('config_browser_version', '')
            os_name = event.get('config_os', '')
            os_ver = event.get('config_os_version', '')
            if browser:
                ua = f"Mozilla/5.0 ({os_name} {os_ver}) AppleWebKit/537.36 (KHTML, like Gecko) {browser} {browser_ver}"
        if ua:
            user_data['client_user_agent'] = ua
            self.logger.debug(f"  [META] ua: {ua[:50]}...")

        # Country (hashed for Meta)
        country = event.get('location_country')
        if country:
            user_data['country'] = [self._hash_sha256(str(country).lower())]
            self.logger.debug(f"  [META] country: {country} -> hashed")

        # City
        city = event.get('location_city')
        if city:
            user_data['ct'] = [self._hash_sha256(str(city).lower())]
            self.logger.debug(f"  [META] city: {city} -> hashed")

        # State/Region
        region = event.get('location_region')
        if region:
            user_data['st'] = [self._hash_sha256(str(region).lower())]
            self.logger.debug(f"  [META] state: {region} -> hashed")

        if include_pii:
            # Email (dimension 5 — pre-hashed SHA256)
            email_hash = event.get('custom_dimension_5') or event.get('email_hash')
            if email_hash:
                if self._is_valid_sha256(email_hash):
                    user_data['em'] = [email_hash.lower()]
                    self.logger.debug("  [META] email: (pre-hashed)")
                else:
                    user_data['em'] = [self._hash_sha256(email_hash)]
                    self.logger.debug("  [META] email: ***@*** -> hashed")

            # Phone (dimension 6 — pre-hashed SHA256)
            phone_hash = event.get('custom_dimension_6') or event.get('phone_hash')
            if phone_hash:
                if self._is_valid_sha256(phone_hash):
                    user_data['ph'] = [phone_hash.lower()]
                    self.logger.debug("  [META] phone: (pre-hashed)")
                else:
                    user_data['ph'] = [self._hash_phone(phone_hash)]
                    self.logger.debug(f"  [META] phone: ***")

            # External ID (User ID, dimension 7)
            uid = event.get('custom_dimension_7') or event.get('user_id')
            if uid:
                user_data['external_id'] = [self._hash_sha256(str(uid))]
                self.logger.debug(f"  [META] external_id: {uid}")

            # Firstname (dimension 23 — pre-hashed)
            fn_hash = event.get('firstname_hash') or event.get('custom_dimension_23')
            if fn_hash and self._is_valid_sha256(fn_hash):
                user_data['fn'] = [fn_hash.lower()]
                self.logger.debug("  [META] firstname: (pre-hashed)")

            # Lastname (dimension 24 — pre-hashed)
            ln_hash = event.get('lastname_hash') or event.get('custom_dimension_24')
            if ln_hash and self._is_valid_sha256(ln_hash):
                user_data['ln'] = [ln_hash.lower()]
                self.logger.debug("  [META] lastname: (pre-hashed)")

            # ZIP (dimension 26 — pre-hashed)
            zip_hash = event.get('zip_hash') or event.get('custom_dimension_26')
            if zip_hash and self._is_valid_sha256(zip_hash):
                user_data['zp'] = [zip_hash.lower()]
                self.logger.debug("  [META] zip: (pre-hashed)")

            # State from WHMCS (dimension 25 — pre-hashed, more accurate than Matomo geo)
            state_hash = event.get('state_hash') or event.get('custom_dimension_25')
            if state_hash and self._is_valid_sha256(state_hash):
                user_data['st'] = [state_hash.lower()]
                self.logger.debug("  [META] state: (pre-hashed from WHMCS)")

        return user_data

    def send_page_view(self, event) -> bool:
        """Отправляет PageView в Meta"""
        ts = int(event.get('event_timestamp') or time.time())

        payload = {
            "data": [{
                "event_name": "PageView",
                "event_time": ts,
                "event_id": f"pv_{event.get('idlink_va', '')}",
                "event_source_url": event.get('page_url', ''),
                "action_source": "website",
                "user_data": self._build_user_data(event, include_pii=False),
            }]
        }

        return self._send(payload, 'PageView', payload["data"][0]["event_id"])

    def send_initiate_checkout(self, event) -> bool:
        """Отправляет InitiateCheckout в Meta"""
        ts = int(event.get('event_timestamp') or time.time())

        payload = {
            "data": [{
                "event_name": "InitiateCheckout",
                "event_time": ts,
                "event_id": f"ic_{event.get('idlink_va', '')}",
                "event_source_url": event.get('page_url', ''),
                "action_source": "website",
                "user_data": self._build_user_data(event, include_pii=False),
            }]
        }

        return self._send(payload, 'InitiateCheckout', payload["data"][0]["event_id"])

    def send_start_trial(self, event, items: list = None) -> bool:
        """Отправляет StartTrial в Meta (trial с revenue=0)"""
        ts = int(event.get('event_timestamp') or time.time())

        custom_data = {
            "value": 0,
            "currency": "USD",
        }
        if items:
            custom_data["content_ids"] = [str(item.get('item_name', '')) for item in items]
            custom_data["content_type"] = "product"

        payload = {
            "data": [{
                "event_name": "StartTrial",
                "event_time": ts,
                "event_id": f"trial_{event.get('transaction_id', '')}",
                "event_source_url": f"{self.base_url}/viewinvoice.php?id={event.get('invoice_id', '')}",
                "action_source": "website",
                "user_data": self._build_user_data(event, include_pii=True),
                "custom_data": custom_data,
            }]
        }

        return self._send(payload, 'StartTrial', payload["data"][0]["event_id"])

    def send_purchase(self, event, items: list = None) -> bool:
        """
        Отправляет Purchase в Meta.

        Событие: оплаченный заказ (plan_type=paid или revenue > 0)

        user_data (ПОЛНЫЙ НАБОР!):
          - fbc, fbp
          - client_ip_address, client_user_agent
          - em, ph, fn, ln, ct, st, zp, country
          - external_id
        """
        ts = int(event.get('event_timestamp') or time.time())

        revenue = float(event.get('revenue') or 0)
        currency = (event.get('orig_currency') or 'USD').upper()

        custom_data = {
            "value": revenue,
            "currency": currency,
        }

        if items:
            custom_data["content_ids"] = [str(item.get('item_name') or item.get('product_name', '')) for item in items]
            custom_data["content_type"] = "product"

        invoice_id = event.get('invoice_id') or event.get('transaction_id', '')
        event_source_url = f"{self.base_url}/viewinvoice.php?id={invoice_id}"

        payload = {
            "data": [{
                "event_name": "Purchase",
                "event_time": ts,
                "event_id": f"purchase_{event.get('transaction_id', '')}",
                "event_source_url": event_source_url,
                "action_source": "website",
                "user_data": self._build_user_data(event, include_pii=True),
                "custom_data": custom_data,
            }]
        }

        return self._send(payload, 'Purchase', payload["data"][0]["event_id"])


    def send_view_content(self, event) -> bool:
        """Sends ViewContent to Meta (page view as content view)"""
        ts = int(event.get('event_timestamp') or time.time())

        payload = {
            "data": [{
                "event_name": "ViewContent",
                "event_time": ts,
                "event_id": f"vc_{event.get('idlink_va', '')}",
                "event_source_url": event.get('page_url', ''),
                "action_source": "website",
                "user_data": self._build_user_data(event, include_pii=False),
            }]
        }

        return self._send(payload, 'ViewContent', payload["data"][0]["event_id"])

    def send_contact(self, event) -> bool:
        """Sends Contact to Meta (form submission with PII when available)"""
        ts = int(event.get('event_timestamp') or time.time())

        payload = {
            "data": [{
                "event_name": "Contact",
                "event_time": ts,
                "event_id": f"contact_{event.get('idlink_va', '')}",
                "event_source_url": event.get('page_url', ''),
                "action_source": "website",
                "user_data": self._build_user_data(event, include_pii=True),
            }]
        }

        return self._send(payload, 'Contact', payload["data"][0]["event_id"])

    def send_lead(self, event) -> bool:
        """Sends Lead to Meta"""
        ts = int(event.get('event_timestamp') or time.time())

        payload = {
            "data": [{
                "event_name": "Lead",
                "event_time": ts,
                "event_id": f"lead_{event.get('idlink_va', '')}",
                "event_source_url": event.get('page_url', ''),
                "action_source": "website",
                "user_data": self._build_user_data(event, include_pii=True),
            }]
        }

        return self._send(payload, 'Lead', payload["data"][0]["event_id"])

    def _send(self, payload: dict, event_type: str, event_id: str = '') -> bool:
        """
        Отправляет payload в Meta Conversions API.

        Args:
            payload: данные для отправки
            event_type: тип события (для логов)
            event_id: ID события (для логов)
        """
        if self.test_event_code:
            payload['test_event_code'] = self.test_event_code
            self.logger.info(f"  [META] 🧪 TEST MODE: {self.test_event_code}")

        if self.dry_run:
            self.logger.info(f"  [META] [DRY-RUN] Would send {event_type}")
            self.logger.debug(f"  [META] Payload: {json.dumps(payload, indent=2)[:500]}")
            return True

        try:
            url = self._get_url()
            response = requests.post(
                url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                events_received = data.get('events_received', 0)
                self.logger.info(
                    f"  [META] ✅ {event_type} ({event_id}) -> events_received={events_received}"
                )
                # Log user_data keys for debugging
                ud = payload.get('data', [{}])[0].get('user_data', {})
                self.logger.info(
                    f"  [META]    user_data keys: {', '.join(list(ud.keys()))}"
                )

                if events_received == 0:
                    self.logger.warning(
                        f"  [META] ⚠️ {event_type} ({event_id}) -> events_received=0"
                    )
                    # Log response details
                    for key, val in data.items():
                        if key != 'events_received':
                            self.logger.info(f"  [META]    {key}: {val}")

                return events_received > 0
            else:
                error = response.json() if response.text else 'Unknown error'
                self.logger.error(
                    f"  [META] ❌ {event_type} ({event_id}) -> HTTP {response.status_code}"
                )
                self.logger.error(f"  [META]    Error {error}")
                return False

        except requests.exceptions.Timeout:
            self.logger.error(f"  [META] ❌ {event_type} ({event_id}) -> TIMEOUT")
            return False
        except requests.exceptions.RequestException as e:
            self.logger.error(f"  [META] ❌ {event_type} ({event_id}) -> {e}")
            return False
        except Exception as e:
            self.logger.error(f"  [META] ❌ {event_type} ({event_id}) -> Unexpected error: {e}")
            return False


# ============================================================
# MICROSOFT ADS SENDER
# ============================================================

class MicrosoftAdsSender:
    """
    Отправка офлайн-конверсий в Microsoft Ads (Bing).

    Использует:
      - OAuth2 refresh token для получения access_token
      - REST API: ApplyOfflineConversions
      - Матчинг по msclkid (предпочтительно) или Enhanced Conversions (email hash)

    Config (sites.yaml):
        microsoft:
          enabled: true
          developer_token: "..."
          account_id: "..."
          customer_id: "..."
          client_id: "..."
          refresh_token: "..."
          conversion_goals:
            purchase: "Purchase"
            start_trial: "StartTrial"
    """

    def __init__(self, config: dict, logger, dry_run: bool = False):
        self.developer_token = config.get('developer_token', '')
        self.account_id = config.get('account_id', '')
        self.customer_id = config.get('customer_id', '')
        self.client_id = config.get('client_id', '')
        self.refresh_token = config.get('refresh_token', '')
        self.conversion_goals = config.get('conversion_goals', {})
        self.logger = logger
        self.dry_run = dry_run
        self._access_token = None
        self._token_expires_at = 0

    def _ensure_access_token(self) -> bool:
        """Получает или обновляет OAuth2 access_token через refresh_token"""
        if self._access_token and time.time() < self._token_expires_at - 60:
            return True

        try:
            response = requests.post(MSFT_TOKEN_URL, data={
                'client_id': self.client_id,
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
                'scope': MSFT_OAUTH_SCOPE,
            }, timeout=30)

            if response.status_code != 200:
                self.logger.error(f"[MSFT] OAuth token refresh failed: HTTP {response.status_code}")
                self.logger.error(f"[MSFT] Response: {response.text[:300]}")
                return False

            tokens = response.json()
            self._access_token = tokens.get('access_token')
            expires_in = int(tokens.get('expires_in', 3600))
            self._token_expires_at = time.time() + expires_in

            # Если пришёл новый refresh_token — обновляем в .env
            new_refresh = tokens.get('refresh_token')
            if new_refresh and new_refresh != self.refresh_token:
                self.refresh_token = new_refresh
                self._update_refresh_token_in_env(new_refresh)
                self.logger.info("[MSFT] ✅ Refresh token updated in .env")

            self.logger.info(f"[MSFT] ✅ OAuth token refreshed (expires in {expires_in}s)")
            return True

        except Exception as e:
            self.logger.error(f"[MSFT] OAuth error: {e}")
            return False

    def _update_refresh_token_in_env(self, new_token: str):
        """Обновляет MICROSOFT_REFRESH_TOKEN в .env файле"""
        try:
            env_path = str(ENV_FILE)
            with open(env_path, 'r') as f:
                content = f.read()

            # Заменяем old refresh token на новый
            content = re.sub(
                r'MICROSOFT_REFRESH_TOKEN=.*',
                f'MICROSOFT_REFRESH_TOKEN={new_token}',
                content
            )

            with open(env_path, 'w') as f:
                f.write(content)

        except Exception as e:
            self.logger.error(f"[MSFT] Failed to update .env: {e}")

    def send_purchase(self, event: dict, items: list = None) -> bool:
        """
        Отправляет Purchase конверсию в Microsoft Ads.

        Поддерживает матчинг по:
        1. MicrosoftClickId (msclkid) — предпочтительно
        2. HashedEmailAddress — Enhanced Conversions (fallback)
        """
        if not self._ensure_access_token():
            return False

        # Данные конверсии
        msclkid = event.get('msclkid') or ''
        email_hash = event.get('email_hash') or event.get('custom_dimension_5') or ''
        phone_hash = event.get('phone_hash') or event.get('custom_dimension_6') or ''
        revenue = float(event.get('revenue') or 0)
        currency = (event.get('orig_currency') or 'USD').upper()
        order_id = event.get('transaction_id', '')

        # Определяем goal name
        plan_type = (event.get('plan_type') or '').lower().strip()
        if plan_type == 'trial':
            goal_name = self.conversion_goals.get('start_trial', 'StartTrial')
        else:
            goal_name = self.conversion_goals.get('purchase', 'Purchase')

        # Время конверсии (формат ISO 8601)
        ts = event.get('event_timestamp') or int(time.time())
        conv_time = datetime.utcfromtimestamp(int(ts)).strftime('%Y-%m-%dT%H:%M:%S.0000000Z')

        # Нужен хотя бы один идентификатор
        has_msclkid = bool(msclkid) and msclkid not in ('', 'null', 'undefined')
        has_email = bool(email_hash) and len(email_hash) == 64

        if not has_msclkid and not has_email:
            self.logger.debug(f"[MSFT] ⏭️ Skip order={order_id}: no msclkid and no email_hash")
            return False

        # Логируем что отправляем
        self.logger.info(
            f"[MSFT] Sending {goal_name}: order={order_id}, "
            f"msclkid={'✓' if has_msclkid else '✗'}, "
            f"email={'✓' if has_email else '✗'}, "
            f"revenue=${revenue}"
        )

        if self.dry_run:
            self.logger.info(f"[MSFT] [DRY-RUN] Would send {goal_name}")
            return True

        # Формируем конверсию
        conversion = {
            "ConversionName": goal_name,
            "ConversionTime": conv_time,
            "ConversionValue": revenue,
            "ConversionCurrencyCode": currency,
        }

        if has_msclkid:
            conversion["MicrosoftClickId"] = msclkid

        # Enhanced Conversions данные
        if has_email:
            conversion["HashedEmailAddress"] = email_hash.lower()
        if phone_hash and len(phone_hash) == 64:
            conversion["HashedPhoneNumber"] = phone_hash.lower()

        # Отправляем
        try:
            headers = {
                'Authorization': f'Bearer {self._access_token}',
                'DeveloperToken': self.developer_token,
                'CustomerAccountId': str(self.account_id),
                'CustomerId': str(self.customer_id),
                'Content-Type': 'application/json',
            }

            body = {
                "OfflineConversions": [conversion]
            }

            response = requests.post(
                MSFT_API_URL,
                headers=headers,
                json=body,
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                # Проверяем partial errors
                partial_errors = data.get('PartialErrors', [])
                if partial_errors:
                    for err in partial_errors:
                        self.logger.warning(
                            f"[MSFT] ⚠️ Partial error: {err.get('Message', err)}"
                        )
                    return False

                self.logger.info(
                    f"[MSFT] ✅ Sent {goal_name}: order={order_id}, revenue=${revenue}"
                )
                return True
            else:
                self.logger.error(
                    f"[MSFT] API error {response.status_code}: {response.text[:300]}"
                )
                # Если 401 — сбрасываем токен для перегенерации
                if response.status_code == 401:
                    self._access_token = None
                    self._token_expires_at = 0
                return False

        except requests.exceptions.Timeout:
            self.logger.error(f"[MSFT] Timeout sending {goal_name}")
            return False
        except Exception as e:
            self.logger.error(f"[MSFT] Error: {e}")
            return False

    def send_start_trial(self, event: dict, items: list = None) -> bool:
        """StartTrial — то же что purchase но с goal_name = StartTrial"""
        # Принудительно ставим plan_type=trial для правильного goal_name
        event = dict(event)
        event['plan_type'] = 'trial'
        return self.send_purchase(event, items)


# ============================================================
# UNIVERSAL FORWARDER
# ============================================================

class UniversalForwarder:
    """Основной оркестратор: читает события из Matomo, шлёт в GA4/Meta/MSFT"""

    def __init__(self, validate=False, dry_run=False, force_now=False):
        self.config = load_config()
        self.validate = validate
        self.dry_run = dry_run
        self.logger = setup_logging()
        self.matomo_db = MatomoDB(self.config)
        self.dedup_db = DedupDB(
            self.config.get('global', {}).get('dedup_db', '/opt/whm-analytics/data/forwarder_sent.db')
        )
        self.fetcher = None  # Will be set per site
        self.force_now = force_now

    def process_site(self, site_id: int, site_config: dict):
        """Обрабатывает один сайт"""
        if not site_config.get('enabled', True):
            return

        name = site_config.get('name', f'Site {site_id}')
        self.logger.info(f"📊 Processing {name} (id={site_id})")

        self.fetcher = EventFetcher(self.matomo_db, site_id)

        # GA4
        ga4_config = site_config.get('ga4')
        if ga4_config and ga4_config.get('enabled'):
            self._process_ga4(site_id, site_config, ga4_config)

        # Meta
        meta_config = site_config.get('meta')
        if meta_config and meta_config.get('enabled'):
            self._process_meta(site_id, site_config, meta_config)

        # Microsoft Ads
        msft_config = site_config.get('microsoft')
        if msft_config and msft_config.get('enabled'):
            self._process_microsoft(site_id, site_config, msft_config)

    def _process_ga4(self, site_id: int, site_config: dict, ga4_config: dict):
        """Обрабатывает GA4 для сайта"""
        events_cfg = ga4_config.get('events', [])
        sender = GA4Sender(ga4_config, self.logger,
                           validate=self.validate, dry_run=self.dry_run,
                           force_now=self.force_now)
        sent_count = 0

        # --- Page Views ---
        if 'page_view' in events_cfg:
            page_views = self.fetcher.fetch_page_views()
            new_pvs = [e for e in page_views if not self.dedup_db.is_sent(e['event_hash'], 'ga4')]
            self.logger.info(f"  📄 Found {len(new_pvs)} new page_views (of {len(page_views)} total)")

            # NOTE: session_start is a GA4 reserved event name — GA4 generates it
            # automatically when it sees a new session_id. Sending it via MP is silently
            # dropped (validationCode: NAME_RESERVED). Source attribution comes from
            # page_view's source/medium/page_referrer params instead.

            for e in new_pvs:
                if sender.send_page_view(e):
                    self.dedup_db.mark_sent(e['event_hash'], site_id, 'page_view', 'ga4')
                    sent_count += 1
                    time.sleep(0.1)

        # --- Conversions (purchase/start_trial/start_free) ---
        if 'purchase' in events_cfg or 'start_trial' in events_cfg or 'start_free' in events_cfg:
            conversions = self.fetcher.fetch_conversions()
            new_convs = [c for c in conversions if not self.dedup_db.is_sent(c['event_hash'], 'ga4')]
            self.logger.info(f"  💰 Found {len(new_convs)} new conversions (of {len(conversions)} total)")

            for c in new_convs:
                items = self.fetcher.fetch_ecommerce_items(c['idvisit'], c['transaction_id'])
                revenue = float(c.get('revenue') or 0)
                plan_type = (c.get('plan_type') or '').lower().strip()
                event_type = c.get('event_type', 'purchase')

                success = False
                if revenue > 0:
                    event_type = 'purchase'
                    success = sender.send_purchase(c, items)
                elif plan_type == 'trial':
                    event_type = 'start_trial'
                    success = sender.send_start_trial(c, items)
                elif plan_type == 'free':
                    event_type = 'start_free'
                    success = sender.send_start_free(c, items)
                else:
                    self.logger.info(f"    ⏭️ Skipping unknown zero-revenue order {c.get('transaction_id')}")
                    continue

                if success:
                    self.dedup_db.mark_sent(c['event_hash'], site_id, event_type, 'ga4')
                    sent_count += 1
                    self.logger.info(
                        f"    ✓ Sent {event_type} for order {c.get('transaction_id')} "
                        f"(revenue=${revenue}, plan_type={plan_type})"
                    )
                    time.sleep(0.1)

        # --- Begin Checkout ---
        if 'begin_checkout' in events_cfg:
            checkouts = self.fetcher.fetch_begin_checkout_events()
            new_cos = [e for e in checkouts if not self.dedup_db.is_sent(e['event_hash'], 'ga4')]
            self.logger.info(f"  🛒 Found {len(new_cos)} new begin_checkout events (of {len(checkouts)} total)")

            for e in new_cos:
                if sender.send_begin_checkout(e):
                    self.dedup_db.mark_sent(e['event_hash'], site_id, 'begin_checkout', 'ga4')
                    sent_count += 1
                    self.logger.info(f"    ✓ Sent begin_checkout for visitor {e.get('visitor_id', '')[:8]}...")
                    time.sleep(0.1)

        # --- Scroll ---
        if 'scroll' in events_cfg:
            scrolls = self.fetcher.fetch_scroll_events()
            new_scrolls = [e for e in scrolls if not self.dedup_db.is_sent(e['event_hash'], 'ga4')]
            self.logger.info(f"  📜 Found {len(new_scrolls)} new scroll events (of {len(scrolls)} total)")

            for e in new_scrolls:
                if sender.send_scroll(e):
                    self.dedup_db.mark_sent(e['event_hash'], site_id, 'scroll', 'ga4')
                    sent_count += 1
                    time.sleep(0.1)

        self.logger.info(f"  ✅ GA4: sent {sent_count} events")

    def _process_meta(self, site_id: int, site_config: dict, meta_config: dict):
        """
        Обрабатывает Meta CAPI для сайта.

        События:
          - page_view: PageView
          - begin_checkout: InitiateCheckout (клик Buy/Try)
          - purchase: StartTrial (если plan_type=trial) или Purchase
        """
        events_cfg = meta_config.get('events', [])
        sender = MetaSender(meta_config, self.logger, dry_run=self.dry_run)
        sent_count = 0

        # --- Page Views ---
        if 'page_view' in events_cfg:
            page_views = self.fetcher.fetch_page_views()
            new_pvs = [e for e in page_views if not self.dedup_db.is_sent(e['event_hash'], 'meta')]
            self.logger.info(f"  [META] 📄 Found {len(new_pvs)} new page_views")

            for e in new_pvs:
                if self.dedup_db.should_skip_event(e['event_hash'], 'meta'):
                    self.logger.info(f"  [META] ⏭️ Skipping {e['event_hash'][:8]}... (max retries exceeded)")
                    continue
                try:
                    if sender.send_page_view(e):
                        self.dedup_db.mark_sent(e['event_hash'], site_id, 'page_view', 'meta')
                        self.dedup_db.clear_failures(e['event_hash'], 'meta')
                        sent_count += 1
                except Exception as ex:
                    self.dedup_db.record_failure(e['event_hash'], 'meta', 'send_page_view failed')
                time.sleep(0.1)

        # --- Begin Checkout ---
        if 'begin_checkout' in events_cfg or 'initiate_checkout' in events_cfg:
            checkouts = self.fetcher.fetch_begin_checkout_events()
            new_cos = [e for e in checkouts if not self.dedup_db.is_sent(e['event_hash'], 'meta')]
            self.logger.info(f"  [META] 🛒 Found {len(new_cos)} new begin_checkout events")

            for e in new_cos:
                try:
                    if sender.send_initiate_checkout(e):
                        self.dedup_db.mark_sent(e['event_hash'], site_id, 'begin_checkout', 'meta')
                        self.logger.info("    [META] ✓ InitiateCheckout sent")
                        sent_count += 1
                except Exception as ex:
                    self.dedup_db.record_failure(e['event_hash'], 'meta', 'send_initiate_checkout failed')
                time.sleep(0.1)

        # --- Conversions (Purchase / StartTrial) ---
        if 'purchase' in events_cfg or 'start_trial' in events_cfg:
            conversions = self.fetcher.fetch_conversions()
            new_convs = [c for c in conversions if not self.dedup_db.is_sent(c['event_hash'], 'meta')]
            self.logger.info(f"  [META] 💰 Found {len(new_convs)} new conversions")

            for c in new_convs:
                plan_type = (c.get('plan_type') or '').lower().strip()
                revenue = float(c.get('revenue') or 0)

                self.logger.info(
                    f"    [META] DEBUG: order={c.get('transaction_id')}, "
                    f"plan_type={plan_type}, revenue={revenue}"
                )

                if self.dedup_db.is_sent(c['event_hash'], 'meta'):
                    self.logger.info(f"    [META] ⏭️ Already sent (dedup)")
                    continue

                # Пропускаем renewals
                is_renewal = (c.get('is_renewal') or '').lower().strip()
                if is_renewal == 'yes':
                    self.logger.info(f"    [META] ⏭️ Skipping renewal order {c.get('transaction_id')}")
                    continue

                items = self.fetcher.fetch_ecommerce_items(c['idvisit'], c['transaction_id'])
                success = False

                if plan_type == 'trial' or (revenue == 0 and plan_type != 'free'):
                    success = sender.send_start_trial(c, items)
                elif revenue > 0:
                    success = sender.send_purchase(c, items)

                if success:
                    self.dedup_db.mark_sent(c['event_hash'], site_id, 'purchase', 'meta')
                    self.dedup_db.clear_failures(c['event_hash'], 'meta')
                    sent_count += 1
                    time.sleep(0.2)


        # --- ViewContent (page_view as ViewContent for non-WHMCS sites) ---
        if 'view_content' in events_cfg:
            page_views = self.fetcher.fetch_page_views()
            new_pvs = [e for e in page_views if not self.dedup_db.is_sent(e['event_hash'], 'meta')]
            self.logger.info(f"  [META] 👁️ Found {len(new_pvs)} new view_content events")

            for e in new_pvs:
                if self.dedup_db.should_skip_event(e['event_hash'], 'meta'):
                    continue
                try:
                    if sender.send_view_content(e):
                        self.dedup_db.mark_sent(e['event_hash'], site_id, 'view_content', 'meta')
                        self.dedup_db.clear_failures(e['event_hash'], 'meta')
                        sent_count += 1
                except Exception as ex:
                    self.logger.error(f"  [META] ViewContent error: {ex}")
                    self.dedup_db.record_failure(e['event_hash'], 'meta', 'send_view_content failed')
                time.sleep(0.1)

        # --- Contact (custom event: Form/contact_submit) ---
        if 'contact' in events_cfg:
            custom_events_cfg = meta_config.get('custom_events', {}).get('contact', {})
            cat = custom_events_cfg.get('matomo_category', 'Form')
            act = custom_events_cfg.get('matomo_action', 'contact_submit')
            contacts = self.fetcher.fetch_custom_events(cat, act)
            new_contacts = [e for e in contacts if not self.dedup_db.is_sent(e['event_hash'], 'meta')]
            self.logger.info(f"  [META] 📞 Found {len(new_contacts)} new contact events")

            for e in new_contacts:
                if self.dedup_db.should_skip_event(e['event_hash'], 'meta'):
                    continue
                try:
                    if sender.send_contact(e):
                        self.dedup_db.mark_sent(e['event_hash'], site_id, 'contact', 'meta')
                        self.dedup_db.clear_failures(e['event_hash'], 'meta')
                        self.logger.info("    [META] ✓ Contact sent")
                        sent_count += 1
                except Exception as ex:
                    self.logger.error(f"  [META] Contact error: {ex}")
                    self.dedup_db.record_failure(e['event_hash'], 'meta', 'send_contact failed')
                time.sleep(0.1)

        # --- Lead (custom event) ---
        if 'lead' in events_cfg:
            custom_events_cfg = meta_config.get('custom_events', {}).get('lead', {})
            cat = custom_events_cfg.get('matomo_category', 'CTA')
            act = custom_events_cfg.get('matomo_action', 'lead')
            leads = self.fetcher.fetch_custom_events(cat, act)
            new_leads = [e for e in leads if not self.dedup_db.is_sent(e['event_hash'], 'meta')]
            self.logger.info(f"  [META] 🎯 Found {len(new_leads)} new lead events")

            for e in new_leads:
                if self.dedup_db.should_skip_event(e['event_hash'], 'meta'):
                    continue
                try:
                    if sender.send_lead(e):
                        self.dedup_db.mark_sent(e['event_hash'], site_id, 'lead', 'meta')
                        self.dedup_db.clear_failures(e['event_hash'], 'meta')
                        self.logger.info("    [META] ✓ Lead sent")
                        sent_count += 1
                except Exception as ex:
                    self.logger.error(f"  [META] Lead error: {ex}")
                    self.dedup_db.record_failure(e['event_hash'], 'meta', 'send_lead failed')
                time.sleep(0.1)

        # --- InitiateCheckout from custom events ---
        if 'initiate_checkout' in events_cfg:
            custom_events_cfg = meta_config.get('custom_events', {})
            if 'initiate_checkout' in custom_events_cfg:
                cat = custom_events_cfg['initiate_checkout'].get('matomo_category', 'CTA')
                act = custom_events_cfg['initiate_checkout'].get('matomo_action', 'book_now')
                checkouts = self.fetcher.fetch_custom_events(cat, act)
            else:
                checkouts = self.fetcher.fetch_begin_checkout_events()
            new_cos = [e for e in checkouts if not self.dedup_db.is_sent(e['event_hash'], 'meta')]
            self.logger.info(f"  [META] 🛒 Found {len(new_cos)} new initiate_checkout events")

            for e in new_cos:
                if self.dedup_db.should_skip_event(e['event_hash'], 'meta'):
                    continue
                try:
                    if sender.send_initiate_checkout(e):
                        self.dedup_db.mark_sent(e['event_hash'], site_id, 'initiate_checkout', 'meta')
                        self.dedup_db.clear_failures(e['event_hash'], 'meta')
                        self.logger.info("    [META] ✓ InitiateCheckout sent")
                        sent_count += 1
                except Exception as ex:
                    self.logger.error(f"  [META] InitiateCheckout error: {ex}")
                    self.dedup_db.record_failure(e['event_hash'], 'meta', 'send_initiate_checkout failed')
                time.sleep(0.1)

        self.logger.info(f"  [META] ✅ Meta CAPI: sent {sent_count} events")

    def _process_microsoft(self, site_id: int, site_config: dict, msft_config: dict):
        """
        Обрабатывает Microsoft Ads Offline Conversions для сайта.

        Отправляет:
          - purchase: когда есть msclkid или email_hash
          - start_trial: когда plan_type='trial'
        """
        events_cfg = msft_config.get('events', ['purchase'])
        sender = MicrosoftAdsSender(msft_config, self.logger, dry_run=self.dry_run)
        sent_count = 0

        # --- Conversions ---
        if 'purchase' in events_cfg or 'start_trial' in events_cfg:
            conversions = self.fetcher.fetch_conversions()
            self.logger.info(f"  [MSFT] 💰 Found {len(conversions)} conversions")

            for c in conversions:
                event_hash = c.get('event_hash', '')

                # Проверяем dedup для microsoft
                if self.dedup_db.is_sent(event_hash, 'microsoft'):
                    continue

                # Max retries
                if self.dedup_db.should_skip_event(event_hash, 'microsoft'):
                    continue

                # Пропускаем renewals
                is_renewal = (c.get('is_renewal') or '').lower().strip()
                if is_renewal == 'yes':
                    continue

                plan_type = (c.get('plan_type') or '').lower().strip()
                revenue = float(c.get('revenue') or 0)

                success = False
                if plan_type == 'trial':
                    success = sender.send_start_trial(c)
                elif revenue > 0 or plan_type == 'paid':
                    success = sender.send_purchase(c)

                if success:
                    self.dedup_db.mark_sent(event_hash, site_id, 'purchase', 'microsoft')
                    self.dedup_db.clear_failures(event_hash, 'microsoft')
                    sent_count += 1
                    time.sleep(0.2)
                else:
                    # Если были данные для отправки, записываем failure
                    msclkid = c.get('msclkid') or ''
                    email_hash = c.get('email_hash') or c.get('custom_dimension_5') or ''
                    has_data = bool(msclkid) or (bool(email_hash) and len(email_hash) == 64)
                    if has_data:
                        self.dedup_db.record_failure(event_hash, 'microsoft', 'send failed')

        self.logger.info(f"  [MSFT] ✅ Microsoft Ads: sent {sent_count} events")

    def run_once(self):
        """Один проход по всем сайтам"""
        self.logger.info("=" * 60)
        self.logger.info(f"🚀 Universal Forwarder - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info("=" * 60)

        sites = self.config.get('sites', {})
        for site_id, site_config in sites.items():
            try:
                self.process_site(int(site_id), site_config)
            except Exception as e:
                self.logger.error(f"❌ Error processing site {site_id}: {e}")

        # Cleanup old dedup entries
        try:
            self.dedup_db.cleanup_old()
        except Exception:
            pass

        self.logger.info("=" * 60)
        self.logger.info("✅ Run completed")

    def run_daemon(self, interval: int = 30):
        """Бесконечный цикл с паузой между итерациями"""
        while True:
            try:
                self.run_once()
            except Exception as e:
                self.logger.error(f"❌ Fatal error in run_once: {e}")

            self.logger.info(f"💤 Sleeping {interval}s...")
            time.sleep(interval)


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="WHM Analytics Universal Forwarder")
    parser.add_argument("--daemon", action="store_true", help="Daemon mode (loop every 30s)")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually send events")
    parser.add_argument("--validate", action="store_true", help="Use GA4 debug endpoint")
    parser.add_argument("--force-now", action="store_true", help="Use current timestamp")
    parser.add_argument("--interval", type=int, default=30, help="Sleep interval in daemon mode")

    args = parser.parse_args()

    forwarder = UniversalForwarder(
        validate=args.validate,
        dry_run=args.dry_run,
        force_now=args.force_now,
    )

    if args.daemon:
        forwarder.run_daemon(interval=args.interval)
    else:
        forwarder.run_once()


if __name__ == "__main__":
    main()
