"""Tests for BaSyx digital twin query functionality."""
from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock, patch

from agent.schemas import Constraints, StateObservation
from agent.tools import (
    AgentContext,
    execute_tool,
    _query_digital_twin,
    _get_submodel_id,
    _fallback_value,
)
from digital_twin.basyx_adapter import BasyxAdapter, BasyxConfig, CircuitBreaker
from digital_twin.cache import (
    BasyxPropertyCache,
    CachedProperty,
    make_cache_key,
    get_ttl_for_submodel,
    get_property_cache,
    reset_property_cache,
)


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
def sample_context(sample_observation, sample_constraints):
    return AgentContext(
        obs=sample_observation,
        constraints=sample_constraints,
        last_recommendation=None,
        speed_history=[],
        temp_history=[],
        basyx_adapter=None,
    )


@pytest.fixture
def basyx_config():
    return BasyxConfig(
        base_url="http://localhost:8081",
        aas_id="urn:test:aas:001",
    )


class TestCircuitBreaker:
    """Test cases for CircuitBreaker."""

    def test_circuit_breaker_initial_closed(self):
        cb = CircuitBreaker(threshold=5, cooldown_s=30)
        assert cb.is_open() is False

    def test_circuit_breaker_stays_closed_under_threshold(self):
        cb = CircuitBreaker(threshold=5, cooldown_s=30)
        for _ in range(4):  # Below threshold
            cb.record_failure()
        assert cb.is_open() is False

    def test_circuit_breaker_opens_at_threshold(self):
        cb = CircuitBreaker(threshold=5, cooldown_s=30)
        for _ in range(5):
            cb.record_failure()
        assert cb.is_open() is True

    def test_circuit_breaker_closes_after_cooldown(self):
        cb = CircuitBreaker(threshold=3, cooldown_s=0.1)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open() is True

        time.sleep(0.15)  # Wait for cooldown
        assert cb.is_open() is False

    def test_circuit_breaker_resets_on_success(self):
        cb = CircuitBreaker(threshold=5, cooldown_s=30)
        for _ in range(4):
            cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.is_open() is False


