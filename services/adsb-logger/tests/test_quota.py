import json
from unittest.mock import patch

import pytest

from adsb_logger.models import BoundingBox
from adsb_logger.quota import QuotaGuard


@pytest.mark.parametrize(
    "latitude,longitude,cost",
    [
        (5, 5, 1),
        (5, 5.001, 2),
        (10, 10, 2),
        (10, 10.001, 3),
        (20, 20, 3),
        (20, 20.001, 4),
    ],
)
def test_credit_cost_boundaries(latitude, longitude, cost):
    assert BoundingBox(0, 0, latitude, longitude).credit_cost == cost


@pytest.mark.parametrize("payload", ["broken json", "[]", json.dumps({"retry_at": "wrong"})])
def test_corrupt_quota_state_blocks_instead_of_allowing_api_requests(tmp_path, payload):
    (tmp_path / ".opensky-quota.json").write_text(payload)
    guard = QuotaGuard(tmp_path)
    assert guard.blocked
    assert "unreadable" in guard.reason
    assert QuotaGuard(tmp_path).blocked


def test_lock_remains_in_memory_if_persistence_fails(tmp_path):
    guard = QuotaGuard(tmp_path)
    with patch("adsb_logger.quota.Path.replace", side_effect=OSError("disk full")):
        guard.block(3600, 0)
    assert guard.blocked
    assert not list(tmp_path.glob("*.tmp"))


def test_expired_state_does_not_lock_after_restart(tmp_path):
    with patch("adsb_logger.quota.time.time", return_value=1700000000):
        guard = QuotaGuard(tmp_path)
        guard.block(60, 0)
    with patch("adsb_logger.quota.time.time", return_value=1700000061):
        reloaded = QuotaGuard(tmp_path)
        assert not reloaded.blocked
        assert reloaded.retry_at_iso is None
        assert not reloaded.path.exists()
