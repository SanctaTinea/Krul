"""Krul transport v1 framing plus JSON/BSON/compact-CBOR codecs."""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from typing import Any

FORMAT_JSON = "json"
FORMAT_BSON = "bson"
FORMAT_CBOR = "cbor"
MAX_PAYLOAD_SIZE = 10 * 1024
HEADER_SIZE = 8
CRC_SIZE = 2
MAGICS = {FORMAT_JSON: b"KRJ1", FORMAT_BSON: b"KRB1",
          FORMAT_CBOR: b"KRC1"}


def cbor_key_tag(name: str) -> int:
    """Return the stable 16-bit numeric key used by Krul compact CBOR."""
    value = 5381
    for byte in name.encode("utf-8"):
        value = ((value * 33) ^ byte) % 65521
    return value + 1


_CBOR_PROTOCOL_KEYS = (
    # Keys the firmware emits in DESCRIBE/results. A key missing here decodes
    # back as its raw numeric tag (e.g. a union's 'variants' key would vanish).
    "id", "cmd", "params", "success", "result", "error", "code",
    "message", "event", "data", "severity", "source", "format",
    "name", "tag", "label", "type", "widget_hint", "constraints",
    "default", "minimum", "maximum", "step", "minLength", "maxLength",
    "values", "value", "title", "minItems", "maxItems", "items",
    "variants", "fields", "builtin", "nogui", "tab", "description", "group",
    "order", "autoupdate", "timeout_ms", "min_period", "max_period",
    "default_period", "protocol_version", "device_name", "device_id", "firmware",
    "cmd_name",
)
_CBOR_TAG_NAMES = {cbor_key_tag(name): name for name in _CBOR_PROTOCOL_KEYS}
_CBOR_NAME_TAGS = {name: tag for tag, name in _CBOR_TAG_NAMES.items()}


def _register_cbor_name(name: str) -> int:
    tag = _CBOR_NAME_TAGS.get(name, cbor_key_tag(name))
    previous = _CBOR_TAG_NAMES.get(tag)
    if previous not in (None, name):
        raise ValueError(f"CBOR tag collision: {tag} maps to {previous!r} "
                         f"and {name!r}")
    _CBOR_TAG_NAMES[tag] = name
    _CBOR_NAME_TAGS[name] = tag
    return tag


def _cbor_head(major: int, value: int) -> bytes:
    if value < 0:
        raise ValueError("negative CBOR argument")
    prefix = major << 5
    if value < 24:
        return bytes((prefix | value,))
    if value <= 0xff:
        return bytes((prefix | 24, value))
    if value <= 0xffff:
        return bytes((prefix | 25,)) + struct.pack(">H", value)
    if value <= 0xffffffff:
        return bytes((prefix | 26,)) + struct.pack(">I", value)
    if value <= 0xffffffffffffffff:
        return bytes((prefix | 27,)) + struct.pack(">Q", value)
    raise ValueError("integer is outside CBOR uint64 range")


