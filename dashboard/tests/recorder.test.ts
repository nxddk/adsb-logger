import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { test } from 'node:test'
import type { MqttClient } from 'mqtt'
import { validateCommand } from '../shared/recorder.ts'
import { MqttBridge } from '../server/utils/mqtt-bridge.ts'

const bbox = { lamin: 43, lomin: -80.5, lamax: 44.5, lomax: -78.5 }

class FakeClient extends EventEmitter {
  connected = false
  messages: { topic: string; payload: string; options: unknown }[] = []
  publishError: Error | null = null

  subscribe(topics: string[], options: unknown, callback: Function) {
    callback(
      null,
      topics.map((topic) => ({ topic, qos: 1 })),
    )
  }

  publish(
    topic: string,
    payload: string,
    options: unknown,
    callback: Function,
  ) {
    this.messages.push({ topic, payload, options })
    callback(this.publishError)
  }

  async endAsync() {
    this.connected = false
  }
  message(topic: string, value: unknown) {
    this.emit(
      'message',
      `recorder/${topic}`,
      Buffer.from(typeof value === 'string' ? value : JSON.stringify(value)),
    )
  }

  online() {
    this.connected = true
    this.emit('connect')
    this.message('availability', 'online')
  }
}

test('start commands validate coordinates, types, and optional intervals', () => {
  assert.deepEqual(validateCommand({ command: 'start', bbox }), {
    command: 'start',
    bbox,
  })
  for (const body of [
    null,
    [],
    { command: 'delete' },
    { command: 'stop', bbox },
    { command: 'start', bbox: { ...bbox, lamin: true } },
    { command: 'start', bbox: { ...bbox, lomin: NaN } },
    { command: 'start', bbox: { ...bbox, lamax: 91 } },
    { command: 'start', bbox: { ...bbox, lomin: -181 } },
    { command: 'start', bbox: { ...bbox, lamin: 45 } },
    { command: 'start', bbox, interval_seconds: 5 },
    { command: 'start', bbox, interval_seconds: '30' },
  ])
    assert.throws(() => validateCommand(body))
})

test('retained status is separate from live availability, and malformed messages are ignored', async () => {
  const client = new FakeClient()
  const bridge = new MqttBridge(client as unknown as MqttClient, 'recorder', 45)
  client.message('status', { state: 'recording', snapshots: 2 })
  assert.equal(bridge.state().brokerConnected, false)
  assert.equal(bridge.state().availability, 'unknown')
  assert.equal(bridge.state().defaultInterval, 45)
  client.online()
  assert.equal(bridge.state().availability, 'online')
  client.emit('message', 'recorder/status', Buffer.from('bad json'))
  assert.equal(bridge.state().status?.snapshots, 2)
  client.connected = false
  client.emit('close')
  assert.equal(bridge.state().availability, 'unknown')
  assert.equal(bridge.state().status?.state, 'recording')
  await assert.rejects(bridge.command({ command: 'stop' }), /not connected/)
  await bridge.close()
})

test('concurrent commands resolve only for their matching request IDs', async () => {
  const client = new FakeClient()
  const bridge = new MqttBridge(client as unknown as MqttClient, 'recorder')
  client.online()
  const first = bridge.command({ command: 'start', bbox })
  const second = bridge.command({ command: 'status' })
  const one = JSON.parse(client.messages[0]!.payload)
  const two = JSON.parse(client.messages[1]!.payload)
  assert.notEqual(one.request_id, two.request_id)
  assert.deepEqual(client.messages[0]!.options, { qos: 1, retain: false })
  client.message('response', { ok: true, request_id: 'unrelated' })
  client.message('response', {
    ok: false,
    request_id: two.request_id,
    error: 'rejected',
  })
  client.message('response', { ok: true, request_id: one.request_id })
  assert.equal((await first).ok, true)
  assert.equal((await second).error, 'rejected')
  await bridge.close()
})

test('offline loggers, publish failures, timeouts, and disconnects reject commands', async () => {
  const client = new FakeClient()
  const bridge = new MqttBridge(
    client as unknown as MqttClient,
    'recorder',
    30,
    20,
  )
  client.online()
  client.message('availability', 'offline')
  await assert.rejects(bridge.command({ command: 'stop' }), /offline/)
  assert.equal(client.messages.length, 0)
  client.message('availability', 'online')
  client.publishError = new Error('test failure')
  await assert.rejects(bridge.command({ command: 'stop' }), /Could not publish/)
  client.publishError = null
  await assert.rejects(
    bridge.command({ command: 'stop' }),
    /No command confirmation/,
  )
  const pending = bridge.command({ command: 'stop' })
  client.connected = false
  client.emit('close')
  await assert.rejects(pending, /connection lost/)
  await bridge.close()
})
