import pytest

from adsb_logger.models import BoundingBox, normalize_snapshot, positive_seconds

BOX = {"lamin": 43, "lomin": -80.5, "lamax": 44.5, "lomax": -78.5}
VECTOR = [
    "c01234",
    " ACA123 ",
    "Canada",
    1700000000,
    1700000001,
    -79.5,
    43.7,
    1000,
    False,
    120,
    90,
    2,
    None,
    1050,
    "1200",
    False,
    0,
    3,
]


@pytest.mark.parametrize(
    "value",
    [
        None,
        [43, -80, 44, -79],
        {},
        {**BOX, "other": 1},
        {**BOX, "lamin": True},
        {**BOX, "lamin": float("nan")},
        {**BOX, "lamax": 91},
        {**BOX, "lomin": -181},
        {**BOX, "lamin": 45},
        {**BOX, "lomax": -81},
        {**BOX, "lamax": "44.5"},
    ],
)
def test_invalid_bbox(value):
    with pytest.raises(ValueError):
        BoundingBox.parse(value)


@pytest.mark.parametrize("value", [True, "30", None, 0, 9.9, float("inf"), float("nan"), 10**400])
def test_invalid_interval(value):
    with pytest.raises(ValueError):
        positive_seconds(value)


def test_named_fields_and_future_extensions():
    result = normalize_snapshot({"time": 1700000001, "states": [VECTOR + ["future"]]})
    state = result["states"][0]
    assert result["opensky_time"] == 1700000001
    assert state["callsign"] == "ACA123"
    assert state["longitude"] == -79.5
    assert state["latitude"] == 43.7
    assert state["sensors"] is None
    assert state["position_source"] == 0
    assert state["category"] == 3
    assert state["extra_fields"] == ["future"]


def test_optional_category_and_empty_results():
    result = normalize_snapshot({"time": 1, "states": [VECTOR[:17]]})
    assert result["states"][0]["category"] is None
    assert normalize_snapshot({"time": 1, "states": None})["states"] == []


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"states": []},
        {"time": True},
        {"time": 1},
        {"time": 1, "states": "bad"},
        {"time": 1, "states": [["short"]]},
    ],
)
def test_malformed_response(payload):
    with pytest.raises(ValueError):
        normalize_snapshot(payload)
