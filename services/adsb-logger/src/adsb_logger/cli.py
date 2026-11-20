"""Command-line entry point; secrets are read only from the environment."""

import argparse
import logging
import os
import signal
from pathlib import Path

import paho.mqtt.client as mqtt

from .models import positive_seconds
from .opensky import OpenSkyClient
from .service import RecorderService


def env_bool(name: str) -> bool:
    value = os.getenv(name, "false").lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true/false, 1/0, or yes/no")
    return value in {"true", "1", "yes"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--log-dir",
        type=Path,
        default=os.getenv("LOG_DIR", "data"),
        help="recording directory (default: LOG_DIR or ./data)",
    )
    result.add_argument("--mqtt-host", default=os.getenv("MQTT_HOST", "localhost"))
    result.add_argument("--mqtt-port", type=int, default=os.getenv("MQTT_PORT", "1883"))
    result.add_argument("--topic-prefix", default=os.getenv("MQTT_TOPIC_PREFIX", "adsb-logger"))
    result.add_argument("--mqtt-client-id", default=os.getenv("MQTT_CLIENT_ID", ""))
    result.add_argument(
        "--mqtt-tls",
        action=argparse.BooleanOptionalAction,
        default=env_bool("MQTT_TLS"),
        help="enable verified MQTT TLS",
    )
    result.add_argument(
        "--mqtt-ca",
        default=os.getenv("MQTT_CA") or None,
        help="custom CA certificate (also enables TLS)",
    )
    result.add_argument(
        "--interval-seconds",
        type=float,
        default=os.getenv("INTERVAL_SECONDS", "30"),
        help="default polling interval, >= 10 (default: 30)",
    )
    result.add_argument(
        "--verbose", action=argparse.BooleanOptionalAction, default=env_bool("LOG_VERBOSE")
    )
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        arg_parser = parser()
    except ValueError as exc:
        raise SystemExit(f"adsb-logger: {exc}") from exc
    args = arg_parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        positive_seconds(args.interval_seconds)
        if not 1 <= args.mqtt_port <= 65535:
            raise ValueError("MQTT port must be between 1 and 65535")
        prefix = args.topic_prefix.strip("/")
        if not prefix or any(c in prefix for c in ("+", "#", "\0")):
            raise ValueError("topic prefix must be nonempty and contain no MQTT wildcards or NUL")
        opensky = OpenSkyClient(os.getenv("OPENSKY_CLIENT_ID"), os.getenv("OPENSKY_CLIENT_SECRET"))
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.mqtt_client_id)
        if os.getenv("MQTT_USERNAME"):
            client.username_pw_set(os.environ["MQTT_USERNAME"], os.getenv("MQTT_PASSWORD"))
        if args.mqtt_tls or args.mqtt_ca:
            client.tls_set(ca_certs=args.mqtt_ca)
        service = RecorderService(client, opensky, args.log_dir, prefix, args.interval_seconds)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda signum, frame: service.stop_event.set())
        service.run(args.mqtt_host, args.mqtt_port)
    except (ValueError, OSError) as exc:
        arg_parser.exit(2, f"adsb-logger: {exc}\n")
    return 0
