"""Réglages de Cartouche, enregistrés dans %APPDATA%\\Cartouche\\settings.json."""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path

from .paths import data_dir

DEFAULTS: dict = {
    "shadps4_exe": "",        # vide = choix automatique
    "pkg_extractor": "",      # vide = recherche automatique
    "rpcs3_exe": "",
    "ps4_games_dir": "",      # vide = premier dossier de jeux de shadPS4
    "pkg_dirs": [],           # dossiers parcourus à la recherche de .pkg
    "pkg_files": [],          # fichiers .pkg ajoutés un par un
    "fullscreen": False,
    "show_fps": False,
    "show_console": False,
    "prefer_french_titles": True,
    "first_run_done": False,
}

_TYPES = {key: type(value) for key, value in DEFAULTS.items()}


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "settings.json"
        self._lock = threading.RLock()
        self._data = deepcopy(DEFAULTS)
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in _TYPES and isinstance(value, _TYPES[key]):
                    self._data[key] = value

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    def get(self, key: str):
        with self._lock:
            return deepcopy(self._data.get(key, DEFAULTS.get(key)))

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self._data)

    def update(self, changes: dict) -> dict:
        """Applique les changements valides et renvoie ceux qui ont été refusés."""
        rejected = {}
        with self._lock:
            for key, value in changes.items():
                expected = _TYPES.get(key)
                if expected is None:
                    rejected[key] = "réglage inconnu"
                    continue
                if expected is list:
                    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                        rejected[key] = "liste de chemins attendue"
                        continue
                    value = _unique_paths(value)
                elif not isinstance(value, expected):
                    rejected[key] = f"type {expected.__name__} attendu"
                    continue
                self._data[key] = value
            self.save()
        return rejected

    def add_to_list(self, key: str, values: list[str]) -> None:
        with self._lock:
            current = list(self._data.get(key, []))
            self._data[key] = _unique_paths(current + values)
            self.save()

    def remove_from_list(self, key: str, value: str) -> None:
        with self._lock:
            target = os.path.normcase(os.path.abspath(value))
            self._data[key] = [
                v for v in self._data.get(key, []) if os.path.normcase(os.path.abspath(v)) != target
            ]
            self.save()


def _unique_paths(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        norm = os.path.normcase(os.path.abspath(value))
        if norm not in seen:
            seen.add(norm)
            out.append(os.path.abspath(value))
    return out
