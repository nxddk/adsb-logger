from pathlib import Path

import pytest

from adsb_logger.cli import main, parser


def test_environment_settings_and_flag_overrides(monkeypatch):
    for key, value in {
        "LOG_DIR": "/recordings",
        "INTERVAL_SECONDS": "45",
        "MQTT_HOST": "mqtt",
        "MQTT_PORT": "8883",
        "MQTT_TOPIC_PREFIX": "aircraft",
        "MQTT_TLS": "true",
        "MQTT_CA": "/certs/ca.pem",
        "LOG_VERBOSE": "yes",
    }.items():
        monkeypatch.setenv(key, value)
    args = parser().parse_args([])
    assert args.log_dir == Path("/recordings")
    assert args.interval_seconds == 45
    assert args.mqtt_host == "mqtt"
    assert args.mqtt_port == 8883
    assert args.topic_prefix == "aircraft"
    assert args.mqtt_tls and args.verbose
    assert args.mqtt_ca == "/certs/ca.pem"
    args = parser().parse_args(
        ["--log-dir", "/other", "--interval-seconds", "60", "--no-mqtt-tls", "--no-verbose"]
    )
    assert args.log_dir == Path("/other")
    assert args.interval_seconds == 60
    assert not args.mqtt_tls and not args.verbose


@pytest.mark.parametrize(
    "key,value",
    [
        ("MQTT_TLS", "maybe"),
        ("LOG_VERBOSE", "debug"),
        ("INTERVAL_SECONDS", "invalid"),
        ("INTERVAL_SECONDS", "5"),
    ],
)
def test_invalid_environment_configuration_exits_cleanly(monkeypatch, capsys, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit):
        main([])
    assert "Traceback" not in capsys.readouterr().err
