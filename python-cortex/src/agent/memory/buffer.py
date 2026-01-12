"""Rolling observation buffer with persistence."""
from __future__ import annotations

import os
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..schemas import StateObservation
    from .store import DecisionStore


@dataclass
class BufferConfig:
    """Configuration for observation buffer."""

    max_size: int = 500
    persist_interval: int = 10  # Persist every N observations
    preload_on_start: bool = True


class ObservationBuffer:
    """Thread-safe rolling buffer for sensor observations.

    Maintains an in-memory buffer with periodic persistence to SQLite.
    Preloads history from database on startup.
    """

    def __init__(
        self,
        config: Optional[BufferConfig] = None,
        store: Optional["DecisionStore"] = None,
    ):
        """Initialize the buffer.

        Args:
            config: Buffer configuration
            store: Decision store for persistence
        """
        self.config = config or BufferConfig(
            max_size=int(os.environ.get("NEUROPLC_HISTORY_BUFFER_SIZE", "500")),
            persist_interval=int(os.environ.get("NEUROPLC_HISTORY_PERSIST_INTERVAL", "10")),
            preload_on_start=os.environ.get("NEUROPLC_HISTORY_PRELOAD", "1") == "1",
        )
        self._store = store
        self._speed_buffer: deque[float] = deque(maxlen=self.config.max_size)
        self._temp_buffer: deque[float] = deque(maxlen=self.config.max_size)
        self._pressure_buffer: deque[float] = deque(maxlen=self.config.max_size)
        self._timestamps: deque[int] = deque(maxlen=self.config.max_size)
        self._lock = threading.RLock()
        self._persist_counter = 0

        if self.config.preload_on_start:
            self._preload()

    def _preload(self) -> None:
        """Preload history from database."""
        from .store import get_decision_store

        store = self._store or get_decision_store()
        if store is None:
            return

        try:
            records = store.get_recent_observations(limit=self.config.max_size)
            # Records are newest-first, we want oldest-first for buffer
            for record in reversed(records):
                self._speed_buffer.append(record["motor_speed_rpm"])
                self._temp_buffer.append(record["motor_temp_c"])
                self._pressure_buffer.append(record["pressure_bar"])
                self._timestamps.append(record["timestamp_unix_us"])
        except Exception:
            # Fail silently on preload errors
            pass

    def add(self, obs: "StateObservation", timestamp_us: int) -> None:
        """Add an observation to the buffer.

        Args:
            obs: State observation to add
            timestamp_us: Timestamp in microseconds
        """
        with self._lock:
            self._speed_buffer.append(obs.motor_speed_rpm)
            self._temp_buffer.append(obs.motor_temp_c)
            self._pressure_buffer.append(obs.pressure_bar)
            self._timestamps.append(timestamp_us)

            # Periodic persistence
            self._persist_counter += 1
            if self._persist_counter >= self.config.persist_interval:
                self._persist_counter = 0
                self._persist_latest(obs, timestamp_us)

    def _persist_latest(self, obs: "StateObservation", timestamp_us: int) -> None:
        """Persist latest observation to database."""
        from .store import get_decision_store

        store = self._store or get_decision_store()
        if store is None:
            return

        try:
            store.add_observation(obs, timestamp_us)
        except Exception:
            # Non-critical, fail silently
            pass

    @property
    def speed_history(self) -> list[float]:
        """Get speed history as list (oldest to newest)."""
        with self._lock:
            return list(self._speed_buffer)

    @property
    def temp_history(self) -> list[float]:
        """Get temperature history as list (oldest to newest)."""
        with self._lock:
            return list(self._temp_buffer)

    @property
    def pressure_history(self) -> list[float]:
        """Get pressure history as list (oldest to newest)."""
        with self._lock:
            return list(self._pressure_buffer)

    def get_window(self, n: int) -> tuple[list[float], list[float]]:
        """Get last N observations.

        Args:
            n: Number of observations to retrieve

        Returns:
            Tuple of (speed_list, temp_list)
        """
        with self._lock:
            speed = list(self._speed_buffer)[-n:] if n > 0 else []
            temp = list(self._temp_buffer)[-n:] if n > 0 else []
            return speed, temp

    def get_stats(self) -> dict:
        """Get buffer statistics.

        Returns:
            Dict with min, max, avg for speed and temp
        """
        with self._lock:
            speed = list(self._speed_buffer)
            temp = list(self._temp_buffer)

            if not speed:
                return {
                    "count": 0,
                    "speed": {"min": 0, "max": 0, "avg": 0},
                    "temp": {"min": 0, "max": 0, "avg": 0},
                }

            return {
                "count": len(speed),
                "speed": {
                    "min": min(speed),
                    "max": max(speed),
                    "avg": sum(speed) / len(speed),
                },
                "temp": {
                    "min": min(temp),
                    "max": max(temp),
                    "avg": sum(temp) / len(temp),
                },
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._speed_buffer)

    def clear(self) -> None:
        """Clear the buffer."""
        with self._lock:
            self._speed_buffer.clear()
            self._temp_buffer.clear()
            self._pressure_buffer.clear()
            self._timestamps.clear()


# Global buffer instance
_BUFFER: Optional[ObservationBuffer] = None


def get_observation_buffer(
    config: Optional[BufferConfig] = None,
) -> ObservationBuffer:
    """Get or create the global observation buffer.

    Args:
        config: Optional configuration override

    Returns:
        ObservationBuffer instance
    """
    global _BUFFER

    if _BUFFER is None:
        _BUFFER = ObservationBuffer(config=config)

    return _BUFFER


def reset_observation_buffer() -> None:
    """Reset the global buffer (for testing)."""
    global _BUFFER
    _BUFFER = None
