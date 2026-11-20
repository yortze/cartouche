"""Intégration avec RPCS3 (PS3) : détection, firmware, jeux installés, commandes."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from .paths import downloads_dir
from .sfo import SfoError, format_ps3_system_ver, pick_title, read_sfo, text

LAUNCHABLE_CATEGORIES = {"HG", "DG"}  # jeu sur disque dur, jeu disque copié


def find_rpcs3(custom: str = "") -> str:
    candidates: list[Path] = []
    if custom:
        candidates.append(Path(custom))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates += [
        Path(r"C:\RPCS3\rpcs3.exe"),
        program_files / "RPCS3" / "rpcs3.exe",
        Path.home() / "RPCS3" / "rpcs3.exe",
    ]
    for base in (downloads_dir(), Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop"):
        for pattern in ("rpcs3.exe", "*/rpcs3.exe", "*/*/rpcs3.exe"):
            candidates.extend(sorted(base.glob(pattern)))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


def vfs_paths(exe: str) -> dict[str, Path]:
    """Emplacements de dev_hdd0 et dev_flash (config/vfs.yml s'il existe)."""
    emu_dir = str(Path(exe).parent) + os.sep
    values: dict[str, str] = {}
    for candidate in (Path(emu_dir) / "config" / "vfs.yml", Path(emu_dir) / "vfs.yml"):
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            match = re.match(r'^\s*"?(\$\(EmulatorDir\)|/dev_hdd0/|/dev_flash/)"?\s*:\s*(.*?)\s*$', line)
            if match:
                values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
        break
    base = values.get("$(EmulatorDir)") or emu_dir

    def resolve(key: str, default: str) -> Path:
        value = values.get(key) or f"$(EmulatorDir){default}"
        return Path(value.replace("$(EmulatorDir)", base))

    return {"dev_hdd0": resolve("/dev_hdd0/", "dev_hdd0/"), "dev_flash": resolve("/dev_flash/", "dev_flash/")}


def firmware_version(dev_flash: Path) -> str:
    """Version du firmware PS3 installé dans RPCS3, ou '' s'il manque."""
    try:
        content = (dev_flash / "vsh" / "etc" / "version.txt").read_text(encoding="utf-8", errors="replace")
        match = re.search(r"release:(\d+\.\d+)", content)
        if match:
            return format_ps3_system_ver(match.group(1))
    except OSError:
        pass
    if (dev_flash / "vsh" / "module" / "vsh.self").is_file():
        return "installé"
    return ""


def game_key(folder: str | Path) -> str:
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8")).hexdigest()
    return "ps3-" + digest[:12]


def scan_games(dev_hdd0: Path, prefer_french: bool = True) -> tuple[list[dict], dict[str, str]]:
    """Jeux installés dans dev_hdd0/game, et version installée par identifiant."""
    games: list[dict] = []
    versions: dict[str, str] = {}
    root = Path(dev_hdd0) / "game"
    try:
        folders = sorted(c for c in root.iterdir() if c.is_dir())
    except OSError:
        return games, versions
    for folder in folders:
        try:
            sfo = read_sfo(folder / "PARAM.SFO")
        except (OSError, SfoError):
            continue
        title_id = text(sfo, "TITLE_ID") or folder.name
        version = text(sfo, "APP_VER") or text(sfo, "VERSION")
        versions[title_id] = version
        category = text(sfo, "CATEGORY").upper()
        if category not in LAUNCHABLE_CATEGORIES:
            continue
        eboot = folder / "USRDIR" / "EBOOT.BIN"
        games.append({
            "key": game_key(folder),
            "platform": "PS3",
            "emulator": "RPCS3",
            "title": pick_title(sfo, prefer_french) or folder.name,
            "title_id": title_id,
            "content_id": "",
            "category": category,
            "version": version,
            "system_version": format_ps3_system_ver(text(sfo, "PS3_SYSTEM_VER")),
            "folder": str(folder),
            "launch_target": str(eboot),
            "launchable": eboot.is_file(),
            "icon": str(folder / "ICON0.PNG") if (folder / "ICON0.PNG").is_file() else "",
            "background": str(folder / "PIC1.PNG") if (folder / "PIC1.PNG").is_file() else "",
            "update": None,
            "mods": False,
            "dlc": [],
            "custom_config": False,
            "note": "" if eboot.is_file() else "EBOOT.BIN introuvable dans USRDIR.",
        })
    games.sort(key=lambda g: g["title"].lower())
    return games, versions


def launch_command(exe: str, eboot: str, fullscreen: bool = False) -> list[str]:
    command = [exe, "--no-gui"]
    if fullscreen:
        command.append("--fullscreen")
    command.append(eboot)
    return command


def install_command(exe: str, pkg: str) -> list[str]:
    return [exe, "--installpkg", pkg]


def firmware_command(exe: str, pup: str) -> list[str]:
    return [exe, "--installfw", pup]
