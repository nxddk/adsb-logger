import { validateCommand } from '#shared/recorder'
import { BridgeError } from '../utils/mqtt-bridge'
import { getRecorderBridge } from '../utils/recorder'

export default defineEventHandler(async (event) => {
  const origin = getHeader(event, 'origin')
  if (origin && origin !== getRequestURL(event).origin) {
    throw createError({
      statusCode: 403,
      statusMessage: 'Commands must come from this dashboard.',
    })
  }
  if (
    getHeader(event, 'content-type')?.split(';')[0]?.trim() !==
    'application/json'
  ) {
    throw createError({
      statusCode: 415,
      statusMessage: 'Use application/json.',
    })
  }
  const body = await readBody(event)
  let command
  try {
    command = validateCommand(body)
  } catch (error) {
    throw createError({
      statusCode: 400,
      statusMessage: (error as Error).message,
    })
  }
  try {
    return await getRecorderBridge().command(command)
  } catch (error) {
    throw createError({
      statusCode: error instanceof BridgeError ? error.statusCode : 503,
      statusMessage:
        error instanceof BridgeError
          ? error.message
          : 'The recording service is unavailable.',
    })
  }
})
