"""Tests for semantic caching."""
from __future__ import annotations

import time
import pytest

from agent.llm.cache import SemanticCache, CacheStats, get_cache, reset_cache
from agent.schemas import Constraints, RecommendationCandidate, StateObservation


@pytest.fixture
def sample_observation():
    return StateObservation(
        motor_speed_rpm=1500.0,
        motor_temp_c=55.0,
        pressure_bar=5.0,
        safety_state="SAFE",
        cycle_jitter_us=50,
        timestamp_us=1000000,
    )


@pytest.fixture
def sample_constraints():
    return Constraints(
        min_speed_rpm=0.0,
        max_speed_rpm=3000.0,
        max_rate_rpm=50.0,
        max_temp_c=80.0,
    )


@pytest.fixture
def sample_candidate():
    return RecommendationCandidate(
        action="adjust_setpoint",
        target_speed_rpm=1550.0,
        confidence=0.85,
        reasoning="Test recommendation",
    )


class TestSemanticCache:
    """Test cases for SemanticCache."""

    def test_cache_initialization(self):
        cache = SemanticCache()
        assert cache.similarity_threshold == 0.95
        assert cache.ttl_s == 60.0
        assert cache.max_entries == 100

    def test_cache_custom_initialization(self):
        cache = SemanticCache(
            similarity_threshold=0.9,
            ttl_s=30.0,
            max_entries=50,
        )
        assert cache.similarity_threshold == 0.9
        assert cache.ttl_s == 30.0
        assert cache.max_entries == 50

    def test_cache_store_and_lookup(
        self, sample_observation, sample_constraints, sample_candidate
    ):
        cache = SemanticCache()

        # Store
        cache.store(sample_observation, sample_constraints, sample_candidate)

        # Lookup same observation - should hit
        result = cache.lookup(sample_observation, sample_constraints)
        assert result is not None
        assert result.target_speed_rpm == sample_candidate.target_speed_rpm

    def test_cache_miss_different_observation(
        self, sample_observation, sample_constraints, sample_candidate
    ):
        cache = SemanticCache()
        cache.store(sample_observation, sample_constraints, sample_candidate)

        # Create different observation
        different_obs = StateObservation(
            motor_speed_rpm=3000.0,  # Very different speed
            motor_temp_c=100.0,      # Very different temp
            pressure_bar=15.0,
            safety_state="SAFE",
            cycle_jitter_us=50,
            timestamp_us=1000000,
        )

        # Should miss due to low similarity
        result = cache.lookup(different_obs, sample_constraints)
        assert result is None

    def test_cache_miss_different_constraints(
        self, sample_observation, sample_constraints, sample_candidate
    ):
        cache = SemanticCache()
        cache.store(sample_observation, sample_constraints, sample_candidate)

        # Create different constraints
        different_constraints = Constraints(
            min_speed_rpm=0.0,
            max_speed_rpm=5000.0,  # Different max
            max_rate_rpm=100.0,    # Different rate
            max_temp_c=80.0,
        )

        # Should miss due to constraints mismatch
        result = cache.lookup(sample_observation, different_constraints)
        assert result is None

    def test_cache_hit_similar_observation(
        self, sample_observation, sample_constraints, sample_candidate
    ):
        cache = SemanticCache(similarity_threshold=0.9)  # Lower threshold
        cache.store(sample_observation, sample_constraints, sample_candidate)

        # Create slightly different observation
        similar_obs = StateObservation(
            motor_speed_rpm=1510.0,  # Only 10 RPM different
            motor_temp_c=56.0,       # Only 1C different
            pressure_bar=5.1,
            safety_state="SAFE",
            cycle_jitter_us=50,
            timestamp_us=1000000,
        )

        # Should hit due to high similarity
        result = cache.lookup(similar_obs, sample_constraints)
        assert result is not None

    def test_cache_ttl_expiration(
        self, sample_observation, sample_constraints, sample_candidate
    ):
        cache = SemanticCache(ttl_s=0.1)  # Very short TTL
        cache.store(sample_observation, sample_constraints, sample_candidate)

        # Wait for expiration
        time.sleep(0.15)

        # Should miss due to TTL
        result = cache.lookup(sample_observation, sample_constraints)
        assert result is None

    def test_cache_eviction(self, sample_constraints, sample_candidate):
        cache = SemanticCache(max_entries=3)

        # Fill cache
        for i in range(3):
            obs = StateObservation(
                motor_speed_rpm=1000.0 + i * 500,
                motor_temp_c=50.0,
                pressure_bar=5.0,
                safety_state="SAFE",
                cycle_jitter_us=50,
                timestamp_us=1000000 + i,
            )
            cache.store(obs, sample_constraints, sample_candidate)

        # Add one more - should evict oldest
        obs_new = StateObservation(
            motor_speed_rpm=2500.0,
            motor_temp_c=50.0,
            pressure_bar=5.0,
            safety_state="SAFE",
            cycle_jitter_us=50,
            timestamp_us=2000000,
        )
        cache.store(obs_new, sample_constraints, sample_candidate)

        assert cache.stats.evictions == 1

    def test_cache_stats(
        self, sample_observation, sample_constraints, sample_candidate
    ):
        cache = SemanticCache()

        # Miss
        cache.lookup(sample_observation, sample_constraints)
        assert cache.stats.total_lookups == 1
        assert cache.stats.cache_misses == 1
        assert cache.stats.cache_hits == 0

        # Store and hit
        cache.store(sample_observation, sample_constraints, sample_candidate)
        cache.lookup(sample_observation, sample_constraints)

        assert cache.stats.total_lookups == 2
        assert cache.stats.cache_hits == 1

    def test_cache_hit_rate(self):
        stats = CacheStats(total_lookups=10, cache_hits=3, cache_misses=7)
        assert stats.hit_rate == 0.3

    def test_cache_hit_rate_zero_lookups(self):
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_cache_clear(
        self, sample_observation, sample_constraints, sample_candidate
    ):
        cache = SemanticCache()
        cache.store(sample_observation, sample_constraints, sample_candidate)

        # Verify hit
        result = cache.lookup(sample_observation, sample_constraints)
        assert result is not None

        # Clear and verify miss
        cache.clear()
        result = cache.lookup(sample_observation, sample_constraints)
        assert result is None


