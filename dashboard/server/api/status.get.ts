import { getRecorderBridge } from '../utils/recorder'

export default defineEventHandler((event) => {
  setHeader(event, 'Cache-Control', 'no-store')
  return getRecorderBridge().state()
})
