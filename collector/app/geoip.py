"""
WHM Analytics Collector - GeoIP lookup

Определение геолокации по IP с помощью MaxMind GeoIP2.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any
from functools import lru_cache

logger = logging.getLogger(__name__)

# Пытаемся импортировать geoip2
try:
    import geoip2.database
    import geoip2.errors
    GEOIP_AVAILABLE = True
except ImportError:
    GEOIP_AVAILABLE = False
    logger.warning("geoip2 not installed, GeoIP lookup disabled")


class GeoIPLookup:
    """Обёртка над MaxMind GeoIP2"""
    
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self._reader = None
        self._load_database()
    
    def _load_database(self):
        """Загрузка базы данных"""
        if not GEOIP_AVAILABLE:
            logger.warning("GeoIP2 library not available")
            return
        
        if not self.db_path.exists():
            logger.warning(f"GeoIP database not found: {self.db_path}")
            logger.info("Download it from: https://dev.maxmind.com/geoip/geolite2-free-geolocation-data")
            return
        
        try:
            self._reader = geoip2.database.Reader(str(self.db_path))
            logger.info(f"GeoIP database loaded: {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to load GeoIP database: {e}")
    
    def lookup(self, ip: str) -> Optional[Dict[str, Any]]:
        """
        Определение геолокации по IP.
        
        Returns:
            dict с полями: country_code, country_name, region, city, lat, lon
            или None если не найдено
        """
        if not self._reader:
            return None
        
        # Пропускаем локальные адреса
        if ip in ("127.0.0.1", "::1") or ip.startswith(("10.", "192.168.", "172.")):
            return None
        
        try:
            response = self._reader.city(ip)
            
            return {
                "country_code": response.country.iso_code,
                "country_name": response.country.name,
                "region": response.subdivisions.most_specific.name if response.subdivisions else None,
                "region_code": response.subdivisions.most_specific.iso_code if response.subdivisions else None,
                "city": response.city.name,
                "latitude": response.location.latitude,
                "longitude": response.location.longitude,
                "timezone": response.location.time_zone,
            }
        
        except geoip2.errors.AddressNotFoundError:
            logger.debug(f"IP not found in GeoIP database: {ip}")
            return None
        
        except Exception as e:
            logger.error(f"GeoIP lookup error for {ip}: {e}")
            return None
    
    def close(self):
        """Закрытие базы данных"""
        if self._reader:
            self._reader.close()
            self._reader = None


# Singleton instance
_geoip_instance: Optional[GeoIPLookup] = None


def get_geoip(db_path: str) -> GeoIPLookup:
    """Получить singleton экземпляр GeoIPLookup"""
    global _geoip_instance
    
    if _geoip_instance is None:
        _geoip_instance = GeoIPLookup(db_path)
    
    return _geoip_instance


# Тест
if __name__ == "__main__":
    from config import get_settings
    
    settings = get_settings()
    geoip = get_geoip(settings.geoip_db_path)
    
    # Тест с публичным IP
    test_ips = ["8.8.8.8", "77.88.8.8", "1.1.1.1"]
    
    for ip in test_ips:
        result = geoip.lookup(ip)
        print(f"{ip}: {result}")
