"""Lecture des fichiers PKG PlayStation (PS4 et PS3).

Seules les parties non chiffrées sont lues : l'en-tête, la table des entrées,
le PARAM.SFO et l'icône pour la PS4, les métadonnées pour la PS3. Aucune clé
de console n'est utilisée : l'extraction réelle reste le travail de
pkg_extractor (PS4) et de RPCS3 (PS3).
"""

from __future__ import annotations

import os
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .sfo import (
    SfoError,
    format_ps3_system_ver,
    format_ps4_system_ver,
    parse_sfo,
    pick_title,
    text,
)

PS4_MAGIC = b"\x7fCNT"
PS3_MAGIC = b"\x7fPKG"
XAR_MAGIC = b"xar!"  # installeurs macOS (.pkg)

_PS4_ENTRY_PARAM_SFO = 0x1000
_PS4_ENTRY_PIC1 = 0x1006
_PS4_ENTRY_ICON0 = 0x1200
_PS4_ENTRY_ENCRYPTED = 0x80000000
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_IMAGE_SIZE = 16 << 20

_PS3_FLAG_EBOOT = 0x02
_PS3_FLAG_PATCH = 0x10
_PS3_FLAG_EDAT = 0x80

# Types de contenu PS3 (d'après rpcs3/Crypto/unpkg.h) -> (catégorie, libellé)
_PS3_CONTENT_TYPES = {
    0x04: ("data", "Données de jeu"),
    0x05: ("game", "Jeu PS3"),
    0x06: ("game", "Jeu PS1 Classic"),
    0x07: ("other", "Jeu PSP / PC Engine"),
    0x09: ("theme", "Thème"),
    0x0A: ("other", "Widget"),
    0x0B: ("license", "Licence"),
    0x0C: ("other", "Module système"),
    0x0D: ("other", "Avatar PSN"),
    0x0E: ("other", "Jeu PSPgo"),
    0x0F: ("other", "Minis (PSP)"),
    0x10: ("other", "NEOGEO Station"),
    0x11: ("other", "Carte mémoire virtuelle"),
    0x12: ("game", "Jeu PS2 Classic"),
    0x14: ("game", "PSP Remastered"),
}
_VITA_CONTENT_TYPES = {0x15, 0x16, 0x17, 0x18, 0x19, 0x1D, 0x1F}
_PS4_HEADER_TYPES = {
    0x1A: ("game", "Jeu ou mise à jour"),
    0x1B: ("dlc", "DLC"),
    0x1C: ("dlc", "Licence de DLC"),
    0x1E: ("update", "Mise à jour (delta)"),
}
_PS4_CATEGORIES = {
    "gd": ("game", "Jeu"),
    "gp": ("update", "Mise à jour"),
    "ac": ("dlc", "DLC"),
}


class PkgError(ValueError):
    """Le fichier n'est pas un PKG PlayStation lisible."""


@dataclass
class Region:
    offset: int
    size: int


@dataclass
class PkgInfo:
    path: str
    file_size: int
    platform: str  # PS4 | PS3 | VITA | PSP | PS5
    content_id: str = ""
    title_id: str = ""
    title: str = ""
    category: str = "unknown"  # game | update | dlc | data | app | theme | license | other | unknown
    kind_label: str = ""
    version: str = ""
    system_version: str = ""
    dlc_label: str = ""
    install_dir: str = ""
    content_size: int = 0
    declared_size: int = 0
    retail: bool | None = None
    supported: bool = True
    notes: list[str] = field(default_factory=list)
    sfo: dict = field(default_factory=dict)
    icon: Region | None = None
    background: Region | None = None

    @property
    def truncated(self) -> bool:
        return bool(self.declared_size and self.declared_size > self.file_size)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["truncated"] = self.truncated
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "PkgInfo":
        data = dict(data)
        data.pop("truncated", None)
        for key in ("icon", "background"):
            if data.get(key):
                data[key] = Region(**data[key])
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def sniff(path: str | os.PathLike) -> str:
    """Identifie rapidement un fichier .pkg : 'PS4', 'PS3', 'macOS' ou ''."""
    try:
        with open(path, "rb") as handle:
            magic = handle.read(4)
    except OSError:
        return ""
    if magic == PS4_MAGIC:
        return "PS4"
    if magic == PS3_MAGIC:
        return "PS3"
    if magic == XAR_MAGIC:
        return "macOS"
    return ""


