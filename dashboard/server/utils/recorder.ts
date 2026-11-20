import { readFileSync } from 'node:fs'
import { connect } from 'mqtt'
import { MqttBridge } from './mqtt-bridge'

let bridge: MqttBridge | undefined

export function getRecorderBridge(): MqttBridge {
  if (bridge) return bridge
  const port = Number(process.env.MQTT_PORT || 1883)
  const prefix = (process.env.MQTT_TOPIC_PREFIX || 'adsb-logger').replace(
    /^\/+|\/+$/g,
    '',
  )
  const interval = Number(process.env.INTERVAL_SECONDS || 30)
  const tls = (process.env.MQTT_TLS || 'false').toLowerCase()
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error('Invalid MQTT_PORT')
  if (!prefix || /[+#\0]/.test(prefix))
    throw new Error('Invalid MQTT_TOPIC_PREFIX')
  if (!Number.isFinite(interval) || interval < 10)
    throw new Error('Invalid INTERVAL_SECONDS')
  if (!['true', 'false', '1', '0', 'yes', 'no'].includes(tls))
    throw new Error('Invalid MQTT_TLS')
  const secure = ['true', '1', 'yes'].includes(tls) || !!process.env.MQTT_CA
  // Read credentials directly from the runtime environment so numeric-looking
  // passwords remain exact strings. Nothing here is included in the client bundle.
  const client = connect({
    host: process.env.MQTT_HOST || 'localhost',
    port,
    protocol: secure ? 'mqtts' : 'mqtt',
    username: process.env.MQTT_USERNAME || undefined,
    password: process.env.MQTT_PASSWORD || undefined,
    ca: process.env.MQTT_CA ? readFileSync(process.env.MQTT_CA) : undefined,
    reconnectPeriod: 2000,
    connectTimeout: 10_000,
    clean: true,
    queueQoSZero: false,
  })
  bridge = new MqttBridge(client, prefix, interval)
  return bridge
}
