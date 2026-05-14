"""
WHM Analytics Collector - Validator

Валидация входящих запросов:
- Проверка site_id (whitelist)
- Проверка visitor_id формата
- Базовые проверки данных
"""

import logging
from typing import Dict, Any, Tuple, Optional

from .config import get_sites_registry, SitesRegistry

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Ошибка валидации"""
    
    def __init__(self, message: str, code: str = "validation_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class EventValidator:
    """Валидатор событий"""
    
    def __init__(self):
        self._sites = get_sites_registry()
    
    def validate_site_id(self, site_id: Any) -> int:
        """
        Проверка site_id.
        
        Raises:
            ValidationError: если site_id невалидный или не в whitelist
        """
        # Конвертируем в int
        try:
            site_id = int(site_id)
        except (ValueError, TypeError):
            raise ValidationError(
                f"Invalid site_id: {site_id}",
                code="invalid_site_id"
            )
        
        # Проверяем whitelist
        if not self._sites.is_allowed(site_id):
            logger.warning(f"Site ID not in whitelist: {site_id}")
            raise ValidationError(
                f"Site ID not allowed: {site_id}",
                code="site_not_allowed"
            )
        
        return site_id
    
    def validate_visitor_id(self, visitor_id: Any) -> str:
        """
        Проверка visitor_id.
        
        Должен быть hex строкой 16-32 символа.
        
        Raises:
            ValidationError: если visitor_id невалидный
        """
        if not visitor_id:
            raise ValidationError(
                "visitor_id is required",
                code="missing_visitor_id"
            )
        
        visitor_id = str(visitor_id).lower()
        
        # Проверяем длину
        if len(visitor_id) < 16 or len(visitor_id) > 32:
            raise ValidationError(
                f"visitor_id must be 16-32 characters, got {len(visitor_id)}",
                code="invalid_visitor_id_length"
            )
        
        # Проверяем hex
        try:
            int(visitor_id, 16)
        except ValueError:
            raise ValidationError(
                "visitor_id must be hexadecimal",
                code="invalid_visitor_id_format"
            )
        
        return visitor_id
    
    def validate_url(self, url: Any) -> str:
        """
        Проверка URL.
        
        Raises:
            ValidationError: если URL невалидный
        """
        if not url:
            raise ValidationError(
                "url is required",
                code="missing_url"
            )
        
        url = str(url)
        
        # Базовая проверка
        if len(url) > 2048:
            raise ValidationError(
                "url too long (max 2048)",
                code="url_too_long"
            )
        
        # Должен начинаться с http:// или https://
        if not url.startswith(("http://", "https://")):
            raise ValidationError(
                "url must start with http:// or https://",
                code="invalid_url_scheme"
            )
        
        return url
    
    def validate_event(self, data: Dict[str, Any]) -> Tuple[int, str, str]:
        """
        Полная валидация события.
        
        Args:
            data: Данные события
            
        Returns:
            Tuple (site_id, visitor_id, url)
            
        Raises:
            ValidationError: если данные невалидны
        """
        # site_id (может быть в idsite или site_id)
        site_id_raw = data.get("idsite") or data.get("site_id")
        site_id = self.validate_site_id(site_id_raw)
        
        # visitor_id (может быть в _id или visitor_id)
        visitor_id_raw = data.get("_id") or data.get("visitor_id")
        visitor_id = self.validate_visitor_id(visitor_id_raw)
        
        # url
        url = self.validate_url(data.get("url"))
        
        return site_id, visitor_id, url


# Singleton
_validator_instance: Optional[EventValidator] = None


def get_validator() -> EventValidator:
    """Получить singleton экземпляр EventValidator"""
    global _validator_instance
    
    if _validator_instance is None:
        _validator_instance = EventValidator()
    
    return _validator_instance
