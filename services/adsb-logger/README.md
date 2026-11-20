# ADS-B logger

A self-contained Python service that records live OpenSky aircraft state snapshots to SQLite, controlled over MQTT. This directory owns the Python package, tests, container image, and startup script. Python 3.10+ is required for local development; Docker supplies Python in the stack.

## Run with Argus Stack

From the repository root:

```sh
docker compose up -d --build adsb-logger
docker compose logs -f adsb-logger
```

The logger starts idle and supports one recording at a time. Use the [dashboard](../dashboard/README.md) or publish MQTT commands to start and stop sessions. Existing MQTT topics and command payloads are unchanged.

Configuration comes from the root [.env.example](../../.env.example). Set both `OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET` for OAuth authentication, or leave both empty for anonymous access. `INTERVAL_SECONDS` defaults to 30. The host `LOG_DIR` is mounted at `/data` inside the container; the existing `./data` default is retained. See the [stack setup guide](../../README.md) for credentials, permissions, and volume configuration.

The service continues recording through MQTT outages, reconnects automatically, and returns to idle after a process restart. Exhausted API credits stop the recording and block further starts. SIGINT/SIGTERM closes the active recording. The stack allows two minutes for shutdown so an in-flight API request can finish.

## Send commands

These commands use the MQTT tools inside the included broker container, so no additional client installation is needed. They assume the default `MQTT_TOPIC_PREFIX=adsb-logger`; replace the topic prefix if you change it in `.env`.

Watch status and command responses:

```sh
docker compose exec mqtt mosquitto_sub -h localhost -t 'adsb-logger/#' -v
```

Start a recording around Toronto (WGS84 coordinates in decimal degrees):

```sh
docker compose exec mqtt mosquitto_pub -h localhost -q 1 \
  -t adsb-logger/command \
  -m '{"command":"start","request_id":"toronto-1","bbox":{"lamin":43,"lomin":-80.5,"lamax":44.5,"lomax":-78.5},"interval_seconds":30}'
```

Stop recording, leaving the service available for another start:

```sh
docker compose exec mqtt mosquitto_pub -h localhost -q 1 \
  -t adsb-logger/command -m '{"command":"stop","request_id":"stop-1"}'
```

Request the current status:

```sh
docker compose exec mqtt mosquitto_pub -h localhost -q 1 \
  -t adsb-logger/command -m '{"command":"status"}'
```

With an existing broker, use any MQTT client to publish the same JSON payloads to `<prefix>/command`.

| Command | Parameters | Behavior |
| --- | --- | --- |
| `start` | Required `bbox` object; optional `interval_seconds` | Create a session and poll immediately. Rejected if already recording. |
| `stop` | None | Close the recording; succeeds if already idle. |
| `status` | None | Publish the current status. |

`bbox` requires exactly `lamin`, `lomin`, `lamax`, `lomax`, with increasing latitude bounds within -90..90 and longitude bounds within -180..180. Boxes crossing the antimeridian are unsupported. Intervals must be finite numbers >= 10 seconds; an omitted interval uses the service default.

Every command can include an optional `request_id` (1–128 characters). The service remembers the last 128 IDs in memory: retransmitting the same command returns its original response without applying the action again. Reusing an ID for a different command is rejected. Use a new ID for each new action. This cache does not survive service restarts. While quota is blocked, every `start` is rejected, including replayed starts; quota rejections are not cached.

A quota-blocked start returns `ok: false`, `error_code: "quota_exhausted"`, a readable `error`, and the current `status`. It creates no recording session and makes no OpenSky request. `stop` and `status` remain available.

Publish commands **without retain** (do not add `mosquitto_pub -r`). Retained commands delivered when subscribing/reconnecting are ignored to prevent stale starts or stops. MQTT 3.1.1 does not expose the publisher's retain flag for messages delivered to an existing subscriber, so publishers must follow this rule.

| Topic (default prefix) | Contents |
| --- | --- |
| `adsb-logger/command` | Input JSON commands; subscribe/publish with QoS 1 |
| `adsb-logger/response` | JSON `{ "ok": true, "request_id": "…", "status": {…} }` or `{ "ok": false, "request_id": "…", "error": "…" }`; not retained |
| `adsb-logger/status` | Retained JSON with `state` (`idle` or `recording`), database path, session ID, bbox, interval, snapshot/observation counts, last error, retry delay, and quota fields described below |
| `adsb-logger/availability` | Retained `online`, `quota_exhausted`, or `offline` |

