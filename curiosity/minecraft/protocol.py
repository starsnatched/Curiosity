from __future__ import annotations

import asyncio
import struct
import zlib
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

if TYPE_CHECKING:
    pass


class ConnectionState(IntEnum):
    HANDSHAKING = 0
    STATUS = 1
    LOGIN = 2
    CONFIGURATION = 3
    PLAY = 4


class PacketDirection(IntEnum):
    SERVERBOUND = 0
    CLIENTBOUND = 1


@dataclass
class VarInt:
    @staticmethod
    def read(data: bytes, offset: int = 0) -> tuple[int, int]:
        result = 0
        shift = 0
        while True:
            if offset >= len(data):
                raise ValueError("VarInt is too short")
            byte = data[offset]
            offset += 1
            result |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
            if shift >= 32:
                raise ValueError("VarInt is too big")
        if result >= 2**31:
            result -= 2**32
        return result, offset

    @staticmethod
    def write(value: int) -> bytes:
        if value < 0:
            value += 2**32
        result = b""
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                result += bytes([byte | 0x80])
            else:
                result += bytes([byte])
                break
        return result


@dataclass
class PacketBuffer:
    data: bytes = b""
    offset: int = 0

    def read_varint(self) -> int:
        value, self.offset = VarInt.read(self.data, self.offset)
        return value

    def read_string(self) -> str:
        length = self.read_varint()
        string = self.data[self.offset : self.offset + length].decode("utf-8")
        self.offset += length
        return string

    def read_bytes(self, length: int) -> bytes:
        result = self.data[self.offset : self.offset + length]
        self.offset += length
        return result

    def read_byte(self) -> int:
        result = self.data[self.offset]
        self.offset += 1
        return result

    def read_ubyte(self) -> int:
        return self.read_byte() & 0xFF

    def read_bool(self) -> bool:
        return self.read_byte() != 0

    def read_short(self) -> int:
        result = struct.unpack(">h", self.data[self.offset : self.offset + 2])[0]
        self.offset += 2
        return result

    def read_ushort(self) -> int:
        result = struct.unpack(">H", self.data[self.offset : self.offset + 2])[0]
        self.offset += 2
        return result

    def read_int(self) -> int:
        result = struct.unpack(">i", self.data[self.offset : self.offset + 4])[0]
        self.offset += 4
        return result

    def read_long(self) -> int:
        result = struct.unpack(">q", self.data[self.offset : self.offset + 8])[0]
        self.offset += 8
        return result

    def read_float(self) -> float:
        result = struct.unpack(">f", self.data[self.offset : self.offset + 4])[0]
        self.offset += 4
        return result

    def read_double(self) -> float:
        result = struct.unpack(">d", self.data[self.offset : self.offset + 8])[0]
        self.offset += 8
        return result

    def read_uuid(self) -> str:
        uuid_bytes = self.read_bytes(16)
        hex_str = uuid_bytes.hex()
        return f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:]}"

    def read_position(self) -> tuple[int, int, int]:
        val = self.read_long()
        x = val >> 38
        y = val & 0xFFF
        z = (val >> 12) & 0x3FFFFFF
        if x >= 2**25:
            x -= 2**26
        if y >= 2**11:
            y -= 2**12
        if z >= 2**25:
            z -= 2**26
        return (x, y, z)

    def read_remaining(self) -> bytes:
        result = self.data[self.offset :]
        self.offset = len(self.data)
        return result

    def remaining(self) -> int:
        return len(self.data) - self.offset

    def read_nbt(self) -> dict:
        nbt_data = self.read_remaining()
        return {"raw": nbt_data}

    @staticmethod
    def write_varint(value: int) -> bytes:
        return VarInt.write(value)

    @staticmethod
    def write_string(value: str) -> bytes:
        encoded = value.encode("utf-8")
        return VarInt.write(len(encoded)) + encoded

    @staticmethod
    def write_bytes(value: bytes) -> bytes:
        return value

    @staticmethod
    def write_byte(value: int) -> bytes:
        return bytes([value & 0xFF])

    @staticmethod
    def write_bool(value: bool) -> bytes:
        return bytes([1 if value else 0])

    @staticmethod
    def write_short(value: int) -> bytes:
        return struct.pack(">h", value)

    @staticmethod
    def write_ushort(value: int) -> bytes:
        return struct.pack(">H", value)

    @staticmethod
    def write_int(value: int) -> bytes:
        return struct.pack(">i", value)

    @staticmethod
    def write_long(value: int) -> bytes:
        return struct.pack(">q", value)

    @staticmethod
    def write_float(value: float) -> bytes:
        return struct.pack(">f", value)

    @staticmethod
    def write_double(value: float) -> bytes:
        return struct.pack(">d", value)

    @staticmethod
    def write_uuid(uuid_str: str) -> bytes:
        hex_str = uuid_str.replace("-", "")
        return bytes.fromhex(hex_str)


