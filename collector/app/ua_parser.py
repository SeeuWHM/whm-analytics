"""
WHM Analytics Collector - User-Agent Parser

Парсинг User-Agent строки для определения браузера, ОС, устройства.
"""

import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Пытаемся импортировать user_agents
try:
    from user_agents import parse as ua_parse
    UA_PARSER_AVAILABLE = True
except ImportError:
    UA_PARSER_AVAILABLE = False
    logger.warning("user-agents not installed, UA parsing disabled")


@dataclass
class ParsedUserAgent:
    """Результат парсинга User-Agent"""
    
    # Браузер
    browser_family: str = "Unknown"
    browser_version: str = ""
    
    # ОС
    os_family: str = "Unknown"
    os_version: str = ""
    
    # Устройство
    device_family: str = "Other"
    device_brand: str = ""
    device_model: str = ""
    
    # Флаги
    is_mobile: bool = False
    is_tablet: bool = False
    is_pc: bool = True
    is_bot: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "browser_family": self.browser_family,
            "browser_version": self.browser_version,
            "os_family": self.os_family,
            "os_version": self.os_version,
            "device_family": self.device_family,
            "device_brand": self.device_brand,
            "device_model": self.device_model,
            "is_mobile": self.is_mobile,
            "is_tablet": self.is_tablet,
            "is_pc": self.is_pc,
            "is_bot": self.is_bot,
        }


def parse_user_agent(ua_string: Optional[str]) -> ParsedUserAgent:
    """
    Парсинг User-Agent строки.
    
    Args:
        ua_string: User-Agent строка из заголовка
        
    Returns:
        ParsedUserAgent с информацией о браузере/ОС/устройстве
    """
    result = ParsedUserAgent()
    
    if not ua_string:
        return result
    
    if not UA_PARSER_AVAILABLE:
        # Базовый fallback без библиотеки
        ua_lower = ua_string.lower()
        
        # Определяем мобильное устройство
        if "mobile" in ua_lower or "android" in ua_lower and "mobile" in ua_lower:
            result.is_mobile = True
            result.is_pc = False
        elif "tablet" in ua_lower or "ipad" in ua_lower:
            result.is_tablet = True
            result.is_pc = False
        
        # Определяем бота
        bot_keywords = ["bot", "crawler", "spider", "scraper", "headless"]
        if any(kw in ua_lower for kw in bot_keywords):
            result.is_bot = True
        
        return result
    
    try:
        ua = ua_parse(ua_string)
        
        # Браузер
        result.browser_family = ua.browser.family or "Unknown"
        result.browser_version = ".".join(
            str(v) for v in ua.browser.version if v is not None
        )
        
        # ОС
        result.os_family = ua.os.family or "Unknown"
        result.os_version = ".".join(
            str(v) for v in ua.os.version if v is not None
        )
        
        # Устройство
        result.device_family = ua.device.family or "Other"
        result.device_brand = ua.device.brand or ""
        result.device_model = ua.device.model or ""
        
        # Флаги
        result.is_mobile = ua.is_mobile
        result.is_tablet = ua.is_tablet
        result.is_pc = ua.is_pc
        result.is_bot = ua.is_bot
        
    except Exception as e:
        logger.error(f"Failed to parse User-Agent: {e}")
    
    return result


# Тест
if __name__ == "__main__":
    test_uas = [
        # Desktop
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        # Mobile
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        # Bot
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        # Empty
        "",
        None,
    ]
    
    for ua in test_uas:
        result = parse_user_agent(ua)
        print(f"\nUA: {ua[:50] if ua else 'None'}...")
        print(f"  Browser: {result.browser_family} {result.browser_version}")
        print(f"  OS: {result.os_family} {result.os_version}")
        print(f"  Device: {result.device_family}")
        print(f"  Mobile: {result.is_mobile}, Tablet: {result.is_tablet}, Bot: {result.is_bot}")