Status updates follow commands, polls, and reconnections. Check availability before treating retained status as current. An MQTT outage does not interrupt an active recording; the client reconnects automatically and republishes current status. Commands sent while disconnected are not saved for later, and command responses are not durable. Service restarts always return to idle.

Availability is a lifecycle/readiness message, not a heartbeat. `online` means the connected process is not quota-blocked; `quota_exhausted` means it is connected and accepts status/stop requests but cannot start recordings. `offline` is published during graceful shutdown, or by the broker's MQTT last will when it detects an unexpected lost connection. Stopping a recording leaves the service connected and does not publish `offline`. During a broker outage, subscribers cannot receive updates until connectivity returns.

Quota fields in status:

| Field | Meaning |
| --- | --- |
| `quota_exhausted` | Whether the service is currently quota-blocked |
| `quota_retry_at` | Earliest permitted retry as an ISO 8601 UTC timestamp, or `null` |
| `credits_remaining` | Last reported balance, or `null` if unknown |
| `can_record` | True when idle and not quota-blocked |

The usual `retry_after_seconds` also reports the remaining quota cooldown while blocked. Status remains `state: "idle"` after the automatic stop; quota availability and fields explain why recording is unavailable.

## SQLite storage

All sessions share **`LOG_DIR/adsb.sqlite3`**, which defaults to `./data/adsb.sqlite3` on the host and `/data/adsb.sqlite3` in Docker. Python's built-in SQLite module supplies the database; no extra server, container, dependency, or configuration is needed. The file is created when the first recording or import starts.

There are two tables:

| Table | Stored data |
| --- | --- |
| `sessions` | `session_id`, start/end timestamps, `bbox_json`, interval, snapshot/observation counts, stop reason, and optional legacy `source_file` |
| `records` | One whole event per row: ID, session ID, type, recording timestamp, OpenSky timestamp, and `payload_json` |

Each successful poll is **one snapshot row containing the full aircraft list**, rather than separate rows for every aircraft. Each session still has `start`, `snapshot`, `error`, and `stop` events. `payload_json` retains `schema_version: 1`, `type`, `session_id`, `recorded_at` (UTC ISO 8601), and the same named aircraft fields previously written to JSONL.

Every event is committed in a transaction. Snapshot insertion and session-counter updates are atomic. SQLite uses WAL mode with full synchronous commits, so another local connection can read committed records while capture continues. Only run one recorder against a given database. A process crash preserves committed snapshots; an unclosed session has a null `ended_at` and no final stop record. Restarting the service does not resume it.

MQTT status includes `storage: "sqlite"` and `database`, even while idle. The existing `file` field points to the database while recording and is null while idle. Use `session_id` to distinguish sessions within that file. Storage failures stop recording and are reported through status and stderr.

