"""Recording sessions with whole snapshots committed to SQLite."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import BoundingBox
from .storage import DATABASE_FILENAME, StorageError, append_record, connect_database


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Recording:
    def __init__(self, directory: Path, bbox: BoundingBox, interval: float):
        self.session_id = uuid4().hex
        self.path = directory / DATABASE_FILENAME
        self.bbox = bbox
        self.interval = interval
        self.started_at = utc_now()
        self.snapshots = 0
        self.aircraft_observations = 0
        self.closed = False
        try:
            self._connection = connect_database(directory)
        except sqlite3.Error as exc:
            raise StorageError("Could not open the recording database") from exc
        try:
            self.write(
                "start", started_at=self.started_at, bbox=bbox.as_dict(), interval_seconds=interval
            )
        except Exception:
            self._connection.close()
            self.closed = True
            raise

    def write(self, record_type: str, **data) -> None:
        record = {
            "schema_version": 1,
            "type": record_type,
            "session_id": self.session_id,
            "recorded_at": utc_now(),
            **data,
        }
        try:
            with self._connection:
                append_record(self._connection, record)
        except sqlite3.Error as exc:
            raise StorageError("Could not commit the recording database entry") from exc

    def snapshot(self, data: dict) -> None:
        self.write("snapshot", **data)
        self.snapshots += 1
        self.aircraft_observations += len(data["states"])

    def close(self, reason: str) -> None:
        if self.closed:
            return
        try:
            self.write(
                "stop",
                reason=reason,
                snapshots=self.snapshots,
                aircraft_observations=self.aircraft_observations,
            )
        finally:
            try:
                self._connection.close()
            except sqlite3.Error as exc:
                raise StorageError("Could not close the recording database") from exc
            finally:
                self.closed = True