@dataclass
class Position:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    on_ground: bool = True


@dataclass
class PlayerState:
    entity_id: int = 0
    uuid: str = ""
    username: str = ""
    position: Position = field(default_factory=Position)
    health: float = 20.0
    food: int = 20
    saturation: float = 5.0
    gamemode: int = 0
    dimension: str = "minecraft:overworld"
    is_hardcore: bool = False


@dataclass
class Entity:
    entity_id: int
    entity_type: int
    uuid: str
    x: float
    y: float
    z: float
    yaw: float = 0.0
    pitch: float = 0.0
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    velocity_z: float = 0.0


@dataclass
class BlockState:
    block_id: int
    x: int
    y: int
    z: int


@dataclass
class ChunkSection:
    block_count: int = 0
    blocks: list[int] = field(default_factory=list)
    palette: list[int] = field(default_factory=list)
    bits_per_entry: int = 0


@dataclass
class ChunkData:
    x: int
    z: int
    sections: dict[int, ChunkSection] = field(default_factory=dict)
    heightmap: dict = field(default_factory=dict)


class EncryptionContext:
    def __init__(self, shared_secret: bytes) -> None:
        self._cipher_encrypt = Cipher(
            algorithms.AES(shared_secret), modes.CFB8(shared_secret)
        )
        self._cipher_decrypt = Cipher(
            algorithms.AES(shared_secret), modes.CFB8(shared_secret)
        )
        self._encryptor = self._cipher_encrypt.encryptor()
        self._decryptor = self._cipher_decrypt.decryptor()

    def encrypt(self, data: bytes) -> bytes:
        return self._encryptor.update(data)

    def decrypt(self, data: bytes) -> bytes:
        return self._decryptor.update(data)


