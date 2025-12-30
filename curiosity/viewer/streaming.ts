import type { Bot } from 'mineflayer'
import { EventEmitter } from 'events'
import { chromium, type Browser, type Page, type CDPSession } from 'playwright'
import { mineflayer as prismarineViewer } from 'prismarine-viewer'
import http from 'http'

interface StreamingViewerOptions {
  width: number
  height: number
  fps: number
  port: number
  viewDistance?: number
}

interface StreamingViewer extends EventEmitter {
  close: () => Promise<void>
  getStats: () => { frameCount: number; avgCaptureTime: number; activeClients: number }
}

const INTERNAL_VIEWER_PORT = 13337

export async function createStreamingViewer(
  bot: Bot,
  options: StreamingViewerOptions
): Promise<StreamingViewer> {
  const {
    width,
    height,
    fps,
    port,
    viewDistance = 6
  } = options

  const emitter = new EventEmitter() as StreamingViewer

  prismarineViewer(bot, {
    port: INTERNAL_VIEWER_PORT,
    firstPerson: true,
    viewDistance
  })

  await new Promise(resolve => setTimeout(resolve, 2000))

  const browser: Browser = await chromium.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--enable-gpu',
      '--use-gl=egl',
      '--enable-webgl',
      '--ignore-gpu-blocklist'
    ]
  })

  const context = await browser.newContext({
    viewport: { width, height }
  })

  const page: Page = await context.newPage()
  const cdpSession: CDPSession = await page.context().newCDPSession(page)

  await page.goto(`http://localhost:${INTERNAL_VIEWER_PORT}`, {
    waitUntil: 'networkidle',
    timeout: 30000
  })

  await page.evaluate(() => {
    const controls = document.querySelector('.dg.ac')
    if (controls) {
      (controls as HTMLElement).style.display = 'none'
    }
  })

  await new Promise(resolve => setTimeout(resolve, 3000))

  const mjpegClients: Set<http.ServerResponse> = new Set()
  let frameCount = 0
  let totalCaptureTime = 0
  let isRunning = true
  let latestFrame: Buffer | null = null
  let lastFrameTime = 0

  cdpSession.on('Page.screencastFrame', async (params: { data: string; sessionId: number; metadata: { timestamp?: number } }) => {
    if (!isRunning) return

    const now = performance.now()
    const captureTime = now - lastFrameTime
    lastFrameTime = now

    if (frameCount > 0) {
      totalCaptureTime += captureTime
    }
    frameCount++

    latestFrame = Buffer.from(params.data, 'base64')

    await cdpSession.send('Page.screencastFrameAck', { sessionId: params.sessionId })

    if (mjpegClients.size > 0 && latestFrame) {
      const boundary = '--mjpegboundary'
      const header = `${boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: ${latestFrame.length}\r\n\r\n`

      for (const client of mjpegClients) {
        try {
          client.write(header)
          client.write(latestFrame)
          client.write('\r\n')
        } catch {
          mjpegClients.delete(client)
        }
      }
    }
  })

  await cdpSession.send('Page.startScreencast', {
    format: 'jpeg',
    quality: 70,
    maxWidth: width,
    maxHeight: height,
    everyNthFrame: 1
  })

  lastFrameTime = performance.now()

  const app = (await import('express')).default()

  app.get('/', (_req, res) => {
    res.setHeader('Content-Type', 'text/html')
    res.send(`<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Curiosity - Minecraft Bot Stream</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap');
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      background: linear-gradient(135deg, #0c0c0c 0%, #1a1a2e 50%, #16213e 100%);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      font-family: 'Space Mono', monospace;
      color: #e0e0e0;
      overflow: hidden;
    }
    body::before {
      content: '';
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: 
        radial-gradient(ellipse at 20% 80%, rgba(120, 0, 255, 0.15) 0%, transparent 50%),
        radial-gradient(ellipse at 80% 20%, rgba(255, 0, 150, 0.1) 0%, transparent 50%),
        radial-gradient(ellipse at 50% 50%, rgba(0, 255, 200, 0.05) 0%, transparent 70%);
      pointer-events: none;
      z-index: 0;
    }
    .container {
      position: relative;
      z-index: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 20px;
    }
    .header {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    h1 {
      font-size: 1.8rem;
      font-weight: 700;
      background: linear-gradient(90deg, #00ffd5, #7b2dff, #ff007b);
      background-size: 200% auto;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      animation: gradient-shift 3s ease infinite;
      text-transform: uppercase;
      letter-spacing: 0.2em;
    }
    @keyframes gradient-shift {
      0%, 100% { background-position: 0% center; }
      50% { background-position: 100% center; }
    }
    .stream-container {
      position: relative;
    }
    .stream-frame {
      position: relative;
      padding: 3px;
      background: linear-gradient(135deg, #00ffd5, #7b2dff, #ff007b, #00ffd5);
      background-size: 300% 300%;
      animation: border-dance 4s ease infinite;
      border-radius: 4px;
    }
    @keyframes border-dance {
      0%, 100% { background-position: 0% 50%; }
      50% { background-position: 100% 50%; }
    }
    .stream-inner {
      background: #0a0a0a;
      border-radius: 2px;
      overflow: hidden;
    }
    img {
      display: block;
      width: ${width}px;
      height: ${height}px;
      background: #0a0a0a;
    }
    .scanlines {
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        rgba(0, 0, 0, 0.1) 2px,
        rgba(0, 0, 0, 0.1) 4px
      );
      pointer-events: none;
      border-radius: 4px;
    }
    .stats-bar {
      display: flex;
      gap: 32px;
      padding: 12px 24px;
      background: rgba(0, 0, 0, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 4px;
      backdrop-filter: blur(10px);
    }
    .stat {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.15em;
      color: rgba(255, 255, 255, 0.6);
    }
    .stat-value {
      color: #00ffd5;
      font-weight: 700;
    }
    .live-indicator {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .live-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #ff0050;
      box-shadow: 0 0 12px #ff0050;
      animation: live-pulse 1.5s ease-in-out infinite;
    }
    @keyframes live-pulse {
      0%, 100% { transform: scale(1); opacity: 1; }
      50% { transform: scale(1.2); opacity: 0.7; }
    }
    .live-text {
      color: #ff0050;
      font-weight: 700;
      font-size: 0.8rem;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Curiosity</h1>
    </div>
    <div class="stream-container">
      <div class="stream-frame">
        <div class="stream-inner">
          <img src="/stream" alt="Minecraft Bot Stream">
        </div>
        <div class="scanlines"></div>
      </div>
    </div>
    <div class="stats-bar">
      <div class="stat live-indicator">
        <div class="live-dot"></div>
        <span class="live-text">LIVE</span>
      </div>
      <div class="stat">
        <span>Resolution</span>
        <span class="stat-value">${width}×${height}</span>
      </div>
      <div class="stat">
        <span>Target</span>
        <span class="stat-value">${fps} FPS</span>
      </div>
      <div class="stat">
        <span>Bot</span>
        <span class="stat-value">${bot.username}</span>
      </div>
    </div>
  </div>
</body>
</html>`)
  })

  app.get('/stream', (req, res) => {
    res.setHeader('Content-Type', 'multipart/x-mixed-replace; boundary=--mjpegboundary')
    res.setHeader('Cache-Control', 'no-cache, no-store, must-revalidate')
    res.setHeader('Pragma', 'no-cache')
    res.setHeader('Expires', '0')
    res.setHeader('Connection', 'keep-alive')

    mjpegClients.add(res)
    emitter.emit('client-connected', { total: mjpegClients.size })

    req.on('close', () => {
      mjpegClients.delete(res)
      emitter.emit('client-disconnected', { total: mjpegClients.size })
    })
  })

  app.get('/stats', (_req, res) => {
    const actualFps = frameCount > 1 ? 1000 / (totalCaptureTime / (frameCount - 1)) : 0
    res.json({
      frameCount,
      avgCaptureTime: frameCount > 1 ? totalCaptureTime / (frameCount - 1) : 0,
      actualFps: Math.round(actualFps * 10) / 10,
      activeClients: mjpegClients.size,
      targetFps: fps,
      resolution: { width, height },
      botUsername: bot.username
    })
  })

  const server = http.createServer(app)

  await new Promise<void>((resolve) => {
    server.listen(port, () => resolve())
  })

  emitter.close = async () => {
    isRunning = false

    await cdpSession.send('Page.stopScreencast')

    for (const client of mjpegClients) {
      try {
        client.end()
      } catch {
        // ignore
      }
    }
    mjpegClients.clear()

    await context.close()
    await browser.close()
    server.close()

    const botWithViewer = bot as Bot & { viewer?: { close: () => void } }
    if (botWithViewer.viewer && typeof botWithViewer.viewer.close === 'function') {
      botWithViewer.viewer.close()
    }
  }

  emitter.getStats = () => ({
    frameCount,
    avgCaptureTime: frameCount > 1 ? totalCaptureTime / (frameCount - 1) : 0,
    activeClients: mjpegClients.size
  })

  bot.on('end', async () => {
    await emitter.close()
  })

  return emitter
}
