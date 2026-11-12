"""Append-only, flushed JSON Lines recording sessions."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import BoundingBox


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Recording:
    def __init__(self, directory: Path, bbox: BoundingBox, interval: float):
        directory.mkdir(parents=True, exist_ok=True)
        self.session_id = uuid4().hex
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = directory / f"{stamp}-{self.session_id}.jsonl"
        self.bbox = bbox
        self.interval = interval
        self.started_at = utc_now()
        self.snapshots = 0
        self.aircraft_observations = 0
        self._file = self.path.open("x", encoding="utf-8")
        try:
            self.write(
                "start", started_at=self.started_at, bbox=bbox.as_dict(), interval_seconds=interval
            )
        except Exception:
            self._file.close()
            raise

    def write(self, record_type: str, **data) -> None:
        record = {
            "schema_version": 1,
            "type": record_type,
            "session_id": self.session_id,
            "recorded_at": utc_now(),
            **data,
        }
        self._file.write(json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n")
        self._file.flush()

    def snapshot(self, data: dict) -> None:
        self.write("snapshot", **data)
        self.snapshots += 1
        self.aircraft_observations += len(data["states"])

    def close(self, reason: str) -> None:
        try:
            self.write(
                "stop",
                reason=reason,
                snapshots=self.snapshots,
                aircraft_observations=self.aircraft_observations,
            )
        finally:
            self._file.close()
