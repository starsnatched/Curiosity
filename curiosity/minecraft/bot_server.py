from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from curiosity.minecraft.bot import BotConfig, MinecraftBot

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BotStreamManager:
    def __init__(self) -> None:
        self._bot: MinecraftBot | None = None
        self._clients: set[WebSocket] = set()
        self._streaming = False
        self._stream_task: asyncio.Task | None = None
        self._bot_task: asyncio.Task | None = None

    async def start_bot(
        self,
        host: str = "localhost",
        port: int = 25565,
        username: str = "StreamBot",
    ) -> bool:
        if self._bot and self._bot.running:
            return True

        config = BotConfig(
            host=host,
            port=port,
            username=username,
            auto_reconnect=True,
        )
        self._bot = MinecraftBot(config)

        self._bot.on("join", self._on_bot_join)
        self._bot.on("spawn", self._on_bot_spawn)
        self._bot.on("health", self._on_bot_health)
        self._bot.on("death", self._on_bot_death)
        self._bot.on("disconnect", self._on_bot_disconnect)

        self._bot_task = asyncio.create_task(self._bot.run())
        return True

    async def stop_bot(self) -> None:
        if self._bot:
            await self._bot.disconnect()
        if self._bot_task:
            self._bot_task.cancel()
            try:
                await self._bot_task
            except asyncio.CancelledError:
                pass

    async def _on_bot_join(self, player) -> None:
        await self._broadcast({"type": "event", "event": "join", "player": player.username})

    async def _on_bot_spawn(self, position) -> None:
        await self._broadcast({
            "type": "event",
            "event": "spawn",
            "position": {"x": position.x, "y": position.y, "z": position.z},
        })

    async def _on_bot_health(self, health: float, food: int) -> None:
        await self._broadcast({
            "type": "event",
            "event": "health",
            "health": health,
            "food": food,
        })

    async def _on_bot_death(self) -> None:
        await self._broadcast({"type": "event", "event": "death"})

    async def _on_bot_disconnect(self) -> None:
        await self._broadcast({"type": "event", "event": "disconnect"})

    async def add_client(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        logger.info(f"Client connected. Total clients: {len(self._clients)}")

        if not self._streaming and len(self._clients) == 1:
            await self._start_streaming()

    def remove_client(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)
        logger.info(f"Client disconnected. Total clients: {len(self._clients)}")

        if self._streaming and len(self._clients) == 0:
            self._stop_streaming()

    async def _start_streaming(self) -> None:
        self._streaming = True
        self._stream_task = asyncio.create_task(self._stream_loop())
        logger.info("Started state streaming")

    def _stop_streaming(self) -> None:
        self._streaming = False
        if self._stream_task:
            self._stream_task.cancel()
            self._stream_task = None
        logger.info("Stopped state streaming")

    async def _stream_loop(self) -> None:
        while self._streaming and self._clients:
            try:
                if self._bot:
                    state = self._bot.get_state_dict()
                    await self._broadcast({"type": "state", "data": state})
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Stream error: {e}")
                await asyncio.sleep(0.1)

    async def _broadcast(self, message: dict) -> None:
        if not self._clients:
            return

        message_str = json.dumps(message)
        disconnected = set()

        for client in self._clients:
            try:
                await client.send_text(message_str)
            except Exception:
                disconnected.add(client)

        for client in disconnected:
            self._clients.discard(client)

    async def handle_input(self, websocket: WebSocket, data: dict) -> None:
        action = data.get("action", "")
        success = False

        if action == "connect":
            host = data.get("host", "localhost")
            port = data.get("port", 25565)
            username = data.get("username", "StreamBot")
            try:
                await self.start_bot(host, port, username)
                await websocket.send_text(json.dumps({
                    "type": "ack",
                    "action": action,
                    "success": True,
                }))
            except Exception as e:
                logger.error(f"Connection error: {e}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "action": action,
                    "error": str(e),
                }))
            return

        if not self._bot:
            await websocket.send_text(json.dumps({
                "type": "error",
                "error": "Bot not connected",
            }))
            return

        try:
            if action == "move_forward":
                await self._bot.move_forward(data.get("start", True))
                success = True
            elif action == "move_backward":
                await self._bot.move_backward(data.get("start", True))
                success = True
            elif action == "move_left":
                await self._bot.move_left(data.get("start", True))
                success = True
            elif action == "move_right":
                await self._bot.move_right(data.get("start", True))
                success = True
            elif action == "jump":
                await self._bot.jump()
                success = True
            elif action == "sneak":
                await self._bot.sneak(data.get("start", True))
                success = True
            elif action == "sprint":
                await self._bot.sprint(data.get("start", True))
                success = True
            elif action == "look":
                yaw = data.get("yaw", 0)
                pitch = data.get("pitch", 0)
                await self._bot.look(yaw, pitch)
                success = True
            elif action == "look_relative":
                yaw_delta = data.get("yaw_delta", 0)
                pitch_delta = data.get("pitch_delta", 0)
                await self._bot.look_relative(yaw_delta, pitch_delta)
                success = True
            elif action == "attack":
                await self._bot.attack()
                success = True
            elif action == "use_item":
                await self._bot.use_item()
                success = True
            elif action == "select_slot":
                slot = data.get("slot", 0)
                await self._bot.select_slot(slot)
                success = True
            elif action == "chat":
                message = data.get("message", "")
                await self._bot.chat(message)
                success = True
            elif action == "respawn":
                await self._bot.respawn()
                success = True
            elif action == "get_state":
                state = self._bot.get_state_dict()
                await websocket.send_text(json.dumps({"type": "state", "data": state}))
                return
            elif action == "disconnect":
                await self.stop_bot()
                success = True

            await websocket.send_text(json.dumps({
                "type": "ack",
                "action": action,
                "success": success,
            }))
        except Exception as e:
            logger.error(f"Input handling error: {e}")
            await websocket.send_text(json.dumps({
                "type": "error",
                "action": action,
                "error": str(e),
            }))

    def close(self) -> None:
        self._stop_streaming()


