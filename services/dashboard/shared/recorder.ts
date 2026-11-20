export interface BoundingBox {
  lamin: number
  lomin: number
  lamax: number
  lomax: number
}

export interface RecorderStatus {
  state: 'idle' | 'recording'
  updated_at: string
  session_id: string | null
  file: string | null
  bbox: BoundingBox | null
  interval_seconds: number
  snapshots: number
  aircraft_observations: number
  last_error: string | null
  retry_after_seconds: number
  quota_exhausted: boolean
  quota_retry_at: string | null
  credits_remaining: number | null
  can_record: boolean
}

export interface DashboardState {
  brokerConnected: boolean
  availability: 'online' | 'offline' | 'quota_exhausted' | 'unknown'
  status: RecorderStatus | null
  defaultInterval: number
}

export type RecorderCommand =
  | { command: 'start'; bbox: BoundingBox; interval_seconds?: number }
  | { command: 'stop' | 'status' }

export interface CommandResponse {
  ok: boolean
  request_id: string
  status?: RecorderStatus
  error?: string
  error_code?: string
}

export function formatBoundingBox(bbox: BoundingBox): string {
  return [bbox.lomin, bbox.lamin, bbox.lomax, bbox.lamax].join(',')
}

export function parseBoundingBox(input: string): BoundingBox {
  const parts = input.split(',').map((part) => part.trim())
  if (parts.length !== 4 || parts.some((part) => part === '')) {
    throw new Error(
      'Enter four comma-separated coordinates: west,south,east,north.',
    )
  }
  const bbox = {
    lomin: Number(parts[0]),
    lamin: Number(parts[1]),
    lomax: Number(parts[2]),
    lamax: Number(parts[3]),
  }
  validateCommand({ command: 'start', bbox })
  return bbox
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function validateCommand(value: unknown): RecorderCommand {
  if (!isObject(value)) throw new Error('Send a JSON command object.')
  const allowed =
    value.command === 'start'
      ? ['command', 'bbox', 'interval_seconds']
      : ['command']
  if (Object.keys(value).some((key) => !allowed.includes(key))) {
    throw new Error('Unknown command parameter.')
  }
  if (value.command === 'stop' || value.command === 'status')
    return { command: value.command }
  if (value.command !== 'start')
    throw new Error('Choose start, stop, or status.')
  const box = value.bbox
  const keys = ['lamin', 'lomin', 'lamax', 'lomax']
  if (
    !isObject(box) ||
    Object.keys(box).length !== 4 ||
    keys.some(
      (key) => typeof box[key] !== 'number' || !Number.isFinite(box[key]),
    )
  ) {
    throw new Error(
      'Enter all four bounding box coordinates as finite numbers.',
    )
  }
  const bbox = box as unknown as BoundingBox
  if (!(bbox.lamin >= -90 && bbox.lamin < bbox.lamax && bbox.lamax <= 90)) {
    throw new Error('South must be less than north, within −90° to 90°.')
  }
  if (!(bbox.lomin >= -180 && bbox.lomin < bbox.lomax && bbox.lomax <= 180)) {
    throw new Error('West must be less than east, within −180° to 180°.')
  }
  if (
    value.interval_seconds !== undefined &&
    (typeof value.interval_seconds !== 'number' ||
      !Number.isFinite(value.interval_seconds) ||
      value.interval_seconds < 10)
  )
    throw new Error('The polling interval must be at least 10 seconds.')
  return {
    command: 'start',
    bbox,
    ...(value.interval_seconds === undefined
      ? {}
      : { interval_seconds: value.interval_seconds as number }),
  }
}