class MinecraftProtocol:
    PROTOCOL_VERSION = 774
    VERSION_NAME = "1.21.11"

    def __init__(self, host: str, port: int = 25565) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._state = ConnectionState.HANDSHAKING
        self._compression_threshold = -1
        self._encryption: EncryptionContext | None = None
        self._connected = False
        self._receive_buffer = b""

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port
        )
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    def _apply_encryption(self, data: bytes) -> bytes:
        if self._encryption:
            return self._encryption.encrypt(data)
        return data

    def _decrypt_data(self, data: bytes) -> bytes:
        if self._encryption:
            return self._encryption.decrypt(data)
        return data

    async def send_packet(self, packet_id: int, data: bytes = b"") -> None:
        if not self._writer:
            raise RuntimeError("Not connected")

        packet_data = VarInt.write(packet_id) + data

        if self._compression_threshold >= 0:
            if len(packet_data) >= self._compression_threshold:
                compressed = zlib.compress(packet_data)
                packet_with_length = VarInt.write(len(packet_data)) + compressed
            else:
                packet_with_length = VarInt.write(0) + packet_data
            final_packet = VarInt.write(len(packet_with_length)) + packet_with_length
        else:
            final_packet = VarInt.write(len(packet_data)) + packet_data

        encrypted = self._apply_encryption(final_packet)
        self._writer.write(encrypted)
        await self._writer.drain()

    async def receive_packet(self) -> tuple[int, PacketBuffer]:
        if not self._reader:
            raise RuntimeError("Not connected")

        while True:
            try:
                length, offset = VarInt.read(self._receive_buffer)
                if len(self._receive_buffer) >= offset + length:
                    packet_data = self._receive_buffer[offset : offset + length]
                    self._receive_buffer = self._receive_buffer[offset + length :]
                    break
            except ValueError:
                pass

            new_data = await self._reader.read(4096)
            if not new_data:
                raise ConnectionError("Connection closed")
            self._receive_buffer += self._decrypt_data(new_data)

        if self._compression_threshold >= 0:
            buffer = PacketBuffer(data=packet_data)
            data_length = buffer.read_varint()
            if data_length > 0:
                packet_data = zlib.decompress(buffer.read_remaining())
            else:
                packet_data = buffer.read_remaining()

        buffer = PacketBuffer(data=packet_data)
        packet_id = buffer.read_varint()
        return packet_id, buffer

    async def send_handshake(self, next_state: ConnectionState) -> None:
        data = (
            VarInt.write(self.PROTOCOL_VERSION)
            + PacketBuffer.write_string(self._host)
            + PacketBuffer.write_ushort(self._port)
            + VarInt.write(next_state)
        )
        await self.send_packet(0x00, data)
        self._state = next_state

    async def send_login_start(self, username: str) -> None:
        import uuid as uuid_lib

        player_uuid = str(uuid_lib.uuid3(uuid_lib.NAMESPACE_DNS, f"OfflinePlayer:{username}"))
        data = PacketBuffer.write_string(username) + PacketBuffer.write_uuid(player_uuid)
        await self.send_packet(0x00, data)

    async def send_login_acknowledged(self) -> None:
        await self.send_packet(0x03)
        self._state = ConnectionState.CONFIGURATION

    async def send_client_information(self) -> None:
        data = (
            PacketBuffer.write_string("en_US")
            + PacketBuffer.write_byte(16)
            + VarInt.write(0)
            + PacketBuffer.write_bool(True)
            + PacketBuffer.write_byte(0x7F)
            + VarInt.write(1)
            + PacketBuffer.write_bool(True)
            + PacketBuffer.write_bool(False)
            + VarInt.write(0)
        )
        await self.send_packet(0x00, data)

    async def send_player_position(self, position: Position) -> None:
        data = (
            PacketBuffer.write_double(position.x)
            + PacketBuffer.write_double(position.y)
            + PacketBuffer.write_double(position.z)
            + PacketBuffer.write_bool(position.on_ground)
        )
        await self.send_packet(0x1C, data)

    async def send_player_position_and_rotation(self, position: Position) -> None:
        data = (
            PacketBuffer.write_double(position.x)
            + PacketBuffer.write_double(position.y)
            + PacketBuffer.write_double(position.z)
            + PacketBuffer.write_float(position.yaw)
            + PacketBuffer.write_float(position.pitch)
            + PacketBuffer.write_bool(position.on_ground)
        )
        await self.send_packet(0x1D, data)

    async def send_player_rotation(self, yaw: float, pitch: float, on_ground: bool = True) -> None:
        data = (
            PacketBuffer.write_float(yaw)
            + PacketBuffer.write_float(pitch)
            + PacketBuffer.write_bool(on_ground)
        )
        await self.send_packet(0x1E, data)

    async def send_player_on_ground(self, on_ground: bool = True) -> None:
        data = PacketBuffer.write_bool(on_ground)
        await self.send_packet(0x1F, data)

    async def send_player_command(self, entity_id: int, action_id: int, jump_boost: int = 0) -> None:
        data = (
            VarInt.write(entity_id)
            + VarInt.write(action_id)
            + VarInt.write(jump_boost)
        )
        await self.send_packet(0x25, data)

    async def send_swing_arm(self, hand: int = 0) -> None:
        data = VarInt.write(hand)
        await self.send_packet(0x39, data)

    async def send_use_item(self, hand: int = 0, sequence: int = 0) -> None:
        data = VarInt.write(hand) + VarInt.write(sequence) + PacketBuffer.write_float(0.0) + PacketBuffer.write_float(0.0)
        await self.send_packet(0x3D, data)

    async def send_held_item_change(self, slot: int) -> None:
        data = PacketBuffer.write_short(slot)
        await self.send_packet(0x2F, data)

    async def send_chat_message(self, message: str) -> None:
        if message.startswith("/"):
            await self.send_chat_command(message[1:])
        else:
            import time
            timestamp = int(time.time() * 1000)
            data = (
                PacketBuffer.write_string(message)
                + PacketBuffer.write_long(timestamp)
                + PacketBuffer.write_long(0)
                + VarInt.write(0)
            )
            await self.send_packet(0x07, data)

    async def send_chat_command(self, command: str) -> None:
        data = PacketBuffer.write_string(command)
        await self.send_packet(0x05, data)

    async def send_keep_alive(self, keep_alive_id: int) -> None:
        data = PacketBuffer.write_long(keep_alive_id)
        await self.send_packet(0x18, data)

    async def send_teleport_confirm(self, teleport_id: int) -> None:
        data = VarInt.write(teleport_id)
        await self.send_packet(0x00, data)

    async def send_configuration_finish_ack(self) -> None:
        await self.send_packet(0x03)
        self._state = ConnectionState.PLAY

    async def send_configuration_keep_alive(self, keep_alive_id: int) -> None:
        data = PacketBuffer.write_long(keep_alive_id)
        await self.send_packet(0x04, data)

    async def send_configuration_resource_pack_response(self, uuid: str, result: int) -> None:
        data = PacketBuffer.write_uuid(uuid) + VarInt.write(result)
        await self.send_packet(0x06, data)

    async def send_configuration_client_information(self) -> None:
        data = (
            PacketBuffer.write_string("en_US")
            + PacketBuffer.write_byte(16)
            + VarInt.write(0)
            + PacketBuffer.write_bool(True)
            + PacketBuffer.write_byte(0x7F)
            + VarInt.write(1)
            + PacketBuffer.write_bool(True)
            + PacketBuffer.write_bool(False)
            + VarInt.write(0)
        )
        await self.send_packet(0x00, data)

    async def send_known_packs_response(self, packs: list[tuple[str, str, str]] | None = None) -> None:
        if packs is None:
            packs = []
        data = VarInt.write(len(packs))
        for namespace, pack_id, version in packs:
            data += PacketBuffer.write_string(namespace)
            data += PacketBuffer.write_string(pack_id)
            data += PacketBuffer.write_string(version)
        await self.send_packet(0x07, data)

    def enable_encryption(self, shared_secret: bytes) -> None:
        self._encryption = EncryptionContext(shared_secret)

    def set_compression(self, threshold: int) -> None:
        self._compression_threshold = threshold

    @property
    def state(self) -> ConnectionState:
        return self._state

    @state.setter
    def state(self, value: ConnectionState) -> None:
        self._state = value

    @property
    def connected(self) -> bool:
        return self._connected