Aircraft objects use the [OpenSky field names and units](https://openskynetwork.github.io/opensky-api/rest.html#response):

| Fields | Meaning / units |
| --- | --- |
| `icao24`, `callsign`, `origin_country` | Transponder address, trimmed callsign, country inferred from address |
| `time_position`, `last_contact` | Last position/message timestamps, Unix seconds |
| `longitude`, `latitude` | WGS84 decimal degrees |
| `baro_altitude`, `geo_altitude` | Barometric/geometric altitude in meters |
| `velocity`, `vertical_rate` | Ground speed and climb/descent rate in meters/second |
| `true_track` | Degrees clockwise from north |
| `on_ground`, `squawk`, `spi` | Ground flag, transponder code, special purpose indicator |
| `sensors`, `position_source`, `category` | OpenSky receiver IDs and numeric source/category codes |

Unavailable values remain JSON `null`. The API is requested with `extended=1` for aircraft categories. Unexpected additional vector fields are preserved in `extra_fields`. Snapshots are saved as received, with repeated aircraft observations across polls; there is no deduplication or interpolation. These are aircraft state summaries, not raw transponder messages or flight itineraries. The service retains all returned position sources, not just source code 0 (ADS-B).

List sessions with SQL:

```sql
SELECT session_id, started_at, ended_at, snapshots, aircraft_observations, stop_reason
FROM sessions ORDER BY started_at DESC;
```

Read whole snapshots in Python without loading the entire recording into memory:

```python
import json
import sqlite3
from contextlib import closing

with closing(sqlite3.connect("file:data/adsb.sqlite3?mode=ro", uri=True)) as database:
    for (payload,) in database.execute(
        "SELECT payload_json FROM records WHERE type = 'snapshot' "
        "AND session_id = ? ORDER BY id", ("YOUR_SESSION_ID",)
    ):
        snapshot = json.loads(payload)
        for aircraft in snapshot["states"]:
            print(snapshot["opensky_time"], aircraft["icao24"], aircraft["latitude"])
```

### Import legacy JSONL recordings

Existing JSONL files are retained. With the recorder idle, import completed sessions from the stack root:

```sh
docker compose run --rm --no-deps adsb-logger sh -c \
  'python -m adsb_logger.migrate --log-dir /data /data/*.jsonl'
```

Imports stream one record at a time and commit each whole session atomically. Repeating the command skips session IDs already present. Invalid or incomplete files are reported and rolled back; original files are never deleted. Imported sessions preserve original timestamps, payloads, stop reasons, and counts.

### Backups

SQLite may keep `adsb.sqlite3-wal` and `adsb.sqlite3-shm` companion files while a connection is open. For an online backup, use SQLite's backup API rather than copying just the main file:

```sh
docker compose exec -T adsb-logger python - <<'PYTHON'
import sqlite3
from contextlib import closing

with closing(sqlite3.connect("file:/data/adsb.sqlite3?mode=ro", uri=True)) as source:
    with closing(sqlite3.connect("/data/adsb-backup.sqlite3")) as target:
        source.backup(target)
PYTHON
```

Keep the quota state file `.opensky-quota.json` alongside the database when moving deployments. It remains separate so exhausted-credit protection does not depend on opening the recording database.

## Polling and quotas

OpenSky applies [daily credits and bounding-box-dependent costs](https://openskynetwork.github.io/opensky-api/rest.html#api-credits). At the time of implementation, anonymous access allows 400 credits/day, standard authenticated access 4,000, and a box of at most 25 square degrees costs one credit per poll. At 30 seconds, that is roughly 3 hours 20 minutes of anonymous recording, or 2,880 polls over a full day with credentials and a small box. Larger boxes cost more. The logger does not claim to enforce a daily budget.

An HTTP 429 from the states API, or a reported balance below the next bounding-box query's cost, closes the session with a final `stop` record whose `reason` is `quota_exhausted`. The last successful snapshot is saved before closing. Insufficient known credits also reject a new start before requesting data. This is conservative: if a large box needs more credits than remain, the service blocks new recordings until cooldown even if a smaller box could fit that remaining balance.

The service uses `X-Rate-Limit-Retry-After-Seconds` or `Retry-After` when provided. With no valid retry header, it blocks for **24 hours** rather than probing repeatedly. The lock is saved atomically in `LOG_DIR/.opensky-quota.json` and loaded on startup, so stop/start commands and container restarts cannot bypass it. An unreadable state file blocks conservatively. If filesystem errors prevent persistence, the in-memory lock still applies and an error is logged.

At the deadline, the service publishes `online` and clears the quota block **without calling OpenSky or resuming the recording**. Start a new session explicitly. The deadline permits a retry; it does not verify that the provider has refilled credits. Other clients can consume the shared balance, so another attempt may be refused again. Keep the quota state file with the recording directory when migrating deployments.

Other API/network failures keep the session open and retry after at least 60 seconds (or the configured interval, whichever is longer), honoring retry headers. That transient backoff persists across stop/start commands within the process. There is no catch-up polling: missing intervals remain gaps. Polls are scheduled one interval after each completed request. HTTP requests use 5-second connection and 20-second read timeouts; commands and shutdown wait for an in-flight poll to finish. The MQTT network loop runs separately during requests.

## Development

From the repository root, run the container checks:

```sh
docker compose build adsb-logger-tests
docker compose run --rm adsb-logger-tests
docker compose run --rm adsb-logger-tests ruff check .
```

The optional integration test uses the real bundled broker and a local OpenSky HTTP stub; it consumes no API quota:

```sh
docker compose up -d mqtt
docker compose run --rm -e MQTT_TEST_PORT=1883 adsb-logger-tests pytest -q tests/test_mqtt_integration.py
```

For local Python development, from this service directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
```

The standalone `adsb-logger` CLI still supports `--log-dir` and the MQTT flags shown by `adsb-logger --help`. It reads process environment variables directly; Compose loads the root `.env` for container deployments. Outside Compose, the default broker is `localhost:1883` and the default recording directory is `./data` relative to the current working directory.
