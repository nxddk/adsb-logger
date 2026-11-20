<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { validateCommand } from '#shared/recorder'
import type {
  CommandResponse,
  DashboardState,
  RecorderCommand,
} from '#shared/recorder'

const {
  data,
  error: connectionError,
  refresh,
} = await useFetch<DashboardState>('/api/status')
const form = reactive({
  lamin: 43,
  lamax: 44.5,
  lomin: -80.5,
  lomax: -78.5,
  interval: 30,
})
const pending = ref<string | null>(null)
const message = ref('')
const failure = ref('')
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | undefined
let refreshing = false
const status = computed(() => data.value?.status)
const online = computed(
  () =>
    !connectionError.value &&
    data.value?.brokerConnected &&
    data.value?.availability === 'online',
)
const recording = computed(() => status.value?.state === 'recording')
const stateLabel = computed(() => {
  if (connectionError.value) return 'Dashboard unavailable'
  if (!data.value?.brokerConnected) return 'Broker disconnected'
  if (data.value.availability === 'offline') return 'Logger offline'
  if (!online.value || !status.value) return 'Waiting for logger'
  return recording.value ? 'Recording' : 'Ready to record'
})
const retrySeconds = computed(() =>
  Math.max(
    0,
    Math.ceil(
      (status.value?.retry_after_seconds || 0) -
        Math.max(0, now.value - Date.parse(status.value?.updated_at || '')) /
          1000,
    ),
  ),
)
const count = (value?: number) => (value || 0).toLocaleString('en-US')

watch(
  () => status.value?.session_id,
  (sessionId) => {
    if (sessionId && status.value?.bbox) {
      Object.assign(form, status.value.bbox, {
        interval: status.value.interval_seconds,
      })
    }
  },
  { immediate: true },
)

onMounted(() => {
  form.interval =
    status.value?.interval_seconds || data.value?.defaultInterval || 30
  timer = setInterval(async () => {
    now.value = Date.now()
    if (refreshing) return
    refreshing = true
    try {
      await refresh()
    } finally {
      refreshing = false
    }
  }, 2000)
})
onUnmounted(() => clearInterval(timer))

async function send(command: RecorderCommand) {
  failure.value = ''
  message.value = ''
  try {
    const validated = validateCommand(command)
    pending.value = command.command
    const response = await $fetch<CommandResponse>('/api/command', {
      method: 'POST',
      body: validated,
      retry: 0,
      timeout: 125_000,
    })
    if (!response.ok)
      throw new Error(response.error || 'The logger rejected the command.')
    message.value =
      command.command === 'start'
        ? 'Recording started.'
        : command.command === 'stop'
          ? 'Recording stopped. Your file has been closed.'
          : 'Status refreshed.'
    await refresh()
  } catch (error) {
    const details = error as {
      data?: { statusMessage?: string }
      message?: string
    }
    failure.value =
      details.data?.statusMessage ||
      details.message ||
      'The command could not be confirmed.'
  } finally {
    pending.value = null
  }
}

function start() {
  return send({
    command: 'start',
    bbox: {
      lamin: form.lamin,
      lomin: form.lomin,
      lamax: form.lamax,
      lomax: form.lomax,
    },
    interval_seconds: form.interval,
  })
}

function useToronto() {
  Object.assign(form, { lamin: 43, lamax: 44.5, lomin: -80.5, lomax: -78.5 })
}
</script>