class PlayPacketIds:
    BUNDLE_DELIMITER = 0x00
    SPAWN_ENTITY = 0x01
    SPAWN_EXPERIENCE_ORB = 0x02
    ENTITY_ANIMATION = 0x03
    AWARD_STATISTICS = 0x04
    ACKNOWLEDGE_BLOCK_CHANGE = 0x05
    SET_BLOCK_DESTROY_STAGE = 0x06
    BLOCK_ENTITY_DATA = 0x07
    BLOCK_ACTION = 0x08
    BLOCK_UPDATE = 0x09
    BOSS_BAR = 0x0A
    CHANGE_DIFFICULTY = 0x0B
    CHUNK_BATCH_FINISHED = 0x0C
    CHUNK_BATCH_START = 0x0D
    CHUNK_BIOMES = 0x0E
    CLEAR_TITLES = 0x0F
    COMMAND_SUGGESTIONS_RESPONSE = 0x10
    COMMANDS = 0x11
    CLOSE_CONTAINER = 0x12
    SET_CONTAINER_CONTENT = 0x13
    SET_CONTAINER_PROPERTY = 0x14
    SET_CONTAINER_SLOT = 0x15
    COOKIE_REQUEST = 0x16
    SET_COOLDOWN = 0x17
    CHAT_SUGGESTIONS = 0x18
    PLUGIN_MESSAGE = 0x19
    DAMAGE_EVENT = 0x1A
    DEBUG_SAMPLE = 0x1B
    DELETE_MESSAGE = 0x1C
    DISCONNECT = 0x1D
    DISGUISED_CHAT = 0x1E
    ENTITY_EVENT = 0x1F
    TELEPORT_ENTITY = 0x20
    EXPLOSION = 0x21
    UNLOAD_CHUNK = 0x22
    GAME_EVENT = 0x23
    OPEN_HORSE_SCREEN = 0x24
    HURT_ANIMATION = 0x25
    INITIALIZE_WORLD_BORDER = 0x26
    KEEP_ALIVE = 0x27
    CHUNK_DATA_AND_UPDATE_LIGHT = 0x28
    WORLD_EVENT = 0x29
    PARTICLE = 0x2A
    UPDATE_LIGHT = 0x2B
    LOGIN = 0x2C
    MAP_DATA = 0x2D
    MERCHANT_OFFERS = 0x2E
    UPDATE_ENTITY_POSITION = 0x2F
    UPDATE_ENTITY_POSITION_AND_ROTATION = 0x30
    UPDATE_ENTITY_ROTATION = 0x31
    MOVE_VEHICLE = 0x32
    OPEN_BOOK = 0x33
    OPEN_SCREEN = 0x34
    OPEN_SIGN_EDITOR = 0x35
    PING = 0x36
    PONG_RESPONSE = 0x37
    PLACE_GHOST_RECIPE = 0x38
    PLAYER_ABILITIES = 0x39
    PLAYER_CHAT_MESSAGE = 0x3A
    END_COMBAT = 0x3B
    ENTER_COMBAT = 0x3C
    COMBAT_DEATH = 0x3D
    PLAYER_INFO_REMOVE = 0x3E
    PLAYER_INFO_UPDATE = 0x3F
    LOOK_AT = 0x40
    SYNCHRONIZE_PLAYER_POSITION = 0x41
    UPDATE_RECIPE_BOOK = 0x42
    REMOVE_ENTITIES = 0x43
    REMOVE_ENTITY_EFFECT = 0x44
    RESET_SCORE = 0x45
    REMOVE_RESOURCE_PACK = 0x46
    ADD_RESOURCE_PACK = 0x47
    RESPAWN = 0x48
    SET_HEAD_ROTATION = 0x49
    UPDATE_SECTION_BLOCKS = 0x4A
    SELECT_ADVANCEMENTS_TAB = 0x4B
    SERVER_DATA = 0x4C
    SET_ACTION_BAR_TEXT = 0x4D
    SET_BORDER_CENTER = 0x4E
    SET_BORDER_LERP_SIZE = 0x4F
    SET_BORDER_SIZE = 0x50
    SET_BORDER_WARNING_DELAY = 0x51
    SET_BORDER_WARNING_DISTANCE = 0x52
    SET_CAMERA = 0x53
    SET_CENTER_CHUNK = 0x54
    SET_RENDER_DISTANCE = 0x55
    SET_DEFAULT_SPAWN_POSITION = 0x56
    DISPLAY_OBJECTIVE = 0x57
    SET_ENTITY_METADATA = 0x58
    LINK_ENTITIES = 0x59
    SET_ENTITY_VELOCITY = 0x5A
    SET_EQUIPMENT = 0x5B
    SET_EXPERIENCE = 0x5C
    SET_HEALTH = 0x5D
    UPDATE_OBJECTIVES = 0x5E
    SET_PASSENGERS = 0x5F
    UPDATE_TEAMS = 0x60
    UPDATE_SCORE = 0x61
    SET_SIMULATION_DISTANCE = 0x62
    SET_SUBTITLE_TEXT = 0x63
    UPDATE_TIME = 0x64
    SET_TITLE_TEXT = 0x65
    SET_TITLE_ANIMATION_TIMES = 0x66
    ENTITY_SOUND_EFFECT = 0x67
    SOUND_EFFECT = 0x68
    START_CONFIGURATION = 0x69
    STOP_SOUND = 0x6A
    STORE_COOKIE = 0x6B
    SYSTEM_CHAT_MESSAGE = 0x6C
    SET_TAB_LIST_HEADER_AND_FOOTER = 0x6D
    TAG_QUERY_RESPONSE = 0x6E
    PICKUP_ITEM = 0x6F
    TRANSFER = 0x70
    UPDATE_ADVANCEMENTS = 0x71
    UPDATE_ATTRIBUTES = 0x72
    ENTITY_EFFECT = 0x73
    UPDATE_RECIPES = 0x74
    UPDATE_TAGS = 0x75
    PROJECTILE_POWER = 0x76
    CUSTOM_REPORT_DETAILS = 0x77
    SERVER_LINKS = 0x78
