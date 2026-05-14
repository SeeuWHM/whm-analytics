"""
WHM Analytics Collector - Rate Limiter

Защита от DDoS и злоупотреблений.
Использует slowapi (обёртка над limits).
"""

import logging
from typing import Callable

from fastapi import Request, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from .config import get_settings

logger = logging.getLogger(__name__)


def get_client_ip(request: Request) -> str:
    """
    Получение реального IP клиента.
    
    Порядок проверки:
    1. CF-Connecting-IP (Cloudflare) - самый надёжный за CF
    2. X-Forwarded-For (первый IP) - реальный клиент за прокси
    3. X-Real-IP (Nginx) - fallback
    4. Прямой IP
    """
    # Cloudflare - если используется, это самый надёжный
    if cf_ip := request.headers.get("cf-connecting-ip"):
        return cf_ip.strip()
    
    # X-Forwarded-For - первый IP это реальный клиент
    # Nginx добавляет свой IP в конец, поэтому берём первый
    if forwarded := request.headers.get("x-forwarded-for"):
        first_ip = forwarded.split(",")[0].strip()
        if first_ip:
            return first_ip
    
    # X-Real-IP как fallback
    if real_ip := request.headers.get("x-real-ip"):
        return real_ip.strip()
    
    # Прямое соединение
    return request.client.host if request.client else "0.0.0.0"


def create_limiter() -> Limiter:
    """Создание rate limiter"""
    settings = get_settings()
    
    # Используем наш get_client_ip вместо стандартного
    limiter = Limiter(
        key_func=get_client_ip,
        default_limits=[f"{settings.rate_limit_per_second}/second"],
        enabled=settings.rate_limit_enabled,
        strategy="fixed-window",  # или "moving-window"
    )
    
    return limiter


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """
    Обработчик превышения лимита.
    
    Возвращает 429 Too Many Requests.
    """
    client_ip = get_client_ip(request)
    logger.warning(f"Rate limit exceeded: ip={client_ip} path={request.url.path}")
    
    # Инкрементируем счётчик (импортируем METRICS)
    try:
        from .main import METRICS
        METRICS["requests_rate_limited"] += 1
    except ImportError:
        pass
    
    return Response(
        content='{"status":"error","code":"rate_limit_exceeded","message":"Too many requests"}',
        status_code=429,
        media_type="application/json",
        headers={
            "Retry-After": "1",
            "X-RateLimit-Limit": str(exc.detail),
        }
    )


# Singleton limiter
_limiter: Limiter = None


def get_limiter() -> Limiter:
    """Получить singleton limiter"""
    global _limiter
    
    if _limiter is None:
        _limiter = create_limiter()
    
    return _limiter
