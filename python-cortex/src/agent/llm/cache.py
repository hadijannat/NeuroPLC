"""Semantic caching for LLM responses to reduce API costs."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

from ..schemas import Constraints, RecommendationCandidate, StateObservation


@dataclass
class CacheEntry:
    """A cached recommendation with metadata."""
    observation: StateObservation
    constraints: Constraints
    candidate: RecommendationCandidate
    created_at: float
    hit_count: int = 0


@dataclass
class CacheStats:
    """Statistics for cache performance monitoring."""
    total_lookups: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float:
        if self.total_lookups == 0:
            return 0.0
        return self.cache_hits / self.total_lookups


class SemanticCache:
    """Cache LLM responses based on similarity of inputs.

    Uses a simple Euclidean distance metric on normalized state values
    to determine if a cached response is valid for a new query.

    This avoids expensive embedding models while still providing
    meaningful similarity matching for numeric industrial data.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.95,
        ttl_s: float = 60.0,
        max_entries: int = 100,
    ):
        """Initialize the semantic cache.

        Args:
            similarity_threshold: Minimum similarity (0-1) for cache hit
            ttl_s: Time-to-live in seconds for cache entries
            max_entries: Maximum number of cached entries
        """
        self.similarity_threshold = similarity_threshold
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._entries: list[CacheEntry] = []
        self._stats = CacheStats()

        # Normalization ranges for distance calculation
        # Based on typical industrial motor parameters
        self._ranges = {
            "speed": (0.0, 5000.0),    # RPM
            "temp": (0.0, 150.0),       # Celsius
            "pressure": (0.0, 20.0),    # Bar
        }

    @property
    def stats(self) -> CacheStats:
        """Get cache statistics."""
        return self._stats

    def lookup(
        self,
        observation: StateObservation,
        constraints: Constraints,
    ) -> Optional[RecommendationCandidate]:
        """Look up a cached recommendation for similar inputs.

        Args:
            observation: Current sensor state
            constraints: Current safety constraints

        Returns:
            Cached RecommendationCandidate if found, None otherwise
        """
        self._stats.total_lookups += 1
        now = time.time()

        # Clean expired entries
        self._entries = [
            e for e in self._entries
            if (now - e.created_at) < self.ttl_s
        ]

        # Find best match
        best_entry: Optional[CacheEntry] = None
        best_similarity: float = 0.0

        for entry in self._entries:
            # Check constraints match exactly
            if not self._constraints_match(constraints, entry.constraints):
                continue

            # Calculate similarity
            similarity = self._calculate_similarity(observation, entry.observation)
            if similarity >= self.similarity_threshold and similarity > best_similarity:
                best_similarity = similarity
                best_entry = entry

        if best_entry is not None:
            best_entry.hit_count += 1
            self._stats.cache_hits += 1
            return best_entry.candidate

        self._stats.cache_misses += 1
        return None

    def store(
        self,
        observation: StateObservation,
        constraints: Constraints,
        candidate: RecommendationCandidate,
    ) -> None:
        """Store a recommendation in the cache.

        Args:
            observation: Sensor state when recommendation was made
            constraints: Safety constraints that were active
            candidate: The recommendation to cache
        """
        # Evict oldest entries if at capacity
        while len(self._entries) >= self.max_entries:
            self._entries.sort(key=lambda e: e.created_at)
            self._entries.pop(0)
            self._stats.evictions += 1

        entry = CacheEntry(
            observation=observation,
            constraints=constraints,
            candidate=candidate,
            created_at=time.time(),
        )
        self._entries.append(entry)

    def clear(self) -> None:
        """Clear all cache entries."""
        self._entries.clear()

    def _constraints_match(self, c1: Constraints, c2: Constraints) -> bool:
        """Check if two constraint sets are identical."""
        return (
            c1.max_speed_rpm == c2.max_speed_rpm
            and c1.min_speed_rpm == c2.min_speed_rpm
            and c1.max_rate_rpm == c2.max_rate_rpm
            and c1.max_temp_c == c2.max_temp_c
        )

    def _calculate_similarity(
        self,
        obs1: StateObservation,
        obs2: StateObservation,
    ) -> float:
        """Calculate similarity between two observations.

        Uses normalized Euclidean distance converted to similarity score.
        Similarity of 1.0 means identical, 0.0 means maximally different.
        """
        # Extract and normalize values
        speed_range = self._ranges["speed"]
        temp_range = self._ranges["temp"]
        pressure_range = self._ranges["pressure"]

        def normalize(value: float, range_: tuple[float, float]) -> float:
            min_val, max_val = range_
            if max_val == min_val:
                return 0.0
            return (value - min_val) / (max_val - min_val)

        # Normalized differences
        d_speed = normalize(obs1.motor_speed_rpm, speed_range) - normalize(obs2.motor_speed_rpm, speed_range)
        d_temp = normalize(obs1.motor_temp_c, temp_range) - normalize(obs2.motor_temp_c, temp_range)
        d_pressure = normalize(obs1.pressure_bar, pressure_range) - normalize(obs2.pressure_bar, pressure_range)

        # Euclidean distance in normalized space
        distance = math.sqrt(d_speed**2 + d_temp**2 + d_pressure**2)

        # Max possible distance is sqrt(3) ~ 1.732 (corner to corner of unit cube)
        max_distance = math.sqrt(3)

        # Convert to similarity (1 = identical, 0 = maximally different)
        similarity = 1.0 - (distance / max_distance)

        return similarity


# Singleton cache instance (module-level for shared access)
_cache: Optional[SemanticCache] = None


def get_cache(
    enabled: bool = True,
    similarity_threshold: float = 0.95,
    ttl_s: float = 60.0,
) -> Optional[SemanticCache]:
    """Get or create the singleton cache instance.

    Args:
        enabled: If False, returns None (cache disabled)
        similarity_threshold: Threshold for cache hits
        ttl_s: Time-to-live for entries

    Returns:
        SemanticCache instance or None if disabled
    """
    global _cache

    if not enabled:
        return None

    if _cache is None:
        _cache = SemanticCache(
            similarity_threshold=similarity_threshold,
            ttl_s=ttl_s,
        )

    return _cache


def reset_cache() -> None:
    """Reset the singleton cache (mainly for testing)."""
    global _cache
    if _cache:
        _cache.clear()
    _cache = None
