"""Fabrique de faux fichiers PlayStation (PARAM.SFO, PKG PS4 et PS3) pour les tests."""

from __future__ import annotations

import struct
import zlib

PNG_1PX = (
    b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    + struct.pack(">I", zlib.crc32(b"IHDR" + struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)))
    + struct.pack(">I", 12) + b"IDAT" + zlib.compress(b"\x00\xff\x80\x00")
    + struct.pack(">I", zlib.crc32(b"IDAT" + zlib.compress(b"\x00\xff\x80\x00")))
    + struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
)


def build_sfo(entries: dict) -> bytes:
    keys = list(entries)
    key_table = b""
    key_offsets = []
    for key in keys:
        key_offsets.append(len(key_table))
        key_table += key.encode("ascii") + b"\0"
    while len(key_table) % 4:
        key_table += b"\0"
    index = b""
    data_table = b""
    for key, key_offset in zip(keys, key_offsets):
        value = entries[key]
        if isinstance(value, int):
            raw, fmt, length, max_len = struct.pack("<I", value), 0x0404, 4, 4
        else:
            encoded = value.encode("utf-8") + b"\0"
            max_len = (len(encoded) + 3) // 4 * 4
            raw, fmt, length = encoded.ljust(max_len, b"\0"), 0x0204, len(encoded)
        index += struct.pack("<HHIII", key_offset, fmt, length, max_len, len(data_table))
        data_table += raw
    key_table_offset = 20 + len(index)
    data_table_offset = key_table_offset + len(key_table)
    header = struct.pack("<4sIIII", b"\0PSF", 0x101, key_table_offset, data_table_offset, len(keys))
    return header + index + key_table + data_table


def ps4_sfo(title_id="CUSA12345", category="gd", app_ver="01.00", title="Jeu Test",
            content_id=None, version="01.00", title_fr=None) -> bytes:
    content_id = content_id or f"EP0001-{title_id}_00-TESTGAME00000000"
    entries = {"APP_VER": app_ver, "CATEGORY": category, "CONTENT_ID": content_id,
               "SYSTEM_VER": 0x05050000, "TITLE": title, "TITLE_ID": title_id, "VERSION": version}
    if title_fr:
        entries["TITLE_02"] = title_fr
    return build_sfo(entries)


def build_ps4_pkg(sfo: bytes | None, content_id: str, icon: bytes | None = PNG_1PX,
                  encrypted_sfo: bool = False, declared_size: int | None = None,
                  content_type: int = 0x1A, pfs_size: int = 0x200000) -> bytes:
    entries = []
    if sfo is not None:
        entries.append((0x1000, sfo, encrypted_sfo))
    if icon is not None:
        entries.append((0x1200, icon, False))
    table_offset = 0x1000
    data_offset = table_offset + len(entries) * 32
    table = b""
    blobs = b""
    for entry_id, blob, encrypted in entries:
        offset = data_offset + len(blobs)
        table += struct.pack(">IIIIIIQ", entry_id, 0, 0x80000000 if encrypted else 0, 0, offset, len(blob), 0)
        blobs += blob + b"\0" * ((-len(blob)) % 16)
    total = data_offset + len(blobs)
    header = bytearray(0x1000)
    struct.pack_into(">IIIIIHHIIQQQQ", header, 0, 0x7F434E54, 0, 0, len(entries), len(entries), 0,
                     len(entries), table_offset, 0, data_offset, len(blobs), 0, 0)
    header[0x40:0x40 + len(content_id)] = content_id.encode("ascii")
    struct.pack_into(">III", header, 0x70, 0xF, content_type, 0)
    struct.pack_into(">QQ", header, 0x410, 0, pfs_size)
    struct.pack_into(">Q", header, 0x430, declared_size if declared_size is not None else total)
    return bytes(header) + table + blobs


def build_ps3_pkg(content_id: str, content_type: int = 0x05, flags: int = 0x02,
                  package_version: bytes = b"\x01\x00", app_version: bytes = b"\x01\x02",
                  firmware: bytes = b"\x04\x41\x00", platform: int = 1, release: int = 0x8000,
                  install_dir: str | None = None, declared_size: int | None = None) -> bytes:
    items = []

    def item(meta_id: int, data: bytes) -> None:
        items.append(struct.pack(">II", meta_id, len(data)) + data)

    item(0x1, struct.pack(">I", 2))
    item(0x2, struct.pack(">I", content_type))
    item(0x3, struct.pack(">I", flags))
    item(0x4, struct.pack(">Q", 0x1000))
    item(0x5, b"\x12\x34" + package_version)
    item(0x8, b"\x00" + firmware + b"\x00\x00" + app_version)
    if install_dir:
        item(0xA, b"\0" * 8 + install_dir.encode("ascii").ljust(0x20, b"\0"))
    meta = b"".join(items)
    header = bytearray(0xC0)
    header[0:4] = b"\x7fPKG"
    struct.pack_into(">HH", header, 4, release, platform)
    struct.pack_into(">III", header, 8, 0xC0, len(items), len(meta))
    struct.pack_into(">I", header, 0x14, 1)
    total = 0xC0 + len(meta) + 0x100
    struct.pack_into(">QQQ", header, 0x18, declared_size or total, 0xC0 + len(meta), 0x100)
    header[0x30:0x30 + len(content_id)] = content_id.encode("ascii")
    return bytes(header) + meta + b"\0" * 0x100
