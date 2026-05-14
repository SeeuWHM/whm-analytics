"""
WHM Analytics Collector - Pydantic Schemas

Модели для валидации входящих данных.
"""

from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field, field_validator
import re


class BaseEvent(BaseModel):
    """Базовые поля для всех событий"""
    
    # Обязательные
    site_id: int = Field(..., alias="idsite", ge=1, le=999)
    visitor_id: str = Field(..., alias="_id", min_length=16, max_length=32)
    url: str = Field(..., max_length=2048)
    
    # Опциональные идентификаторы
    user_id: Optional[str] = Field(None, alias="uid", max_length=200)
    
    # Реферер
    referrer: Optional[str] = Field(None, alias="urlref", max_length=2048)
    
    # Время (Unix timestamp в секундах)
    timestamp: Optional[int] = Field(None, alias="cdt")
    
    # Random для дедупликации
    random: Optional[str] = Field(None, alias="rand")
    
    # Custom Dimensions (visit level)
    dimension1: Optional[str] = Field(None, max_length=255)  # fbc
    dimension2: Optional[str] = Field(None, max_length=255)  # fbp
    dimension3: Optional[str] = Field(None, max_length=255)  # gclid
    dimension4: Optional[str] = Field(None, max_length=255)  # yclid
    dimension5: Optional[str] = Field(None, max_length=255)  # email_hash
    dimension6: Optional[str] = Field(None, max_length=255)  # phone_hash
    dimension7: Optional[str] = Field(None, max_length=255)  # user_id
    dimension8: Optional[str] = Field(None, max_length=255)  # utm_source
    dimension9: Optional[str] = Field(None, max_length=255)  # utm_medium
    dimension27: Optional[str] = Field(None, max_length=255)  # msclkid (Microsoft Click ID)
    
    @field_validator("visitor_id")
    @classmethod
    def validate_visitor_id(cls, v: str) -> str:
        """visitor_id должен быть hex строкой"""
        if not re.match(r"^[a-fA-F0-9]{16,32}$", v):
            raise ValueError("visitor_id must be 16-32 hex characters")
        return v.lower()
    
    class Config:
        populate_by_name = True  # Позволяет использовать alias


class PageviewEvent(BaseEvent):
    """Событие просмотра страницы"""
    
    # Заголовок страницы
    title: Optional[str] = Field(None, alias="action_name", max_length=500)
    
    # Тип действия: pageview
    action_type: Literal["pageview"] = "pageview"
    
    # Размеры экрана
    screen_width: Optional[int] = Field(None, alias="res_w", ge=0, le=10000)
    screen_height: Optional[int] = Field(None, alias="res_h", ge=0, le=10000)
    
    # Браузерное время
    hour: Optional[int] = Field(None, alias="h", ge=0, le=23)
    minute: Optional[int] = Field(None, alias="m", ge=0, le=59)
    second: Optional[int] = Field(None, alias="s", ge=0, le=59)


class CustomEvent(BaseEvent):
    """Произвольное событие (клик, форма, скролл и т.д.)"""
    
    # Event category/action/name (Matomo формат)
    event_category: str = Field(..., alias="e_c", max_length=255)
    event_action: str = Field(..., alias="e_a", max_length=255)
    event_name: Optional[str] = Field(None, alias="e_n", max_length=255)
    event_value: Optional[float] = Field(None, alias="e_v")
    
    action_type: Literal["event"] = "event"


class EcommerceEvent(BaseEvent):
    """Событие e-commerce (покупка, добавление в корзину)"""
    
    action_type: Literal["ecommerce"] = "ecommerce"
    
    # Order ID (для покупки)
    order_id: Optional[str] = Field(None, alias="ec_id", max_length=100)
    
    # Сумма
    revenue: Optional[float] = Field(None, alias="revenue", ge=0)
    subtotal: Optional[float] = Field(None, alias="ec_st", ge=0)
    tax: Optional[float] = Field(None, alias="ec_tx", ge=0)
    shipping: Optional[float] = Field(None, alias="ec_sh", ge=0)
    discount: Optional[float] = Field(None, alias="ec_dt", ge=0)
    
    # Items в корзине (JSON строка)
    items: Optional[str] = Field(None, alias="ec_items")
    
    # Тип: 
    # - "cart" для корзины
    # - "order" для покупки
    ecommerce_type: Literal["cart", "order"] = Field("order", alias="ec_type")


class ConversionEvent(BaseEvent):
    """Конверсия (goal в Matomo)"""
    
    action_type: Literal["goal"] = "goal"
    
    # Goal ID в Matomo
    goal_id: int = Field(..., alias="idgoal", ge=0)
    
    # Revenue (опционально)
    revenue: Optional[float] = Field(None, ge=0)


# ===========================================
# API Response Models
# ===========================================

class CollectResponse(BaseModel):
    """Ответ на /collect"""
    status: Literal["ok", "error", "skipped"]
    visitor_id: Optional[str] = None
    code: Optional[str] = None
    message: Optional[str] = None
    reason: Optional[str] = None
    stored: Optional[Dict[str, str]] = None  # Server-stored tracking params
    
    class Config:
        json_schema_extra = {
            "examples": [
                {"status": "ok", "visitor_id": "abcd1234"},
                {"status": "ok", "visitor_id": "abcd1234", "stored": {"dimension27": "TEST_MSCLKID"}},
                {"status": "error", "code": "invalid_site_id", "message": "Site ID not allowed: 999"},
                {"status": "skipped", "code": "bot", "message": "Bot detected"},
            ]
        }


