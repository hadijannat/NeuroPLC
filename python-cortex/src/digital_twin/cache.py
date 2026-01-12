"""Property cache for BaSyx digital twin queries."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional


# TTL defaults from environment variables
DEFAULT_TTL_SAFETY_S = float(os.environ.get("BASYX_CACHE_TTL_SAFETY_S", "300"))
DEFAULT_TTL_NAMEPLATE_S = float(os.environ.get("BASYX_CACHE_TTL_NAMEPLATE_S", "3600"))
DEFAULT_TTL_FUNC_SAFETY_S = float(os.environ.get("BASYX_CACHE_TTL_FUNC_SAFETY_S", "300"))
DEFAULT_TTL_OPERATIONAL_S = float(os.environ.get("BASYX_CACHE_TTL_OPERATIONAL_S", "0"))  # No cache

# TTL mapping by submodel type
TTL_BY_SUBMODEL = {
    "safety": DEFAULT_TTL_SAFETY_S,
    "nameplate": DEFAULT_TTL_NAMEPLATE_S,
    "functional_safety": DEFAULT_TTL_FUNC_SAFETY_S,
    "operational": DEFAULT_TTL_OPERATIONAL_S,
}


@dataclass
class CachedProperty:
    """A cached property value with TTL."""
    value: Any
    fetched_at: float
    ttl_s: float

    def is_expired(self) -> bool:
        """Check if this cache entry has expired."""
        if self.ttl_s <= 0:
            return True  # TTL of 0 means no caching
        return (time.time() - self.fetched_at) > self.ttl_s


class BasyxPropertyCache:
    """Cache for BaSyx property values with configurable TTL.

    This cache reduces redundant BaSyx API calls for properties that
    change infrequently (safety parameters, nameplate info).
    """

    def __init__(self, default_ttl_s: float = 60.0, enabled: bool = True):
        """Initialize the cache.

        Args:
            default_ttl_s: Default TTL for entries without specific TTL.
            enabled: Whether caching is enabled.
        """
        self._cache: dict[str, CachedProperty] = {}
        self._default_ttl = default_ttl_s
        self._enabled = enabled
        self._hits = 0
        self._misses = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def get(self, key: str) -> Optional[Any]:
        """Get a cached value by key.

        Args:
            key: Cache key (typically submodel_id:property_name).

        Returns:
            The cached value, or None if not found/expired.
        """
        if not self._enabled:
            self._misses += 1
            return None

        entry = self._cache.get(key)
        if entry and not entry.is_expired():
            self._hits += 1
            return entry.value

        self._misses += 1
        if entry:
            # Remove expired entry
            del self._cache[key]
        return None

    def set(self, key: str, value: Any, ttl_s: Optional[float] = None) -> None:
        """Store a value in the cache.

        Args:
            key: Cache key.
            value: Value to cache.
            ttl_s: Optional TTL override (uses default if not specified).
        """
        if not self._enabled:
            return

        effective_ttl = ttl_s if ttl_s is not None else self._default_ttl
        if effective_ttl <= 0:
            return  # Don't cache zero-TTL items

        self._cache[key] = CachedProperty(
            value=value,
            fetched_at=time.time(),
            ttl_s=effective_ttl,
        )

    def invalidate(self, key: str) -> None:
        """Remove a specific entry from the cache."""
        self._cache.pop(key, None)

    def invalidate_submodel(self, submodel_id: str) -> None:
        """Remove all entries for a specific submodel."""
        keys_to_remove = [k for k in self._cache if k.startswith(f"{submodel_id}:")]
        for key in keys_to_remove:
            del self._cache[key]

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()

    def reset_stats(self) -> None:
        """Reset hit/miss counters."""
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "enabled": self._enabled,
            "entries": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate,
        }


def make_cache_key(submodel_id: str, property_name: str) -> str:
    """Create a cache key from submodel ID and property name."""
    return f"{submodel_id}:{property_name}"


def get_ttl_for_submodel(submodel_type: str) -> float:
    """Get the appropriate TTL for a submodel type.

    Args:
        submodel_type: One of "safety", "nameplate", "functional_safety", "operational".

    Returns:
        TTL in seconds.
    """
    return TTL_BY_SUBMODEL.get(submodel_type, 60.0)


# Global cache instance
_CACHE: Optional[BasyxPropertyCache] = None


def get_property_cache(enabled: Optional[bool] = None) -> Optional[BasyxPropertyCache]:
    """Get the global property cache instance.

    Args:
        enabled: Override for cache enabled state. If None, uses BASYX_CACHE_ENABLED env var.

    Returns:
        The cache instance, or None if caching is disabled.
    """
    global _CACHE

    if enabled is None:
        enabled = os.environ.get("BASYX_CACHE_ENABLED", "1") == "1"

    if not enabled:
        return None

    if _CACHE is None:
        _CACHE = BasyxPropertyCache(enabled=True)

    return _CACHE


def reset_property_cache() -> None:
    """Reset the global property cache (useful for testing)."""
    global _CACHE
    _CACHE = None
