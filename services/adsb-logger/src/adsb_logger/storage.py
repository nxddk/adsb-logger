"""SQLite storage: one session row and whole JSON payloads for its records."""

import json
import sqlite3
from pathlib import Path

DATABASE_FILENAME = "adsb.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    bbox_json TEXT NOT NULL,
    interval_seconds REAL NOT NULL,
    snapshots INTEGER NOT NULL DEFAULT 0,
    aircraft_observations INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    source_file TEXT
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    type TEXT NOT NULL CHECK(type IN ('start', 'snapshot', 'error', 'stop')),
    recorded_at TEXT NOT NULL,
    opensky_time INTEGER,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_session ON records(session_id, id);
PRAGMA user_version = 1;
"""


class StorageError(OSError):
    """A database failure, handled like other recording storage failures."""


def connect_database(directory: Path) -> sqlite3.Connection:
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / DATABASE_FILENAME, timeout=5)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise sqlite3.DatabaseError(f"Unsupported recording database schema version {version}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.executescript(SCHEMA)
        return connection
    except Exception:
        connection.close()
        raise


def append_record(connection: sqlite3.Connection, record: dict) -> None:
    """Append within the caller's transaction; update metadata atomically."""
    payload = json.dumps(record, allow_nan=False, separators=(",", ":"))
    session_id, record_type = record["session_id"], record["type"]
    if record_type == "start":
        connection.execute(
            "INSERT INTO sessions(session_id, started_at, bbox_json, interval_seconds) "
            "VALUES (?, ?, ?, ?)",
            (
                session_id,
                record["started_at"],
                json.dumps(record["bbox"], allow_nan=False),
                record["interval_seconds"],
            ),
        )
    connection.execute(
        "INSERT INTO records(session_id, type, recorded_at, opensky_time, payload_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, record_type, record["recorded_at"], record.get("opensky_time"), payload),
    )
    if record_type == "snapshot":
        connection.execute(
            "UPDATE sessions SET snapshots = snapshots + 1, "
            "aircraft_observations = aircraft_observations + ? WHERE session_id = ?",
            (len(record["states"]), session_id),
        )
    elif record_type == "stop":
        connection.execute(
            "UPDATE sessions SET ended_at = ?, stop_reason = ? WHERE session_id = ?",
            (record["recorded_at"], record["reason"], session_id),
        )