def read_pkg(path: str | os.PathLike, prefer_french: bool = True) -> PkgInfo:
    """Analyse un PKG PS4 ou PS3 sans le déchiffrer."""
    path = Path(path)
    try:
        file_size = path.stat().st_size
        handle = open(path, "rb")
    except OSError as exc:
        raise PkgError(f"Impossible d'ouvrir le fichier : {exc.strerror or exc}") from exc
    with handle:
        magic = handle.read(4)
        if magic == PS4_MAGIC:
            info = PkgInfo(path=str(path), file_size=file_size, platform="PS4")
            _read_ps4(handle, info, prefer_french)
        elif magic == PS3_MAGIC:
            info = PkgInfo(path=str(path), file_size=file_size, platform="PS3")
            _read_ps3(handle, info)
        elif magic == XAR_MAGIC:
            raise PkgError("C'est un installeur macOS, pas un PKG PlayStation")
        else:
            raise PkgError("Ce n'est pas un PKG PlayStation")
    if info.truncated:
        info.notes.insert(0, "Fichier incomplet : le PKG est plus petit que prévu (téléchargement interrompu ?)")
        info.supported = False
    return info


def read_region(path: str | os.PathLike, region: Region | None, png_only: bool = True) -> bytes | None:
    """Lit un bloc (icône, fond) dans un PKG."""
    if not region or region.size <= 0 or region.size > _MAX_IMAGE_SIZE:
        return None
    try:
        with open(path, "rb") as handle:
            handle.seek(region.offset)
            data = handle.read(region.size)
    except OSError:
        return None
    if len(data) != region.size:
        return None
    if png_only and not data.startswith(_PNG_SIGNATURE):
        return None
    return data


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", "replace").strip()


def _title_id_from_content_id(content_id: str) -> str:
    # Format : XX0000-CUSA00000_00-LIBELLE0000000000
    if len(content_id) >= 16 and content_id[6] == "-":
        return content_id[7:16]
    return ""


def _hex_version(raw: bytes) -> str:
    """Octets BCD vers 'XX.YY' (même règle que RPCS3)."""
    value = raw.hex()
    return f"{value[:2]}.{value[2:]}" if len(value) > 2 else value


# --------------------------------------------------------------------------- PS4

