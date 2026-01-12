"""SQLite-based decision store for agent memory."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..schemas import Constraints, RecommendationCandidate, StateObservation


@dataclass
class DecisionRecord:
    """A recorded decision for persistence."""

    trace_id: str
    timestamp_unix_us: int
    observation: "StateObservation"
    candidate: "RecommendationCandidate"
    constraints: "Constraints"
    engine: str = "baseline"
    model: Optional[str] = None
    llm_latency_ms: Optional[int] = None
    llm_output_hash: Optional[str] = None
    approved: bool = False
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tool_traces: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)


@dataclass
class OutcomeFeedback:
    """Feedback about the outcome of a decision."""

    trace_id: str
    spine_accepted: bool
    actual_speed_rpm: Optional[float] = None
    outcome_timestamp_us: Optional[int] = None
    notes: Optional[str] = None


def _hash_envelope(data: dict) -> str:
    """Compute SHA-256 hash of data envelope."""
    import hashlib

    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class DecisionStore:
    """Thread-safe SQLite store for agent decisions.

    Implements connection pooling and write-ahead logging for
    concurrent access from supervisor and feedback threads.
    """

    DEFAULT_PATH = Path.home() / ".neuroplc" / "decisions.db"

    def __init__(
        self,
        db_path: Optional[Path] = None,
        max_decisions: int = 10000,
        enable_wal: bool = True,
    ):
        """Initialize the decision store.

        Args:
            db_path: Path to SQLite database. Defaults to ~/.neuroplc/decisions.db
            max_decisions: Maximum decisions to retain (oldest pruned)
            enable_wal: Enable write-ahead logging for concurrency
        """
        self.db_path = db_path or Path(
            os.environ.get("NEUROPLC_DECISION_DB", str(self.DEFAULT_PATH))
        )
        self.max_decisions = max_decisions
        self.enable_wal = enable_wal
        self._local = threading.local()
        self._lock = threading.RLock()

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize schema
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                check_same_thread=False,
            )
            self._local.conn.row_factory = sqlite3.Row
            if self.enable_wal:
                self._local.conn.execute("PRAGMA journal_mode=WAL")
                self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        """Context manager for cursor with automatic commit/rollback."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        schema_sql = """
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT UNIQUE NOT NULL,
            timestamp_unix_us INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            observation_json TEXT NOT NULL,
            observation_hash TEXT NOT NULL,
            action TEXT NOT NULL,
            target_speed_rpm REAL NOT NULL,
            confidence REAL NOT NULL,
            reasoning TEXT,
            constraints_json TEXT NOT NULL,
            constraints_hash TEXT NOT NULL,
            engine TEXT DEFAULT 'baseline',
            model TEXT,
            llm_latency_ms INTEGER,
            llm_output_hash TEXT,
            approved INTEGER NOT NULL DEFAULT 0,
            violations_json TEXT DEFAULT '[]',
            warnings_json TEXT DEFAULT '[]',
            spine_accepted INTEGER DEFAULT NULL,
            actual_speed_rpm REAL DEFAULT NULL,
            outcome_timestamp_us INTEGER DEFAULT NULL,
            outcome_notes TEXT DEFAULT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON decisions(timestamp_unix_us);
        CREATE INDEX IF NOT EXISTS idx_decisions_observation_hash ON decisions(observation_hash);
        CREATE INDEX IF NOT EXISTS idx_decisions_engine ON decisions(engine);
        CREATE INDEX IF NOT EXISTS idx_decisions_approved ON decisions(approved);

        CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            timestamp_unix_us INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            args_json TEXT NOT NULL,
            args_hash TEXT NOT NULL,
            result_json TEXT,
            result_hash TEXT,
            FOREIGN KEY (trace_id) REFERENCES decisions(trace_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_tool_calls_trace ON tool_calls(trace_id);
        CREATE INDEX IF NOT EXISTS idx_tool_calls_name ON tool_calls(tool_name);

        CREATE TABLE IF NOT EXISTS llm_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT DEFAULT NULL,
            tool_calls_json TEXT DEFAULT NULL,
            FOREIGN KEY (trace_id) REFERENCES decisions(trace_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_llm_messages_trace ON llm_messages(trace_id);

        CREATE TABLE IF NOT EXISTS observation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_unix_us INTEGER NOT NULL,
            motor_speed_rpm REAL NOT NULL,
            motor_temp_c REAL NOT NULL,
            pressure_bar REAL NOT NULL,
            safety_state TEXT NOT NULL,
            cycle_jitter_us INTEGER DEFAULT 0,
            cycle_count INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_history_timestamp ON observation_history(timestamp_unix_us DESC);
        """

        with self._cursor() as cursor:
            cursor.executescript(schema_sql)

    def record_decision(self, record: DecisionRecord) -> None:
        """Record a decision to the store.

        Args:
            record: Decision record to persist
        """
        obs_dict = record.observation.model_dump()
        obs_hash = _hash_envelope({"observation": obs_dict})
        constraints_dict = record.constraints.model_dump()
        constraints_hash = _hash_envelope({"constraints": constraints_dict})

        with self._cursor() as cursor:
            # Insert main decision
            cursor.execute(
                """
                INSERT INTO decisions (
                    trace_id, timestamp_unix_us,
                    observation_json, observation_hash,
                    action, target_speed_rpm, confidence, reasoning,
                    constraints_json, constraints_hash,
                    engine, model, llm_latency_ms, llm_output_hash,
                    approved, violations_json, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    record.trace_id,
                    record.timestamp_unix_us,
                    json.dumps(obs_dict),
                    obs_hash,
                    record.candidate.action,
                    record.candidate.target_speed_rpm,
                    record.candidate.confidence,
                    record.candidate.reasoning,
                    json.dumps(constraints_dict),
                    constraints_hash,
                    record.engine,
                    record.model,
                    record.llm_latency_ms,
                    record.llm_output_hash,
                    1 if record.approved else 0,
                    json.dumps(record.violations),
                    json.dumps(record.warnings),
                ),
            )

            # Insert tool traces
            for seq, trace in enumerate(record.tool_traces):
                cursor.execute(
                    """
                    INSERT INTO tool_calls (
                        trace_id, sequence, timestamp_unix_us,
                        tool_name, args_json, args_hash, result_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        record.trace_id,
                        seq,
                        record.timestamp_unix_us,
                        trace.get("name", ""),
                        json.dumps(trace.get("arguments", {})),
                        trace.get("args_hash", ""),
                        trace.get("result_hash", ""),
                    ),
                )

            # Insert LLM messages if present
            for seq, msg in enumerate(record.messages):
                cursor.execute(
                    """
                    INSERT INTO llm_messages (
                        trace_id, sequence, role, content,
                        tool_call_id, tool_calls_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        record.trace_id,
                        seq,
                        msg.get("role", ""),
                        msg.get("content"),
                        msg.get("tool_call_id"),
                        json.dumps(msg.get("tool_calls")) if msg.get("tool_calls") else None,
                    ),
                )

        # Prune old decisions if needed
        self._maybe_prune()

    def record_feedback(self, feedback: OutcomeFeedback) -> bool:
        """Record outcome feedback for a decision.

        Args:
            feedback: Feedback about decision outcome

        Returns:
            True if decision was found and updated
        """
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE decisions SET
                    spine_accepted = ?,
                    actual_speed_rpm = ?,
                    outcome_timestamp_us = ?,
                    outcome_notes = ?
                WHERE trace_id = ?
            """,
                (
                    1 if feedback.spine_accepted else 0,
                    feedback.actual_speed_rpm,
                    feedback.outcome_timestamp_us,
                    feedback.notes,
                    feedback.trace_id,
                ),
            )
            return cursor.rowcount > 0

    def get_decision(self, trace_id: str) -> Optional[dict]:
        """Get a decision by trace ID.

        Args:
            trace_id: The trace ID to look up

        Returns:
            Decision dict or None if not found
        """
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM decisions WHERE trace_id = ?
            """,
                (trace_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return dict(row)

    def query_decisions(
        self,
        start_time_us: Optional[int] = None,
        end_time_us: Optional[int] = None,
        engine: Optional[str] = None,
        approved_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query decisions with filters.

        Args:
            start_time_us: Start of time range (inclusive)
            end_time_us: End of time range (inclusive)
            engine: Filter by inference engine
            approved_only: Only return approved decisions
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of decision dicts
        """
        conditions = []
        params: list[Any] = []

        if start_time_us is not None:
            conditions.append("timestamp_unix_us >= ?")
            params.append(start_time_us)

        if end_time_us is not None:
            conditions.append("timestamp_unix_us <= ?")
            params.append(end_time_us)

        if engine is not None:
            conditions.append("engine = ?")
            params.append(engine)

        if approved_only:
            conditions.append("approved = 1")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        with self._cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM decisions
                WHERE {where_clause}
                ORDER BY timestamp_unix_us DESC
                LIMIT ? OFFSET ?
            """,
                (*params, limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_observations(
        self,
        limit: int = 500,
        since_us: Optional[int] = None,
    ) -> list[dict]:
        """Get recent observations from history buffer.

        Args:
            limit: Maximum observations to return
            since_us: Only observations after this timestamp

        Returns:
            List of observation dicts (newest first)
        """
        conditions = []
        params: list[Any] = []

        if since_us is not None:
            conditions.append("timestamp_unix_us > ?")
            params.append(since_us)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        with self._cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM observation_history
                WHERE {where_clause}
                ORDER BY timestamp_unix_us DESC
                LIMIT ?
            """,
                (*params, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def add_observation(self, obs: "StateObservation", timestamp_us: int) -> None:
        """Add an observation to the history buffer.

        Args:
            obs: State observation to record
            timestamp_us: Timestamp in microseconds
        """
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO observation_history (
                    timestamp_unix_us, motor_speed_rpm, motor_temp_c,
                    pressure_bar, safety_state, cycle_jitter_us, cycle_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    timestamp_us,
                    obs.motor_speed_rpm,
                    obs.motor_temp_c,
                    obs.pressure_bar,
                    obs.safety_state,
                    getattr(obs, "cycle_jitter_us", 0),
                    getattr(obs, "cycle_count", 0),
                ),
            )

    def _maybe_prune(self) -> None:
        """Prune old decisions if over capacity."""
        with self._lock:
            with self._cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM decisions")
                count = cursor.fetchone()[0]

                if count > self.max_decisions:
                    # Delete oldest 10%
                    delete_count = int(self.max_decisions * 0.1)
                    cursor.execute(
                        """
                        DELETE FROM decisions
                        WHERE id IN (
                            SELECT id FROM decisions
                            ORDER BY timestamp_unix_us ASC
                            LIMIT ?
                        )
                    """,
                        (delete_count,),
                    )

    def close(self) -> None:
        """Close the database connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def stats(self) -> dict:
        """Get store statistics."""
        with self._cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM decisions")
            decision_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM observation_history")
            history_count = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN approved = 1 THEN 1 ELSE 0 END) as approved,
                    SUM(CASE WHEN spine_accepted = 1 THEN 1 ELSE 0 END) as accepted,
                    SUM(CASE WHEN spine_accepted = 0 THEN 1 ELSE 0 END) as rejected
                FROM decisions
            """
            )
            row = cursor.fetchone()

            return {
                "decision_count": decision_count,
                "history_count": history_count,
                "total_decisions": row[0],
                "approved_decisions": row[1] or 0,
                "accepted_by_spine": row[2] or 0,
                "rejected_by_spine": row[3] or 0,
                "db_path": str(self.db_path),
            }


# Global store instance
_STORE: Optional[DecisionStore] = None


def get_decision_store(
    db_path: Optional[Path] = None,
    enabled: bool = True,
) -> Optional[DecisionStore]:
    """Get or create the global decision store.

    Args:
        db_path: Override database path
        enabled: Whether persistence is enabled

    Returns:
        DecisionStore instance or None if disabled
    """
    global _STORE

    if not enabled:
        return None

    if _STORE is None:
        _STORE = DecisionStore(db_path=db_path)

    return _STORE


def reset_decision_store() -> None:
    """Reset the global store (for testing)."""
    global _STORE
    if _STORE:
        _STORE.close()
    _STORE = None
