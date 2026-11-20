# MQTT broker

Argus Stack uses the official `eclipse-mosquitto:2` image. This directory owns its `mosquitto.conf`; the root Compose file mounts it read-only. A custom broker image is unnecessary.

From the stack root:

```sh
docker compose up -d mqtt
docker compose logs -f mqtt
```

Other containers connect to `mqtt:1883` on the stack's Docker network. The host listener defaults to `127.0.0.1:1883`; `MQTT_BIND_ADDRESS` and `MQTT_PUBLISHED_PORT` in the root `.env` control that published address. They do not change the internal port.

The included configuration allows anonymous connections and disables persistence. It is intended for local stack communication. Configure authentication and TLS before exposing the broker to an untrusted network. Setting `MQTT_USERNAME` or `MQTT_PASSWORD` in `.env` configures clients, not the broker's access policy.

The Compose health check publishes an empty message to `__health/mqtt`. Dependent services wait for this check before starting. Changes to the config require `docker compose restart mqtt`.

Inspect ADS-B messages with the included client:

```sh
docker compose exec mqtt mosquitto_sub -h localhost -t 'adsb-logger/#' -v
```

Replace the topic prefix if you changed `MQTT_TOPIC_PREFIX`. The broker itself is shared infrastructure and is not tied to ADS-B topics. New services can use their own topic namespaces through the same broker.