def _read_ps4(handle, info: PkgInfo, prefer_french: bool) -> None:
    handle.seek(0)
    header = handle.read(0x1000)
    if len(header) < 0x440:
        raise PkgError("En-tête PS4 tronqué")
    (_magic, _pkg_type, _unknown, _file_count, entry_count, _sc_count, _entry_count2,
     table_offset, _sc_size, _body_offset, body_size, _content_offset,
     _content_size) = struct.unpack_from(">IIIIIHHIIQQQQ", header, 0)
    info.content_id = _cstr(header[0x40:0x64])
    _drm_type, content_type, _content_flags = struct.unpack_from(">III", header, 0x70)
    _pfs_offset, pfs_size = struct.unpack_from(">QQ", header, 0x410)
    info.declared_size = struct.unpack_from(">Q", header, 0x430)[0]
    info.content_size = pfs_size or body_size or info.file_size

    entries: dict[int, tuple[int, int, int]] = {}
    if 0 < entry_count < 20000 and table_offset + entry_count * 32 <= info.file_size:
        handle.seek(table_offset)
        table = handle.read(entry_count * 32)
        for i in range(len(table) // 32):
            entry_id, _name_off, flags1, _flags2, offset, size, _pad = struct.unpack_from(">IIIIIIQ", table, i * 32)
            entries.setdefault(entry_id, (offset, size, flags1))
    else:
        info.notes.append("Table des fichiers illisible")

    sfo_entry = entries.get(_PS4_ENTRY_PARAM_SFO)
    if sfo_entry:
        offset, size, flags = sfo_entry
        if flags & _PS4_ENTRY_ENCRYPTED:
            info.notes.append("PARAM.SFO chiffré : nom du jeu indisponible avant installation")
        elif 0 < size <= (1 << 20) and offset + size <= info.file_size:
            handle.seek(offset)
            try:
                info.sfo = parse_sfo(handle.read(size))
            except SfoError:
                info.notes.append("PARAM.SFO illisible")
    else:
        info.notes.append("Pas de PARAM.SFO dans le PKG")

    for entry_id, attr in ((_PS4_ENTRY_ICON0, "icon"), (_PS4_ENTRY_PIC1, "background")):
        entry = entries.get(entry_id)
        if entry:
            offset, size, flags = entry
            if not flags & _PS4_ENTRY_ENCRYPTED and 0 < size <= _MAX_IMAGE_SIZE and offset + size <= info.file_size:
                setattr(info, attr, Region(offset, size))

    sfo = info.sfo
    info.title = pick_title(sfo, prefer_french)
    info.title_id = text(sfo, "TITLE_ID") or _title_id_from_content_id(info.content_id)
    if text(sfo, "CONTENT_ID"):
        info.content_id = text(sfo, "CONTENT_ID")
    category = text(sfo, "CATEGORY").lower()
    if category in _PS4_CATEGORIES:
        info.category, info.kind_label = _PS4_CATEGORIES[category]
    elif category.startswith("gd"):
        info.category, info.kind_label = "app", "Application"
    elif category:
        info.category, info.kind_label = "other", f"Contenu « {category} »"
    else:
        info.category, info.kind_label = _PS4_HEADER_TYPES.get(content_type, ("unknown", "Type inconnu"))

    if info.category == "dlc":
        info.version = text(sfo, "VERSION") or text(sfo, "APP_VER")
        if len(info.content_id) >= 36:
            info.dlc_label = info.content_id[20:36]
    else:
        info.version = text(sfo, "APP_VER") or text(sfo, "VERSION")
    info.system_version = format_ps4_system_ver(sfo.get("SYSTEM_VER"))
    if info.category in ("other", "unknown"):
        info.supported = False


# --------------------------------------------------------------------------- PS3

def _read_ps3(handle, info: PkgInfo) -> None:
    handle.seek(0)
    header = handle.read(0x100)
    if len(header) < 0x80:
        raise PkgError("En-tête PS3 tronqué")
    (_magic, release_type, platform, meta_offset, meta_count, meta_size, _file_count,
     declared_size, _data_offset, data_size) = struct.unpack_from(">IHHIIIIQQQ", header, 0)
    info.content_id = _cstr(header[0x30:0x60])
    info.title_id = _title_id_from_content_id(info.content_id)
    info.retail = release_type == 0x8000
    info.declared_size = declared_size
    info.content_size = data_size or info.file_size

    content_type = 0
    flags = 0
    package_version = ""
    app_version = ""
    if 0 < meta_count < 64 and 0 < meta_offset < info.file_size:
        handle.seek(meta_offset)
        meta = handle.read(min(max(meta_size, 0x400), 0x4000))
        pos = 0
        for _ in range(meta_count):
            if pos + 8 > len(meta):
                break
            meta_id, size = struct.unpack_from(">II", meta, pos)
            pos += 8
            value = meta[pos:pos + size]
            pos += size
            if meta_id == 0x2 and size == 4:
                content_type = struct.unpack(">I", value)[0]
            elif meta_id == 0x3 and size == 4:
                flags = struct.unpack(">I", value)[0]
            elif meta_id == 0x5 and size == 4:
                package_version = _hex_version(value[2:4])
            elif meta_id == 0x6 and size == 12:
                title_id = _cstr(value)
                if title_id:
                    info.title_id = title_id
            elif meta_id == 0x8 and size == 8:
                info.system_version = format_ps3_system_ver(_hex_version(value[1:4]))
                app_version = _hex_version(value[6:8])
            elif meta_id == 0xA and size > 8:
                info.install_dir = _cstr(value[8:])
    else:
        info.notes.append("Métadonnées PS3 illisibles")

    info.version = app_version if app_version not in ("", "00.00") else package_version

    if platform == 0x0002 or content_type in _VITA_CONTENT_TYPES:
        info.platform = "VITA" if content_type in _VITA_CONTENT_TYPES else "PSP"
        info.category, info.kind_label = "other", "PKG PSP / PS Vita"
        info.supported = False
        info.notes.append("RPCS3 n'émule que la PS3 : ce paquet PSP / PS Vita n'est pas pris en charge")
        return
    if content_type == 0x20:
        info.platform = "PS5"
        info.category, info.kind_label = "other", "PKG PS5"
        info.supported = False
        return

    info.category, info.kind_label = _PS3_CONTENT_TYPES.get(content_type, ("unknown", "Type inconnu"))
    if content_type == 0x04:
        if flags & _PS3_FLAG_PATCH:
            info.category, info.kind_label = "update", "Mise à jour"
        elif flags & _PS3_FLAG_EDAT and not flags & _PS3_FLAG_EBOOT:
            info.category, info.kind_label = "dlc", "DLC"
    if content_type in (0x06, 0x12):
        info.notes.append("RPCS3 ne lance pas encore les PS1 / PS2 Classics")
    if info.category in ("other", "unknown"):
        info.notes.append("Ce type de contenu ne se lance pas comme un jeu")
