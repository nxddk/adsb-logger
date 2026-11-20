import json
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt
import pytest

from adsb_logger.opensky import OpenSkyClient, OpenSkyError, OpenSkyQuotaExhausted
from adsb_logger.service import RecorderService

BOX = {"lamin": 43, "lomin": -80.5, "lamax": 44.5, "lomax": -78.5}


@pytest.fixture
def service(tmp_path):
    client = Mock(spec=mqtt.Client)
    client.is_connected.return_value = True
    client.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS
    opensky = Mock(spec=OpenSkyClient)
    opensky.credits_remaining = None
    opensky.quota_retry_after = 86400
    opensky.fetch.return_value = {"opensky_time": 1, "states": [{"icao24": "c01234"}]}
    recorder = RecorderService(client, opensky, tmp_path / "recordings")
    yield recorder
    recorder._stop_recording("test_cleanup")


def command(service, action, **kwargs):
    service.handle_command(json.dumps({"command": action, **kwargs}).encode())
    calls = service.client.publish.call_args_list
    return json.loads([call.args[1] for call in calls if call.args[0].endswith("/response")][-1])


def test_full_recording_lifecycle_and_new_session(service):
    assert service.status()["state"] == "idle"
    assert not service.log_dir.exists()
    reply = command(service, "start", bbox=BOX)
    assert reply["ok"]
    first_file = service.recording.path
    service.poll()
    assert service.status()["snapshots"] == 1
    assert service.status()["aircraft_observations"] == 1
    assert command(service, "stop")["ok"]
    assert service.recording is None
    records = [json.loads(line) for line in first_file.read_text().splitlines()]
    assert [r["type"] for r in records] == ["start", "snapshot", "stop"]
    assert records[0]["bbox"] == BOX
    assert records[1]["states"] == [{"icao24": "c01234"}]
    assert records[2]["snapshots"] == 1
    assert records[2]["reason"] == "command"
    assert len({r["session_id"] for r in records}) == 1
    assert command(service, "start", bbox=BOX, interval_seconds=60)["ok"]
    assert service.recording.path != first_file
    assert service.recording.interval == 60


def test_start_while_active_rejected_and_stop_idle_succeeds(service):
    assert command(service, "stop")["ok"]
    command(service, "start", bbox=BOX)
    first = service.recording
    assert not command(service, "start", bbox=BOX)["ok"]
    assert service.recording is first


@pytest.mark.parametrize(
    "payload",
    [
        b"bad json",
        b"\xff",
        b"[]",
        b"null",
        b'{"command":"unknown"}',
        b'{"command":"start"}',
        b'{"command":"stop","bbox":{}}',
        b'{"command":"start","interval_seconds":NaN}',
        b'{"command":"status","request_id":[]}',
        json.dumps({"command": "start", "bbox": BOX, "interval_seconds": 10**400}).encode(),
        json.dumps({"command": "start", "bbox": {**BOX, "lamin": 10**400}}).encode(),
    ],
)
def test_invalid_commands_do_not_start_or_crash(service, payload):
    service.handle_command(payload)
    responses = [
        c for c in service.client.publish.call_args_list if c.args[0].endswith("/response")
    ]
    assert not json.loads(responses[-1].args[1])["ok"]
    assert service.recording is None


def test_request_ids_prevent_replayed_stop_affecting_new_session(service):
    command(service, "start", bbox=BOX, request_id="start-1")
    command(service, "stop", request_id="stop-1")
    command(service, "start", bbox=BOX, request_id="start-2")
    current = service.recording
    assert command(service, "stop", request_id="stop-1")["ok"]
    assert service.recording is current
    assert not command(service, "stop", request_id="start-1")["ok"]
    assert service.recording is current


def test_backoff_survives_stop_and_start_and_writes_error(service):
    command(service, "start", bbox=BOX)
    log = service.recording.path
    service.opensky.fetch.side_effect = OpenSkyError("rate limited", retry_after=7200)
    with patch("adsb_logger.service.time.monotonic", return_value=100):
        service.poll()
        assert service.next_poll == 7300
        assert service.status()["retry_after_seconds"] == 7200
        command(service, "stop")
        command(service, "start", bbox=BOX)
        assert service.next_poll == 7300
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert records[1]["type"] == "error"
    assert records[1]["retry_after_seconds"] == 7200


def test_failed_write_stops_recording_and_updates_status(service):
    command(service, "start", bbox=BOX)
    recording = service.recording
    with patch.object(recording, "snapshot", side_effect=OSError("disk full")):
        service.poll()
    assert service.recording is None
    assert "writing the log failed" in service.last_error
    assert recording._file.closed


def test_unwritable_directory_is_reported_as_failed_start(service):
    service.log_dir.parent.joinpath("file").write_text("occupied")
    service.log_dir = service.log_dir.parent / "file" / "child"
    assert not command(service, "start", bbox=BOX)["ok"]
    assert service.recording is None


