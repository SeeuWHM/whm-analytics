"""
Visitor Dimensions Storage — Redis Backend

Redis хранилище для attribution dimensions по visitor_id.
Поддерживает First Touch + Last Touch attribution с per-field TTL.

Redis structure:
  vstore:{visitor_id}        — Hash: fbc, fbp, gclid, msclkid, utm_*, user_id, first_*
  vstore:user:{user_id}     — Set: all visitor_ids linked to this user

Replaces the old SQLite-based visitor_store.
"""

import os
import json
import logging
import re
import time
from typing import Dict, Optional, Any

import redis

logger = logging.getLogger(__name__)

# Dimensions которые сохраняем (attribution)
ATTRIBUTION_DIMENSIONS = [
    'dimension1',   # fbc (Facebook Click ID)
    'dimension2',   # fbp (Facebook Browser ID)
    'dimension3',   # gclid (Google Click ID)
    'dimension4',   # yclid (Yandex Click ID)
    'dimension5',   # email_hash
    'dimension6',   # phone_hash
    'dimension7',   # user_id (WHMCS)
    'dimension8',   # utm_source
    'dimension9',   # utm_medium
    'dimension10',  # utm_campaign
    'dimension11',  # utm_content
    'dimension12',  # utm_term
    'dimension27',  # msclkid (Microsoft Click ID)
]

# TTL per dimension type (seconds)
# Click IDs expire fast (attribution window), browser IDs and UTM longer
DIM_TTL = {
    'dimension1':  7 * 86400,    # fbc: 7 days (Meta click attribution window)
    'dimension2':  90 * 86400,   # fbp: 90 days (browser ID)
    'dimension3':  30 * 86400,   # gclid: 30 days (Google Ads)
    'dimension4':  30 * 86400,   # yclid: 30 days
    'dimension5':  180 * 86400,  # email_hash: 6 months
    'dimension6':  180 * 86400,  # phone_hash: 6 months
    'dimension7':  0,            # user_id: no expiry
    'dimension8':  90 * 86400,   # utm_source: 90 days
    'dimension9':  90 * 86400,   # utm_medium: 90 days
    'dimension10': 90 * 86400,   # utm_campaign: 90 days
    'dimension11': 90 * 86400,   # utm_content: 90 days
    'dimension12': 90 * 86400,   # utm_term: 90 days
    'dimension27': 30 * 86400,   # msclkid: 30 days
}

# First-touch dimensions (stored separately, never overwritten)
FIRST_TOUCH_DIMS = ['dimension1', 'dimension2', 'dimension3', 'dimension8', 'dimension9', 'dimension27']

# Key TTL for the whole visitor hash (180 days — individual fields may expire sooner)
VISITOR_KEY_TTL = 180 * 86400

# Validation patterns
_FBC_PATTERN = re.compile(r'^fb\.\d+\.\d+\..+$')
_FBP_PATTERN = re.compile(r'^fb\.\d+\.\d+\.\d+$')


def _parse_fb_timestamp(fb_value: str):
    """Parse timestamp (epoch seconds) from fb.X.TIMESTAMP_MS.xxx format."""
    try:
        parts = fb_value.split('.')
        if len(parts) >= 3:
            return int(parts[2]) / 1000
    except (ValueError, IndexError):
        pass
    return None


def _is_fbc_fresh(fbc_value: str) -> bool:
    """Check if fbc is within attribution window (90 days).
    Meta click window is up to 28 days, but we keep 90 days (same as fbp cookie)
    because Meta CAPI should always receive fbc — Meta decides attribution, not us.
    Returns True if fresh or if timestamp cannot be parsed (safe fallback)."""
    ts = _parse_fb_timestamp(fbc_value)
    if ts is None:
        return True
    age_days = (time.time() - ts) / 86400
    return age_days <= 90


# Key prefixes
PREFIX = 'vstore:'
USER_PREFIX = 'vstore:user:'


