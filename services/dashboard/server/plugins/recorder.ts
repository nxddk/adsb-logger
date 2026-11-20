import { getRecorderBridge } from '../utils/recorder'

export default defineNitroPlugin((nitroApp) => {
  const bridge = getRecorderBridge()
  nitroApp.hooks.hook('close', () => bridge.close())
})
