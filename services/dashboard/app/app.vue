<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import {
  formatBoundingBox,
  estimateBboxCredits,
  parseBoundingBox,
  validateCommand,
} from '#shared/recorder'
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
const exampleBBox = '-73.617067,45.471297,-73.576427,45.489201'
const form = reactive({
  bbox: exampleBBox,
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
    ['online', 'quota_exhausted'].includes(data.value?.availability || ''),
)
const quotaExhausted = computed(
  () =>
    data.value?.availability === 'quota_exhausted' ||
    !!status.value?.quota_exhausted,
)
const recording = computed(() => status.value?.state === 'recording')
const stateLabel = computed(() => {
  if (connectionError.value) return 'Dashboard unavailable'
  if (!data.value?.brokerConnected) return 'Broker disconnected'
  if (data.value.availability === 'offline') return 'Logger offline'
  if (!online.value || !status.value) return 'Waiting for logger'
  if (quotaExhausted.value) return 'Quota exhausted'
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
const creditEstimate = computed(() => {
  try {
    return estimateBboxCredits(parseBoundingBox(form.bbox))
  } catch {
    return null
  }
})
const creditRate = computed(() => {
  if (
    !creditEstimate.value ||
    !Number.isFinite(form.interval) ||
    form.interval < 10
  )
    return null
  const perHour = (creditEstimate.value.credits * 3600) / form.interval
  return { perHour, perDay: perHour * 24 }
})
const estimateNumber = (value: number) =>
  value.toLocaleString('en-US', { maximumFractionDigits: 1 })

watch(
  () => status.value?.session_id,
  (sessionId) => {
    if (sessionId && status.value?.bbox) {
      Object.assign(form, {
        bbox: formatBoundingBox(status.value.bbox),
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
          ? 'Recording stopped. Your session has been saved.'
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
  try {
    return send({
      command: 'start',
      bbox: parseBoundingBox(form.bbox),
      interval_seconds: form.interval,
    })
  } catch (error) {
    message.value = ''
    failure.value = (error as Error).message
  }
}

function useExample() {
  form.bbox = exampleBBox
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
        <span class="format-tag">SQLITE</span>
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
              ready: online && !recording && !quotaExhausted,
              exhausted: online && quotaExhausted,
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

      <div v-else-if="quotaExhausted" class="notice" role="status">
        <strong>OpenSky quota exhausted. Recording has stopped.</strong>
        <span
          >New recordings are blocked until
          <ClientOnly
            >{{
              status?.quota_retry_at
                ? new Date(status.quota_retry_at).toLocaleString()
                : 'the retry time'
            }}<template #fallback>the retry time</template></ClientOnly
          >. Start a new recording after that time; capture will not resume
          automatically.
        </span>
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
            Paste the bounding box as longitude, latitude, longitude, latitude
            in decimal degrees.
          </p>
          <form @submit.prevent="start">
            <fieldset :disabled="!!pending || recording">
              <legend class="sr-only">Recording configuration</legend>
              <label for="bbox">Bounding box</label>
              <p id="bbox-format" class="bbox-format">west,south,east,north</p>
              <textarea
                id="bbox"
                v-model="form.bbox"
                class="bbox-input"
                :placeholder="exampleBBox"
                rows="2"
                spellcheck="false"
                autocomplete="off"
                aria-describedby="bbox-format"
                required
              />
              <div class="area-hint">
                <span>Set your area before starting.</span
                ><button type="button" class="text-button" @click="useExample">
                  Use example ↗
                </button>
              </div>
              <div
                v-if="creditEstimate"
                class="credit-estimate"
                aria-live="polite"
              >
                <div class="estimate-heading">
                  <strong
                    >{{ creditEstimate.credits }}
                    {{ creditEstimate.credits === 1 ? 'credit' : 'credits' }} /
                    query</strong
                  >
                  <span
                    >{{
                      creditEstimate.area.toLocaleString('en-US', {
                        maximumSignificantDigits: 4,
                      })
                    }}
                    °²</span
                  >
                </div>
                <p v-if="creditRate">
                  Approximately
                  {{ estimateNumber(creditRate.perHour) }} credits/hour ·
                  {{ estimateNumber(creditRate.perDay) }} credits/day at
                  {{ form.interval }} sec.
                </p>
                <p>
                  Based on BBOX area, not aircraft count. Estimates assume
                  continuous successful polling.
                </p>
              </div>
              <p v-else class="estimate-placeholder" aria-live="polite">
                Enter a valid BBOX to estimate credit use.
              </p>
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
                ≤25 °²: 1 credit · ≤100: 2 · ≤400: 3 · larger: 4 per query.
              </p>
            </fieldset>
            <div class="control-actions">
              <button
                class="button primary"
                type="submit"
                :disabled="
                  !!pending || !online || !status || recording || quotaExhausted
                "
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
              One recording session at a time. Snapshots are saved
              automatically.
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
                quotaExhausted && online
                  ? 'Quota exhausted'
                  : recording
                    ? online
                      ? 'Capture in progress'
                      : 'Last seen recording'
                    : 'Standing by'
              }}
            </h3>
            <p>
              {{
                quotaExhausted && online
                  ? 'Your recording was closed. Start a new session after the quota cooldown.'
                  : recording
                    ? 'Each poll is saved to the recording database.'
                    : 'Start a recording to begin collecting aircraft snapshots.'
              }}
            </p>
          </div>
          <dl class="session-details">
            <div>
              <dt>OpenSky credits remaining</dt>
              <dd>{{ status?.credits_remaining ?? 'Not reported yet' }}</dd>
            </div>
            <div>
              <dt>Session ID</dt>
              <dd class="mono">{{ status?.session_id || '—' }}</dd>
            </div>
            <div>
              <dt>SQLite database</dt>
              <dd class="mono">
                {{
                  status?.database ||
                  status?.file ||
                  'Created when recording starts'
                }}
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
