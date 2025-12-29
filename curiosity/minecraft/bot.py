from __future__ import annotations

import asyncio
import logging
import math
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from curiosity.minecraft.protocol import (
    ChunkData,
    ChunkSection,
    ConnectionState,
    MinecraftProtocol,
    PacketBuffer,
    PlayPacketIds,
    PlayerState,
    Position,
    VarInt,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class WorldState:
    spawn_position: tuple[int, int, int] = (0, 64, 0)
    time_of_day: int = 0
    weather: str = "clear"
    difficulty: int = 2
    loaded_chunks: dict[tuple[int, int], ChunkData] = field(default_factory=dict)
    entities: dict[int, dict] = field(default_factory=dict)
    block_changes: list[dict] = field(default_factory=list)
    world_height: int = 384
    min_y: int = -64


@dataclass
class BotConfig:
    host: str = "localhost"
    port: int = 25565
    username: str = "PythonBot"
    auth_mode: str = "offline"
    view_distance: int = 8
    auto_reconnect: bool = True
    reconnect_delay: float = 5.0


class MinecraftBot:
    def __init__(self, config: BotConfig | None = None) -> None:
        self._config = config or BotConfig()
        self._protocol: MinecraftProtocol | None = None
        self._player = PlayerState(username=self._config.username)
        self._world = WorldState()
        self._running = False
        self._packet_handlers: dict[tuple[ConnectionState, int], Callable] = {}
        self._event_handlers: dict[str, list[Callable]] = {}
        self._keep_alive_task: asyncio.Task | None = None
        self._position_update_task: asyncio.Task | None = None
        self._last_keep_alive: int = 0
        self._last_keep_alive_time: float = 0
        self._movement_keys: set[str] = set()
        self._is_sneaking = False
        self._is_sprinting = False
        self._target_yaw: float | None = None
        self._target_pitch: float | None = None
        self._in_configuration = False
        self._joined_game = False
        self._spawn_confirmed = False
        self._chunk_batch_size = 0
        self._setup_packet_handlers()

    def _setup_packet_handlers(self) -> None:
        self._packet_handlers[(ConnectionState.LOGIN, 0x00)] = self._handle_login_disconnect
        self._packet_handlers[(ConnectionState.LOGIN, 0x01)] = self._handle_encryption_request
        self._packet_handlers[(ConnectionState.LOGIN, 0x02)] = self._handle_login_success
        self._packet_handlers[(ConnectionState.LOGIN, 0x03)] = self._handle_set_compression

        self._packet_handlers[(ConnectionState.CONFIGURATION, 0x01)] = self._handle_config_plugin_message
        self._packet_handlers[(ConnectionState.CONFIGURATION, 0x02)] = self._handle_config_disconnect
        self._packet_handlers[(ConnectionState.CONFIGURATION, 0x03)] = self._handle_config_finish
        self._packet_handlers[(ConnectionState.CONFIGURATION, 0x04)] = self._handle_config_keep_alive
        self._packet_handlers[(ConnectionState.CONFIGURATION, 0x07)] = self._handle_config_registry_data
        self._packet_handlers[(ConnectionState.CONFIGURATION, 0x09)] = self._handle_config_resource_pack_push
        self._packet_handlers[(ConnectionState.CONFIGURATION, 0x0C)] = self._handle_config_feature_flags
        self._packet_handlers[(ConnectionState.CONFIGURATION, 0x0E)] = self._handle_config_known_packs

        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.KEEP_ALIVE)] = self._handle_keep_alive
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.SYNCHRONIZE_PLAYER_POSITION)] = self._handle_synchronize_player_position
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.SET_HEALTH)] = self._handle_update_health
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.DISCONNECT)] = self._handle_disconnect
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.LOGIN)] = self._handle_login_play
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.SET_DEFAULT_SPAWN_POSITION)] = self._handle_set_default_spawn
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.GAME_EVENT)] = self._handle_game_event
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.UPDATE_TIME)] = self._handle_set_time
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.CHUNK_DATA_AND_UPDATE_LIGHT)] = self._handle_chunk_data
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.BLOCK_UPDATE)] = self._handle_block_update
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.SPAWN_ENTITY)] = self._handle_spawn_entity
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.REMOVE_ENTITIES)] = self._handle_remove_entities
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.UPDATE_ENTITY_POSITION)] = self._handle_entity_position
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.UPDATE_ENTITY_POSITION_AND_ROTATION)] = self._handle_entity_position_rotation
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.UPDATE_ENTITY_ROTATION)] = self._handle_entity_rotation
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.UNLOAD_CHUNK)] = self._handle_unload_chunk
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.CHUNK_BATCH_START)] = self._handle_chunk_batch_start
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.CHUNK_BATCH_FINISHED)] = self._handle_chunk_batch_finished
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.START_CONFIGURATION)] = self._handle_start_configuration
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.PING)] = self._handle_ping
        self._packet_handlers[(ConnectionState.PLAY, PlayPacketIds.SET_CENTER_CHUNK)] = self._handle_set_center_chunk

    def on(self, event: str, handler: Callable) -> None:
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    async def _emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        if event in self._event_handlers:
            for handler in self._event_handlers[event]:
                try:
                    result = handler(*args, **kwargs)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"Error in event handler for {event}: {e}")

    async def connect(self) -> bool:
        try:
            self._protocol = MinecraftProtocol(self._config.host, self._config.port)
            await self._protocol.connect()
            logger.info(f"Connected to {self._config.host}:{self._config.port}")

            await self._protocol.send_handshake(ConnectionState.LOGIN)
            await self._protocol.send_login_start(self._config.username)

            self._running = True
            self._joined_game = False
            self._spawn_confirmed = False
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    async def disconnect(self) -> None:
        self._running = False
        if self._keep_alive_task:
            self._keep_alive_task.cancel()
            try:
                await self._keep_alive_task
            except asyncio.CancelledError:
                pass
        if self._position_update_task:
            self._position_update_task.cancel()
            try:
                await self._position_update_task
            except asyncio.CancelledError:
                pass
        if self._protocol:
            await self._protocol.disconnect()
        await self._emit("disconnect")

    async def run(self) -> None:
        if not await self.connect():
            return

        try:
            while self._running and self._protocol and self._protocol.connected:
                try:
                    packet_id, buffer = await asyncio.wait_for(
                        self._protocol.receive_packet(), timeout=30.0
                    )
                    await self._handle_packet(packet_id, buffer)
                except asyncio.TimeoutError:
                    logger.warning("Receive timeout")
                    if self._protocol.state == ConnectionState.PLAY:
                        continue
                    break
                except ConnectionError as e:
                    logger.error(f"Connection error: {e}")
                    break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        finally:
            await self.disconnect()
            if self._config.auto_reconnect:
                logger.info(f"Reconnecting in {self._config.reconnect_delay} seconds...")
                await asyncio.sleep(self._config.reconnect_delay)
                await self.run()

    async def _handle_packet(self, packet_id: int, buffer: PacketBuffer) -> None:
        state = self._protocol.state if self._protocol else ConnectionState.HANDSHAKING
        handler = self._packet_handlers.get((state, packet_id))
        if handler:
            try:
                await handler(buffer)
            except Exception as e:
                logger.debug(f"Error handling packet 0x{packet_id:02X} in state {state}: {e}")

    async def _handle_login_disconnect(self, buffer: PacketBuffer) -> None:
        reason = buffer.read_string()
        logger.error(f"Disconnected during login: {reason}")
        self._running = False

    async def _handle_encryption_request(self, buffer: PacketBuffer) -> None:
        logger.warning("Server requires authentication - offline mode servers only supported")
        self._running = False

    async def _handle_login_success(self, buffer: PacketBuffer) -> None:
        self._player.uuid = buffer.read_uuid()
        self._player.username = buffer.read_string()
        logger.info(f"Login success: {self._player.username} ({self._player.uuid})")

        if self._protocol:
            await self._protocol.send_login_acknowledged()
            self._in_configuration = True
            logger.info("Entering configuration phase...")
            await self._protocol.send_configuration_client_information()

    async def _handle_set_compression(self, buffer: PacketBuffer) -> None:
        threshold = buffer.read_varint()
        if self._protocol:
            self._protocol.set_compression(threshold)
        logger.info(f"Compression enabled with threshold {threshold}")

    async def _handle_config_plugin_message(self, buffer: PacketBuffer) -> None:
        channel = buffer.read_string()
        logger.debug(f"Configuration plugin message on channel: {channel}")

    async def _handle_config_disconnect(self, buffer: PacketBuffer) -> None:
        reason = buffer.read_string()
        logger.error(f"Disconnected during configuration: {reason}")
        self._running = False

    async def _handle_config_finish(self, buffer: PacketBuffer) -> None:
        logger.info("Configuration finished, transitioning to play state")
        if self._protocol:
            await self._protocol.send_configuration_finish_ack()
        self._in_configuration = False

    async def _handle_config_keep_alive(self, buffer: PacketBuffer) -> None:
        keep_alive_id = buffer.read_long()
        if self._protocol:
            await self._protocol.send_configuration_keep_alive(keep_alive_id)

    async def _handle_config_registry_data(self, buffer: PacketBuffer) -> None:
        registry_id = buffer.read_string()
        logger.debug(f"Received registry data: {registry_id}")

    async def _handle_config_resource_pack_push(self, buffer: PacketBuffer) -> None:
        pack_uuid = buffer.read_uuid()
        url = buffer.read_string()
        hash_str = buffer.read_string()
        forced = buffer.read_bool()
        logger.info(f"Resource pack pushed: {pack_uuid} (forced: {forced})")
        if self._protocol:
            result = 3
            await self._protocol.send_configuration_resource_pack_response(pack_uuid, result)

    async def _handle_config_feature_flags(self, buffer: PacketBuffer) -> None:
        count = buffer.read_varint()
        flags = [buffer.read_string() for _ in range(count)]
        logger.debug(f"Feature flags: {flags}")

    async def _handle_config_known_packs(self, buffer: PacketBuffer) -> None:
        count = buffer.read_varint()
        packs = []
        for _ in range(count):
            namespace = buffer.read_string()
            pack_id = buffer.read_string()
            version = buffer.read_string()
            packs.append((namespace, pack_id, version))
        logger.debug(f"Known packs request: {packs}")
        if self._protocol:
            await self._protocol.send_known_packs_response(packs)

    async def _handle_keep_alive(self, buffer: PacketBuffer) -> None:
        keep_alive_id = buffer.read_long()
        self._last_keep_alive = keep_alive_id
        self._last_keep_alive_time = time.time()
        if self._protocol:
            await self._protocol.send_keep_alive(keep_alive_id)
            logger.debug(f"Keep-alive response sent: {keep_alive_id}")

    async def _handle_synchronize_player_position(self, buffer: PacketBuffer) -> None:
        teleport_id = buffer.read_varint()
        x = buffer.read_double()
        y = buffer.read_double()
        z = buffer.read_double()
        vx = buffer.read_double()
        vy = buffer.read_double()
        vz = buffer.read_double()
        yaw = buffer.read_float()
        pitch = buffer.read_float()
        flags = buffer.read_int()

        if flags & 0x01:
            self._player.position.x += x
        else:
            self._player.position.x = x
        if flags & 0x02:
            self._player.position.y += y
        else:
            self._player.position.y = y
        if flags & 0x04:
            self._player.position.z += z
        else:
            self._player.position.z = z
        if flags & 0x08:
            self._player.position.yaw += yaw
        else:
            self._player.position.yaw = yaw
        if flags & 0x10:
            self._player.position.pitch += pitch
        else:
            self._player.position.pitch = pitch

        if self._protocol:
            await self._protocol.send_teleport_confirm(teleport_id)

        if not self._spawn_confirmed:
            self._spawn_confirmed = True
            logger.info(f"Position synchronized: ({self._player.position.x:.2f}, {self._player.position.y:.2f}, {self._player.position.z:.2f})")
            await self._emit("spawn", self._player.position)

        if not self._position_update_task:
            self._position_update_task = asyncio.create_task(self._position_update_loop())

    async def _handle_update_health(self, buffer: PacketBuffer) -> None:
        self._player.health = buffer.read_float()
        self._player.food = buffer.read_varint()
        self._player.saturation = buffer.read_float()

        await self._emit("health", self._player.health, self._player.food)

        if self._player.health <= 0:
            logger.info("Player died, respawning...")
            await self._emit("death")

    async def _handle_disconnect(self, buffer: PacketBuffer) -> None:
        logger.info("Disconnected by server")
        self._running = False

    async def _handle_login_play(self, buffer: PacketBuffer) -> None:
        if self._joined_game:
            return
        self._joined_game = True

        self._player.entity_id = buffer.read_int()
        self._player.is_hardcore = buffer.read_bool()

        logger.info(f"Joined game with entity ID {self._player.entity_id}")
        self._in_configuration = False

        await self._emit("join", self._player)

    async def _handle_set_default_spawn(self, buffer: PacketBuffer) -> None:
        position = buffer.read_position()
        self._world.spawn_position = position
        logger.info(f"Spawn position set to {position}")

    async def _handle_game_event(self, buffer: PacketBuffer) -> None:
        event_id = buffer.read_ubyte()
        value = buffer.read_float()
        if event_id == 1:
            self._world.weather = "rain" if value > 0 else "clear"
        elif event_id == 3:
            self._player.gamemode = int(value)

    async def _handle_set_time(self, buffer: PacketBuffer) -> None:
        world_age = buffer.read_long()
        time_of_day = buffer.read_long()
        self._world.time_of_day = int(abs(time_of_day) % 24000)

    async def _handle_chunk_data(self, buffer: PacketBuffer) -> None:
        chunk_x = buffer.read_int()
        chunk_z = buffer.read_int()

        chunk = ChunkData(x=chunk_x, z=chunk_z)

        try:
            heightmap_nbt = self._read_chunk_heightmap(buffer)
            chunk.heightmap = heightmap_nbt
        except Exception:
            pass

        try:
            data_size = buffer.read_varint()
            chunk_data = buffer.read_bytes(data_size)
            self._parse_chunk_sections(chunk, chunk_data)
        except Exception:
            pass

        self._world.loaded_chunks[(chunk_x, chunk_z)] = chunk

    def _read_chunk_heightmap(self, buffer: PacketBuffer) -> dict:
        tag_type = buffer.read_byte()
        if tag_type == 0:
            return {}
        return {"type": tag_type}

    def _parse_chunk_sections(self, chunk: ChunkData, data: bytes) -> None:
        if len(data) < 10:
            return

        offset = 0
        num_sections = (self._world.world_height) // 16
        section_y = self._world.min_y // 16

        for section_idx in range(num_sections):
            if offset >= len(data) - 2:
                break

            try:
                block_count = int.from_bytes(data[offset:offset+2], 'big', signed=True)
                offset += 2

                if offset >= len(data):
                    break
                bits_per_entry = data[offset]
                offset += 1

                section = ChunkSection(block_count=block_count, bits_per_entry=bits_per_entry)

                if bits_per_entry == 0:
                    if offset >= len(data):
                        break
                    palette_value, varint_size = VarInt.read(data, offset)
                    offset += varint_size
                    section.palette = [palette_value]
                    data_length, varint_size = VarInt.read(data, offset)
                    offset += varint_size
                    offset += data_length * 8
                elif bits_per_entry <= 8:
                    palette_length, varint_size = VarInt.read(data, offset)
                    offset += varint_size
                    for _ in range(palette_length):
                        if offset >= len(data):
                            break
                        entry, varint_size = VarInt.read(data, offset)
                        offset += varint_size
                        section.palette.append(entry)
                    data_length, varint_size = VarInt.read(data, offset)
                    offset += varint_size
                    offset += data_length * 8
                else:
                    data_length, varint_size = VarInt.read(data, offset)
                    offset += varint_size
                    offset += data_length * 8

                if offset >= len(data):
                    break
                biome_bits = data[offset]
                offset += 1

                if biome_bits == 0:
                    if offset >= len(data):
                        break
                    biome_value, varint_size = VarInt.read(data, offset)
                    offset += varint_size
                    biome_data_length, varint_size = VarInt.read(data, offset)
                    offset += varint_size
                    offset += biome_data_length * 8
                elif biome_bits <= 3:
                    palette_length, varint_size = VarInt.read(data, offset)
                    offset += varint_size
                    for _ in range(palette_length):
                        if offset >= len(data):
                            break
                        entry, varint_size = VarInt.read(data, offset)
                        offset += varint_size
                    data_length, varint_size = VarInt.read(data, offset)
                    offset += varint_size
                    offset += data_length * 8
                else:
                    data_length, varint_size = VarInt.read(data, offset)
                    offset += varint_size
                    offset += data_length * 8

                chunk.sections[section_y + section_idx] = section

            except Exception:
                break

    async def _handle_block_update(self, buffer: PacketBuffer) -> None:
        position = buffer.read_position()
        block_id = buffer.read_varint()
        self._world.block_changes.append({
            "position": position,
            "block_id": block_id,
            "time": time.time(),
        })
        if len(self._world.block_changes) > 1000:
            self._world.block_changes = self._world.block_changes[-500:]

    async def _handle_spawn_entity(self, buffer: PacketBuffer) -> None:
        entity_id = buffer.read_varint()
        uuid = buffer.read_uuid()
        entity_type = buffer.read_varint()
        x = buffer.read_double()
        y = buffer.read_double()
        z = buffer.read_double()

        self._world.entities[entity_id] = {
            "entity_id": entity_id,
            "uuid": uuid,
            "type": entity_type,
            "x": x,
            "y": y,
            "z": z,
        }

    async def _handle_remove_entities(self, buffer: PacketBuffer) -> None:
        count = buffer.read_varint()
        for _ in range(count):
            entity_id = buffer.read_varint()
            self._world.entities.pop(entity_id, None)

    async def _handle_entity_position(self, buffer: PacketBuffer) -> None:
        entity_id = buffer.read_varint()
        dx = buffer.read_short() / 4096.0
        dy = buffer.read_short() / 4096.0
        dz = buffer.read_short() / 4096.0

        if entity_id in self._world.entities:
            self._world.entities[entity_id]["x"] += dx
            self._world.entities[entity_id]["y"] += dy
            self._world.entities[entity_id]["z"] += dz

    async def _handle_entity_position_rotation(self, buffer: PacketBuffer) -> None:
        entity_id = buffer.read_varint()
        dx = buffer.read_short() / 4096.0
        dy = buffer.read_short() / 4096.0
        dz = buffer.read_short() / 4096.0

        if entity_id in self._world.entities:
            self._world.entities[entity_id]["x"] += dx
            self._world.entities[entity_id]["y"] += dy
            self._world.entities[entity_id]["z"] += dz

    async def _handle_entity_rotation(self, buffer: PacketBuffer) -> None:
        pass

    async def _handle_unload_chunk(self, buffer: PacketBuffer) -> None:
        chunk_z = buffer.read_int()
        chunk_x = buffer.read_int()
        self._world.loaded_chunks.pop((chunk_x, chunk_z), None)

    async def _handle_chunk_batch_start(self, buffer: PacketBuffer) -> None:
        self._chunk_batch_size = 0

    async def _handle_chunk_batch_finished(self, buffer: PacketBuffer) -> None:
        batch_size = buffer.read_varint()
        logger.debug(f"Chunk batch finished, size: {batch_size}")

    async def _handle_start_configuration(self, buffer: PacketBuffer) -> None:
        logger.info("Server requested configuration state")
        if self._protocol:
            await self._protocol.send_packet(0x0C)
            self._protocol.state = ConnectionState.CONFIGURATION
            self._in_configuration = True
            self._joined_game = False
            self._spawn_confirmed = False
            await self._protocol.send_configuration_client_information()

    async def _handle_ping(self, buffer: PacketBuffer) -> None:
        ping_id = buffer.read_int()
        if self._protocol:
            data = PacketBuffer.write_int(ping_id)
            await self._protocol.send_packet(0x28, data)

    async def _handle_set_center_chunk(self, buffer: PacketBuffer) -> None:
        chunk_x = buffer.read_varint()
        chunk_z = buffer.read_varint()
        logger.debug(f"Center chunk set to ({chunk_x}, {chunk_z})")

    async def _position_update_loop(self) -> None:
        movement_speed = 4.317
        sprint_multiplier = 1.3
        sneak_multiplier = 0.3

        while self._running:
            try:
                await asyncio.sleep(0.05)

                if self._target_yaw is not None or self._target_pitch is not None:
                    if self._target_yaw is not None:
                        self._player.position.yaw = self._target_yaw
                        self._target_yaw = None
                    if self._target_pitch is not None:
                        self._player.position.pitch = self._target_pitch
                        self._target_pitch = None

                if self._movement_keys:
                    speed = movement_speed * 0.05
                    if self._is_sprinting:
                        speed *= sprint_multiplier
                    if self._is_sneaking:
                        speed *= sneak_multiplier

                    yaw_rad = math.radians(self._player.position.yaw)
                    dx, dz = 0.0, 0.0

                    if "w" in self._movement_keys:
                        dx -= math.sin(yaw_rad) * speed
                        dz += math.cos(yaw_rad) * speed
                    if "s" in self._movement_keys:
                        dx += math.sin(yaw_rad) * speed
                        dz -= math.cos(yaw_rad) * speed
                    if "a" in self._movement_keys:
                        dx += math.cos(yaw_rad) * speed
                        dz += math.sin(yaw_rad) * speed
                    if "d" in self._movement_keys:
                        dx -= math.cos(yaw_rad) * speed
                        dz -= math.sin(yaw_rad) * speed

                    self._player.position.x += dx
                    self._player.position.z += dz

                if self._protocol and self._protocol.state == ConnectionState.PLAY:
                    await self._protocol.send_player_position_and_rotation(self._player.position)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Position update error: {e}")

    async def move_forward(self, start: bool = True) -> None:
        if start:
            self._movement_keys.add("w")
        else:
            self._movement_keys.discard("w")

    async def move_backward(self, start: bool = True) -> None:
        if start:
            self._movement_keys.add("s")
        else:
            self._movement_keys.discard("s")

    async def move_left(self, start: bool = True) -> None:
        if start:
            self._movement_keys.add("a")
        else:
            self._movement_keys.discard("a")

    async def move_right(self, start: bool = True) -> None:
        if start:
            self._movement_keys.add("d")
        else:
            self._movement_keys.discard("d")

    async def jump(self) -> None:
        self._player.position.y += 1.25
        self._player.position.on_ground = False
        if self._protocol:
            await self._protocol.send_player_position(self._player.position)

    async def sneak(self, start: bool = True) -> None:
        self._is_sneaking = start
        if self._protocol:
            action = 0 if start else 1
            await self._protocol.send_player_command(self._player.entity_id, action)

    async def sprint(self, start: bool = True) -> None:
        self._is_sprinting = start
        if self._protocol:
            action = 3 if start else 4
            await self._protocol.send_player_command(self._player.entity_id, action)

    async def look(self, yaw: float, pitch: float) -> None:
        self._target_yaw = yaw
        self._target_pitch = max(-90, min(90, pitch))

    async def look_relative(self, yaw_delta: float, pitch_delta: float) -> None:
        new_yaw = (self._player.position.yaw + yaw_delta) % 360
        new_pitch = max(-90, min(90, self._player.position.pitch + pitch_delta))
        await self.look(new_yaw, new_pitch)

    async def attack(self) -> None:
        if self._protocol:
            await self._protocol.send_swing_arm(0)

    async def use_item(self) -> None:
        if self._protocol:
            await self._protocol.send_use_item(0, int(time.time() * 1000) % 1000000)

    async def select_slot(self, slot: int) -> None:
        if 0 <= slot <= 8 and self._protocol:
            await self._protocol.send_held_item_change(slot)

    async def chat(self, message: str) -> None:
        if self._protocol:
            await self._protocol.send_chat_message(message)

    async def respawn(self) -> None:
        if self._protocol and self._player.health <= 0:
            data = VarInt.write(0)
            await self._protocol.send_packet(0x09, data)

    def get_position(self) -> Position:
        return self._player.position

    def get_health(self) -> float:
        return self._player.health

    def get_food(self) -> int:
        return self._player.food

    def get_player_state(self) -> PlayerState:
        return self._player

    def get_world_state(self) -> WorldState:
        return self._world

    def get_chunk_at(self, x: int, z: int) -> ChunkData | None:
        chunk_x = x >> 4
        chunk_z = z >> 4
        return self._world.loaded_chunks.get((chunk_x, chunk_z))

    def get_visible_chunks(self, radius: int = 4) -> list[dict]:
        player_chunk_x = int(self._player.position.x) >> 4
        player_chunk_z = int(self._player.position.z) >> 4

        visible = []
        for (cx, cz), chunk in self._world.loaded_chunks.items():
            if abs(cx - player_chunk_x) <= radius and abs(cz - player_chunk_z) <= radius:
                visible.append({
                    "x": cx,
                    "z": cz,
                    "sections": len(chunk.sections),
                    "heightmap": chunk.heightmap,
                })
        return visible

    def get_state_dict(self) -> dict:
        return {
            "player": {
                "entity_id": self._player.entity_id,
                "uuid": self._player.uuid,
                "username": self._player.username,
                "position": {
                    "x": self._player.position.x,
                    "y": self._player.position.y,
                    "z": self._player.position.z,
                    "yaw": self._player.position.yaw,
                    "pitch": self._player.position.pitch,
                    "on_ground": self._player.position.on_ground,
                },
                "health": self._player.health,
                "food": self._player.food,
                "saturation": self._player.saturation,
                "gamemode": self._player.gamemode,
            },
            "world": {
                "spawn_position": self._world.spawn_position,
                "time_of_day": self._world.time_of_day,
                "weather": self._world.weather,
                "loaded_chunks_count": len(self._world.loaded_chunks),
                "entities_count": len(self._world.entities),
                "visible_chunks": self.get_visible_chunks(3),
            },
            "entities": [
                {
                    "id": e["entity_id"],
                    "type": e["type"],
                    "x": e["x"],
                    "y": e["y"],
                    "z": e["z"],
                }
                for e in list(self._world.entities.values())[:50]
            ],
            "connected": self._running and self._joined_game,
            "movement_keys": list(self._movement_keys),
            "is_sneaking": self._is_sneaking,
            "is_sprinting": self._is_sprinting,
        }

    @property
    def running(self) -> bool:
        return self._running


async def run_bot(
    host: str = "localhost",
    port: int = 25565,
    username: str = "PythonBot",
) -> MinecraftBot:
    config = BotConfig(host=host, port=port, username=username)
    bot = MinecraftBot(config)

    @bot.on("join")
    async def on_join(player: PlayerState) -> None:
        logger.info(f"Bot joined as {player.username}")

    @bot.on("spawn")
    async def on_spawn(position: Position) -> None:
        logger.info(f"Bot spawned at {position.x:.2f}, {position.y:.2f}, {position.z:.2f}")

    await bot.run()
    return bot


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Minecraft Bot")
    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=25565, help="Server port")
    parser.add_argument("--username", default="PythonBot", help="Bot username")
    args = parser.parse_args()

    def signal_handler(sig, frame) -> None:
        logger.info("Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    asyncio.run(run_bot(args.host, args.port, args.username))


if __name__ == "__main__":
    main()