bot_manager: BotStreamManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global bot_manager
    bot_manager = BotStreamManager()
    logger.info("Bot streaming server initialized")
    yield
    if bot_manager:
        await bot_manager.stop_bot()
        bot_manager.close()
    logger.info("Bot streaming server shutdown")


app = FastAPI(
    title="Minecraft Bot Streaming Server",
    description="Control a Minecraft bot remotely",
    version="1.0.0",
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "bot.html")


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "bot_connected": bot_manager._bot.running if bot_manager and bot_manager._bot else False}


@app.get("/bot-state")
async def bot_state() -> dict:
    if not bot_manager or not bot_manager._bot:
        return {"error": "Bot not initialized"}
    return bot_manager._bot.get_state_dict()


@app.post("/connect")
async def connect_bot(
    host: str = "localhost",
    port: int = 25565,
    username: str = "StreamBot",
) -> dict:
    if not bot_manager:
        return {"success": False, "error": "Server not initialized"}

    success = await bot_manager.start_bot(host, port, username)
    return {"success": success}


@app.post("/disconnect")
async def disconnect_bot() -> dict:
    if not bot_manager:
        return {"success": False, "error": "Server not initialized"}

    await bot_manager.stop_bot()
    return {"success": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    if not bot_manager:
        await websocket.close(code=1011, reason="Server not initialized")
        return

    await bot_manager.add_client(websocket)

    try:
        while True:
            message = await websocket.receive_text()
            try:
                data = json.loads(message)
                await bot_manager.handle_input(websocket, data)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "error": "Invalid JSON",
                }))
    except WebSocketDisconnect:
        bot_manager.remove_client(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        bot_manager.remove_client(websocket)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Minecraft Bot Streaming Server")
    parser.add_argument("--host", default="0.0.0.0", help="Server bind host")
    parser.add_argument("--port", type=int, default=8766, help="Server port")
    parser.add_argument("--mc-host", default="localhost", help="Minecraft server host")
    parser.add_argument("--mc-port", type=int, default=25565, help="Minecraft server port")
    parser.add_argument("--username", default="StreamBot", help="Bot username")
    args = parser.parse_args()

    def signal_handler(sig, frame) -> None:
        logger.info("Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info(f"Starting Minecraft Bot Streaming Server on http://{args.host}:{args.port}")
    uvicorn.run(
        "curiosity.minecraft.bot_server:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()

