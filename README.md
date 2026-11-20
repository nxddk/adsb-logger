# Argus Stack

A Docker Compose workspace for independently packaged services. The current stack includes an MQTT broker, a Nuxt dashboard, and a Python service that records aircraft state snapshots from the OpenSky REST API. Add new services under `services/` and register them in the root Compose file.

## Run the stack

Requires Docker Engine and Docker Compose. Python and Node are supplied by the containers.

```sh
# From the repository root; keep an existing .env:
test -f .env || cp .env.example .env
# Edit .env as needed.
docker compose up -d --build
docker compose logs -f
```

Open **http://localhost:3000**. The ADS-B recorder starts idle. Paste an area's bounding box as `west,south,east,north` (for example `-73.617067,45.471297,-73.576427,45.489201`), set the polling interval, then use **Start recording**, **Stop**, or **Refresh status**. The dashboard shows service availability, recording counts, output paths, and API errors.

If upgrading an existing checkout, see [Migrating from the previous layout](#migrating-from-the-previous-layout) before starting the renamed stack.

```sh
docker compose ps                        # Service state
docker compose logs -f adsb-logger        # Recorder logs
docker compose up -d --build dashboard    # Rebuild one service
docker compose down                      # Stop the stack; keep recordings
```

The Compose project is named `argus-stack`, regardless of the checkout directory's name. Docker's standard `-p` / `COMPOSE_PROJECT_NAME` overrides are available for additional isolated stacks.

## Services

| Compose service | Location | Responsibility |
| --- | --- | --- |
| `mqtt` | [services/mqtt](services/mqtt/README.md) | Shared Mosquitto MQTT broker and configuration |
| `dashboard` | [services/dashboard](services/dashboard/README.md) | Nuxt UI and server-side MQTT bridge |
| `adsb-logger` | [services/adsb-logger](services/adsb-logger/README.md) | OpenSky polling, recording sessions, SQLite storage |

```text
argus-stack/
├── compose.yaml                 # Service wiring, networking, mounts, and health checks
├── .env.example                 # Shared deployment configuration
├── data/                        # Existing ADS-B recordings; gitignored
└── services/
    ├── mqtt/
    │   ├── mosquitto.conf
    │   └── README.md
    ├── dashboard/
    │   ├── Dockerfile
    │   ├── package.json
    │   ├── app/, server/, shared/, tests/
    │   └── README.md
    └── adsb-logger/
        ├── Dockerfile
        ├── pyproject.toml
        ├── src/adsb_logger/
        ├── tests/
        ├── docker/entrypoint.sh
        └── README.md
```

The root manages deployment; application dependencies, source, tests, and image definitions belong to their service. The ADS-B Python package remains independently installable as `adsb-logger`, with the same `adsb-logger` command and `adsb_logger` import. The broker uses its upstream image directly.

MQTT connects services on the Compose network. Both applications use the shared connection settings, while the recorder's command namespace remains `adsb-logger` by default. Other services can use separate namespaces on the same broker. The dashboard keeps broker credentials on its server; browser status polling does not consume OpenSky credits.

## Configuration

Compose reads the root `.env` automatically. Existing environment variable names and values are supported; no credential migration is needed. After editing `.env`, run `docker compose up -d` to recreate affected containers. `docker compose restart` alone does not reload configuration. Exported shell variables take precedence over `.env` values.

| Setting | Used by | Default / meaning |
| --- | --- | --- |
| `MQTT_HOST`, `MQTT_PORT` | Dashboard, ADS-B | `mqtt`, `1883` on the Docker network |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | Dashboard, ADS-B | Optional broker client credentials |
| `MQTT_TLS`, `MQTT_CA` | Dashboard, ADS-B | `false`; optional CA path inside each container |
| `MQTT_TOPIC_PREFIX` | Dashboard, ADS-B | `adsb-logger`; recorder controls/status namespace |
| `MQTT_CLIENT_ID` | ADS-B | Empty for an automatically assigned client ID |
| `MQTT_BIND_ADDRESS`, `MQTT_PUBLISHED_PORT` | Broker | Host listener `127.0.0.1:1883` |
| `DASHBOARD_BIND_ADDRESS`, `DASHBOARD_PORT` | Dashboard | Host listener `127.0.0.1:3000` |
| `LOG_DIR` | ADS-B | Existing host recording directory, default `./data` |
| `INTERVAL_SECONDS` | ADS-B, dashboard form | Default polling interval, `30` seconds |
| `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET` | ADS-B | Optional OAuth API client credentials; set both or neither |
| `LOGGER_UID`, `LOGGER_GID` | ADS-B | Recording ownership and process user, default `1000:1000` |
| `LOG_VERBOSE` | ADS-B | Debug logging, default `false` |

Put secrets containing `$` in single quotes in `.env`. The file is gitignored and excluded from service image builds. [.env.example](.env.example) documents every setting.

For authenticated OpenSky access, create an API client in your OpenSky account and set both OAuth values. Anonymous access works with a smaller quota. The [ADS-B service guide](services/adsb-logger/README.md) covers polling, quota handling, the MQTT protocol, and output fields.

### Network access

The dashboard and broker bind to host loopback by default. For access to a Raspberry Pi from another machine on a trusted LAN, set `DASHBOARD_BIND_ADDRESS=0.0.0.0` and open `http://<pi-address>:3000`. The dashboard has no login and permits recording controls to anyone who can reach it. Broker host access can remain local because the dashboard uses the Docker network.

The included broker allows anonymous clients. Configure authentication and TLS before exposing it outside the local stack. MQTT username/password settings configure clients; they do not configure the broker's access policy.

To use an external broker, set its reachable `MQTT_HOST`, `MQTT_PORT`, credentials, and TLS settings, then start only the applications:

```sh
docker compose up -d --build --no-deps adsb-logger dashboard
```

Stop the bundled broker if it was already running. For a private CA, mount the certificate read-only into both applications using `compose.override.yaml` and set `MQTT_CA` to the shared container path. The dashboard uses its own generated MQTT client ID.

### Recordings

The existing root `./data` directory and any configured `LOG_DIR` are preserved. Custom host directories must already exist; a missing mount path fails early. Files are mounted at `/data` inside the recorder, so dashboard file paths refer to that location. All sessions are stored in `LOG_DIR/adsb.sqlite3` (default `./data/adsb.sqlite3`). SQLite runs inside the recorder without a separate service. Each poll is one whole JSON snapshot entry, linked to session metadata; no new JSONL files are created. Status reports the database path and current session ID. The dashboard estimates credits per query and hourly/daily usage before capture.

On Linux, set `LOGGER_UID` and `LOGGER_GID` to your user's numeric IDs if different from 1000 (`id -u`, `id -g`). The recorder prepares directory ownership before dropping privileges. It does not recursively change existing files.

The SQLite database survives rebuilds and `docker compose down`. Existing JSONL files are preserved; the [recorder guide](services/adsb-logger/README.md#import-legacy-jsonl-recordings) covers repeatable, non-destructive import and safe backups. The recorder continues through broker outages, but process restarts return it to idle. Shutdown allows two minutes for an in-flight request and final file closure.

OpenSky quota exhaustion automatically closes the session, publishes `quota_exhausted` on MQTT availability, and rejects new recordings until the provider's retry time. With no retry header, the cooldown is 24 hours. A hidden `.opensky-quota.json` file in `LOG_DIR` preserves the block across restarts. Expiry allows an explicit new start; it does not poll or resume automatically. `offline` availability denotes shutdown or a broker-detected connection loss, rather than an idle recording session.

## Development and checks

Run checks from the stack root without installing host language runtimes:

```sh
docker compose build adsb-logger-tests dashboard-tests
docker compose run --rm adsb-logger-tests
docker compose run --rm adsb-logger-tests ruff check .
docker compose run --rm dashboard-tests
```

Test services use the `test` profile and are excluded from normal startup. For the ADS-B MQTT integration test, which uses an HTTP stub and consumes no OpenSky quota:

```sh
docker compose up -d mqtt
docker compose run --rm -e MQTT_TEST_PORT=1883 adsb-logger-tests pytest -q tests/test_mqtt_integration.py
```

See each service's README for local development and standalone builds. The runtime images support AMD64 and ARM64, including 64-bit Raspberry Pi OS.

## Adding a service

1. Create `services/<service-name>/` with its source, dependency manifest, tests, and README. Include a Dockerfile and `.dockerignore` if it needs a custom image.
2. Add a uniquely named service to the root `compose.yaml`, with a build context scoped to that directory or an upstream image. Set health checks, required dependencies, mounts, and restart behavior there.
3. Add deployment settings to the root `.env.example`. Reuse the shared MQTT environment anchor when appropriate; use a distinct MQTT topic namespace for the new service's commands and status.
4. Add an optional `<service-name>-tests` service under the `test` profile, and document it in the service table above.

Keep runtime data outside image build contexts. New data mounts should have an explicit location and should not move another service's existing files.

## Migrating from the previous layout

The former root Python package is now `services/adsb-logger/`, the Nuxt app is `services/dashboard/`, and broker configuration is `services/mqtt/mosquitto.conf`. Root-level Python packaging and image files have moved into the ADS-B service. Existing `.env`, recording files, MQTT topics, and command payloads are retained.

Compose names changed from `logger` to `adsb-logger` and from `tests` to `adsb-logger-tests`. Update scripts that refer to the old service names. The project now defaults to `argus-stack` instead of the old checkout-derived name.

If the old `adsb-logger` Compose project is running, finish any active recording and stop it before starting the new stack so both projects do not compete for the same ports:

```sh
docker compose -p adsb-logger down --remove-orphans
docker compose up -d --build
```

If you previously selected a different project name, use that name with `-p`. For a local editable Python install, reinstall with `pip install -e './services/adsb-logger[dev]'` from the root, or create a fresh environment in the service directory.
