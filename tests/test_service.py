import json
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt
import pytest

from adsb_logger.opensky import OpenSkyClient, OpenSkyError
from adsb_logger.service import RecorderService

BOX = {"lamin": 43, "lomin": -80.5, "lamax": 44.5, "lomax": -78.5}


@pytest.fixture
def service(tmp_path):
    client = Mock(spec=mqtt.Client)
    client.is_connected.return_value = True
    client.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS
    opensky = Mock(spec=OpenSkyClient)
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
