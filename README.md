# ADS-B logger

A small Python service that records live [OpenSky aircraft state snapshots](https://openskynetwork.github.io/opensky-api/rest.html), controlled over MQTT. Docker Compose runs both the logger and its Mosquitto MQTT broker. It starts **idle**, supports one recording at a time, and polls every 30 seconds by default.

## Install and run

Requires Docker Engine and Docker Compose. No host Python installation is needed. From this repository:

```sh
cp .env.example .env
# Edit .env to set the recording directory, credentials, and other settings.
docker compose up -d --build
docker compose logs -f logger
```

Compose reads `.env` automatically and supplies configuration to the logger. It waits for the broker health check before starting the logger, and both services restart with Docker unless explicitly stopped. After editing `.env`, run `docker compose up -d` again to apply changes; `docker compose restart` alone does not reload environment variables. Rebuild with `--build` after changing Python source.

Recordings go to `LOG_DIR` on the **host**, defaulting to this repo's `./data` folder (included as an empty directory; recordings are gitignored). Set `LOG_DIR=/path/to/recordings` in `.env` to choose another location, and create that host directory before starting Compose. The mount requires an existing directory so a missing or mistyped path fails instead of silently putting recordings elsewhere. Inside the logger container this folder is always `/data`, so MQTT status reports paths such as `/data/SESSION.jsonl`. Every session gets a unique file; recordings survive container restarts, rebuilds, and `docker compose down`.

On Linux, set `LOGGER_UID` and `LOGGER_GID` in `.env` to your user's numeric IDs (`id -u` and `id -g`) if they differ from 1000. Startup sets ownership of the recording directory to those IDs, then runs Python as that user. Existing recording files are not recursively changed.

Stop the stack with `docker compose down`. Compose stops the logger before the broker, allowing it to close its current recording and publish offline status. It allows up to two minutes for an in-flight request to finish. Restarting the logger returns it to idle; recording does not resume automatically.

An MQTT broker routes messages: controllers publish commands to a topic, the logger subscribes to it, and the broker delivers those messages. The logger publishes status through the same broker. Inside Docker, the logger connects to the `mqtt` service on port 1883. The broker also exposes port 1883 on host `127.0.0.1` by default. The bundled broker has no authentication; keep the default loopback binding for local access. For controllers on other machines, use an authenticated external broker or configure Mosquitto authentication before exposing its listener.

The Docker images support ARM64, including 64-bit Raspberry Pi OS, and AMD64. No platform emulation is needed on those systems.

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

Every command can include an optional `request_id` (1–128 characters). The service remembers the last 128 IDs in memory: retransmitting the same command returns its original response without applying the action again. Reusing an ID for a different command is rejected. Use a new ID for each new action. This cache does not survive service restarts.

Publish commands **without retain** (do not add `mosquitto_pub -r`). Retained commands delivered when subscribing/reconnecting are ignored to prevent stale starts or stops. MQTT 3.1.1 does not expose the publisher's retain flag for messages delivered to an existing subscriber, so publishers must follow this rule.

| Topic (default prefix) | Contents |
| --- | --- |
| `adsb-logger/command` | Input JSON commands; subscribe/publish with QoS 1 |
| `adsb-logger/response` | JSON `{ "ok": true, "request_id": "…", "status": {…} }` or `{ "ok": false, "request_id": "…", "error": "…" }`; not retained |
| `adsb-logger/status` | Retained JSON with `state` (`idle` or `recording`), file path, session ID, bbox, interval, snapshot/observation counts, last error and retry delay |
| `adsb-logger/availability` | Retained `online` / `offline`; an MQTT last will marks an unexpected disconnect |

Status updates follow commands, polls, and reconnections. Check availability before treating retained status as current. An MQTT outage does not interrupt an active recording; the client reconnects automatically and republishes current status. Commands sent while disconnected are not saved for later, and command responses are not durable. Service restarts always return to idle.

## Credentials and connection settings

All deployment parameters are listed in [.env.example](.env.example). OpenSky supports anonymous access and optional OAuth2 API client credentials. Create an API client in your [OpenSky account](https://opensky-network.org/my-opensky/account) and set both values in `.env` to enable authentication:

```dotenv
OPENSKY_CLIENT_ID=your-api-client-id
OPENSKY_CLIENT_SECRET='your-client-secret'
```

Tokens refresh automatically before expiry and once on a 401 response. Website username/password authentication is not supported by OpenSky.

For an authenticated external MQTT broker, use its reachable hostname (not `localhost`, which refers to the logger container):

```dotenv
MQTT_HOST=broker.example.com
MQTT_PORT=8883
MQTT_USERNAME=logger
MQTT_PASSWORD='your-password'
MQTT_TLS=true
MQTT_TOPIC_PREFIX=aircraft/logger
```

Then run `docker compose up -d --build --no-deps logger` to start only the logger. If you previously ran the bundled broker, stop it with `docker compose stop mqtt`. Setting MQTT credentials configures the logger's connection; it does not configure authentication on the bundled broker.

`MQTT_TLS=true` uses verified TLS with system CA certificates. For a private CA, set `MQTT_CA=/certs/ca.pem` and add a read-only certificate bind mount to `logger` in a `compose.override.yaml` file. The port remains 1883 unless explicitly changed. Use a separate topic prefix for each logger instance.

| `.env` setting | Meaning | Default |
| --- | --- | --- |
| `LOG_DIR` | Host directory mounted at `/data` | `./data` |
| `INTERVAL_SECONDS` | Default polling interval, >= 10 seconds | `30` |
| `LOGGER_UID`, `LOGGER_GID` | Numeric user/group for Python and recording ownership | `1000` |
| `MQTT_HOST`, `MQTT_PORT` | Broker address from inside the logger container | `mqtt`, `1883` |
| `MQTT_TOPIC_PREFIX` | Topic prefix for commands and status | `adsb-logger` |
| `MQTT_CLIENT_ID` | MQTT client identifier | Automatically assigned |
| `MQTT_BIND_ADDRESS` | Host interface for the bundled broker | `127.0.0.1` |
| `MQTT_PUBLISHED_PORT` | Host port for the bundled broker; internal port stays 1883 | `1883` |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | Optional broker credentials | Empty |
| `MQTT_TLS`, `MQTT_CA` | Verified TLS and optional CA path inside the container | `false`, empty |
| `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET` | Optional OAuth credentials; set both or neither | Empty |
| `LOG_VERBOSE` | Debug logging | `false` |

`.env` is ignored by Git and excluded from Docker builds. Compose passes the selected values at runtime. Single-quote secrets containing `$` to prevent Compose interpolation. Exported shell variables take precedence over `.env` values. Bounding boxes remain parameters of MQTT `start` commands, and an MQTT interval overrides `INTERVAL_SECONDS` for that session.

The package can still run directly with Python 3.10+ if needed. CLI flags override environment settings; the Python process itself does not read `.env`. View available flags with `docker compose run --rm --no-deps logger adsb-logger --help`.

## Output format

JSON Lines stores one independent JSON object per line. Each session contains:

1. A `start` record with session ID, bounding box, start time, and polling interval.
2. A `snapshot` record per successful poll, including empty polls. It contains `opensky_time` (Unix seconds) and a `states` list of aircraft objects.
3. An `error` record for failed polls, with the reason and retry delay.
4. A `stop` record with the reason and observation counts on normal closure.

Every record has `schema_version: 1`, `type`, `session_id`, and `recorded_at` (UTC ISO 8601). Records are flushed after each write, but not fsynced; abrupt termination may leave an incomplete final line and no stop record. A write failure stops the session and is reported in MQTT status and stderr.

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

For example, read snapshots in Python without loading the entire session into memory:

```python
import json

with open("data/YOUR_SESSION.jsonl") as recording:
    for line in recording:
        record = json.loads(line)
        if record["type"] == "snapshot":
            for aircraft in record["states"]:
                print(record["opensky_time"], aircraft["icao24"], aircraft["latitude"])
```

## Polling and quotas

OpenSky applies [daily credits and bounding-box-dependent costs](https://openskynetwork.github.io/opensky-api/rest.html#api-credits). At the time of implementation, anonymous access allows 400 credits/day, standard authenticated access 4,000, and a box of at most 25 square degrees costs one credit per poll. At 30 seconds, that is roughly 3 hours 20 minutes of anonymous recording, or 2,880 polls over a full day with credentials and a small box. Larger boxes cost more. The logger does not claim to enforce a daily budget.

On API failures the session stays open and retries after at least 60 seconds (or the configured interval, whichever is longer). Retry headers, including `X-Rate-Limit-Retry-After-Seconds`, override the 60-second fallback when provided. Backoff persists across stop/start commands within the same service process. There is no catch-up polling: missing intervals remain gaps. Polls are scheduled one interval after each completed request. HTTP requests use 5-second connection and 20-second read timeouts; commands and shutdown wait for an in-flight poll to finish. The MQTT network loop runs separately during requests.

## Development

```sh
docker compose build tests
docker compose run --rm tests
docker compose run --rm tests ruff check .
```

Tests use mocked HTTP responses and MQTT clients; they require no OpenSky account or running broker.

An optional integration test uses a real local broker and an HTTP stub (no OpenSky quota):

```sh
docker compose up -d mqtt
docker compose run --rm -e MQTT_TEST_PORT=1883 tests pytest -q tests/test_mqtt_integration.py
```

The `tests` service uses a separate image stage and is excluded from normal startup. It connects to the bundled broker by its Docker service name.
