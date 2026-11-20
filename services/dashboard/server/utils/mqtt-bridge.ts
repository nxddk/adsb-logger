import { randomUUID } from 'node:crypto'
import type { MqttClient } from 'mqtt'
import type {
  CommandResponse,
  DashboardState,
  RecorderCommand,
  RecorderStatus,
} from '../../shared/recorder.ts'

export class BridgeError extends Error {
  statusCode: number

  constructor(message: string, statusCode = 503) {
    super(message)
    this.statusCode = statusCode
  }
}

export class MqttBridge {
  private client: MqttClient
  private prefix: string
  private timeoutMs: number
  private defaultInterval: number
  private ready = false
  private availability: DashboardState['availability'] = 'unknown'
  private status: RecorderStatus | null = null
  private pending = new Map<
    string,
    {
      resolve: (response: CommandResponse) => void
      reject: (error: Error) => void
      timer: ReturnType<typeof setTimeout>
    }
  >()

  constructor(
    client: MqttClient,
    prefix: string,
    defaultInterval = 30,
    timeoutMs = 120_000,
  ) {
    this.client = client
    this.prefix = prefix
    this.timeoutMs = timeoutMs
    this.defaultInterval = defaultInterval
    client.on('connect', () => {
      this.ready = false
      this.availability = 'unknown'
      client.subscribe(
        ['status', 'availability', 'response'].map((t) => `${prefix}/${t}`),
        { qos: 1 },
        (error, granted) => {
          this.ready =
            !error &&
            client.connected &&
            !!granted?.length &&
            granted.every((subscription) => subscription.qos !== 128)
        },
      )
    })
    client.on('message', (topic, payload) => {
      if (topic === `${prefix}/availability`) {
        const value = payload.toString()
        if (
          value === 'online' ||
          value === 'offline' ||
          value === 'quota_exhausted'
        ) {
          this.availability = value
        }
        return
      }
      try {
        const value = JSON.parse(payload.toString())
        if (
          topic === `${prefix}/status` &&
          (value?.state === 'idle' || value?.state === 'recording')
        ) {
          this.status = value
        } else if (
          topic === `${prefix}/response` &&
          value &&
          typeof value.ok === 'boolean'
        ) {
          const pending = this.pending.get(value.request_id)
          if (!pending) return
          clearTimeout(pending.timer)
          this.pending.delete(value.request_id)
          if (value.ok && value.status) this.status = value.status
          pending.resolve(value)
        }
      } catch {
        // A malformed broker message must not interrupt subsequent updates.
      }
    })
    client.on('close', () => {
      this.ready = false
      this.availability = 'unknown'
      this.rejectPending(
        'Broker connection lost. Check status before retrying the command.',
      )
    })
    client.on('error', () => {
      // MQTT.js retries automatically. Never expose connection details or credentials.
    })
  }

  state(): DashboardState {
    return {
      brokerConnected: this.client.connected && this.ready,
      availability: this.ready ? this.availability : 'unknown',
      status: this.status,
      defaultInterval: this.defaultInterval,
    }
  }

  command(command: RecorderCommand): Promise<CommandResponse> {
    if (!this.client.connected || !this.ready) {
      return Promise.reject(
        new BridgeError('The dashboard is not connected to the MQTT broker.'),
      )
    }
    if (
      this.availability !== 'online' &&
      this.availability !== 'quota_exhausted'
    ) {
      return Promise.reject(
        new BridgeError(
          'The recording service is offline or has not reported availability.',
        ),
      )
    }
    if (
      command.command === 'start' &&
      this.availability === 'quota_exhausted'
    ) {
      return Promise.reject(
        new BridgeError(
          'OpenSky quota exhausted. New recordings are blocked until the retry time.',
          409,
        ),
      )
    }
    const request_id = randomUUID()
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(request_id)
        reject(
          new BridgeError(
            'No command confirmation received. Check status before retrying.',
            504,
          ),
        )
      }, this.timeoutMs)
      this.pending.set(request_id, { resolve, reject, timer })
      this.client.publish(
        `${this.prefix}/command`,
        JSON.stringify({ ...command, request_id }),
        { qos: 1, retain: false },
        (error) => {
          if (!error || !this.pending.has(request_id)) return
          clearTimeout(timer)
          this.pending.delete(request_id)
          reject(
            new BridgeError(
              'Could not publish the command. Check status before retrying.',
            ),
          )
        },
      )
    })
  }

  private rejectPending(message: string) {
    for (const { reject, timer } of this.pending.values()) {
      clearTimeout(timer)
      reject(new BridgeError(message))
    }
    this.pending.clear()
  }

  async close() {
    this.rejectPending('The dashboard is shutting down.')
    await this.client.endAsync(true)
  }
}
