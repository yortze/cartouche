"""Lecture des fichiers PARAM.SFO (format commun PS3, PS4 et PS Vita)."""

from __future__ import annotations

import re
import struct
from pathlib import Path

SFO_MAGIC = b"\x00PSF"
FMT_UTF8_SPECIAL = 0x0004  # données brutes, sans zéro final
FMT_UTF8 = 0x0204          # texte terminé par un zéro
FMT_INT32 = 0x0404         # entier 32 bits little-endian

_HEADER = struct.Struct("<4sIIII")
_INDEX = struct.Struct("<HHIII")


class SfoError(ValueError):
    """Le contenu n'est pas un PARAM.SFO valide."""


def parse_sfo(data: bytes) -> dict[str, str | int]:
    """Renvoie les paires clé/valeur d'un PARAM.SFO."""
    if len(data) < _HEADER.size or data[:4] != SFO_MAGIC:
        raise SfoError("Ce n'est pas un fichier PARAM.SFO")
    _magic, _version, key_table, data_table, count = _HEADER.unpack_from(data, 0)
    if count > 4096 or _HEADER.size + count * _INDEX.size > len(data):
        raise SfoError("PARAM.SFO tronqué ou corrompu")
    if key_table > len(data) or data_table > len(data):
        raise SfoError("PARAM.SFO tronqué ou corrompu")

    values: dict[str, str | int] = {}
    for i in range(count):
        key_off, fmt, length, _max_len, data_off = _INDEX.unpack_from(data, _HEADER.size + i * _INDEX.size)
        key_start = key_table + key_off
        key_end = data.find(b"\0", key_start)
        if key_start >= len(data) or key_end < 0:
            raise SfoError("Nom de clé invalide dans PARAM.SFO")
        key = data[key_start:key_end].decode("ascii", "replace")
        start = data_table + data_off
        raw = data[start:start + length]
        if fmt == FMT_INT32 and len(raw) >= 4:
            values[key] = struct.unpack_from("<I", raw)[0]
        else:
            values[key] = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    return values


def read_sfo(path: str | Path) -> dict[str, str | int]:
    with open(path, "rb") as handle:
        return parse_sfo(handle.read(1 << 20))


def pick_title(sfo: dict, prefer_french: bool = True) -> str:
    """Titre lisible du jeu, en français s'il existe (TITLE_02)."""
    candidates = ["TITLE_02", "TITLE"] if prefer_french else ["TITLE", "TITLE_02"]
    for key in candidates:
        value = sfo.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def text(sfo: dict, key: str) -> str:
    value = sfo.get(key)
    return value.strip() if isinstance(value, str) else ""


def format_ps4_system_ver(value: object) -> str:
    """0x05050000 -> '5.05'."""
    if not isinstance(value, int) or value <= 0:
        return ""
    major = (value >> 24) & 0xFF
    minor = (value >> 16) & 0xFF
    return f"{major:x}.{minor:02x}"


def format_ps3_system_ver(value: str) -> str:
    """'04.4100' -> '4.41'."""
    match = re.match(r"^\s*0*(\d+)\.(\d{2})", value or "")
    if not match:
        return (value or "").strip()
    return f"{int(match.group(1))}.{match.group(2)}"


def version_key(value: str) -> tuple[int, ...]:
    """Clé de comparaison pour des versions du type '01.09'."""
    return tuple(int(part) for part in re.findall(r"\d+", value or ""))
