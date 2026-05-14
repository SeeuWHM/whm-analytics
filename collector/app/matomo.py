"""
WHM Analytics Collector - Matomo Integration

Отправка событий в Matomo Tracking API.
"""

import logging
import time
from typing import Dict, Any, Optional
from urllib.parse import urlencode

import httpx

from .config import get_settings
from .enricher import EnrichedData

logger = logging.getLogger(__name__)


class MatomoClient:
    """Клиент для Matomo Tracking API"""
    
    def __init__(self):
        settings = get_settings()
        self._base_url = settings.matomo_url.rstrip("/")
        self._tracking_url = f"{self._base_url}/matomo.php"
        self._token = settings.matomo_token
        
        # HTTP клиент с пулом соединений
        self._client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        
        logger.info(f"MatomoClient initialized: {self._tracking_url}")
    
    async def close(self):
        """Закрытие HTTP клиента"""
        await self._client.aclose()
    
    def _build_params(
        self,
        event_data: Dict[str, Any],
        enriched: EnrichedData
    ) -> Dict[str, Any]:
        """
        Формирование параметров для Matomo Tracking API.
        
        См. https://developer.matomo.org/api-reference/tracking-api
        """
        params = {
            "rec": 1,  # Required, indicates tracking request
            "apiv": 1,  # API version
            
            # Site & Visitor
            "idsite": event_data.get("idsite") or event_data.get("site_id"),
            "_id": event_data.get("_id") or event_data.get("visitor_id"),
            
            # URL
            "url": event_data.get("url"),
            
            # Timestamp
            "cdt": enriched.timestamp,
            
            # IP (server-side, нужен token_auth)
            "cip": enriched.client_ip,
        }
        
        # Token для server-side tracking (требуется для cip)
        if self._token:
            params["token_auth"] = self._token
        
        # User ID (если есть)
        if uid := event_data.get("uid") or event_data.get("user_id"):
            params["uid"] = uid
        
        # Referrer
        if urlref := event_data.get("urlref") or event_data.get("referrer"):
            params["urlref"] = urlref
        
        # Page title
        if action_name := event_data.get("action_name") or event_data.get("title"):
            params["action_name"] = action_name
        
        # Random (для дедупликации)
        if rand := event_data.get("rand"):
            params["rand"] = rand
        else:
            params["rand"] = str(int(time.time() * 1000))
        
        # Screen resolution
        if res_w := event_data.get("res_w") or event_data.get("screen_width"):
            if res_h := event_data.get("res_h") or event_data.get("screen_height"):
                params["res"] = f"{res_w}x{res_h}"
        
        # Browser time (h, m, s)
        if (h := event_data.get("h")) is not None:
            params["h"] = h
        if (m := event_data.get("m")) is not None:
            params["m"] = m
        if (s := event_data.get("s")) is not None:
            params["s"] = s
        
        # User-Agent info
        if enriched.browser_family != "Unknown":
            # Matomo определит сам из UA, но мы можем передать
            pass
        
        # GeoIP - Matomo определит сам из cip (client IP)
        # Не передаём country/region/city - Matomo не поддерживает их в Tracking API
        # Он сам определит геолокацию по IP
        
        # Custom Dimensions (dimension1-dimension30)
        # 1-9: attribution (fbc, fbp, gclid, yclid, email_hash, phone_hash, user_id, utm_source, utm_medium)
        # 10-14: action scope (utm_campaign, utm_content, utm_term, page_type, scroll_depth)
        # 15-18: WHMCS e-commerce (plan_type, is_renewal, client_domain, product_name)
        # 19-26: WHMCS extended (invoice_id, orig_currency, orig_value, product_type, hashes...)
        # 27: msclkid (Microsoft Click ID)
        # 30: fbc_action (Action-level fbc)
        for i in range(1, 31):
            dim_key = f"dimension{i}"
            if dim_value := event_data.get(dim_key):
                params[dim_key] = dim_value
        
        # Event tracking (e_c, e_a, e_n, e_v)
        if e_c := event_data.get("e_c") or event_data.get("event_category"):
            params["e_c"] = e_c
            if e_a := event_data.get("e_a") or event_data.get("event_action"):
                params["e_a"] = e_a
            if e_n := event_data.get("e_n") or event_data.get("event_name"):
                params["e_n"] = e_n
            if e_v := event_data.get("e_v") or event_data.get("event_value"):
                params["e_v"] = e_v
        
        # Goal tracking
        if idgoal := event_data.get("idgoal") or event_data.get("goal_id"):
            params["idgoal"] = idgoal
            if revenue := event_data.get("revenue"):
                params["revenue"] = revenue
        
        # E-commerce
        if ec_id := event_data.get("ec_id") or event_data.get("order_id"):
            params["ec_id"] = ec_id
            params["idgoal"] = 0  # Special goal ID for ecommerce
            
            if revenue := event_data.get("revenue"):
                params["revenue"] = revenue
            if ec_st := event_data.get("ec_st") or event_data.get("subtotal"):
                params["ec_st"] = ec_st
            if ec_tx := event_data.get("ec_tx") or event_data.get("tax"):
                params["ec_tx"] = ec_tx
            if ec_sh := event_data.get("ec_sh") or event_data.get("shipping"):
                params["ec_sh"] = ec_sh
            if ec_dt := event_data.get("ec_dt") or event_data.get("discount"):
                params["ec_dt"] = ec_dt
            if ec_items := event_data.get("ec_items") or event_data.get("items"):
                params["ec_items"] = ec_items
        
        # Убираем None значения
        return {k: v for k, v in params.items() if v is not None}
    
    async def track(
        self,
        event_data: Dict[str, Any],
        enriched: EnrichedData,
        user_agent: str = ""
    ) -> bool:
        """
        Отправка события в Matomo.
        
        Args:
            event_data: Данные события
            enriched: Обогащённые данные
            user_agent: User-Agent строка
            
        Returns:
            True если успешно, False если ошибка
        """
        params = self._build_params(event_data, enriched)
        
        # Debug: log ALL params for debugging 400 error
        safe_params = {k: v for k, v in params.items() if k != 'token_auth'}
        logger.info(f"Matomo FULL params: {safe_params}")
        
        # TEMP: Log dimensions for debugging
        dims = {k: v for k, v in params.items() if k.startswith('dimension')}
        if dims:
            logger.info(f"Matomo dimensions: {dims}")
        if 'ec_id' in params:
            logger.info(f"Matomo e-commerce: ec_id={params.get('ec_id')} revenue={params.get('revenue')}")
        
        headers = {}
        if user_agent:
            headers["User-Agent"] = user_agent
        
        try:
            # POST запрос к Matomo
            response = await self._client.post(
                self._tracking_url,
                data=params,
                headers=headers,
            )
            
            if response.status_code == 200:
                logger.debug(
                    f"Event tracked: site={params.get('idsite')}, "
                    f"visitor={params.get('_id')[:8]}..."
                )
                return True
            else:
                logger.error(
                    f"Matomo HTTP error {response.status_code}: {response.text[:200]}"
                )
                return False
                
        except httpx.TimeoutException as e:
            logger.error(f"Matomo timeout: {e}")
            return False
        except httpx.ConnectError as e:
            logger.error(f"Matomo connection error: {e}")
            return False
        except Exception as e:
            logger.exception(f"Matomo unexpected error: {e}")
            return False
    
    async def track_bulk(
        self,
        events: list,
        user_agent: str = ""
    ) -> int:
        """
        Отправка нескольких событий (bulk).
        
        Returns:
            Количество успешно отправленных
        """
        # TODO: Реализовать bulk API Matomo
        # Пока отправляем по одному
        success_count = 0
        for event_data, enriched in events:
            if await self.track(event_data, enriched, user_agent):
                success_count += 1
        return success_count


# Singleton
_matomo_client: Optional[MatomoClient] = None


def get_matomo_client() -> MatomoClient:
    """Получить singleton экземпляр MatomoClient"""
    global _matomo_client
    
    if _matomo_client is None:
        _matomo_client = MatomoClient()
    
    return _matomo_client
