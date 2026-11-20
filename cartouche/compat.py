"""Base de compatibilité publique de shadPS4 (statut de chaque jeu PS4)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path

from .paths import cache_dir

URL = ("https://github.com/shadps4-compatibility/shadps4-game-compatibility/"
       "releases/latest/download/compatibility_data.json")
ISSUE_URL = "https://github.com/shadps4-compatibility/shadps4-game-compatibility/issues/{}"
USER_AGENT = "Cartouche-launcher"

STATUSES = {
    "status-playable": ("playable", "Jouable"),
    "status-ingame": ("ingame", "En jeu"),
    "status-menus": ("menus", "Menus"),
    "status-boots": ("boots", "Démarre"),
    "status-nothing": ("nothing", "Ne démarre pas"),
    "status-unknown": ("unknown", "Inconnu"),
}
OS_ORDER = [("os-windows", "Windows"), ("os-linux", "Linux"), ("os-macOS", "macOS"), ("os-unknown", "")]

log = logging.getLogger(__name__)


class CompatDb:
    def __init__(self, path: Path | None = None, url: str = URL):
        self.path = path or cache_dir() / "compatibility_data.json"
        self.url = url
        self.error = ""
        self.refreshing = False
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self._data = data

    @property
    def updated(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def status(self) -> dict:
        return {"count": len(self._data), "updated": self.updated, "error": self.error,
                "refreshing": self.refreshing}

    def refresh(self, max_age: float = 24 * 3600, force: bool = False) -> bool:
        """Télécharge la base si elle a plus de max_age secondes. Renvoie True si elle a changé."""
        if not force and self._data and time.time() - self.updated < max_age:
            return False
        try:
            request = urllib.request.Request(self.url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read(32 << 20)
            data = json.loads(raw.decode("utf-8", "replace"))
            if not isinstance(data, dict) or not data:
                raise ValueError("format inattendu")
        except Exception as exc:  # réseau absent, GitHub indisponible, JSON invalide
            self.error = f"Base de compatibilité non mise à jour : {exc}"
            log.warning(self.error)
            return False
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, self.path)
        with self._lock:
            self._data = data
        self.error = ""
        return True

    def refresh_async(self, on_done=None) -> None:
        def run():
            try:
                changed = self.refresh()
            finally:
                self.refreshing = False
            if changed and on_done:
                on_done()

        self.refreshing = True
        threading.Thread(target=run, name="compat-refresh", daemon=True).start()

    def lookup(self, title_id: str) -> dict | None:
        if not title_id:
            return None
        with self._lock:
            entry = self._data.get(title_id)
        if not isinstance(entry, dict):
            return None
        for os_key, os_label in OS_ORDER:
            item = entry.get(os_key)
            if isinstance(item, dict):
                code, label = STATUSES.get(str(item.get("status")), ("unknown", "Inconnu"))
                issue = item.get("issue_number")
                return {
                    "status": code,
                    "label": label,
                    "os": os_label,
                    "version": str(item.get("version") or ""),
                    "last_tested": str(item.get("last_tested") or "")[:10],
                    "url": ISSUE_URL.format(issue) if issue else "",
                }
        return None
