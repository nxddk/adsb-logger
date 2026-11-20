# Dashboard

The Argus Stack web dashboard is a Nuxt application with a server-side MQTT bridge. It currently provides ADS-B start/stop controls, bounding-box and polling-interval inputs, live status, recording counts, and database/error details. The Python recorder owns recording state and the SQLite database; the dashboard waits for MQTT acknowledgements before reporting command success.

## Run

From the stack root:

```sh
docker compose up -d --build
```

Open **http://localhost:3000**. `DASHBOARD_PORT` and `DASHBOARD_BIND_ADDRESS` in the root `.env` control host access. The default binding is `127.0.0.1`. Setting the address to `0.0.0.0` allows access from other machines; there is no login, so use this only on a trusted network.

Paste the bounding box into the single field as `west,south,east,north` (longitude, latitude, longitude, latitude), for example:

```text
-73.617067,45.471297,-73.576427,45.489201
```

Whitespace around commas is accepted. The dashboard validates bounds and converts this input into the recorder's named-coordinate MQTT payload.

A live calculator below the BBOX shows its area in square degrees and credits per query: ≤25 °² costs 1, >25–100 costs 2, >100–400 costs 3, and >400 costs 4. It also estimates hourly/daily use at the entered polling interval. These are local calculations and make no OpenSky requests. Totals assume continuous successful polling; request duration and retries affect actual usage. Invalid bounds suppress the estimate until corrected.

The session monitor shows the SQLite database path and session ID. All snapshots live in the recorder's shared database; the dashboard does not need a database container or filesystem mount.

The server uses the same `MQTT_*` settings as the ADS-B service, with its own generated MQTT client ID. Broker credentials stay on the server and are never sent to the browser. `INTERVAL_SECONDS` sets the form's default polling interval. For a private broker CA, mount the certificate read-only into both the dashboard and recorder and set `MQTT_CA` to its container path.

`GET /api/status` returns cached MQTT status and service availability. The browser polls it every two seconds without making additional OpenSky requests. `POST /api/command` accepts `start`, `stop`, and `status` JSON commands. It validates inputs, generates a request ID, and waits for the matching logger acknowledgement. Start commands accept the same `bbox` and optional `interval_seconds` parameters documented by the [ADS-B service](../adsb-logger/README.md). Commands are disabled while the logger is offline; retained values remain visible as last reported status.

Availability `quota_exhausted` means the logger is still connected but has automatically stopped recording to protect its OpenSky credits. The dashboard displays the last reported credit balance and retry time and disables Start. Status refresh and Stop remain usable. When the cooldown ends, a new recording must be started explicitly; the old session never resumes automatically.

## Development

Run container checks from the root:

```sh
docker compose build dashboard-tests
docker compose run --rm dashboard-tests
```

The test image runs MQTT bridge/validation unit tests and Nuxt type checking. Building the runtime image also verifies the production bundle.

For local development with Node 24, start the other services from the root, then use this directory:

```sh
docker compose up -d mqtt adsb-logger
cd services/dashboard
npm ci
MQTT_HOST=localhost npm run dev
```

Set matching `MQTT_PORT` and `MQTT_TOPIC_PREFIX` values if you customized the broker. Local Nuxt development does not automatically load the stack root's `.env`; production Compose passes the shared settings at runtime. Stop the containerized dashboard with `docker compose stop dashboard` before using port 3000 for local development.

## Layout

- `app/`: Vue dashboard and styles.
- `server/`: HTTP API, MQTT connection, and command correlation.
- `shared/`: status types and command validation.
- `tests/`: server bridge and validation tests.
- `Dockerfile`: build, test, and non-root runtime stages.
