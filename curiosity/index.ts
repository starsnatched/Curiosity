import mineflayer from 'mineflayer'
import { mineflayer as mineflayerViewer } from 'prismarine-viewer'

const bot = mineflayer.createBot({
  host: 'localhost',
  username: 'Curiosity',
  auth: 'offline'
})

bot.once('spawn', () => {
  mineflayerViewer(bot, { port: 3007, firstPerson: true })
  console.log('Viewer started at http://localhost:3007')
})

bot.on('chat', (username, message) => {
  if (username === bot.username) return
  bot.chat(message)
})

bot.on('kicked', console.log)
bot.on('error', console.log)
