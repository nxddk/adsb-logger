import json
from unittest.mock import Mock, patch

import pytest
import requests

from adsb_logger.models import BoundingBox
from adsb_logger.opensky import (
    API_URL,
    TOKEN_URL,
    OpenSkyClient,
    OpenSkyError,
    OpenSkyQuotaExhausted,
    retry_delay,
)

BOX = BoundingBox(43, -80.5, 44.5, -78.5)


def response(code=200, data=None, headers=None):
    result = requests.Response()
    result.status_code = code
    result._content = json.dumps(data).encode()
    result.headers.update(headers or {})
    return result


@pytest.fixture
def client():
    instance = OpenSkyClient()
    instance.session.close()
    instance.session = Mock(spec=requests.Session)
    yield instance
    instance.close()


def test_anonymous_fetch_uses_bbox_and_extended_fields(client):
    client.session.get.return_value = response(data={"time": 1, "states": None})
    assert client.fetch(BOX) == {"opensky_time": 1, "states": []}
    client.session.get.assert_called_once_with(
        API_URL, params={**BOX.as_dict(), "extended": 1}, headers={}, timeout=(5, 20)
    )


def test_oauth_cached_refreshed_and_retried_on_401(client):
    client.client_id, client.client_secret = "client", "secret"
    client.session.post.side_effect = [
        response(data={"access_token": "first", "expires_in": 1800}),
        response(data={"access_token": "second", "expires_in": 1800}),
    ]
    ok = response(data={"time": 1, "states": []})
    client.session.get.side_effect = [ok, response(401), ok]
    client.fetch(BOX)
    client.fetch(BOX)
    assert client.session.post.call_count == 2
    assert client.session.get.call_args.kwargs["headers"] == {"Authorization": "Bearer second"}
    client.session.post.assert_called_with(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "client_id": "client", "client_secret": "secret"},
        timeout=(5, 20),
    )


def test_expired_token_is_refreshed_before_request(client):
    client.client_id, client.client_secret = "client", "secret"
    client._token, client._expires_at = "old", 10
    client.session.post.return_value = response(data={"access_token": "new"})
    client.session.get.return_value = response(data={"time": 1, "states": []})
    with patch("adsb_logger.opensky.time.monotonic", return_value=11):
        client.fetch(BOX)
    assert client.session.get.call_args.kwargs["headers"] == {"Authorization": "Bearer new"}


def test_repeated_401_does_not_retry_forever(client):
    client.client_id, client.client_secret = "client", "secret"
    client.session.post.return_value = response(data={"access_token": "token"})
    client.session.get.return_value = response(401)
    with pytest.raises(OpenSkyError, match="HTTP 401"):
        client.fetch(BOX)
    assert client.session.get.call_count == 2


def test_rate_limit_preserves_server_delay(client):
    client.session.get.return_value = response(
        429, headers={"X-Rate-Limit-Retry-After-Seconds": "7200", "Retry-After": "1"}
    )
    with pytest.raises(OpenSkyQuotaExhausted) as error:
        client.fetch(BOX)
    assert error.value.retry_after == 7200


def test_quota_without_retry_header_uses_conservative_daily_cooldown(client):
    client.session.get.return_value = response(429)
    with pytest.raises(OpenSkyQuotaExhausted) as error:
        client.fetch(BOX)
    assert error.value.retry_after == 86400
    assert client.session.get.call_count == 1


@pytest.mark.parametrize("header,expected", [("0", 0), ("99", 99), ("-1", 0), ("unknown", None)])
def test_successful_response_exposes_remaining_credits_and_retry_time(client, header, expected):
    client.session.get.return_value = response(
        data={"time": 1, "states": []},
        headers={"X-Rate-Limit-Remaining": header, "Retry-After": "120"},
    )
    assert client.fetch(BOX)["states"] == []
    assert client.credits_remaining == expected
    assert client.quota_retry_after == 120


def test_missing_headers_clear_previous_balance(client):
    client.credits_remaining = 0
    client.session.get.return_value = response(data={"time": 1, "states": []})
    client.fetch(BOX)
    assert client.credits_remaining is None


def test_retry_after_date_and_invalid_header():
    with patch("adsb_logger.opensky.time.time", return_value=0):
        assert (
            retry_delay(response(headers={"Retry-After": "Thu, 01 Jan 1970 00:02:00 GMT"})) == 120
        )
    assert retry_delay(response(headers={"Retry-After": "NaN"})) == 60


@pytest.mark.parametrize(
    "bad_response", [response(503), response(data={}), response(data={"time": 1, "states": [1]})]
)
def test_bad_api_responses_raise_client_error(client, bad_response):
    client.session.get.return_value = bad_response
    with pytest.raises(OpenSkyError):
        client.fetch(BOX)


def test_network_errors_do_not_disclose_request_details(client):
    client.session.get.side_effect = requests.Timeout("secret request details")
    with pytest.raises(OpenSkyError) as error:
        client.fetch(BOX)
    assert "secret" not in str(error.value)


def test_credentials_must_be_paired():
    with pytest.raises(ValueError, match="both"):
        OpenSkyClient("only-id")