<template>
  <div class="dashboard">
    <header class="topbar">
      <a class="brand" href="/" aria-label="Airspace home">
        <span class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 28 28" fill="none">
            <path
              d="m14 3 3 9 8 4v2l-9-2v6l3 2v1l-5-1-5 1v-1l3-2v-6l-9 2v-2l8-4 3-9Z"
              fill="currentColor"
            />
          </svg>
        </span>
        AIRSPACE<span class="brand-divider">/</span
        ><span class="brand-caption">RECORDER</span>
      </a>
      <span class="connection" :class="{ connected: online }"
        ><i />{{ online ? 'Logger connected' : 'Logger unavailable' }}</span
      >
    </header>

    <main>
      <div class="page-heading">
        <div>
          <p class="eyebrow">OPENSKY NETWORK</p>
          <h1>A window into the airspace.</h1>
          <p class="subtitle">
            Choose an area. Record the aircraft passing through it.
          </p>
        </div>
        <span class="format-tag">JSON LINES</span>
      </div>

      <section
        class="status-strip"
        aria-label="Recorder status"
        aria-live="polite"
      >
        <div class="status-primary">
          <span
            class="status-dot"
            :class="{
              active: online && recording,
              ready: online && !recording,
            }"
          />
          <div>
            <p class="label">RECORDER</p>
            <strong>{{ stateLabel }}</strong>
          </div>
        </div>
        <div class="metric">
          <p class="label">SNAPSHOTS</p>
          <strong>{{ count(status?.snapshots) }}</strong>
        </div>
        <div class="metric">
          <p class="label">AIRCRAFT OBSERVATIONS</p>
          <strong>{{ count(status?.aircraft_observations) }}</strong>
        </div>
        <div class="metric">
          <p class="label">POLL INTERVAL</p>
          <strong
            >{{ status?.interval_seconds || data?.defaultInterval || 30
            }}<small> sec</small></strong
          >
        </div>
      </section>

      <div v-if="!online" class="notice" role="status">
        {{
          connectionError
            ? 'Cannot reach the dashboard server. Reconnecting automatically.'
            : 'Waiting for the recording service. Controls will be available when it connects.'
        }}
        <span v-if="status"
          >The values below are the last reported status.</span
        >
      </div>

      <div class="content-grid">
        <section class="panel control-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">01 / CAPTURE AREA</p>
              <h2>Set your boundaries</h2>
            </div>
            <span class="coordinate-tag">WGS84</span>
          </div>
          <p class="section-description">
            Define a rectangular area using latitude and longitude in decimal
            degrees.
          </p>
          <form @submit.prevent="start">
            <fieldset :disabled="!!pending || recording">
              <legend class="sr-only">Recording configuration</legend>
              <div class="coordinate-grid">
                <label for="north"
                  >North latitude<span>Maximum</span>
                  <div class="input-wrap">
                    <input
                      id="north"
                      v-model.number="form.lamax"
                      type="number"
                      min="-90"
                      max="90"
                      step="any"
                      required
                    /><span>°</span>
                  </div></label
                >
                <label for="south"
                  >South latitude<span>Minimum</span>
                  <div class="input-wrap">
                    <input
                      id="south"
                      v-model.number="form.lamin"
                      type="number"
                      min="-90"
                      max="90"
                      step="any"
                      required
                    /><span>°</span>
                  </div></label
                >
                <label for="west"
                  >West longitude<span>Minimum</span>
                  <div class="input-wrap">
                    <input
                      id="west"
                      v-model.number="form.lomin"
                      type="number"
                      min="-180"
                      max="180"
                      step="any"
                      required
                    /><span>°</span>
                  </div></label
                >
                <label for="east"
                  >East longitude<span>Maximum</span>
                  <div class="input-wrap">
                    <input
                      id="east"
                      v-model.number="form.lomax"
                      type="number"
                      min="-180"
                      max="180"
                      step="any"
                      required
                    /><span>°</span>
                  </div></label
                >
              </div>
              <div class="area-hint">
                <span>Set your area before starting.</span
                ><button type="button" class="text-button" @click="useToronto">
                  Use Toronto example ↗
                </button>
              </div>
              <div class="interval-row">
                <label for="interval"
                  >Polling interval<span
                    >Time between OpenSky requests</span
                  ></label
                >
                <div class="input-wrap interval-input">
                  <input
                    id="interval"
                    v-model.number="form.interval"
                    type="number"
                    min="10"
                    step="any"
                    required
                  /><span>sec</span>
                </div>
              </div>
              <p class="quota-hint">
                Smaller areas and longer intervals use fewer API credits.
              </p>
            </fieldset>
            <div class="control-actions">
              <button
                class="button primary"
                type="submit"
                :disabled="!!pending || !online || !status || recording"
              >
                <span aria-hidden="true">●</span
                >{{ pending === 'start' ? 'Starting…' : 'Start recording' }}
              </button>
              <button
                class="button stop"
                type="button"
                :disabled="!!pending || !online || !recording"
                @click="send({ command: 'stop' })"
              >
                <span aria-hidden="true">■</span
                >{{ pending === 'stop' ? 'Stopping…' : 'Stop' }}
              </button>
            </div>
          </form>
          <div class="feedback" aria-live="polite">
            <p v-if="pending">Waiting for confirmation from the logger…</p>
            <p v-else-if="failure" class="failure" role="alert">
              {{ failure }}
            </p>
            <p v-else-if="message" class="success">{{ message }}</p>
            <p v-else>
              One recording session at a time. Files are saved automatically.
            </p>
          </div>
        </section>

        <section class="panel session-panel">
          <div class="panel-heading">
            <div>
              <p class="eyebrow">02 / SESSION MONITOR</p>
              <h2>
                {{ recording ? 'Current recording' : 'Session overview' }}
              </h2>
            </div>
            <span class="live-tag" :class="{ muted: !online }"
              ><i />{{ online ? 'LIVE' : 'OFFLINE' }}</span
            >
          </div>
          <div
            class="session-state"
            :class="{ recording: recording && online }"
          >
            <span class="session-symbol" aria-hidden="true">{{
              recording ? '◉' : '◎'
            }}</span>
            <h3>
              {{
                recording
                  ? online
                    ? 'Capture in progress'
                    : 'Last seen recording'
                  : 'Standing by'
              }}
            </h3>
            <p>
              {{
                recording
                  ? 'Each poll is appended to your session file.'
                  : 'Start a recording to begin collecting aircraft snapshots.'
              }}
            </p>
          </div>
          <dl class="session-details">
            <div>
              <dt>Session ID</dt>
              <dd class="mono">{{ status?.session_id || '—' }}</dd>
            </div>
            <div>
              <dt>Output file</dt>
              <dd class="mono">
                {{ status?.file || 'Created when recording starts' }}
              </dd>
            </div>
            <div>
              <dt>Active area</dt>
              <dd v-if="status?.bbox" class="mono">
                {{ status.bbox.lamin }}° to {{ status.bbox.lamax }}° latitude<br />{{
                  status.bbox.lomin
                }}° to {{ status.bbox.lomax }}° longitude
              </dd>
              <dd v-else>—</dd>
            </div>
            <div>
              <dt>Last logger update</dt>
              <dd>
                <ClientOnly
                  >{{
                    status?.updated_at
                      ? new Date(status.updated_at).toLocaleString()
                      : 'Waiting for status'
                  }}<template #fallback>—</template></ClientOnly
                >
              </dd>
            </div>
          </dl>
          <div v-if="status?.last_error" class="api-error" role="status">
            <strong>{{
              recording
                ? 'Data collection interrupted'
                : 'Logger reported an error'
            }}</strong>
            <p>{{ status.last_error }}</p>
            <p v-if="recording && retrySeconds > 0">
              Retrying in approximately {{ retrySeconds }} seconds.
            </p>
          </div>
          <button
            class="refresh-button"
            type="button"
            :disabled="!!pending || !online"
            @click="send({ command: 'status' })"
          >
            <span aria-hidden="true">↻</span>
            {{ pending === 'status' ? 'Refreshing…' : 'Refresh status' }}
          </button>
        </section>
      </div>
      <footer>
        <span
          >ADS-B LOGGER <span class="footer-dot">·</span> OPENSKY STATE
          VECTORS</span
        ><span>Status updates every 2 seconds</span>
      </footer>
    </main>
  </div>
</template>