def test_retained_commands_are_ignored(service):
    message = Mock(topic="adsb-logger/command", payload=b'{"command":"stop"}', retain=True)
    service._on_message(service.client, None, message)
    assert service.inbox.empty()


def test_disconnected_service_keeps_recording_and_republishes_on_request(service):
    command(service, "start", bbox=BOX)
    service.client.is_connected.return_value = False
    service.client.publish.reset_mock()
    service.poll()
    assert service.recording.snapshots == 1
    service.client.publish.assert_not_called()
    service.client.is_connected.return_value = True
    service.publish_status()
    assert json.loads(service.client.publish.call_args.args[1])["snapshots"] == 1


def test_429_closes_session_blocks_new_and_replayed_starts_and_publishes_quota(service):
    command(service, "start", bbox=BOX, request_id="first-start")
    recording = service.recording
    service.opensky.fetch.side_effect = OpenSkyQuotaExhausted("quota exhausted", 7200)
    service.poll()
    assert service.recording is None
    assert recording._file.closed
    assert service.status()["quota_exhausted"]
    assert not service.status()["can_record"]
    assert service.status()["quota_retry_at"]
    assert service.status()["retry_after_seconds"] > 7190
    records = [json.loads(line) for line in recording.path.read_text().splitlines()]
    assert [r["type"] for r in records] == ["start", "error", "stop"]
    assert records[-1]["reason"] == "quota_exhausted"
    for request_id in ("next-start", "first-start", None):
        result = command(service, "start", bbox=BOX, request_id=request_id)
        assert not result["ok"]
        assert result["error_code"] == "quota_exhausted"
        assert result["status"]["quota_exhausted"]
        service.poll()
    service.opensky.fetch.assert_called_once()
    assert len(list(service.log_dir.glob("*.jsonl"))) == 1
    assert command(service, "status")["ok"]
    assert command(service, "stop")["ok"]
    availability = [
        c for c in service.client.publish.call_args_list if c.args[0].endswith("/availability")
    ]
    assert availability[-1].args[1] == "quota_exhausted"
    assert availability[-1].kwargs["retain"]


@pytest.mark.parametrize(
    "box,remaining", [(BOX, 0), ({"lamin": 0, "lomin": 0, "lamax": 20, "lomax": 30}, 3)]
)
def test_last_successful_snapshot_is_saved_before_quota_stop(service, box, remaining):
    command(service, "start", bbox=box)
    recording = service.recording
    service.opensky.credits_remaining = remaining
    service.opensky.quota_retry_after = 3600
    service.poll()
    assert service.recording is None
    assert service.status()["credits_remaining"] == remaining
    records = [json.loads(line) for line in recording.path.read_text().splitlines()]
    assert [r["type"] for r in records] == ["start", "snapshot", "stop"]
    assert records[-1]["reason"] == "quota_exhausted"
    assert records[-1]["snapshots"] == 1


def test_known_insufficient_balance_rejects_start_without_a_file_or_request(service):
    service.opensky.credits_remaining = 1
    large_box = {"lamin": 0, "lomin": 0, "lamax": 20, "lomax": 30}
    result = command(service, "start", bbox=large_box)
    assert result["error_code"] == "quota_exhausted"
    assert not list(service.log_dir.glob("*.jsonl"))
    service.opensky.fetch.assert_not_called()


def test_quota_lock_survives_service_restart_and_expires_without_auto_polling(service):
    with patch("adsb_logger.quota.time.time", return_value=1700000000):
        command(service, "start", bbox=BOX)
        service.opensky.fetch.side_effect = OpenSkyQuotaExhausted("quota exhausted", 120)
        service.poll()
        restarted_api = Mock(spec=OpenSkyClient)
        restarted = RecorderService(service.client, restarted_api, service.log_dir)
        assert restarted.status()["quota_exhausted"]
        assert not command(restarted, "start", bbox=BOX)["ok"]
        restarted_api.fetch.assert_not_called()
    with patch("adsb_logger.quota.time.time", return_value=1700000121):
        assert restarted._expire_quota()
        restarted.publish_status()
        assert not restarted.status()["quota_exhausted"]
        assert restarted.status()["can_record"]
        assert restarted.status()["quota_retry_at"] is None
        assert restarted.last_error is None
        assert restarted.recording is None
        restarted_api.fetch.assert_not_called()
        assert not restarted.quota.path.exists()
        assert command(restarted, "start", bbox=BOX)["ok"]
        restarted._stop_recording("test_cleanup")


def test_quota_lock_applies_even_if_the_final_snapshot_write_fails(service):
    command(service, "start", bbox=BOX)
    recording = service.recording
    service.opensky.credits_remaining = 0
    with patch.object(recording, "snapshot", side_effect=OSError("disk full")):
        service.poll()
    assert service.status()["quota_exhausted"]
    assert service.recording is None
    assert recording._file.closed


def test_service_uses_offline_last_will(service):
    service.client.will_set.assert_called_once_with(
        "adsb-logger/availability", "offline", qos=1, retain=True
    )
