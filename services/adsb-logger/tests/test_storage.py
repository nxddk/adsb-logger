import json
import sqlite3
from contextlib import closing

import pytest

from adsb_logger.migrate import import_session, main
from adsb_logger.models import BoundingBox
from adsb_logger.recording import Recording
from adsb_logger.storage import DATABASE_FILENAME, StorageError

BOX = BoundingBox(45.471297, -73.617067, 45.489201, -73.576427)


@pytest.fixture
def recording(tmp_path):
    session = Recording(tmp_path, BOX, 10)
    yield session
    session.close("test_cleanup")


def test_whole_large_snapshot_is_committed_and_readable_while_recording(recording, read_records):
    states = [
        {"icao24": f"{index:06x}", "latitude": None, "sensors": [1, 2]} for index in range(5000)
    ]
    data = {"opensky_time": 1700000000, "states": states}
    recording.snapshot(data)
    records = read_records(recording.path, recording.session_id)
    assert len(records) == 2
    assert records[1]["states"] == states
    assert records[1]["opensky_time"] == 1700000000
    assert recording.snapshots == 1
    assert recording.aircraft_observations == 5000
    with closing(sqlite3.connect(recording.path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute(
            "SELECT snapshots, aircraft_observations FROM sessions"
        ).fetchone() == (1, 5000)
    recording.close("command")
    with closing(sqlite3.connect(recording.path)) as connection:
        ended_at, reason = connection.execute(
            "SELECT ended_at, stop_reason FROM sessions"
        ).fetchone()
        assert ended_at and reason == "command"
    assert recording.closed


def test_snapshot_payload_and_session_counts_rollback_together(recording, read_records):
    recording._connection.execute("""
        CREATE TRIGGER refuse_count BEFORE UPDATE OF snapshots ON sessions
        BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END;
    """)
    with pytest.raises(StorageError):
        recording.snapshot({"opensky_time": 1, "states": [{"icao24": "abc123"}]})
    assert recording.snapshots == recording.aircraft_observations == 0
    assert [row["type"] for row in read_records(recording.path)] == ["start"]
    with closing(sqlite3.connect(recording.path)) as connection:
        assert connection.execute("SELECT snapshots FROM sessions").fetchone()[0] == 0


def test_sessions_share_one_database_without_overwriting_each_other(
    recording, tmp_path, read_records
):
    first_id = recording.session_id
    recording.close("command")
    other = Recording(tmp_path, BOX, 30)
    assert other.path == recording.path == tmp_path / DATABASE_FILENAME
    assert other.session_id != first_id
    other.close("shutdown")
    assert [row["type"] for row in read_records(recording.path, first_id)] == ["start", "stop"]
    assert [row["type"] for row in read_records(recording.path, other.session_id)] == [
        "start",
        "stop",
    ]
    assert not list(tmp_path.glob("*.jsonl"))


def test_future_database_schema_is_rejected_without_rewriting_it(tmp_path):
    database = tmp_path / DATABASE_FILENAME
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(StorageError):
        Recording(tmp_path, BOX, 10)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def legacy_records():
    common = {
        "schema_version": 1,
        "session_id": "legacy-session",
        "recorded_at": "2026-11-20T12:00:00+00:00",
    }
    return [
        {
            **common,
            "type": "start",
            "started_at": common["recorded_at"],
            "bbox": BOX.as_dict(),
            "interval_seconds": 10,
        },
        {
            **common,
            "type": "snapshot",
            "opensky_time": 1700000000,
            "states": [{"icao24": "abc123", "sensors": None}],
        },
        {
            **common,
            "type": "stop",
            "reason": "quota_exhausted",
            "snapshots": 1,
            "aircraft_observations": 1,
        },
    ]


def test_legacy_import_is_atomic_repeatable_and_preserves_original(tmp_path, read_records):
    source = tmp_path / "old.jsonl"
    original = "".join(json.dumps(record) + "\n" for record in legacy_records())
    source.write_text(original)
    assert import_session(source, tmp_path)
    assert not import_session(source, tmp_path)
    assert source.read_text() == original
    assert read_records(tmp_path / DATABASE_FILENAME) == legacy_records()
    with closing(sqlite3.connect(tmp_path / DATABASE_FILENAME)) as connection:
        assert connection.execute(
            "SELECT snapshots, aircraft_observations, stop_reason, source_file FROM sessions"
        ).fetchone() == (1, 1, "quota_exhausted", str(source))


@pytest.mark.parametrize("tail", ["", "invalid json", json.dumps({"type": "stop"})])
def test_incomplete_or_corrupt_import_rolls_back_entire_session(tmp_path, tail):
    source = tmp_path / "incomplete.jsonl"
    source.write_text(json.dumps(legacy_records()[0]) + "\n" + tail)
    with pytest.raises((ValueError, KeyError)):
        import_session(source, tmp_path)
    with closing(sqlite3.connect(tmp_path / DATABASE_FILENAME)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0


def test_import_cli_reports_failures_without_deleting_files(tmp_path, capsys):
    source = tmp_path / "invalid.jsonl"
    source.write_text("invalid")
    assert main(["--log-dir", str(tmp_path), str(source)]) == 1
    assert source.read_text() == "invalid"
    assert "failed 1" in capsys.readouterr().out