class TestBasyxPropertyCache:
    """Test cases for BasyxPropertyCache."""

    def test_cache_initialization(self):
        cache = BasyxPropertyCache(default_ttl_s=60.0)
        assert cache.enabled is True
        assert cache.hits == 0
        assert cache.misses == 0

    def test_cache_disabled(self):
        cache = BasyxPropertyCache(enabled=False)
        cache.set("key", "value")
        result = cache.get("key")
        assert result is None
        assert cache.misses == 1

    def test_cache_set_and_get(self):
        cache = BasyxPropertyCache()
        cache.set("key", "value")
        result = cache.get("key")
        assert result == "value"
        assert cache.hits == 1

    def test_cache_miss_increments_counter(self):
        cache = BasyxPropertyCache()
        result = cache.get("nonexistent")
        assert result is None
        assert cache.misses == 1

    def test_cache_ttl_expiration(self):
        cache = BasyxPropertyCache()
        cache.set("key", "value", ttl_s=0.1)
        time.sleep(0.15)
        result = cache.get("key")
        assert result is None

    def test_cache_zero_ttl_not_stored(self):
        cache = BasyxPropertyCache()
        cache.set("key", "value", ttl_s=0)
        result = cache.get("key")
        assert result is None

    def test_cache_invalidate(self):
        cache = BasyxPropertyCache()
        cache.set("key", "value")
        cache.invalidate("key")
        result = cache.get("key")
        assert result is None

    def test_cache_invalidate_submodel(self):
        cache = BasyxPropertyCache()
        cache.set("submodel1:prop1", "value1")
        cache.set("submodel1:prop2", "value2")
        cache.set("submodel2:prop1", "value3")
        cache.invalidate_submodel("submodel1")
        assert cache.get("submodel1:prop1") is None
        assert cache.get("submodel1:prop2") is None
        assert cache.get("submodel2:prop1") == "value3"

    def test_cache_clear(self):
        cache = BasyxPropertyCache()
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_cache_hit_rate(self):
        cache = BasyxPropertyCache()
        cache.set("key", "value")
        cache.get("key")  # hit
        cache.get("key")  # hit
        cache.get("nonexistent")  # miss
        assert cache.hit_rate == 2 / 3

    def test_cache_stats(self):
        cache = BasyxPropertyCache()
        cache.set("key", "value")
        cache.get("key")
        cache.get("nonexistent")
        stats = cache.stats()
        assert stats["enabled"] is True
        assert stats["entries"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1


class TestCacheHelpers:
    """Test cases for cache helper functions."""

    def test_make_cache_key(self):
        key = make_cache_key("submodel123", "PropertyName")
        assert key == "submodel123:PropertyName"

    def test_get_ttl_for_submodel_safety(self):
        ttl = get_ttl_for_submodel("safety")
        assert ttl == 300.0

    def test_get_ttl_for_submodel_nameplate(self):
        ttl = get_ttl_for_submodel("nameplate")
        assert ttl == 3600.0

    def test_get_ttl_for_submodel_operational(self):
        ttl = get_ttl_for_submodel("operational")
        assert ttl == 0.0  # No caching

    def test_get_ttl_for_submodel_unknown(self):
        ttl = get_ttl_for_submodel("unknown")
        assert ttl == 60.0  # Default


class TestBasyxAdapterReadMethods:
    """Test cases for BasyxAdapter read methods."""

    def test_get_property(self, basyx_config):
        adapter = BasyxAdapter(basyx_config)

        with patch.object(adapter, "_request_json") as mock_request:
            mock_request.return_value = (200, 3000.0)
            status, value = adapter.get_property(
                "urn:neuroplc:sm:safety-parameters:001",
                "MaxSpeedRPM",
            )
            assert status == 200
            assert value == 3000.0
            mock_request.assert_called_once()

    def test_get_submodel(self, basyx_config):
        adapter = BasyxAdapter(basyx_config)

        with patch.object(adapter, "_request_json") as mock_request:
            mock_request.return_value = (200, {"id": "submodel", "elements": []})
            status, submodel = adapter.get_submodel("urn:test:submodel:001")
            assert status == 200
            assert submodel["id"] == "submodel"

    def test_read_safety_property(self, basyx_config):
        adapter = BasyxAdapter(basyx_config)

        with patch.object(adapter, "get_property") as mock_get:
            mock_get.return_value = (200, 3000.0)
            value = adapter.read_safety_property("MaxSpeedRPM")
            assert value == 3000.0

    def test_read_safety_property_not_found(self, basyx_config):
        adapter = BasyxAdapter(basyx_config)

        with patch.object(adapter, "get_property") as mock_get:
            mock_get.return_value = (404, None)
            value = adapter.read_safety_property("NonexistentProp")
            assert value is None

    def test_circuit_breaker_blocks_reads(self, basyx_config):
        adapter = BasyxAdapter(basyx_config)
        # Open circuit breaker
        adapter._circuit_breaker.failure_count = 10
        adapter._circuit_breaker.last_failure_at = time.time()

        status, value = adapter.get_property("submodel", "prop")
        assert status == 503
        assert value == {"error": "circuit_breaker_open"}


class TestQueryDigitalTwin:
    """Test cases for _query_digital_twin function."""

    def test_query_without_adapter_returns_fallback(self, sample_context):
        result = _query_digital_twin("MaxSpeedRPM", sample_context)
        assert result["property"] == "MaxSpeedRPM"
        assert result["value"] == 3000.0
        assert result["source"] == "constraints_fallback"

    def test_query_with_adapter_returns_basyx_value(self, sample_observation, sample_constraints):
        mock_adapter = MagicMock()
        mock_adapter.config.safety_submodel_id = "urn:test:safety:001"
        mock_adapter.config.nameplate_submodel_id = "urn:test:nameplate:001"
        mock_adapter.config.func_safety_submodel_id = "urn:test:func:001"
        mock_adapter.config.operational_submodel_id = "urn:test:ops:001"
        mock_adapter.config.ai_submodel_id = "urn:test:ai:001"
        mock_adapter.get_property.return_value = (200, 5000.0)

        ctx = AgentContext(
            obs=sample_observation,
            constraints=sample_constraints,
            last_recommendation=None,
            speed_history=[],
            temp_history=[],
            basyx_adapter=mock_adapter,
        )

        # Reset cache to avoid interference
        reset_property_cache()

        with patch("digital_twin.cache.get_property_cache") as mock_cache:
            mock_cache.return_value = None  # Disable cache

            result = _query_digital_twin("MaxSpeedRPM", ctx)
            assert result["property"] == "MaxSpeedRPM"
            assert result["value"] == 5000.0
            assert result["source"] == "digital_twin"
            assert result["submodel"] == "safety"

    def test_query_with_adapter_error_falls_back(self, sample_observation, sample_constraints):
        mock_adapter = MagicMock()
        mock_adapter.config.safety_submodel_id = "urn:test:safety:001"
        mock_adapter.config.nameplate_submodel_id = "urn:test:nameplate:001"
        mock_adapter.config.func_safety_submodel_id = "urn:test:func:001"
        mock_adapter.config.operational_submodel_id = "urn:test:ops:001"
        mock_adapter.config.ai_submodel_id = "urn:test:ai:001"
        mock_adapter.get_property.return_value = (500, None)

        ctx = AgentContext(
            obs=sample_observation,
            constraints=sample_constraints,
            last_recommendation=None,
            speed_history=[],
            temp_history=[],
            basyx_adapter=mock_adapter,
        )

        reset_property_cache()

        with patch("digital_twin.cache.get_property_cache") as mock_cache:
            mock_cache.return_value = None

            result = _query_digital_twin("MaxSpeedRPM", ctx)
            assert result["source"] == "constraints_fallback"

    def test_query_unknown_property(self, sample_context):
        result = _query_digital_twin("UnknownProperty", sample_context)
        assert "error" in result
        assert "Unknown property" in result["error"]

    def test_query_with_cache_hit(self, sample_observation, sample_constraints):
        mock_adapter = MagicMock()
        mock_adapter.config.safety_submodel_id = "urn:test:safety:001"
        mock_adapter.config.nameplate_submodel_id = "urn:test:nameplate:001"
        mock_adapter.config.func_safety_submodel_id = "urn:test:func:001"
        mock_adapter.config.operational_submodel_id = "urn:test:ops:001"
        mock_adapter.config.ai_submodel_id = "urn:test:ai:001"

        ctx = AgentContext(
            obs=sample_observation,
            constraints=sample_constraints,
            last_recommendation=None,
            speed_history=[],
            temp_history=[],
            basyx_adapter=mock_adapter,
        )

        # Create a mock cache with a value
        mock_cache = MagicMock()
        mock_cache.get.return_value = 2500.0

        reset_property_cache()

        with patch("digital_twin.cache.get_property_cache", return_value=mock_cache):
            result = _query_digital_twin("MaxSpeedRPM", ctx)
            assert result["value"] == 2500.0
            assert result["source"] == "digital_twin_cached"


class TestGetSubmodelId:
    """Test cases for _get_submodel_id helper."""

    def test_get_safety_submodel_id(self):
        mock_adapter = MagicMock()
        mock_adapter.config.safety_submodel_id = "urn:test:safety"
        assert _get_submodel_id(mock_adapter, "safety") == "urn:test:safety"

    def test_get_nameplate_submodel_id(self):
        mock_adapter = MagicMock()
        mock_adapter.config.nameplate_submodel_id = "urn:test:nameplate"
        assert _get_submodel_id(mock_adapter, "nameplate") == "urn:test:nameplate"

    def test_get_unknown_submodel_id(self):
        mock_adapter = MagicMock()
        assert _get_submodel_id(mock_adapter, "unknown") == ""


class TestFallbackValue:
    """Test cases for _fallback_value helper."""

    def test_fallback_max_speed(self, sample_context):
        result = _fallback_value("MaxSpeedRPM", sample_context)
        assert result["value"] == 3000.0
        assert result["source"] == "constraints_fallback"

    def test_fallback_min_speed(self, sample_context):
        result = _fallback_value("MinSpeedRPM", sample_context)
        assert result["value"] == 0.0

    def test_fallback_safety_integrity_level(self, sample_context):
        result = _fallback_value("SafetyIntegrityLevel", sample_context)
        assert result["value"] == "SIL2"

    def test_fallback_unknown(self, sample_context):
        result = _fallback_value("UnknownProperty", sample_context)
        assert "error" in result


class TestToolExecution:
    """Test cases for tool execution with digital twin."""

    def test_execute_query_digital_twin_tool(self, sample_context):
        result = execute_tool("query_digital_twin", {"property_name": "MaxSpeedRPM"}, sample_context)
        assert result["property"] == "MaxSpeedRPM"
        assert result["source"] == "constraints_fallback"

    def test_execute_query_digital_twin_tool_serial_number(self, sample_context):
        result = execute_tool("query_digital_twin", {"property_name": "SerialNumber"}, sample_context)
        assert result["property"] == "SerialNumber"
        assert result["value"] == "UNKNOWN"
        assert result["source"] == "constraints_fallback"


class TestCachedProperty:
    """Test cases for CachedProperty dataclass."""

    def test_cached_property_not_expired(self):
        prop = CachedProperty(value="test", fetched_at=time.time(), ttl_s=60.0)
        assert prop.is_expired() is False

    def test_cached_property_expired(self):
        prop = CachedProperty(value="test", fetched_at=time.time() - 100, ttl_s=60.0)
        assert prop.is_expired() is True

    def test_cached_property_zero_ttl_always_expired(self):
        prop = CachedProperty(value="test", fetched_at=time.time(), ttl_s=0.0)
        assert prop.is_expired() is True


class TestGetPropertyCacheSingleton:
    """Test cases for global cache singleton."""

    def teardown_method(self):
        reset_property_cache()

    def test_get_cache_enabled(self):
        with patch.dict("os.environ", {"BASYX_CACHE_ENABLED": "1"}):
            reset_property_cache()
            cache = get_property_cache(enabled=True)
            assert cache is not None
            assert isinstance(cache, BasyxPropertyCache)

    def test_get_cache_disabled(self):
        cache = get_property_cache(enabled=False)
        assert cache is None

    def test_get_cache_singleton(self):
        reset_property_cache()
        cache1 = get_property_cache(enabled=True)
        cache2 = get_property_cache(enabled=True)
        assert cache1 is cache2

    def test_reset_cache_creates_new_instance(self):
        reset_property_cache()
        cache1 = get_property_cache(enabled=True)
        reset_property_cache()
        cache2 = get_property_cache(enabled=True)
        assert cache1 is not cache2
