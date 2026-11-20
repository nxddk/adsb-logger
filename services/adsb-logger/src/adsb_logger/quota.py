"""Persist the API cooldown so restarts cannot bypass an exhausted quota."""

import json
import logging
import math
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .opensky import QUOTA_FALLBACK_SECONDS

LOG = logging.getLogger(__name__)
QUOTA_REASON = "OpenSky quota exhausted; new recordings are blocked until the retry time."


class QuotaBlockedError(ValueError):
    pass


class QuotaGuard:
    def __init__(self, directory: Path):
        self.path = directory / ".opensky-quota.json"
        self.retry_at: float | None = None
        self.remaining: int | None = None
        self.reason: str | None = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            deadline = data["retry_at"]
            remaining = data["remaining"]
            reason = data["reason"]
            if (
                isinstance(deadline, bool)
                or not isinstance(deadline, (int, float))
                or not 0 < deadline <= 253402300799
                or not math.isfinite(deadline)
                or not isinstance(reason, str)
                or not reason
                or (remaining is not None and (type(remaining) is not int or remaining < 0))
            ):
                raise ValueError("invalid quota state")
            self.retry_at, self.remaining, self.reason = deadline, remaining, reason
            self.expire()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            LOG.exception("Cannot read saved quota state; blocking requests conservatively")
            self.block(
                QUOTA_FALLBACK_SECONDS,
                None,
                "Saved quota state is unreadable; recording is blocked conservatively.",
            )

    @property
    def blocked(self) -> bool:
        return self.retry_at is not None and time.time() < self.retry_at

    @property
    def retry_at_iso(self) -> str | None:
        if not self.blocked:
            return None
        return datetime.fromtimestamp(self.retry_at, timezone.utc).isoformat()

    def block(self, retry_after: float, remaining: int | None, reason: str = QUOTA_REASON) -> None:
        self.retry_at = min(time.time() + max(1, retry_after), 253402300799)
        self.remaining, self.reason = remaining, reason
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".opensky-quota-",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                json.dump(
                    {"retry_at": self.retry_at, "remaining": remaining, "reason": reason},
                    output,
                    allow_nan=False,
                )
                output.flush()
            temporary.replace(self.path)
        except OSError:
            # The in-memory lock still applies when the filesystem is unavailable.
            LOG.exception("Could not persist quota lock; it will not survive a restart")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    LOG.warning("Could not remove temporary quota state file")

    def expire(self) -> bool:
        if self.retry_at is None or self.blocked:
            return False
        self.retry_at = self.remaining = self.reason = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            LOG.warning("Could not remove expired quota state file")
        return True
