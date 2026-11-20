"""Opt-in integration test against a real local broker, with a local HTTP stub."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import paho.mqtt.client as mqtt
import pytest

from adsb_logger.opensky import OpenSkyClient
from adsb_logger.service import RecorderService

pytestmark = pytest.mark.skipif(
    not os.getenv("MQTT_TEST_PORT"), reason="set MQTT_TEST_PORT to a local test broker port"
)


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for MQTT integration event")


def test_real_mqtt_and_http_recording(tmp_path, monkeypatch):
    requests_received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_received.append(parse_qs(urlsplit(self.path).query))
            body = json.dumps({"time": 1700000000, "states": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http_thread = threading.Thread(target=server.serve_forever, daemon=True)
    http_thread.start()
    monkeypatch.setattr(
        "adsb_logger.opensky.API_URL", f"http://127.0.0.1:{server.server_port}/states/all"
    )
    prefix = f"adsb-logger-test/{uuid4().hex}"
    port = int(os.environ["MQTT_TEST_PORT"])
    host = os.getenv("MQTT_TEST_HOST", "127.0.0.1")
    messages = []
    controller = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    controller.on_message = lambda client, userdata, msg: messages.append(
        (msg.topic, msg.payload.decode())
    )
    controller.connect(host, port)
    controller.loop_start()
    service = RecorderService(
        mqtt.Client(mqtt.CallbackAPIVersion.VERSION2), OpenSkyClient(), tmp_path, prefix
    )
    worker = threading.Thread(target=service.run, args=(host, port), daemon=True)

    def has_status(**expected):
        return any(
            topic == f"{prefix}/status"
            and all(json.loads(body).get(key) == value for key, value in expected.items())
            for topic, body in list(messages)
        )

    def send(payload, retain=False):
        info = controller.publish(f"{prefix}/command", json.dumps(payload), qos=1, retain=retain)
        info.wait_for_publish(timeout=5)
        assert info.is_published()

    try:
        wait_for(controller.is_connected)
        controller.subscribe(f"{prefix}/#", qos=1)
        start = {
            "command": "start",
            "bbox": {"lamin": 43, "lomin": -80.5, "lamax": 44.5, "lomax": -78.5},
        }
        send(start, retain=True)
        worker.start()
        wait_for(lambda: has_status(state="idle"))
        assert not list(tmp_path.glob("*.jsonl"))
        send({**start, "request_id": "start-1"})
        wait_for(lambda: has_status(state="recording", snapshots=1))
        assert requests_received == [
            {
                "lamin": ["43.0"],
                "lomin": ["-80.5"],
                "lamax": ["44.5"],
                "lomax": ["-78.5"],
                "extended": ["1"],
            }
        ]
        send({"command": "stop", "request_id": "stop-1"})
        wait_for(
            lambda: any(
                topic == f"{prefix}/response" and json.loads(body).get("request_id") == "stop-1"
                for topic, body in list(messages)
            )
        )
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        records = [json.loads(line) for line in files[0].read_text().splitlines()]
        assert [record["type"] for record in records] == ["start", "snapshot", "stop"]
        service.stop_event.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        wait_for(lambda: (f"{prefix}/availability", "offline") in messages)
    finally:
        service.stop_event.set()
        if worker.is_alive():
            worker.join(timeout=5)
        controller.publish(f"{prefix}/command", b"", qos=1, retain=True).wait_for_publish(2)
        controller.disconnect()
        controller.loop_stop()
        server.shutdown()
        server.server_close()
        http_thread.join(timeout=2)


@pytest.mark.parametrize("http_status", [200, 429])
def test_quota_stops_requests_and_broadcasts_until_cooldown_expires(
    tmp_path,
    monkeypatch,
    http_status,
):
    requests_received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests_received.append(self.path)
            body = json.dumps({"time": 1700000000, "states": []}).encode()
            self.send_response(http_status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Rate-Limit-Remaining", "0")
            self.send_header("X-Rate-Limit-Retry-After-Seconds", "3")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http_thread = threading.Thread(target=server.serve_forever, daemon=True)
    http_thread.start()
    monkeypatch.setattr(
        "adsb_logger.opensky.API_URL", f"http://127.0.0.1:{server.server_port}/states/all"
    )
    prefix = f"adsb-quota-test/{uuid4().hex}"
    host = os.getenv("MQTT_TEST_HOST", "127.0.0.1")
    port = int(os.environ["MQTT_TEST_PORT"])
    messages = []
    controller = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    controller.on_message = lambda client, userdata, msg: messages.append(
        (msg.topic, msg.payload.decode())
    )
    controller.connect(host, port)
    controller.loop_start()
    service = RecorderService(
        mqtt.Client(mqtt.CallbackAPIVersion.VERSION2), OpenSkyClient(), tmp_path, prefix
    )
    worker = threading.Thread(target=service.run, args=(host, port), daemon=True)
    try:
        wait_for(controller.is_connected)
        controller.subscribe(f"{prefix}/#", qos=1)
        worker.start()
        wait_for(lambda: (f"{prefix}/availability", "online") in messages)
        start = {
            "command": "start",
            "bbox": {"lamin": 43, "lomin": -80.5, "lamax": 44.5, "lomax": -78.5},
        }
        controller.publish(f"{prefix}/command", json.dumps(start), qos=1).wait_for_publish(2)
        wait_for(lambda: (f"{prefix}/availability", "quota_exhausted") in messages)
        quota_index = messages.index((f"{prefix}/availability", "quota_exhausted"))
        controller.publish(
            f"{prefix}/command", json.dumps({**start, "request_id": "blocked"}), qos=1
        ).wait_for_publish(2)
        wait_for(
            lambda: any(
                topic == f"{prefix}/response"
                and json.loads(body).get("request_id") == "blocked"
                and json.loads(body).get("error_code") == "quota_exhausted"
                for topic, body in list(messages)
            )
        )
        assert service.recording is None
        records = [
            json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text().splitlines()
        ]
        assert records[-1]["reason"] == "quota_exhausted"
        wait_for(lambda: (f"{prefix}/availability", "online") in messages[quota_index + 1 :])
        assert len(requests_received) == 1
        assert service.recording is None
    finally:
        service.stop_event.set()
        if worker.is_alive():
            worker.join(timeout=5)
        controller.disconnect()
        controller.loop_stop()
        server.shutdown()
        server.server_close()
        http_thread.join(timeout=2)
