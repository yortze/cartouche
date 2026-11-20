"""Lancement et suivi du jeu en cours."""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from .paths import logs_dir

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000

# Codes de sortie Windows fréquents lors d'un plantage
NTSTATUS = {
    0xC0000005: "violation d'accès mémoire",
    0xC000001D: "instruction que l'émulateur n'a pas su exécuter",
    0xC0000094: "division par zéro",
    0xC00000FD: "dépassement de pile",
    0xC0000374: "mémoire corrompue",
    0xC0000409: "arrêt de sécurité (pile corrompue)",
    0xC0000135: "DLL manquante",
    0xC0000142: "échec d'initialisation d'une DLL",
}


class RunnerError(Exception):
    def __init__(self, message: str, fix: str | None = None):
        super().__init__(message)
        self.fix = fix


def describe_launch_error(name: str, exe: str, exc: OSError) -> str:
    """Traduit une erreur Windows de démarrage de programme en message clair."""
    code = getattr(exc, "winerror", None)
    file_name = Path(exe).name
    if code in (193, 216):
        return f"{name} ne peut pas démarrer : {file_name} semble abîmé ou incomplet. Retélécharge-le."
    if code == 2 or isinstance(exc, FileNotFoundError):
        return f"{name} est introuvable : {exe}"
    if code == 5 or isinstance(exc, PermissionError):
        return f"Windows refuse de lancer {file_name} (accès refusé). Un antivirus le bloque peut-être."
    if code == 740:
        return f"{file_name} demande les droits administrateur."
    return f"Impossible de lancer {name} : {exc.strerror or exc}"


def describe_exit(code: int | None, duration: float) -> str:
    if code is None:
        return ""
    unsigned = code & 0xFFFFFFFF
    if unsigned == 0:
        return "Fermé normalement."
    label = NTSTATUS.get(unsigned)
    text = f"Arrêt avec le code 0x{unsigned:08X}" if unsigned > 0xFFFF else f"Arrêt avec le code {code}"
    if label:
        text += f" ({label})"
    if duration < 15:
        text += ". Le jeu s'est fermé presque tout de suite : regarde le Diagnostic."
    else:
        text += "."
    return text


class Runner:
    def __init__(self, on_exit=None):
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self.current: dict | None = None
        self.history: deque[dict] = deque(maxlen=10)
        self._on_exit = on_exit

    def launch(self, game: dict, command: list[str], show_console: bool = False) -> dict:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RunnerError(f"{self.current['title']} tourne déjà : ferme-le avant d'en lancer un autre.")
            cwd = str(Path(command[0]).parent)
            log_path = logs_dir() / f"dernier_lancement_{game['emulator'].lower()}.txt"
            log_file = open(log_path, "wb")
            try:
                if show_console:
                    process = subprocess.Popen(command, cwd=cwd, creationflags=CREATE_NEW_CONSOLE)
                else:
                    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.DEVNULL, stderr=log_file,
                                               stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            except OSError as exc:
                log_file.close()
                damaged = getattr(exc, "winerror", None) in (193, 216) and game["emulator"] == "shadPS4"
                raise RunnerError(describe_launch_error(game["emulator"], command[0], exc),
                                  "update_shadps4" if damaged else None) from exc
            self._process = process
            self.current = {
                "key": game["key"], "title": game["title"], "platform": game["platform"],
                "emulator": game["emulator"], "pid": process.pid, "started": time.time(),
                "command": command,
            }
            current = dict(self.current)
        threading.Thread(target=self._wait, args=(process, current, log_file), daemon=True).start()
        return current

    def _wait(self, process: subprocess.Popen, current: dict, log_file) -> None:
        code = process.wait()
        log_file.close()
        ended = time.time()
        duration = ended - current["started"]
        record = dict(current, exit_code=code, ended=ended, duration=duration,
                      summary=describe_exit(code, duration), crashed=(code & 0xFFFFFFFF) != 0)
        with self._lock:
            if self._process is process:
                self._process = None
                self.current = None
            self.history.appendleft(record)
        if self._on_exit:
            self._on_exit(record)

    def stop(self) -> bool:
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return False
        process.terminate()
        return True

    def state(self) -> dict:
        with self._lock:
            return {"running": dict(self.current) if self.current else None,
                    "last": dict(self.history[0]) if self.history else None}
