"""
WHM Analytics Collector - Main Application

FastAPI приложение для сбора аналитики.

Endpoints:
- POST /collect       - приём событий
- GET  /collect       - приём событий (pixel fallback)
- GET  /health        - health check
- GET  /health/ready  - readiness check
- GET  /metrics       - Prometheus metrics
"""

import logging
import time
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, HTTPException, Query, Form
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .config import get_settings, get_sites_registry
from .validator import get_validator, ValidationError
from .enricher import get_enricher
from .matomo import get_matomo_client
from .limiter import get_limiter, rate_limit_exceeded_handler, get_client_ip
from .schemas import CollectRequest, CollectResponse, HealthResponse, StatsResponse
from .visitor_store import get_visitor_store

# Настройка логирования с записью в файл
LOG_FORMAT = "%(asctime)s [%(levelname)s] [COLLECTOR] %(name)s: %(message)s"
LOG_FILE = "/opt/whm-analytics/logs/collector.log"

# Базовая настройка
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)

# Добавляем file handler
try:
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S%z"))
    logging.getLogger().addHandler(file_handler)
except Exception as e:
    print(f"Warning: Could not create log file {LOG_FILE}: {e}")

logger = logging.getLogger(__name__)

# ============================================================
# SERVER-SIDE COOKIE PERSISTENCE
# Generic cookie names to avoid browser tracking protection lists
# Brave/Safari block document.cookie for known tracker names (_fbp, _msclkid)
# Server-set cookies via Set-Cookie headers bypass this blocking
# ============================================================

# Cookie name → dimension key (for reading cookies from request)
COOKIE_TO_DIM = {
    # Generic names (server-set, bypass Brave)
    '_whm_mc': 'dimension27',  # msclkid
    '_whm_gc': 'dimension3',   # gclid
    '_whm_fc': 'dimension1',   # fbc
    '_whm_fp': 'dimension2',   # fbp
    '_whm_yc': 'dimension4',   # yclid
    '_whm_us': 'dimension8',   # utm_source
    '_whm_um': 'dimension9',   # utm_medium
    # Standard names (set by whm.js document.cookie in Chrome/Firefox)
    '_msclkid': 'dimension27',
    '_fbc': 'dimension1',
    '_fbp': 'dimension2',
    '_whm_gclid': 'dimension3',
}

# Dimension key → cookie name (for setting cookies in response)
# NOTE: fbp (dimension2) excluded - whm.js generates it client-side, no need for server cookie
DIM_TO_COOKIE = {
    'dimension27': '_whm_mc',  # msclkid
    'dimension3':  '_whm_gc',  # gclid
    'dimension1':  '_whm_fc',  # fbc
    'dimension4':  '_whm_yc',  # yclid
    'dimension8':  '_whm_us',  # utm_source
    'dimension9':  '_whm_um',  # utm_medium
}

COOKIE_MAX_AGE = 90 * 24 * 60 * 60  # 90 days in seconds


