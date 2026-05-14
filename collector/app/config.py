"""
WHM Analytics Collector - Configuration

Загружает настройки из:
1. .env файла (секреты)
2. config/sites.yaml (сайты)
3. Переменных окружения
"""

import os
from pathlib import Path
from functools import lru_cache
from typing import Dict, List, Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


# Базовые пути (можно переопределить через env)
BASE_DIR = Path(os.getenv("WHM_BASE_DIR", "/opt/whm-analytics"))
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"


class Settings(BaseSettings):
    """Основные настройки приложения"""
    
    # Сервер
    host: str = "0.0.0.0"
    port: int = 9100
    debug: bool = False
    
    # Matomo
    matomo_url: str = "https://analytics.webhostmost.com"
    matomo_token: Optional[str] = None  # Опционально, для admin API
    
    # GeoIP
    geoip_db_path: str = str(DATA_DIR / "GeoLite2-City.mmdb")
    
    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_per_second: int = 100  # на IP
    
    # Логирование
    log_level: str = "INFO"
    log_file: str = str(LOGS_DIR / "collector.log")
    
    class Config:
        env_file = str(BASE_DIR / ".env")
        env_prefix = ""  # Без префикса, читаем MATOMO_URL напрямую
        case_sensitive = False


class SiteConfig:
    """Конфигурация одного сайта"""
    
    def __init__(self, site_id: int, data: dict):
        self.site_id = site_id
        self.name = data.get("name", f"Site {site_id}")
        self.domain = data.get("domain", "")
        self.type = data.get("type", "frontend")
        self.enabled = data.get("enabled", True)
        self.notes = data.get("notes", "")
        
        # Cross-domain partner (для связки front ↔ WHMCS)
        self.cross_domain_partner = data.get("cross_domain_partner")


class SitesRegistry:
    """Реестр разрешённых сайтов"""
    
    def __init__(self):
        self._sites: Dict[int, SiteConfig] = {}
        self._allowed_ids: set = set()
        self._load_sites()
    
    def _load_sites(self):
        """Загрузка сайтов из sites.yaml"""
        sites_file = CONFIG_DIR / "sites.yaml"
        
        if not sites_file.exists():
            print(f"WARNING: {sites_file} not found, using empty config")
            return
        
        with open(sites_file, "r") as f:
            data = yaml.safe_load(f)
        
        if not data:
            print("WARNING: sites.yaml is empty")
            return
        
        # Загружаем сайты из секции "sites" (новый формат)
        sites_section = data.get("sites", {})
        if isinstance(sites_section, dict):
            for site_id, site_data in sites_section.items():
                site_id = int(site_id)
                if isinstance(site_data, dict):
                    # Пропускаем disabled сайты
                    if site_data.get("enabled") is False:
                        continue
                    self._sites[site_id] = SiteConfig(site_id, site_data)
                    self._allowed_ids.add(site_id)
        
        # Fallback: старый формат test_sites/production_sites
        if not self._sites:
            test_sites = data.get("test_sites", {})
            if isinstance(test_sites, dict):
                for site_id, site_data in test_sites.items():
                    site_id = int(site_id)
                    if isinstance(site_data, dict):
                        self._sites[site_id] = SiteConfig(site_id, site_data)
                        self._allowed_ids.add(site_id)
            
            prod_sites = data.get("production_sites", {})
            if isinstance(prod_sites, dict):
                for site_id, site_data in prod_sites.items():
                    site_id = int(site_id)
                    if isinstance(site_data, dict):
                        if site_data.get("status") == "legacy":
                            continue
                        self._sites[site_id] = SiteConfig(site_id, site_data)
                        self._allowed_ids.add(site_id)
    
    def is_allowed(self, site_id: int) -> bool:
        """Проверка: разрешён ли site_id"""
        return site_id in self._allowed_ids
    
    def get_site(self, site_id: int) -> Optional[SiteConfig]:
        """Получить конфиг сайта"""
        return self._sites.get(site_id)
    
    def get_all_allowed_ids(self) -> List[int]:
        """Список всех разрешённых site_id"""
        return list(self._allowed_ids)


@lru_cache()
def get_settings() -> Settings:
    """Singleton для настроек"""
    return Settings()


@lru_cache()
def get_sites_registry() -> SitesRegistry:
    """Singleton для реестра сайтов"""
    return SitesRegistry()


# Проверка при импорте
if __name__ == "__main__":
    settings = get_settings()
    sites = get_sites_registry()
    
    print(f"Base dir: {BASE_DIR}")
    print(f"Matomo URL: {settings.matomo_url}")
    print(f"GeoIP DB: {settings.geoip_db_path}")
    print(f"Allowed sites: {sites.get_all_allowed_ids()}")