def _encode_cbor_value(value: Any) -> bytes:
    if value is None:
        return b"\xf6"
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if isinstance(value, int):
        if value >= 0:
            return _cbor_head(0, value)
        return _cbor_head(1, -1 - value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite CBOR number")
        return b"\xfa" + struct.pack(">f", value)
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return _cbor_head(3, len(encoded)) + encoded
    if isinstance(value, list):
        return b"\x9f" + b"".join(_encode_cbor_value(item)
                                  for item in value) + b"\xff"
    if isinstance(value, dict):
        parts = [b"\xbf"]
        for key, item in value.items():
            if isinstance(key, str):
                numeric_key = _register_cbor_name(key)
            elif isinstance(key, int) and 0 < key <= 0xffff:
                numeric_key = key
            else:
                raise ValueError("CBOR map keys must be strings or uint16 tags")
            parts.append(_cbor_head(0, numeric_key))
            parts.append(_encode_cbor_value(item))
        parts.append(b"\xff")
        return b"".join(parts)
    raise ValueError(f"unsupported CBOR value: {type(value).__name__}")


def encode_cbor(message: dict[str, Any]) -> bytes:
    if not isinstance(message, dict):
        raise ValueError("CBOR root must be an object")
    return _encode_cbor_value(message)


def _cbor_argument(data: bytes, offset: int, additional: int) \
        -> tuple[int, int]:
    if additional < 24:
        return additional, offset
    sizes = {24: 1, 25: 2, 26: 4, 27: 8}
    size = sizes.get(additional)
    if size is None or offset + size > len(data):
        raise ValueError("invalid CBOR argument")
    return int.from_bytes(data[offset:offset + size], "big"), offset + size


def _decode_cbor_value(data: bytes, offset: int) -> tuple[Any, int]:
    if offset >= len(data):
        raise ValueError("truncated CBOR value")
    initial = data[offset]
    offset += 1
    if initial == 0xff:
        raise ValueError("unexpected CBOR break")
    major, additional = initial >> 5, initial & 31
    indefinite = additional == 31
    if indefinite and major not in (4, 5):
        raise ValueError("unsupported indefinite CBOR value")
    if major in (0, 1):
        value, offset = _cbor_argument(data, offset, additional)
        return (value if major == 0 else -1 - value), offset
    if major == 3:
        length, offset = _cbor_argument(data, offset, additional)
        if offset + length > len(data):
            raise ValueError("truncated CBOR string")
        return data[offset:offset + length].decode("utf-8"), offset + length
    if major == 4:
        count = None
        if not indefinite:
            count, offset = _cbor_argument(data, offset, additional)
        result = []
        while count is None or len(result) < count:
            if count is None and offset < len(data) and data[offset] == 0xff:
                return result, offset + 1
            item, offset = _decode_cbor_value(data, offset)
            result.append(item)
        return result, offset
    if major == 5:
        count = None
        if not indefinite:
            count, offset = _cbor_argument(data, offset, additional)
        result: dict[str | int, Any] = {}
        while count is None or len(result) < count:
            if count is None and offset < len(data) and data[offset] == 0xff:
                return result, offset + 1
            key, offset = _decode_cbor_value(data, offset)
            if not isinstance(key, (int, str)):
                raise ValueError("invalid CBOR map key")
            mapped_key = _CBOR_TAG_NAMES.get(key, key) if isinstance(key, int) \
                else key
            if mapped_key in result:
                raise ValueError(f"duplicate CBOR key: {mapped_key}")
            result[mapped_key], offset = _decode_cbor_value(data, offset)
        return result, offset
    if major == 7:
        if additional == 20:
            return False, offset
        if additional == 21:
            return True, offset
        if additional == 22:
            return None, offset
        formats = {25: (">e", 2), 26: (">f", 4), 27: (">d", 8)}
        spec = formats.get(additional)
        if spec is None or offset + spec[1] > len(data):
            raise ValueError("unsupported CBOR simple value")
        value = struct.unpack_from(spec[0], data, offset)[0]
        if not math.isfinite(value):
            raise ValueError("non-finite CBOR number")
        return value, offset + spec[1]
    raise ValueError(f"unsupported CBOR major type {major}")


def _register_cbor_tags(value: Any) -> None:
    if isinstance(value, dict):
        name = value.get("name")
        tag = value.get("tag")
        if isinstance(name, str) and isinstance(tag, int) and 0 < tag <= 0xffff:
            previous = _CBOR_TAG_NAMES.get(tag)
            if previous not in (None, name):
                raise ValueError(f"CBOR tag collision: {tag} maps to "
                                 f"{previous!r} and {name!r}")
            _CBOR_TAG_NAMES[tag] = name
            _CBOR_NAME_TAGS[name] = tag
        for item in value.values():
            _register_cbor_tags(item)
    elif isinstance(value, list):
        for item in value:
            _register_cbor_tags(item)


def decode_cbor(payload: bytes) -> dict[str, Any]:
    value, offset = _decode_cbor_value(payload, 0)
    if offset != len(payload) or not isinstance(value, dict):
        raise ValueError("trailing bytes after CBOR object")
    _register_cbor_tags(value)
    return value


def crc16_ccitt_false(data: bytes | bytearray | memoryview) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 \
                else (crc << 1) & 0xFFFF
    return crc


def _cstring(value: str) -> bytes:
    encoded = value.encode("utf-8")
    if b"\0" in encoded:
        raise ValueError("BSON keys cannot contain NUL")
    return encoded + b"\0"


def _bson_element(key: str, value: Any) -> bytes:
    name = _cstring(key)
    if value is None:
        return b"\x0a" + name
    if isinstance(value, bool):
        return b"\x08" + name + bytes((int(value),))
    if isinstance(value, int):
        if -(1 << 31) <= value < (1 << 31):
            return b"\x10" + name + struct.pack("<i", value)
        if -(1 << 63) <= value < (1 << 63):
            return b"\x12" + name + struct.pack("<q", value)
        raise ValueError("integer is outside BSON int64 range")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite BSON number")
        return b"\x01" + name + struct.pack("<d", value)
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if b"\0" in encoded:
            raise ValueError("BSON strings containing NUL are unsupported")
        return (b"\x02" + name + struct.pack("<i", len(encoded) + 1) +
                encoded + b"\0")
    if isinstance(value, dict):
        return b"\x03" + name + encode_bson(value)
    if isinstance(value, list):
        body = b"".join(_bson_element(str(index), item)
                        for index, item in enumerate(value))
        return b"\x04" + name + struct.pack("<i", len(body) + 5) + body + b"\0"
    raise ValueError(f"unsupported BSON value: {type(value).__name__}")


def encode_bson(message: dict[str, Any]) -> bytes:
    if not isinstance(message, dict):
        raise ValueError("BSON root must be an object")
    body = b"".join(_bson_element(str(key), value)
                    for key, value in message.items())
    size = len(body) + 5
    if size > 0x7FFFFFFF:
        raise ValueError("BSON document is too large")
    return struct.pack("<i", size) + body + b"\0"


def _need(data: bytes, offset: int, size: int, limit: int) -> None:
    if offset < 0 or size < 0 or offset + size > limit:
        raise ValueError("truncated BSON value")


def _read_cstring(data: bytes, offset: int, limit: int) -> tuple[str, int]:
    end = data.find(b"\0", offset, limit)
    if end < 0:
        raise ValueError("unterminated BSON cstring")
    return data[offset:end].decode("utf-8"), end + 1


def _decode_document(data: bytes, start: int, array: bool = False) \
        -> tuple[dict[str, Any] | list[Any], int]:
    _need(data, start, 5, len(data))
    size = struct.unpack_from("<i", data, start)[0]
    if size < 5 or start + size > len(data):
        raise ValueError("invalid BSON document length")
    end = start + size
    if data[end - 1] != 0:
        raise ValueError("BSON document has no terminator")
    values: dict[str, Any] = {}
    offset = start + 4
    expected_index = 0
    while offset < end - 1:
        value_type = data[offset]
        offset += 1
        key, offset = _read_cstring(data, offset, end - 1)
        if key in values:
            raise ValueError(f"duplicate BSON key: {key}")
        if array and key != str(expected_index):
            raise ValueError("non-canonical BSON array index")
        expected_index += 1
        if value_type == 0x01:
            _need(data, offset, 8, end - 1)
            value = struct.unpack_from("<d", data, offset)[0]
            offset += 8
            if not math.isfinite(value):
                raise ValueError("non-finite BSON number")
        elif value_type == 0x02:
            _need(data, offset, 4, end - 1)
            length = struct.unpack_from("<i", data, offset)[0]
            offset += 4
            if length < 1:
                raise ValueError("invalid BSON string length")
            _need(data, offset, length, end - 1)
            if data[offset + length - 1] != 0:
                raise ValueError("unterminated BSON string")
            value = data[offset:offset + length - 1].decode("utf-8")
            offset += length
        elif value_type in (0x03, 0x04):
            value, offset = _decode_document(data, offset, value_type == 0x04)
        elif value_type == 0x08:
            _need(data, offset, 1, end - 1)
            if data[offset] not in (0, 1):
                raise ValueError("invalid BSON boolean")
            value = bool(data[offset])
            offset += 1
        elif value_type == 0x0A:
            value = None
        elif value_type == 0x10:
            _need(data, offset, 4, end - 1)
            value = struct.unpack_from("<i", data, offset)[0]
            offset += 4
        elif value_type == 0x12:
            _need(data, offset, 8, end - 1)
            value = struct.unpack_from("<q", data, offset)[0]
            offset += 8
        else:
            raise ValueError(f"unsupported BSON type 0x{value_type:02x}")
        values[key] = value
    if offset != end - 1:
        raise ValueError("invalid BSON element boundary")
    if array:
        return [values[str(index)] for index in range(len(values))], end
    return values, end


def decode_bson(payload: bytes) -> dict[str, Any]:
    value, end = _decode_document(payload, 0)
    if end != len(payload) or not isinstance(value, dict):
        raise ValueError("trailing bytes after BSON document")
    return value


def encode_payload(message: dict[str, Any], wire_format: str) -> bytes:
    if wire_format == FORMAT_JSON:
        return json.dumps(message, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
    if wire_format == FORMAT_BSON:
        return encode_bson(message)
    if wire_format == FORMAT_CBOR:
        return encode_cbor(message)
    raise ValueError(f"unknown Krul wire format: {wire_format}")


def decode_payload(payload: bytes, wire_format: str) -> dict[str, Any]:
    if wire_format == FORMAT_JSON:
        value = json.loads(payload.decode("utf-8"))
    elif wire_format == FORMAT_BSON:
        value = decode_bson(payload)
    elif wire_format == FORMAT_CBOR:
        value = decode_cbor(payload)
    else:
        raise ValueError(f"unknown Krul wire format: {wire_format}")
    if not isinstance(value, dict):
        raise ValueError("Krul payload root must be an object")
    return value


def encode_frame(message: dict[str, Any], wire_format: str) -> bytes:
    payload = encode_payload(message, wire_format)
    if not 0 < len(payload) <= MAX_PAYLOAD_SIZE:
        raise ValueError("Krul payload exceeds the transport limit")
    length = struct.pack("<I", len(payload))
    checksum = crc16_ccitt_false(length + payload)
    return MAGICS[wire_format] + length + payload + struct.pack("<H", checksum)


@dataclass(frozen=True)
class Frame:
    wire_format: str
    payload: bytes


class FrameParser:
    def __init__(self, max_payload_size: int = MAX_PAYLOAD_SIZE):
        if max_payload_size <= 0:
            raise ValueError("max_payload_size must be positive")
        self.max_payload_size = max_payload_size
        self.buffer = bytearray()

    def feed(self, data: bytes) -> tuple[list[Frame], list[str]]:
        self.buffer.extend(data)
        frames: list[Frame] = []
        errors: list[str] = []
        while True:
            candidates = [(self.buffer.find(magic), wire_format)
                          for wire_format, magic in MAGICS.items()]
            candidates = [(position, wire_format)
                          for position, wire_format in candidates
                          if position >= 0]
            if not candidates:
                if len(self.buffer) > 3:
                    del self.buffer[:-3]
                break
            position, wire_format = min(candidates)
            if position:
                del self.buffer[:position]
            if len(self.buffer) < HEADER_SIZE:
                break
            payload_size = struct.unpack_from("<I", self.buffer, 4)[0]
            if not 0 < payload_size <= self.max_payload_size:
                errors.append(f"invalid Krul payload length: {payload_size}")
                del self.buffer[0]
                continue
            frame_size = HEADER_SIZE + payload_size + CRC_SIZE
            if len(self.buffer) < frame_size:
                break
            expected = struct.unpack_from("<H", self.buffer,
                                          HEADER_SIZE + payload_size)[0]
            actual = crc16_ccitt_false(
                memoryview(self.buffer)[4:HEADER_SIZE + payload_size])
            if actual != expected:
                errors.append(
                    f"Krul CRC mismatch: expected 0x{expected:04x}, "
                    f"calculated 0x{actual:04x}")
                del self.buffer[0]
                continue
            payload = bytes(self.buffer[HEADER_SIZE:HEADER_SIZE + payload_size])
            del self.buffer[:frame_size]
            frames.append(Frame(wire_format, payload))
        return frames, errors
