"""
WHM Analytics Collector - Event Enricher

Обогащение событий дополнительными данными:
- IP клиента (с учётом proxy)
- Геолокация (GeoIP)
- User-Agent parsing
- Timestamp
"""

import time
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass

from .geoip import get_geoip, GeoIPLookup
from .ua_parser import parse_user_agent, ParsedUserAgent
from .config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class EnrichedData:
    """Дополнительные данные после обогащения"""
    
    # IP
    client_ip: str = ""
    
    # GeoIP
    country_code: Optional[str] = None
    country_name: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    
    # User-Agent
    browser_family: str = "Unknown"
    browser_version: str = ""
    os_family: str = "Unknown"
    os_version: str = ""
    device_type: str = "desktop"  # desktop, mobile, tablet
    is_bot: bool = False
    
    # Timestamp
    timestamp: int = 0


class EventEnricher:
    """Обогащение событий"""
    
    def __init__(self):
        settings = get_settings()
        self._geoip = get_geoip(settings.geoip_db_path)
    
    def get_client_ip(self, headers: Dict[str, str], direct_ip: str) -> str:
        """
        Определение реального IP клиента.
        
        Порядок проверки:
        1. CF-Connecting-IP (Cloudflare) - самый надёжный за CF
        2. X-Forwarded-For (первый IP) - реальный клиент за прокси
        3. X-Real-IP (Nginx) - fallback
        4. Прямой IP
        """
        # Cloudflare - если используется, это самый надёжный
        if cf_ip := headers.get("cf-connecting-ip"):
            return cf_ip.strip()
        
        # X-Forwarded-For - первый IP это реальный клиент
        # Nginx добавляет свой IP в конец, поэтому берём первый
        if forwarded := headers.get("x-forwarded-for"):
            first_ip = forwarded.split(",")[0].strip()
            if first_ip:
                return first_ip
        
        # X-Real-IP как fallback
        if real_ip := headers.get("x-real-ip"):
            return real_ip.strip()
        
        return direct_ip
    
    def enrich(
        self,
        event_data: Dict[str, Any],
        headers: Dict[str, str],
        direct_ip: str
    ) -> EnrichedData:
        """
        Обогащение события.
        
        Args:
            event_data: Исходные данные события
            headers: HTTP заголовки (lowercase keys)
            direct_ip: IP из сокета
            
        Returns:
            EnrichedData с дополнительными полями
        """
        enriched = EnrichedData()
        
        # 1. IP клиента
        enriched.client_ip = self.get_client_ip(headers, direct_ip)
        
        # 2. GeoIP lookup
        if enriched.client_ip:
            geo = self._geoip.lookup(enriched.client_ip)
            if geo:
                enriched.country_code = geo.get("country_code")
                enriched.country_name = geo.get("country_name")
                enriched.region = geo.get("region")
                enriched.city = geo.get("city")
                enriched.latitude = geo.get("latitude")
                enriched.longitude = geo.get("longitude")
        
        # 3. User-Agent parsing
        ua_string = headers.get("user-agent", "")
        ua = parse_user_agent(ua_string)
        
        enriched.browser_family = ua.browser_family
        enriched.browser_version = ua.browser_version
        enriched.os_family = ua.os_family
        enriched.os_version = ua.os_version
        enriched.is_bot = ua.is_bot
        
        # Device type
        if ua.is_mobile:
            enriched.device_type = "mobile"
        elif ua.is_tablet:
            enriched.device_type = "tablet"
        else:
            enriched.device_type = "desktop"
        
        # 4. Timestamp
        # Если клиент передал cdt (custom datetime), используем его
        # Иначе текущее время
        if cdt := event_data.get("cdt"):
            try:
                enriched.timestamp = int(cdt)
            except (ValueError, TypeError):
                enriched.timestamp = int(time.time())
        else:
            enriched.timestamp = int(time.time())
        
        return enriched


# Singleton
_enricher_instance: Optional[EventEnricher] = None


def get_enricher() -> EventEnricher:
    """Получить singleton экземпляр EventEnricher"""
    global _enricher_instance
    
    if _enricher_instance is None:
        _enricher_instance = EventEnricher()
    
    return _enricher_instance