class TestCacheSimilarity:
    """Test cases for similarity calculation."""

    def test_similarity_identical_observations(self):
        cache = SemanticCache()

        obs1 = StateObservation(
            motor_speed_rpm=1500.0,
            motor_temp_c=55.0,
            pressure_bar=5.0,
            safety_state="SAFE",
            cycle_jitter_us=50,
            timestamp_us=1000000,
        )
        obs2 = StateObservation(
            motor_speed_rpm=1500.0,
            motor_temp_c=55.0,
            pressure_bar=5.0,
            safety_state="SAFE",
            cycle_jitter_us=50,
            timestamp_us=1000000,
        )

        similarity = cache._calculate_similarity(obs1, obs2)
        assert similarity == 1.0

    def test_similarity_maximally_different(self):
        cache = SemanticCache()

        obs1 = StateObservation(
            motor_speed_rpm=0.0,
            motor_temp_c=0.0,
            pressure_bar=0.0,
            safety_state="SAFE",
            cycle_jitter_us=50,
            timestamp_us=1000000,
        )
        obs2 = StateObservation(
            motor_speed_rpm=5000.0,
            motor_temp_c=150.0,
            pressure_bar=20.0,
            safety_state="SAFE",
            cycle_jitter_us=50,
            timestamp_us=1000000,
        )

        similarity = cache._calculate_similarity(obs1, obs2)
        assert similarity == 0.0


class TestGetCacheSingleton:
    """Test cases for get_cache singleton."""

    def teardown_method(self):
        reset_cache()

    def test_get_cache_enabled(self):
        cache = get_cache(enabled=True)
        assert cache is not None
        assert isinstance(cache, SemanticCache)

    def test_get_cache_disabled(self):
        cache = get_cache(enabled=False)
        assert cache is None

    def test_get_cache_singleton(self):
        cache1 = get_cache(enabled=True)
        cache2 = get_cache(enabled=True)
        assert cache1 is cache2

    def test_reset_cache(self):
        cache1 = get_cache(enabled=True)
        reset_cache()
        cache2 = get_cache(enabled=True)
        assert cache1 is not cache2
