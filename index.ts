import mineflayer from 'mineflayer'
import { createStreamingViewer } from './curiosity/viewer/streaming'

const STREAM_WIDTH = 736
const STREAM_HEIGHT = 448
const STREAM_FPS = 30
const STREAM_PORT = 3007

const bot = mineflayer.createBot({
  host: 'localhost',
  username: 'Curiosity',
  auth: 'offline'
})

bot.once('spawn', async () => {
  const viewer = await createStreamingViewer(bot, {
    width: STREAM_WIDTH,
    height: STREAM_HEIGHT,
    fps: STREAM_FPS,
    port: STREAM_PORT,
    viewDistance: 6
  })
  console.log(`Streaming viewer started at http://localhost:${STREAM_PORT}`)
  console.log(`Resolution: ${STREAM_WIDTH}x${STREAM_HEIGHT} @ ${STREAM_FPS}fps`)
})

bot.on('chat', (username, message) => {
  if (username === bot.username) return
  bot.chat(message)
})

bot.on('kicked', console.log)
bot.on('error', console.log)
