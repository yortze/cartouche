"""Cartouche : bibliothèque et lanceur de PKG PS4 (shadPS4) et PS3 (RPCS3).

Lancement : double-clic sur « Lancer Cartouche.bat », ou `python app.py`.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cartouche import APP_NAME, __version__  # noqa: E402
from cartouche.paths import logs_dir  # noqa: E402

DEFAULT_PORT = 47823


def setup_logging(debug: bool) -> None:
    handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(
        logs_dir() / "cartouche.log", maxBytes=1 << 20, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def running_instance(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hello", timeout=1.5) as response:
            return json.loads(response.read().decode("utf-8")).get("app") == "cartouche"
    except Exception:
        return False


def find_edge() -> str:
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            exe = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if exe.is_file():
                return str(exe)
    return ""


def open_window(url: str) -> None:
    """Ouvre l'interface dans une fenêtre d'application (Edge) ou, à défaut, le navigateur."""
    edge = find_edge()
    if edge:
        try:
            subprocess.Popen([edge, f"--app={url}", "--window-size=1380,880", "--no-first-run"])
            return
        except OSError:
            pass
    webbrowser.open(url)


def fatal(message: str) -> None:
    logging.error(message)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    except Exception:
        if sys.stderr is not None:
            print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cartouche : lanceur de PKG PS4 et PS3")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-window", action="store_true",
                        help="ne pas ouvrir de fenêtre ; le serveur reste actif jusqu'à Ctrl+C")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.debug)

    if running_instance(args.port):
        open_window(f"http://127.0.0.1:{args.port}/")
        return 0

    import threading
    import time

    from cartouche import shadps4
    from cartouche.core import Core
    from cartouche.server import make_server, serve_in_thread

    threading.Thread(target=shadps4.list_gpus, name="gpus", daemon=True).start()  # ~1,5 s via WMI
    core = Core()
    try:
        server = make_server(core, args.port)
    except OSError:
        server = make_server(core, 0)  # port pris par un autre programme : on en choisit un libre
    serve_in_thread(server)
    logging.info("Cartouche %s prêt sur %s", __version__, server.url)
    core.compat.refresh_async(on_done=core.invalidate)
    if not args.no_window:
        open_window(server.url)
    if sys.stdout is not None:
        print(f"Cartouche est ouvert sur {server.url} (ferme cette fenêtre pour arrêter).", flush=True)
    try:
        while not core.shutdown_event.wait(2):
            if not args.no_window and core.should_exit():
                logging.info("Fenêtre fermée : arrêt de Cartouche")
                break
    except KeyboardInterrupt:
        pass
    # Quitter pendant une installation : on arrête proprement l'extraction en cours.
    for job in core.jobs.list():
        if job["status"] in ("queued", "running"):
            core.jobs.cancel(job["id"])
    deadline = time.time() + 8
    while core.jobs.busy() and time.time() < deadline:
        time.sleep(0.2)
    server.shutdown()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # erreur au démarrage : on l'affiche dans une boîte de dialogue
        logging.exception("Échec au démarrage")
        fatal(f"Cartouche n'a pas pu démarrer :\n{exc}\n\nDétails : {logs_dir() / 'cartouche.log'}")
        sys.exit(1)