class VisitorStore:
    """
    Redis-backed visitor dimension storage.
    
    Features:
    - Per-field TTL: fbc expires in 7 days, fbp in 90 days, etc.
    - First touch: stored as first_* fields, never overwritten
    - Last touch: updated on each visit (incoming wins)
    - User ID linking: cross-device attribution via user_id
    """
    
    _instance = None
    
    def __new__(cls, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, **kwargs):
        if self._initialized:
            return
        
        self.redis_host = kwargs.get('host') or os.environ.get('REDIS_HOST', '172.30.0.1')
        self.redis_port = int(kwargs.get('port') or os.environ.get('REDIS_PORT', 6379))
        self.redis_db = int(kwargs.get('db') or os.environ.get('REDIS_DB', 3))
        self.redis_password = kwargs.get('password') or os.environ.get('REDIS_PASSWORD', '')
        
        self._pool = redis.ConnectionPool(
            host=self.redis_host,
            port=self.redis_port,
            db=self.redis_db,
            password=self.redis_password,
            decode_responses=True,
            max_connections=20,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        
        # Verify connection
        try:
            r = redis.Redis(connection_pool=self._pool)
            r.ping()
            logger.info(f"VisitorStore Redis connected: {self.redis_host}:{self.redis_port} db={self.redis_db}")
        except redis.ConnectionError as e:
            logger.error(f"VisitorStore Redis connection failed: {e}")
            raise
        
        # Compat attribute for main.py logging
        self.db_path = f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"
        
        self._initialized = True
    
    def _redis(self) -> redis.Redis:
        return redis.Redis(connection_pool=self._pool)
    
    def _key(self, visitor_id: str) -> str:
        return f"{PREFIX}{visitor_id}"
    
    def _user_key(self, user_id: str) -> str:
        return f"{USER_PREFIX}{user_id}"
    
    def save_and_get(
        self, 
        visitor_id: str, 
        incoming_data: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Save incoming dimensions and return merged result.
        
        Logic:
        1. Extract attribution dimensions from incoming data
        2. Load existing from Redis
        3. Merge: incoming wins for last-touch; first-touch never overwritten
        4. Save back with per-field TTL
        5. Return merged dimensions
        """
        if not visitor_id or len(visitor_id) < 8:
            return {}
        
        r = self._redis()
        key = self._key(visitor_id)
        
        try:
            # Extract incoming attribution dims
            incoming = {}
            for dim in ATTRIBUTION_DIMENSIONS:
                val = incoming_data.get(dim)
                if val and str(val).strip():
                    incoming[dim] = str(val).strip()
            
            # Validate incoming formats (reject garbage, don't save it)
            if 'dimension1' in incoming:
                if not _FBC_PATTERN.match(incoming['dimension1']):
                    logger.warning(f"Visitor {visitor_id[:8]}: invalid fbc format dropped: {incoming['dimension1'][:40]}")
                    del incoming['dimension1']
            
            if 'dimension2' in incoming:
                if not _FBP_PATTERN.match(incoming['dimension2']):
                    logger.warning(f"Visitor {visitor_id[:8]}: invalid fbp format dropped: {incoming['dimension2'][:40]}")
                    del incoming['dimension2']
            
            # Load existing
            stored = r.hgetall(key) or {}
            
            # Merge: incoming wins for last-touch
            merged = {}
            for dim in ATTRIBUTION_DIMENSIONS:
                if incoming.get(dim):
                    merged[dim] = incoming[dim]
                elif stored.get(dim):
                    merged[dim] = stored[dim]
            
            # First-touch: store if not already set
            for dim in FIRST_TOUCH_DIMS:
                first_key = f"first_{dim}"
                if incoming.get(dim) and not stored.get(first_key):
                    merged[first_key] = incoming[dim]
                elif stored.get(first_key):
                    merged[first_key] = stored[first_key]
            
            # Preserve user_id from stored if not in incoming
            if stored.get('user_id') and not merged.get('dimension7'):
                merged['dimension7'] = stored['user_id']
            
            # Save to Redis
            if merged:
                pipe = r.pipeline()
                pipe.hset(key, mapping=merged)
                pipe.expire(key, VISITOR_KEY_TTL)
                pipe.execute()
            
            logger.debug(f"Visitor {visitor_id[:8]}: incoming={len(incoming)} stored={len(stored)} merged={len(merged)}")
            
            # Return only dimension keys (not first_* keys)
            result = {k: v for k, v in merged.items() if k.startswith('dimension')}
            
            # Don't return expired fbc (7-day Meta attribution window)
            # Data stays in Redis (first_touch preserved), just not used for attribution
            if 'dimension1' in result and not _is_fbc_fresh(result['dimension1']):
                age = (time.time() - (_parse_fb_timestamp(result['dimension1']) or 0)) / 86400
                logger.info(f"Visitor {visitor_id[:8]}: fbc expired ({age:.0f}d old), not returning for attribution")
                del result['dimension1']
            
            return result
        
        except redis.RedisError as e:
            logger.error(f"VisitorStore Redis error: {e}")
            return {k: v for k, v in incoming.items()} if incoming else {}
    
    MAX_VISITORS_PER_USER = 10  # Limit to prevent bot/incognito spam

    def link_user_id(self, visitor_id: str, user_id: str):
        """Link visitor_id to user_id for cross-device attribution.
        
        Limits visitors per user to MAX_VISITORS_PER_USER.
        When limit exceeded, removes visitors without attribution data (fbc/utm),
        keeping only the valuable ones.
        """
        if not visitor_id or not user_id:
            return
        
        r = self._redis()
        try:
            user_key = self._user_key(user_id)
            
            # Store user_id in visitor hash
            r.hset(self._key(visitor_id), 'user_id', str(user_id))
            # Add visitor to user's set
            r.sadd(user_key, visitor_id)
            r.expire(user_key, VISITOR_KEY_TTL)
            
            # Check if over limit
            count = r.scard(user_key)
            if count > self.MAX_VISITORS_PER_USER:
                self._cleanup_user_visitors(r, user_key, visitor_id)
            
            logger.debug(f"Linked visitor {visitor_id[:8]} to user {user_id}")
        except redis.RedisError as e:
            logger.error(f"Failed to link user_id: {e}")
    
    def _cleanup_user_visitors(self, r, user_key: str, keep_visitor: str):
        """Remove excess visitors for a user. Keeps ones with attribution data (fbc, utm_source)."""
        members = r.smembers(user_key)
        
        valuable = []  # Has fbc or utm_source
        empty = []     # Only fbp or nothing
        
        for vid in members:
            data = r.hgetall(self._key(vid))
            has_attribution = any(data.get(d) for d in ['dimension1', 'dimension3', 'dimension8', 'dimension27'])
            if has_attribution or vid == keep_visitor:
                valuable.append(vid)
            else:
                empty.append(vid)
        
        # Remove empty ones until we're under limit
        to_remove = len(members) - self.MAX_VISITORS_PER_USER
        removed = 0
        for vid in empty:
            if removed >= to_remove:
                break
            r.srem(user_key, vid)
            r.delete(self._key(vid))
            removed += 1
        
        if removed:
            logger.info(f"Cleaned {removed} excess visitors for {user_key} (was {len(members)}, now {len(members)-removed})")
    
    def get_dimensions(self, visitor_id: str, user_id: str = None) -> Dict[str, str]:
        """
        Get dimensions for a visitor, with optional user_id cross-device lookup.
        
        Used by the /visitor/{id}/dimensions API endpoint.
        
        If visitor has no fbc but user_id is provided, searches other visitors
        of the same user for the freshest fbc.
        """
        if not visitor_id:
            return {}
        
        r = self._redis()
        try:
            # Get this visitor's data
            data = r.hgetall(self._key(visitor_id)) or {}
            
            # Filter to dimension keys only
            dims = {k: v for k, v in data.items() if k.startswith('dimension')}
            
            # Don't return expired fbc
            if 'dimension1' in dims and not _is_fbc_fresh(dims['dimension1']):
                logger.info(f"Visitor {visitor_id[:8]}: fbc expired in get_dimensions, excluding")
                del dims['dimension1']
            
            # Cross-device lookup: if no fbc/gclid but have user_id
            lookup_user = user_id or data.get('user_id') or dims.get('dimension7')
            if lookup_user and not dims.get('dimension1'):
                # Find other visitors with this user_id
                other_visitors = r.smembers(self._user_key(lookup_user))
                for other_vid in other_visitors:
                    if other_vid == visitor_id:
                        continue
                    other_data = r.hgetall(self._key(other_vid))
                    if other_data and other_data.get('dimension1') and _is_fbc_fresh(other_data['dimension1']):
                        # Found fresh fbc on another visitor of the same user
                        dims['dimension1'] = other_data['dimension1']
                        logger.info(f"Cross-device fbc found: user={lookup_user} from visitor {other_vid[:8]}")
                        # Also grab fbp if missing
                        if not dims.get('dimension2') and other_data.get('dimension2'):
                            dims['dimension2'] = other_data['dimension2']
                        break
            
            return dims
        
        except redis.RedisError as e:
            logger.error(f"get_dimensions error: {e}")
            return {}
    
    def get_visitor_info(self, visitor_id: str) -> Optional[Dict[str, Any]]:
        """Get full visitor info (for debugging)."""
        r = self._redis()
        try:
            data = r.hgetall(self._key(visitor_id))
            if data:
                return {
                    'visitor_id': visitor_id,
                    'dimensions': {k: v for k, v in data.items() if k.startswith('dimension')},
                    'first_touch': {k: v for k, v in data.items() if k.startswith('first_')},
                    'user_id': data.get('user_id'),
                    'ttl': r.ttl(self._key(visitor_id)),
                }
            return None
        except redis.RedisError as e:
            logger.error(f"get_visitor_info error: {e}")
            return None
    
    def cleanup_old_records(self, ttl_seconds: int = 0) -> int:
        """No-op: Redis handles TTL automatically."""
        return 0
    
    def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        r = self._redis()
        try:
            info = r.info('keyspace')
            db_info = info.get(f'db{self.redis_db}', {})
            return {
                'total_visitors': db_info.get('keys', 0),
                'db_path': self.db_path,
                'redis_connected': True,
            }
        except redis.RedisError:
            return {'total_visitors': 0, 'db_path': self.db_path, 'redis_connected': False}


# Singleton getter (compatible interface with old SQLite version)
_store_instance: Optional[VisitorStore] = None

def get_visitor_store(db_path: str = None) -> VisitorStore:
    """Get singleton VisitorStore instance. db_path param kept for compatibility."""
    global _store_instance
    if _store_instance is None:
        _store_instance = VisitorStore()
    return _store_instance
