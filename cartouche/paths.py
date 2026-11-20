"""Emplacements utilisés par Cartouche et par les émulateurs."""

from __future__ import annotations

import os
from pathlib import Path


def appdata() -> Path:
    """Dossier %APPDATA% (Roaming)."""
    value = os.environ.get("APPDATA")
    return Path(value) if value else Path.home() / "AppData" / "Roaming"


def data_dir() -> Path:
    """Dossier de données de Cartouche (réglages, cache, journaux).

    La variable d'environnement CARTOUCHE_DATA permet de le déplacer (tests).
    """
    override = os.environ.get("CARTOUCHE_DATA")
    path = Path(override) if override else appdata() / "Cartouche"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def downloads_dir() -> Path:
    """Dossier Téléchargements réel de l'utilisateur (il peut avoir été déplacé)."""
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
        path = Path(os.path.expandvars(value))
        if path.is_dir():
            return path
    except (ImportError, OSError):
        pass
    return Path.home() / "Downloads"


def same_path(a: str | os.PathLike | None, b: str | os.PathLike | None) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