class HealthResponse(BaseModel):
    """Ответ на /health"""
    status: Literal["healthy", "unhealthy"]
    timestamp: int
    
    
class StatsResponse(BaseModel):
    """Ответ на /stats"""
    requests: Dict[str, int]
    matomo: Dict[str, int]
    uptime_seconds: int
    success_rate: float
    error_rate: float


# ===========================================
# Unified Collect Request
# ===========================================

class CollectRequest(BaseModel):
    """
    Универсальный запрос на /collect.
    
    Поддерживает все типы событий через event_type field.
    """
    # Тип события
    event_type: Literal["pageview", "event", "ecommerce", "goal"] = Field(
        default="pageview",
        description="Тип события"
    )
    
    # === Обязательные поля ===
    site_id: int = Field(
        ..., 
        ge=1, le=999,
        description="ID сайта в Matomo",
        json_schema_extra={"examples": [3, 4]}
    )
    visitor_id: str = Field(
        ..., 
        min_length=16, max_length=32,
        description="Уникальный ID посетителя (hex)",
        json_schema_extra={"examples": ["abcd1234abcd1234"]}
    )
    url: str = Field(
        ..., 
        max_length=2048,
        description="URL страницы",
        json_schema_extra={"examples": ["https://example.com/page"]}
    )
    
    # === Опциональные идентификаторы ===
    user_id: Optional[str] = Field(None, max_length=200, description="ID пользователя (WHMCS)")
    referrer: Optional[str] = Field(None, max_length=2048, description="Referrer URL")
    title: Optional[str] = Field(None, max_length=500, description="Заголовок страницы")
    
    # === Event fields (для event_type="event") ===
    event_category: Optional[str] = Field(None, max_length=255, description="Категория события")
    event_action: Optional[str] = Field(None, max_length=255, description="Действие")
    event_name: Optional[str] = Field(None, max_length=255, description="Название")
    event_value: Optional[float] = Field(None, description="Числовое значение")
    
    # === Begin checkout fields ===
    checkout_type: Optional[str] = Field(None, max_length=100, description="Тип чекаута: hosting/domain_register/domain_transfer")
    checkout_path: Optional[str] = Field(None, max_length=500, description="URL откуда начали чекаут")
    target_url: Optional[str] = Field(None, max_length=2048, description="URL куда переходят")
    
    # === E-commerce fields (для event_type="ecommerce") ===
    order_id: Optional[str] = Field(None, max_length=100, description="ID заказа")
    revenue: Optional[float] = Field(None, ge=0, description="Сумма")
    
    # === Goal fields (для event_type="goal") ===
    goal_id: Optional[int] = Field(None, ge=0, description="ID цели в Matomo")
    
    # === Custom Dimensions ===
    dimension1: Optional[str] = Field(None, max_length=255, description="fbc (Facebook Click ID)")
    dimension2: Optional[str] = Field(None, max_length=255, description="fbp (Facebook Browser ID)")
    dimension3: Optional[str] = Field(None, max_length=255, description="gclid (Google Click ID)")
    dimension4: Optional[str] = Field(None, max_length=255, description="yclid (Yandex Click ID)")
    dimension5: Optional[str] = Field(None, max_length=255, description="email_hash (SHA256)")
    dimension6: Optional[str] = Field(None, max_length=255, description="phone_hash (SHA256)")
    dimension7: Optional[str] = Field(None, max_length=255, description="user_id (WHMCS)")
    dimension8: Optional[str] = Field(None, max_length=255, description="utm_source")
    dimension9: Optional[str] = Field(None, max_length=255, description="utm_medium")
    dimension27: Optional[str] = Field(None, max_length=255, description="msclkid (Microsoft Click ID)")
    
    # === Screen/Time ===
    screen_width: Optional[int] = Field(None, ge=0, le=10000)
    screen_height: Optional[int] = Field(None, ge=0, le=10000)
    
    @field_validator("visitor_id")
    @classmethod
    def validate_visitor_id_hex(cls, v: str) -> str:
        """visitor_id должен быть hex строкой"""
        if not re.match(r"^[a-fA-F0-9]{16,32}$", v):
            raise ValueError("visitor_id must be 16-32 hex characters")
        return v.lower()
    
    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "pageview",
                "site_id": 3,
                "visitor_id": "abcd1234abcd1234",
                "url": "https://example.com/page",
                "title": "My Page",
                "dimension3": "CL1234567890",
                "dimension8": "google",
                "dimension9": "cpc"
            }
        }


class BulkRequest(BaseModel):
    """Batch запрос (несколько событий сразу)"""
    
    requests: List[Dict[str, Any]] = Field(..., max_length=50)
    
    # Auth token (опционально, для доверенных источников)
    token_auth: Optional[str] = Field(None)


# Mapping типов событий
EVENT_TYPES = {
    "pageview": PageviewEvent,
    "event": CustomEvent,
    "ecommerce": EcommerceEvent,
    "goal": ConversionEvent,
}