# Метрики (детальные счётчики)
METRICS = {
    "requests_total": 0,
    "requests_success": 0,
    "requests_error": 0,
    "requests_bot_filtered": 0,
    "requests_rate_limited": 0,
    "matomo_errors": 0,
    "matomo_timeouts": 0,
    "validation_errors": 0,
    "start_time": time.time(),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle события приложения"""
    # Startup
    logger.info("=" * 50)
    logger.info("Starting WHM Analytics Collector v0.1.0")
    logger.info("=" * 50)
    
    settings = get_settings()
    sites = get_sites_registry()
    
    logger.info(f"Matomo URL: {settings.matomo_url}")
    logger.info(f"Allowed sites: {sites.get_all_allowed_ids()}")
    logger.info(f"Rate limiting: {settings.rate_limit_enabled} ({settings.rate_limit_per_second}/sec)")
    logger.info(f"GeoIP database: {settings.geoip_db_path}")
    
    # Initialize visitor store
    try:
        visitor_store = get_visitor_store()
        logger.info(f"Visitor store initialized: {visitor_store.db_path}")
        # Run cleanup on startup
        cleaned = visitor_store.cleanup_old_records()
        if cleaned > 0:
            logger.info(f"Visitor store cleanup: removed {cleaned} old records")
    except Exception as e:
        logger.warning(f"Visitor store init warning (non-fatal): {e}")
    
    logger.info("=" * 50)
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    matomo = get_matomo_client()
    await matomo.close()


# FastAPI приложение
app = FastAPI(
    title="WHM Analytics Collector API",
    version="1.0.0",
    description="""
## Server-side Analytics Collector

Приём событий аналитики и передача в Matomo.

### Типы событий:
- **pageview** — просмотр страницы
- **event** — произвольное событие (клик, скролл)
- **ecommerce** — покупка, корзина
- **goal** — достижение цели

### Custom Dimensions:
| # | Поле | Описание |
|---|------|----------|
| 1 | dimension1 | fbc (Facebook Click ID) |
| 2 | dimension2 | fbp (Facebook Browser ID) |
| 3 | dimension3 | gclid (Google Click ID) |
| 4 | dimension4 | yclid (Yandex Click ID) |
| 5 | dimension5 | email_hash (SHA256) |
| 6 | dimension6 | phone_hash (SHA256) |
| 7 | dimension7 | user_id (WHMCS) |
| 8 | dimension8 | utm_source |
| 9 | dimension9 | utm_medium |
    """,
    lifespan=lifespan,
    docs_url="/docs",      # Swagger UI
    redoc_url="/redoc",    # ReDoc
    openapi_url="/openapi.json",
    openapi_tags=[
        {"name": "collect", "description": "Приём событий аналитики"},
        {"name": "health", "description": "Health checks"},
        {"name": "metrics", "description": "Метрики и статистика"},
    ]
)

# Rate Limiter
limiter = get_limiter()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# CORS управляется nginx - не добавляем здесь чтобы избежать дублирования заголовков
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["GET", "POST", "OPTIONS"],
#     allow_headers=["*"],
#     expose_headers=["*"],
# )


# 1x1 transparent GIF (для pixel tracking)
TRACKING_PIXEL = bytes([
    0x47, 0x49, 0x46, 0x38, 0x39, 0x61,  # GIF89a
    0x01, 0x00, 0x01, 0x00,              # 1x1
    0x80, 0x00, 0x00,                    # Global color table
    0xFF, 0xFF, 0xFF,                    # White
    0x00, 0x00, 0x00,                    # Black
    0x21, 0xF9, 0x04, 0x01,              # Graphic control
    0x00, 0x00, 0x00, 0x00,              # Delay, transparent
    0x2C, 0x00, 0x00, 0x00, 0x00,        # Image descriptor
    0x01, 0x00, 0x01, 0x00, 0x00,        # 1x1
    0x02, 0x02, 0x44, 0x01, 0x00,        # Image data
    0x3B                                  # Trailer
])


def get_headers_dict(request: Request) -> Dict[str, str]:
    """Получить заголовки как словарь (lowercase keys)"""
    return {k.lower(): v for k, v in request.headers.items()}


async def process_event(
    event_data: Dict[str, Any],
    request: Request
) -> Dict[str, Any]:
    """
    Обработка одного события.
    
    Args:
        event_data: Данные события
        request: FastAPI Request
        
    Returns:
        Результат обработки
    """
    global METRICS
    METRICS["requests_total"] += 1
    
    validator = get_validator()
    enricher = get_enricher()
    matomo = get_matomo_client()
    
    # 1. Валидация
    try:
        site_id, visitor_id, url = validator.validate_event(event_data)
    except ValidationError as e:
        logger.warning(f"Validation error [{e.code}]: {e.message} | data={event_data}")
        METRICS["requests_error"] += 1
        METRICS["validation_errors"] += 1
        return {"status": "error", "code": e.code, "message": e.message}
    
    # 2. Обогащение
    headers = get_headers_dict(request)
    direct_ip = get_client_ip(request)
    enriched = enricher.enrich(event_data, headers, direct_ip)
    
    # 2.5 Enrich from server-side cookies (bypass Brave/Safari blocking)
    try:
        request_cookies = request.cookies
        for cookie_name, dim_key in COOKIE_TO_DIM.items():
            if not event_data.get(dim_key) and cookie_name in request_cookies:
                cookie_val = request_cookies[cookie_name].strip()
                if cookie_val and len(cookie_val) < 500:
                    event_data[dim_key] = cookie_val
                    logger.info(f"Cookie enriched: {dim_key} from {cookie_name}")
    except Exception as e:
        logger.warning(f"Cookie enrichment error (continuing): {e}")
    
    # 3. Visitor Store - восстанавливаем/сохраняем атрибуционные dimensions
    try:
        visitor_store = get_visitor_store()
        
        # Собираем входящие dimensions (1-9 = атрибуция)
        incoming_dims = {}
        for i in range(1, 10):
            dim_key = f"dimension{i}"
            if dim_value := event_data.get(dim_key):
                incoming_dims[dim_key] = dim_value
        
        # user_id может быть в dimension7 или uid
        user_id = event_data.get("dimension7") or event_data.get("uid") or event_data.get("user_id")
        
        # Save and get merged dimensions (First Touch + Last Touch)
        merged_dims = visitor_store.save_and_get(visitor_id, event_data)
        
        # Link user_id if present
        if user_id:
            visitor_store.link_user_id(visitor_id, user_id)
        
        # Обновляем event_data с merged dimensions
        for dim_key, dim_value in merged_dims.items():
            if dim_value:  # Не затираем пустыми значениями
                event_data[dim_key] = dim_value
        
        logger.debug(f"Visitor {visitor_id[:8]}: incoming={len(incoming_dims)} merged={len(merged_dims)}")
        
    except Exception as e:
        # Visitor Store не должен ломать трекинг - логируем и продолжаем
        logger.warning(f"Visitor store error (continuing): {e}")
    
    # Логируем каждый запрос (INFO level)
    logger.info(
        f"Event: site={site_id} ip={enriched.client_ip} "
        f"country={enriched.country_code} type={event_data.get('event_type', 'pageview')}"
    )
    
    # Фильтруем ботов
    if enriched.is_bot:
        logger.info(f"Bot filtered: {headers.get('user-agent', '')[:80]}")
        METRICS["requests_bot_filtered"] += 1
        return {"status": "skipped", "reason": "bot"}
    
    # 4. Отправка в Matomo
    user_agent = headers.get("user-agent", "")
    success = await matomo.track(event_data, enriched, user_agent)
    
    if success:
        METRICS["requests_success"] += 1
        return {"status": "ok", "visitor_id": visitor_id[:8]}
    else:
        logger.error(f"Matomo track failed: site={site_id} visitor={visitor_id[:8]}")
        METRICS["requests_error"] += 1
        METRICS["matomo_errors"] += 1
        return {"status": "error", "code": "matomo_error", "message": "Failed to track"}


@app.post(
    "/collect",
    response_model=CollectResponse,
    tags=["collect"],
    summary="Отправить событие аналитики",
    description="Принимает события и отправляет в Matomo"
)
@limiter.limit("100/second")
async def collect_post(
    request: Request,
    event: Optional[CollectRequest] = None
) -> CollectResponse:
    """
    Приём событий через POST.
    
    Поддерживает:
    - JSON body (рекомендуется)
    - Form data (legacy)
    """
    content_type = request.headers.get("content-type", "")
    
    try:
        if "application/json" in content_type:
            event_data = await request.json()
        else:
            # Form data
            form = await request.form()
            event_data = dict(form)
    except Exception as e:
        logger.error(f"Failed to parse request body: {e}")
        raise HTTPException(status_code=400, detail="Invalid request body")
    
    result = await process_event(event_data, request)
    
    # Add stored dimensions to response for client-side usage
    stored = {}
    for dim_key in DIM_TO_COOKIE:
        value = event_data.get(dim_key)
        if value and isinstance(value, str):
            stored[dim_key] = value
    if stored:
        result['stored'] = stored
    
    # Build response with Set-Cookie headers for tracking persistence
    response = JSONResponse(
        content=result,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }
    )
    
    # Set server-side cookies (first-party, bypass Brave/Safari blocking)
    for dim_key, cookie_name in DIM_TO_COOKIE.items():
        value = event_data.get(dim_key)
        if value and isinstance(value, str) and len(value) < 500:
            response.set_cookie(
                key=cookie_name,
                value=str(value),
                max_age=COOKIE_MAX_AGE,
                path='/',
                samesite='lax',
                secure=True,
                httponly=False,  # whm.js needs to read these
            )
    
    return response


@app.get("/collect")
async def collect_get(
    request: Request,
    idsite: Optional[int] = Query(None, alias="idsite"),
    site_id: Optional[int] = Query(None),
    _id: Optional[str] = Query(None, alias="_id"),
    visitor_id: Optional[str] = Query(None),
    url: Optional[str] = Query(None),
    # Остальные параметры через **kwargs не работают в FastAPI,
    # поэтому собираем из query string
):
    """
    Приём событий через GET (pixel fallback).
    
    Возвращает 1x1 transparent GIF.
    """
    # Собираем все query параметры
    event_data = dict(request.query_params)
    
    # Обрабатываем
    result = await process_event(event_data, request)
    
    # Всегда возвращаем pixel (даже при ошибке)
    return Response(
        content=TRACKING_PIXEL,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health():
    """
    Health check endpoint.
    
    Возвращает статус сервиса и timestamp.
    """
    return HealthResponse(status="healthy", timestamp=int(time.time()))


@app.get("/health/ready", tags=["Health"])
async def health_ready():
    """
    Readiness check.
    
    Проверяет что все компоненты готовы:
    - Sites registry loaded
    - Matomo URL configured
    
    Returns 503 if not ready.
    """
    settings = get_settings()
    sites = get_sites_registry()
    
    checks = {
        "sites_loaded": len(sites.get_all_allowed_ids()) > 0,
        "matomo_configured": bool(settings.matomo_url),
    }
    
    all_ready = all(checks.values())
    
    return JSONResponse(
        content={
            "status": "ready" if all_ready else "not_ready",
            "checks": checks,
        },
        status_code=200 if all_ready else 503,
    )
@app.get("/metrics", tags=["Monitoring"])
async def metrics():
    """
    Prometheus-style метрики.
    
    Для мониторинга в Grafana/Prometheus.
    Возвращает text/plain в формате Prometheus.
    """
    global METRICS
    
    uptime = int(time.time() - METRICS["start_time"])
    
    # Prometheus text format
    output = f"""# HELP whm_collector_requests_total Total requests received
# TYPE whm_collector_requests_total counter
whm_collector_requests_total {METRICS["requests_total"]}

# HELP whm_collector_requests_success Successful requests
# TYPE whm_collector_requests_success counter
whm_collector_requests_success {METRICS["requests_success"]}

# HELP whm_collector_requests_error Failed requests
# TYPE whm_collector_requests_error counter
whm_collector_requests_error {METRICS["requests_error"]}

# HELP whm_collector_requests_bot_filtered Bot requests filtered
# TYPE whm_collector_requests_bot_filtered counter
whm_collector_requests_bot_filtered {METRICS["requests_bot_filtered"]}

# HELP whm_collector_matomo_errors Matomo send errors
# TYPE whm_collector_matomo_errors counter
whm_collector_matomo_errors {METRICS["matomo_errors"]}

# HELP whm_collector_uptime_seconds Uptime in seconds
# TYPE whm_collector_uptime_seconds gauge
whm_collector_uptime_seconds {uptime}
"""
    
    return Response(
        content=output,
        media_type="text/plain; charset=utf-8",
    )


@app.get("/stats", response_model=StatsResponse, tags=["Monitoring"])
async def stats():
    """
    JSON версия метрик для быстрого просмотра.
    
    Возвращает статистику запросов, ошибок и uptime.
    """
    global METRICS
    
    uptime = int(time.time() - METRICS["start_time"])
    total = max(METRICS["requests_total"], 1)
    
    return {
        "requests": {
            "total": METRICS["requests_total"],
            "success": METRICS["requests_success"],
            "error": METRICS["requests_error"],
            "validation_errors": METRICS["validation_errors"],
            "bot_filtered": METRICS["requests_bot_filtered"],
            "rate_limited": METRICS["requests_rate_limited"],
        },
        "matomo": {
            "errors": METRICS["matomo_errors"],
            "timeouts": METRICS["matomo_timeouts"],
        },
        "uptime_seconds": uptime,
        "success_rate": round(METRICS["requests_success"] / total * 100, 2),
        "error_rate": round(METRICS["requests_error"] / total * 100, 2),
    }


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "WHM Analytics Collector",
        "version": "0.1.0",
        "endpoints": {
            "collect": "/collect",
            "health": "/health",
            "metrics": "/metrics",
            "stats": "/stats",
            "config": "/config/{site_id}",
        }
    }


@app.get("/config/{site_id}")
async def get_site_config(site_id: int):
    """
    Get client-side configuration for a site by ID.
    
    Returns trackScroll, linkDomains and other client settings
    from sites.yaml for use by whm.js
    """
    return _get_site_config_by_id(site_id)


@app.get("/config")
async def get_config_by_domain(request: Request):
    """
    Auto-detect siteId by domain (from Referer or Origin header).
    
    This allows minimal client-side code:
    whm('init', {endpoint: '/t/collect'});
    
    The siteId is determined automatically from the domain.
    """
    import yaml
    from pathlib import Path
    
    # Get domain from Referer or Origin
    referer = request.headers.get("referer", "")
    origin = request.headers.get("origin", "")
    
    domain = None
    if referer:
        try:
            from urllib.parse import urlparse
            domain = urlparse(referer).hostname
        except:
            pass
    if not domain and origin:
        try:
            from urllib.parse import urlparse
            domain = urlparse(origin).hostname
        except:
            pass
    
    if not domain:
        raise HTTPException(status_code=400, detail="Cannot determine domain from request")
    
    # Load sites.yaml and find matching site
    # Try multiple paths (host vs docker)
    possible_paths = [
        Path("/opt/whm-analytics/config/sites.yaml"),  # Docker mount
        Path("/opt/whm-analytics/forwarder/sites.yaml"),  # Host path
    ]
    
    sites_file = None
    for p in possible_paths:
        if p.exists():
            sites_file = p
            break
    
    if not sites_file:
        raise HTTPException(status_code=500, detail="Sites config not found")
    
    try:
        with open(sites_file, "r") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Error loading sites.yaml: {e}")
        raise HTTPException(status_code=500, detail="Config load error")
    
    sites = data.get("sites", {})
    
    # Find site by domain
    for site_id, site_config in sites.items():
        site_domains = site_config.get("domains", [])
        # Check exact match or subdomain match
        for d in site_domains:
            if domain == d or domain.endswith('.' + d):
                return _get_site_config_response(int(site_id), site_config)
    
    raise HTTPException(status_code=404, detail=f"No site found for domain: {domain}")


def _get_site_config_by_id(site_id: int):
    """Helper: get config by site_id"""
    import yaml
    from pathlib import Path
    
    # Try multiple paths (host vs docker)
    possible_paths = [
        Path("/opt/whm-analytics/config/sites.yaml"),  # Docker mount
        Path("/opt/whm-analytics/forwarder/sites.yaml"),  # Host path
    ]
    
    sites_file = None
    for p in possible_paths:
        if p.exists():
            sites_file = p
            break
    
    if not sites_file:
        raise HTTPException(status_code=500, detail="Sites config not found")
    
    try:
        with open(sites_file, "r") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Error loading sites.yaml: {e}")
        raise HTTPException(status_code=500, detail="Config load error")
    
    sites = data.get("sites", {})
    site_config = sites.get(site_id) or sites.get(str(site_id))
    
    if not site_config:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")
    
    return _get_site_config_response(site_id, site_config)


def _get_site_config_response(site_id: int, site_config: dict):
    """Helper: format config response"""
    client = site_config.get("client", {})
    
    return {
        "siteId": site_id,
        "trackScroll": client.get("trackScroll", True),
        "linkDomains": client.get("linkDomains", []),
    }


# Debug endpoint (только в dev режиме)
@app.get("/debug/config")
async def debug_config():
    """Debug: показать текущую конфигурацию"""
    settings = get_settings()
    
    if not settings.debug:
        raise HTTPException(status_code=404, detail="Not found")
    
    sites = get_sites_registry()
    
    return {
        "matomo_url": settings.matomo_url,
        "geoip_db": settings.geoip_db_path,
        "allowed_sites": sites.get_all_allowed_ids(),
        "rate_limit_enabled": settings.rate_limit_enabled,
    }



# ============================================================
# VISITOR DIMENSIONS API
# ============================================================

@app.get("/visitor/{visitor_id}/dimensions", tags=["Visitor API"])
async def get_visitor_dimensions(
    visitor_id: str,
    user_id: Optional[str] = Query(None, description="WHMCS user_id for cross-device lookup"),
):
    """
    Get attribution dimensions for a visitor.
    
    Returns fbc, fbp, gclid, msclkid, utm_source, utm_medium, utm_campaign, etc.
    If user_id is provided and visitor has no fbc, searches other visitors of the same user.
    
    Used by WHMCS hooks to get attribution data server-side (no cookie dependency).
    """
    if not visitor_id or len(visitor_id) < 8:
        raise HTTPException(status_code=400, detail="Invalid visitor_id (min 8 chars)")
    
    store = get_visitor_store()
    dims = store.get_dimensions(visitor_id, user_id=user_id)
    
    return {
        "visitor_id": visitor_id,
        "dimensions": dims,
        "has_fbc": bool(dims.get("dimension1")),
        "has_fbp": bool(dims.get("dimension2")),
    }


@app.post("/visitor/{visitor_id}/link", tags=["Visitor API"])
async def link_visitor_user(
    visitor_id: str,
    user_id: str = Query(..., description="WHMCS Client ID"),
):
    """
    Link a visitor_id to a WHMCS user_id for cross-device attribution.
    
    Called when user logs in to WHMCS. Enables finding fbc/fbp from other
    browsers/devices of the same user.
    """
    if not visitor_id or len(visitor_id) < 8:
        raise HTTPException(status_code=400, detail="Invalid visitor_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    
    store = get_visitor_store()
    store.link_user_id(visitor_id, user_id)
    
    return {"status": "linked", "visitor_id": visitor_id, "user_id": user_id}


@app.get("/visitor/{visitor_id}/info", tags=["Visitor API"])
async def get_visitor_info_endpoint(visitor_id: str):
    """Debug: full visitor info including first-touch, TTL, linked user."""
    if not visitor_id or len(visitor_id) < 8:
        raise HTTPException(status_code=400, detail="Invalid visitor_id")
    
    store = get_visitor_store()
    info = store.get_visitor_info(visitor_id)
    
    if not info:
        raise HTTPException(status_code=404, detail="Visitor not found")
    
    return info
