from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import zlib
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

if TYPE_CHECKING:
    pass


class ConnectionState(IntEnum):
    HANDSHAKING = 0
    STATUS = 1
    LOGIN = 2
    PLAY = 3


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
    PROTOCOL_VERSION = 770
    VERSION_NAME = "1.21.5"

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
            await self._writer.wait_closed()
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

        player_uuid = str(uuid_lib.uuid4())
        data = PacketBuffer.write_string(username) + PacketBuffer.write_uuid(player_uuid)
        await self.send_packet(0x00, data)

    async def send_login_acknowledged(self) -> None:
        await self.send_packet(0x03)
        self._state = ConnectionState.PLAY

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
        await self.send_packet(0x3A, data)

    async def send_held_item_change(self, slot: int) -> None:
        data = PacketBuffer.write_short(slot)
        await self.send_packet(0x2F, data)

    async def send_chat_message(self, message: str) -> None:
        import time

        timestamp = int(time.time() * 1000)
        data = (
            PacketBuffer.write_string(message)
            + PacketBuffer.write_long(timestamp)
            + PacketBuffer.write_long(0)
            + PacketBuffer.write_bool(False)
            + VarInt.write(0)
            + PacketBuffer.write_bytes(b"\x00" * 32)
        )
        await self.send_packet(0x06, data)

    async def send_keep_alive(self, keep_alive_id: int) -> None:
        data = PacketBuffer.write_long(keep_alive_id)
        await self.send_packet(0x18, data)

    async def send_teleport_confirm(self, teleport_id: int) -> None:
        data = VarInt.write(teleport_id)
        await self.send_packet(0x00, data)

    async def send_configuration_finish(self) -> None:
        await self.send_packet(0x03)

    async def send_configuration_keep_alive(self, keep_alive_id: int) -> None:
        data = PacketBuffer.write_long(keep_alive_id)
        await self.send_packet(0x04, data)

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

