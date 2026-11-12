"""Small OpenSky REST client with optional OAuth2 client credentials."""

import math
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

import requests

from .models import BoundingBox, normalize_snapshot

API_URL = "https://opensky-network.org/api/states/all"
TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
)


class OpenSkyError(Exception):
    def __init__(self, message: str, retry_after: float = 60):
        super().__init__(message)
        self.retry_after = retry_after


def retry_delay(response: requests.Response) -> float:
    for header in ("X-Rate-Limit-Retry-After-Seconds", "Retry-After"):
        value = response.headers.get(header)
        if value is None:
            continue
        try:
            seconds = float(value)
        except ValueError:
            try:
                deadline = parsedate_to_datetime(value)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                seconds = deadline.timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(seconds):
            return max(1, seconds)
    return 60


class OpenSkyClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        if bool(client_id) != bool(client_secret):
            raise ValueError("set both OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET, or neither")
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "adsb-logger/0.1.0"
        self._token: str | None = None
        self._expires_at = 0.0

    def _headers(self) -> dict[str, str]:
        if not self.client_id:
            return {}
        if not self._token or time.monotonic() >= self._expires_at:
            response = self.session.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=(5, 20),
            )
            self._check_response(response, "authentication")
            data = response.json()
            token = data.get("access_token")
            expires_in = float(data.get("expires_in", 1800))
            if not isinstance(token, str) or not token or not math.isfinite(expires_in):
                raise ValueError("invalid token response")
            self._token = token
            self._expires_at = time.monotonic() + max(0, expires_in - 30)
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _check_response(response: requests.Response, operation: str) -> None:
        if response.status_code >= 400:
            # Do not include response bodies or request objects: they may contain secrets.
            raise OpenSkyError(
                f"OpenSky {operation} returned HTTP {response.status_code}",
                retry_after=retry_delay(response),
            )

    def fetch(self, bbox: BoundingBox) -> dict:
        try:
            for attempt in range(2):
                response = self.session.get(
                    API_URL,
                    params={**bbox.as_dict(), "extended": 1},
                    headers=self._headers(),
                    timeout=(5, 20),
                )
                if response.status_code == 401 and self.client_id and attempt == 0:
                    self._token = None
                    continue
                self._check_response(response, "states request")
                return normalize_snapshot(response.json())
        except requests.RequestException as exc:
            raise OpenSkyError("OpenSky network request failed") from exc
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            raise OpenSkyError("OpenSky returned an invalid response") from exc
        raise OpenSkyError("OpenSky authentication failed")

    def close(self) -> None:
        self.session.close()
