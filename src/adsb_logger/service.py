"""MQTT transport and a serialized command/poll loop.

Paho owns the network thread. Only the main loop touches recordings or OpenSky.
"""

import json
import logging
import queue
import threading
import time
from collections import OrderedDict
from pathlib import Path

import paho.mqtt.client as mqtt

from .models import BoundingBox, positive_seconds
from .opensky import OpenSkyClient, OpenSkyError
from .recording import Recording, utc_now

LOG = logging.getLogger(__name__)


class RecorderService:
    def __init__(
        self,
        client: mqtt.Client,
        opensky: OpenSkyClient,
        log_dir: Path,
        topic_prefix: str = "adsb-logger",
        interval_seconds: float = 30,
    ):
        self.client = client
        self.opensky = opensky
        self.log_dir = log_dir.expanduser().resolve()
        self.prefix = topic_prefix
        self.default_interval = positive_seconds(interval_seconds)
        self.recording: Recording | None = None
        self.last_error: str | None = None
        self.next_poll = 0.0
        self.retry_until = 0.0
        self.stop_event = threading.Event()
        self.inbox: queue.Queue = queue.Queue(maxsize=256)
        self._responses: OrderedDict = OrderedDict()
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.will_set(f"{self.prefix}/availability", "offline", qos=1, retain=True)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.max_queued_messages_set(100)

    def _enqueue(self, item: tuple) -> None:
        try:
            self.inbox.put_nowait(item)
        except queue.Full:
            LOG.warning("MQTT command queue full; incoming message dropped")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            LOG.error("MQTT connection rejected: %s", reason_code)
            return
        client.subscribe(f"{self.prefix}/command", qos=1)
        self._enqueue(("connected", None))

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if not self.stop_event.is_set():
            LOG.warning("MQTT disconnected (%s); reconnecting, recording continues", reason_code)

    def _on_message(self, client, userdata, message):
        if message.topic != f"{self.prefix}/command":
            return
        if message.retain:
            LOG.warning("Ignored retained command; publish controls without retain")
            return
        if len(message.payload) > 65536:
            LOG.warning("Ignored command larger than 64 KiB")
            return
        self._enqueue(("command", message.payload))

    def _publish(self, suffix: str, payload, retain: bool = False):
        if not self.client.is_connected():
            return None
        if not isinstance(payload, str):
            payload = json.dumps(payload, allow_nan=False)
        result = self.client.publish(f"{self.prefix}/{suffix}", payload, qos=1, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            LOG.warning("Could not queue MQTT %s publication: %s", suffix, result.rc)
        return result

    def status(self) -> dict:
        recording = self.recording
        return {
            "state": "recording" if recording else "idle",
            "updated_at": utc_now(),
            "session_id": recording.session_id if recording else None,
            "file": str(recording.path) if recording else None,
            "bbox": recording.bbox.as_dict() if recording else None,
            "interval_seconds": recording.interval if recording else self.default_interval,
            "snapshots": recording.snapshots if recording else 0,
            "aircraft_observations": recording.aircraft_observations if recording else 0,
            "last_error": self.last_error,
            "retry_after_seconds": max(0, self.retry_until - time.monotonic()),
        }

    def publish_status(self) -> None:
        self._publish("status", self.status(), retain=True)

    def _stop_recording(self, reason: str) -> None:
        if self.recording:
            recording, self.recording = self.recording, None
            recording.close(reason)
            LOG.info("Closed recording %s", recording.path)

    @staticmethod
    def _invalid_constant(value):
        raise ValueError(f"invalid JSON number: {value}")

    def handle_command(self, payload: bytes) -> None:
        request_id = None
        command = None
        cacheable = False
        try:
            command = json.loads(payload, parse_constant=self._invalid_constant)
            if not isinstance(command, dict):
                raise ValueError("command payload must be a JSON object")
            request_id = command.get("request_id")
            if request_id is not None and (
                not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
            ):
                request_id = None
                raise ValueError("request_id must be a string of 1 to 128 characters")
            if request_id in self._responses:
                original, response = self._responses[request_id]
                if original != command:
                    raise ValueError("request_id was already used for a different command")
                self._publish("response", response)
                self.publish_status()
                return
            cacheable = request_id is not None
            action = command.get("command")
            allowed = {"command", "request_id"}
            if action == "start":
                allowed |= {"bbox", "interval_seconds"}
            if set(command) - allowed:
                raise ValueError(
                    "unknown command parameters: " + ", ".join(sorted(set(command) - allowed))
                )
            if action == "start":
                if self.recording:
                    raise ValueError("already recording; stop the current session first")
                bbox = BoundingBox.parse(command.get("bbox"))
                interval = positive_seconds(command.get("interval_seconds", self.default_interval))
                self.recording = Recording(self.log_dir, bbox, interval)
                self.last_error = None
                self.next_poll = max(time.monotonic(), self.retry_until)
                LOG.info("Started recording %s", self.recording.path)
            elif action == "stop":
                self._stop_recording("command")
            elif action != "status":
                raise ValueError("command must be start, stop, or status")
            response = {"ok": True, "request_id": request_id, "status": self.status()}
        except (ValueError, UnicodeError, RecursionError) as exc:
            response = {"ok": False, "request_id": request_id, "error": str(exc)}
        except OSError:
            self.last_error = "could not create, write, or close the recording file"
            LOG.exception("Recording file operation failed")
            response = {"ok": False, "request_id": request_id, "error": self.last_error}
        if cacheable:
            self._responses[request_id] = (command, response)
            if len(self._responses) > 128:
                self._responses.popitem(last=False)
        self._publish("response", response)
        self.publish_status()

    def poll(self) -> None:
        recording = self.recording
        if recording is None:
            return
        try:
            data = self.opensky.fetch(recording.bbox)
            recording.snapshot(data)
            self.last_error = None
            self.next_poll = time.monotonic() + recording.interval
        except OpenSkyError as exc:
            self.last_error = str(exc)
            self.retry_until = time.monotonic() + max(recording.interval, exc.retry_after)
            self.next_poll = self.retry_until
            LOG.warning("%s; retry in %.0fs", exc, self.retry_until - time.monotonic())
            try:
                recording.write(
                    "error", error=str(exc), retry_after_seconds=self.retry_until - time.monotonic()
                )
            except OSError:
                self._disk_failure()
        except (OSError, ValueError):
            self._disk_failure()
        self.publish_status()

    def _disk_failure(self) -> None:
        self.last_error = "recording stopped because writing the log failed"
        LOG.exception(self.last_error)
        try:
            self._stop_recording("write_error")
        except OSError:
            LOG.exception("Could not write the final stop record")

    def run(self, host: str, port: int) -> None:
        self.client.connect_async(host, port, keepalive=60)
        self.client.loop_start()
        LOG.info("Connecting to MQTT %s:%s; log directory %s", host, port, self.log_dir)
        try:
            while not self.stop_event.is_set():
                timeout = 0.5
                if self.recording:
                    timeout = min(timeout, max(0, self.next_poll - time.monotonic()))
                try:
                    kind, payload = self.inbox.get(timeout=timeout)
                except queue.Empty:
                    if self.recording and time.monotonic() >= self.next_poll:
                        self.poll()
                    continue
                if kind == "connected":
                    self._publish("availability", "online", retain=True)
                    self.publish_status()
                else:
                    self.handle_command(payload)
        finally:
            try:
                self._stop_recording("shutdown")
            except OSError:
                LOG.exception("Could not write the final stop record")
            self.publish_status()
            result = self._publish("availability", "offline", retain=True)
            if result is not None:
                try:
                    result.wait_for_publish(timeout=2)
                except RuntimeError:
                    pass
            self.client.disconnect()
            self.client.loop_stop()
            self.opensky.close()
