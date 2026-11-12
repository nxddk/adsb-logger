"""Validated recording settings and the OpenSky state vector schema."""

import math
from dataclasses import asdict, dataclass
from typing import Any

STATE_FIELDS = (
    "icao24",
    "callsign",
    "origin_country",
    "time_position",
    "last_contact",
    "longitude",
    "latitude",
    "baro_altitude",
    "on_ground",
    "velocity",
    "true_track",
    "vertical_rate",
    "sensors",
    "geo_altitude",
    "squawk",
    "spi",
    "position_source",
    "category",
)


def positive_seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("interval_seconds must be a finite number >= 10")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError("interval_seconds must be a finite number >= 10") from exc
    if not math.isfinite(value) or value < 10:
        raise ValueError("interval_seconds must be a finite number >= 10")
    return value


@dataclass(frozen=True)
class BoundingBox:
    lamin: float
    lomin: float
    lamax: float
    lomax: float

    @classmethod
    def parse(cls, value: Any) -> "BoundingBox":
        keys = {"lamin", "lomin", "lamax", "lomax"}
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError("bbox must contain exactly lamin, lomin, lamax, lomax")
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not -180 <= v <= 180
            for v in value.values()
        ):
            raise ValueError("bbox coordinates must be finite numbers between -180 and 180")
        box = cls(**{k: float(v) for k, v in value.items()})
        if not -90 <= box.lamin < box.lamax <= 90:
            raise ValueError("bbox requires -90 <= lamin < lamax <= 90")
        if not -180 <= box.lomin < box.lomax <= 180:
            raise ValueError("bbox requires -180 <= lomin < lomax <= 180")
        return box

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def normalize_snapshot(payload: Any) -> dict[str, Any]:
    """Map positional vectors to named fields; keep future fields without data loss."""
    if not isinstance(payload, dict):
        raise ValueError("OpenSky response must be an object")
    timestamp = payload.get("time")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise ValueError("OpenSky response must contain an integer time")
    if "states" not in payload:
        raise ValueError("OpenSky response must contain states")
    states = payload["states"]
    if states is None:
        states = []
    if not isinstance(states, list):
        raise ValueError("OpenSky states must be a list or null")
    aircraft = []
    for vector in states:
        if not isinstance(vector, list) or len(vector) < 17:
            raise ValueError("OpenSky returned a malformed state vector")
        state = {key: vector[i] if i < len(vector) else None for i, key in enumerate(STATE_FIELDS)}
        if isinstance(state["callsign"], str):
            state["callsign"] = state["callsign"].strip()
        if len(vector) > len(STATE_FIELDS):
            state["extra_fields"] = vector[len(STATE_FIELDS) :]
        aircraft.append(state)
    return {"opensky_time": timestamp, "states": aircraft}
