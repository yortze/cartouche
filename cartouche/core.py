"""Cœur de Cartouche : relie réglages, analyse des PKG, émulateurs et tâches."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path

from . import __version__, dialogs, installer, rpcs3, shadps4, updater
from .compat import CompatDb
from .jobs import Job, JobCancelled, JobError, JobManager
from .paths import cache_dir, data_dir, downloads_dir
from .pkg import PkgError, PkgInfo, read_pkg, read_region, sniff
from .runner import Runner, RunnerError
from .settings import Settings
from .sfo import pick_title, version_key

log = logging.getLogger(__name__)

SKIP_DIRS = {"node_modules", ".git", "$recycle.bin", "system volume information", "appdata", "windows"}
MAX_DEPTH = 3
OPENABLE_URL_PREFIXES = (
    "https://github.com/shadps4-compatibility/",
    "https://github.com/shadps4-emu/",
    "https://rpcs3.net/",
    "https://www.playstation.com/",
)
WEAK_GPU_MARKERS = ("MX1", "MX2", "MX3", "MX4", "MX5", "UHD Graphics", "HD Graphics", "Iris")


class ActionError(Exception):
    """Action impossible, avec un message clair et éventuellement une correction proposée."""

    def __init__(self, message: str, fix: str | None = None):
        super().__init__(message)
        self.fix = fix


def item_id(prefix: str, path: str) -> str:
    return prefix + hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:12]


class PkgIndex:
    """Cache des PKG déjà analysés (chemin + taille + date)."""

    def __init__(self, path: Path):
        self.path = path
        self._entries: dict[str, dict] = {}
        self._dirty = False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._entries = data
        except (OSError, ValueError):
            pass

    def get(self, path: str, size: int, mtime_ns: int) -> dict | None:
        entry = self._entries.get(path)
        if entry and entry.get("size") == size and entry.get("mtime_ns") == mtime_ns:
            return entry
        return None

    def put(self, path: str, size: int, mtime_ns: int, entry: dict) -> None:
        self._entries[path] = dict(entry, size=size, mtime_ns=mtime_ns)
        self._dirty = True

    def prune(self, keep: set[str]) -> None:
        for path in list(self._entries):
            if path not in keep:
                del self._entries[path]
                self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._entries, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = False


class Images:
    """Associe des adresses secrètes aux images (icônes de jeux, fonds)."""

    def __init__(self):
        self._secret = secrets.token_bytes(16)
        self._map: dict[str, tuple] = {}
        self._lock = threading.Lock()

    def _key(self, *parts) -> str:
        message = "|".join(str(p) for p in parts).encode("utf-8")
        return hmac.new(self._secret, message, "sha1").hexdigest()[:24]

    def for_file(self, path: str) -> str:
        if not path:
            return ""
        key = self._key("f", os.path.normcase(path))
        with self._lock:
            self._map[key] = ("file", path)
        return f"/img/{key}"

    def for_region(self, pkg_path: str, region) -> str:
        if not region:
            return ""
        key = self._key("r", os.path.normcase(pkg_path), region.offset, region.size)
        with self._lock:
            self._map[key] = ("region", pkg_path, region)
        return f"/img/{key}"

    def read(self, key: str) -> bytes | None:
        with self._lock:
            source = self._map.get(key)
        if not source:
            return None
        if source[0] == "file":
            try:
                with open(source[1], "rb") as handle:
                    return handle.read(16 << 20)
            except OSError:
                return None
        return read_region(source[1], source[2])


class Core:
    def __init__(self, settings: Settings | None = None, compat: CompatDb | None = None):
        self.settings = settings or Settings()
        self.compat = compat or CompatDb()
        self.jobs = JobManager(on_finish=self._job_finished)
        self.runner = Runner(on_exit=self._game_exited)
        self.images = Images()
        self.index = PkgIndex(cache_dir() / "pkg_index.json")
        self.events: deque[dict] = deque(maxlen=40)
        self.revision = 0
        self.last_ping = time.time()
        self.bye_at = 0.0
        self.shutdown_event = threading.Event()
        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self._dialog_lock = threading.Lock()
        self._tools: tuple[float, dict | None] = (0.0, None)
        self._library: dict | None = None
        self._release: dict | None = None
        self._first_run()

    # ------------------------------------------------------------------ général

    def _first_run(self) -> None:
        if self.settings.get("first_run_done"):
            return
        downloads = downloads_dir()
        if downloads.is_dir():
            self.settings.add_to_list("pkg_dirs", [str(downloads)])
        self.settings.update({"first_run_done": True})

    def notify(self, level: str, text: str) -> None:
        with self._lock:
            self.revision += 1
            self.events.append({"id": self.revision, "time": time.time(), "level": level, "text": text})

    def invalidate(self) -> None:
        with self._lock:
            self._library = None
            self._tools = (0.0, None)
            self.revision += 1

    def status(self) -> dict:
        with self._lock:
            events = list(self.events)
            revision = self.revision
        return {"revision": revision, "jobs": self.jobs.list(), "runner": self.runner.state(),
                "events": events[-15:], "busy": self.jobs.busy()}

    def ping(self) -> None:
        self.last_ping = time.time()

    def bye(self) -> None:
        self.bye_at = time.time()

    def should_exit(self) -> bool:
        if self.jobs.busy():
            return False
        now = time.time()
        if self.bye_at and self.bye_at > self.last_ping and now - self.bye_at > 15:
            return True
        return now - self.last_ping > 300

    # ------------------------------------------------------------------ outils

    def tools(self, fresh: bool = False) -> dict:
        now = time.time()
        with self._lock:
            stamp, cached = self._tools
            if cached and not fresh and now - stamp < 3:
                return cached
        s = self.settings.snapshot()
        builds = shadps4.find_builds(s["shadps4_exe"])
        build = shadps4.choose_build(builds, s["shadps4_exe"])
        user_dir = shadps4.user_dir_for(build.path if build else None)
        config = shadps4.EmuConfig(user_dir)
        data = config.load()
        install_dirs = shadps4.install_dirs(data)
        games_dir = s["ps4_games_dir"] or (install_dirs[0] if install_dirs else "")
        extractor = shadps4.find_pkg_extractor(s["pkg_extractor"], [b.path for b in builds])
        rpcs3_exe = rpcs3.find_rpcs3(s["rpcs3_exe"])
        vfs = rpcs3.vfs_paths(rpcs3_exe) if rpcs3_exe else {}
        tools = {
            "shadps4": {
                "found": build is not None,
                "build": build.to_dict() if build else None,
                "builds": [b.to_dict() for b in builds],
                "user_dir": str(user_dir),
                "config_path": str(config.path),
                "config_found": bool(data),
                "games_dir": games_dir,
                "install_dirs": install_dirs,
                "addons_dir": shadps4.addon_dir(data, user_dir),
                "warnings": shadps4.config_warnings(data),
            },
            "pkg_extractor": {"found": bool(extractor), "path": extractor},
            "rpcs3": {
                "found": bool(rpcs3_exe), "path": rpcs3_exe,
                "dev_hdd0": str(vfs.get("dev_hdd0", "")),
                "firmware": rpcs3.firmware_version(vfs["dev_flash"]) if rpcs3_exe else "",
            },
        }
        with self._lock:
            self._tools = (now, tools)
        return tools

    # ------------------------------------------------------------------ bibliothèque

    def library(self, force: bool = False) -> dict:
        with self._lock:
            if self._library is not None and not force:
                return self._library
        with self._scan_lock:
            result = self._scan(force)
        with self._lock:
            self._library = result
        return result

    def _scan(self, force: bool) -> dict:
        tools = self.tools(fresh=True)
        s = self.settings.snapshot()
        prefer_fr = s["prefer_french_titles"]
        infos, ignored = self._scan_pkgs(force)

        sh = tools["shadps4"]
        ps4_dirs = [d for d in dict.fromkeys([sh["games_dir"], *sh["install_dirs"]]) if d]
        ps4_games = shadps4.scan_games(ps4_dirs, sh["addons_dir"], Path(sh["user_dir"]), prefer_fr)
        ps3_games: list[dict] = []
        ps3_versions: dict[str, str] = {}
        if tools["rpcs3"]["found"]:
            ps3_games, ps3_versions = rpcs3.scan_games(Path(tools["rpcs3"]["dev_hdd0"]), prefer_fr)

        bases: dict[str, dict] = {}
        for game in ps4_games:
            if game["title_id"] and game["launch_target"]:
                bases.setdefault(game["title_id"], game)
        ps3_titles = {g["title_id"]: g for g in ps3_games}
        active = self.jobs.active_refs()

        pkgs = [self._pkg_item(info, prefer_fr, bases, ps3_titles, ps3_versions, tools, active)
                for info in infos]
        pkgs.sort(key=lambda p: (p["platform"] != "PS4", p["title"].lower(), p["category"] != "game"))

        games = ps4_games + ps3_games
        for game in games:
            game["icon_url"] = self.images.for_file(game.get("icon", ""))
            game["background_url"] = self.images.for_file(game.get("background", ""))
            game["compat"] = self.compat.lookup(game["title_id"]) if game["platform"] == "PS4" else None
            game["emulator_ready"] = tools["shadps4" if game["platform"] == "PS4" else "rpcs3"]["found"]
        return {"pkgs": pkgs, "games": games, "ignored": ignored, "scanned": time.time(),
                "pkg_dirs": s["pkg_dirs"], "pkg_files": s["pkg_files"]}

    def _pkg_paths(self) -> list[Path]:
        s = self.settings.snapshot()
        found: list[Path] = []
        seen: set[str] = set()

        def add(path: Path) -> None:
            key = os.path.normcase(str(path))
            if key not in seen:
                seen.add(key)
                found.append(path)

        def walk(folder: str, depth: int) -> None:
            try:
                iterator = os.scandir(folder)
            except OSError:
                return
            with iterator:
                for entry in iterator:
                    name = entry.name
                    try:
                        if entry.is_file() and name.lower().endswith(".pkg"):
                            add(Path(entry.path))
                        elif (entry.is_dir(follow_symlinks=False) and depth < MAX_DEPTH
                              and not name.startswith((".", "$")) and name.lower() not in SKIP_DIRS):
                            walk(entry.path, depth + 1)
                    except OSError:
                        continue

        for folder in s["pkg_dirs"]:
            walk(folder, 1)
        for file in s["pkg_files"]:
            if Path(file).is_file():
                add(Path(file))
        return found

    def _scan_pkgs(self, force: bool) -> tuple[list[PkgInfo], list[dict]]:
        infos: list[PkgInfo] = []
        ignored: list[dict] = []
        paths = self._pkg_paths()
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            entry = None if force else self.index.get(str(path), stat.st_size, stat.st_mtime_ns)
            if entry is None:
                kind = sniff(path)
                if kind in ("PS4", "PS3"):
                    try:
                        entry = {"info": read_pkg(path).to_dict()}
                    except PkgError as exc:
                        entry = {"error": str(exc)}
                elif kind == "macOS":
                    entry = {"error": "Installeur macOS, pas un jeu PlayStation"}
                else:
                    entry = {"error": "Pas un PKG PlayStation"}
                self.index.put(str(path), stat.st_size, stat.st_mtime_ns, entry)
            if "info" in entry:
                infos.append(PkgInfo.from_dict(entry["info"]))
            else:
                ignored.append({"path": str(path), "name": path.name, "reason": entry.get("error", "")})
        self.index.prune({str(p) for p in paths})
        try:
            self.index.save()
        except OSError:
            log.warning("Cache des PKG non enregistré")
        return infos, ignored

    def _pkg_item(self, info: PkgInfo, prefer_fr: bool, bases: dict, ps3_titles: dict,
                  ps3_versions: dict, tools: dict, active: set[str]) -> dict:
        base = bases.get(info.title_id) if info.platform == "PS4" else ps3_titles.get(info.title_id)
        title = (pick_title(info.sfo, prefer_fr) if info.sfo else "") or (base or {}).get("title") or Path(info.path).stem
        status, status_label = self._pkg_status(info, bases, ps3_versions)
        if info.platform == "PS4":
            ready = tools["pkg_extractor"]["found"]
            emulator = "shadPS4"
        elif info.platform == "PS3":
            ready = tools["rpcs3"]["found"]
            emulator = "RPCS3"
        else:
            ready, emulator = False, ""
        icon = self.images.for_region(info.path, info.icon) if info.icon else ""
        if not icon and base and base.get("icon"):
            icon = self.images.for_file(base["icon"])
        background = self.images.for_region(info.path, info.background) if info.background else ""
        return {
            "id": item_id("pkg-", info.path),
            "path": info.path,
            "file_name": Path(info.path).name,
            "folder": str(Path(info.path).parent),
            "platform": info.platform,
            "emulator": emulator,
            "emulator_ready": ready,
            "title": title,
            "title_known": bool(info.title or base),
            "title_id": info.title_id,
            "content_id": info.content_id,
            "category": info.category,
            "kind_label": info.kind_label,
            "version": info.version,
            "system_version": info.system_version,
            "size": info.file_size,
            "content_size": info.content_size,
            "supported": info.supported,
            "truncated": info.truncated,
            "retail": info.retail,
            "notes": info.notes,
            "status": status,
            "status_label": status_label,
            "installing": info.path in active,
            "icon_url": icon,
            "background_url": background,
            "compat": self.compat.lookup(info.title_id) if info.platform == "PS4" else None,
        }

    @staticmethod
    def _pkg_status(info: PkgInfo, bases: dict, ps3_versions: dict) -> tuple[str, str]:
        if info.truncated:
            return "unsupported", "Fichier incomplet"
        if not info.supported:
            return "unsupported", "Non pris en charge"
        if info.platform == "PS4":
            base = bases.get(info.title_id)
            if info.category in ("game", "app"):  # une appli (homebrew, catégorie « gde ») s'installe comme un jeu
                if not base:
                    return "not_installed", "Pas installé"
                if info.version and base["version"] and version_key(base["version"]) != version_key(info.version):
                    return "installed", f"Installé (v{base['version']})"
                return "installed", "Installé"
            if info.category == "update":
                if not base:
                    return "needs_base", "Jeu de base absent"
                update = base.get("update")
                if update and version_key(update["version"]) >= version_key(info.version):
                    return "installed", "Installée"
                if update:
                    return "not_installed", f"Plus récente que l'installée (v{update['version']})"
                return "not_installed", "Pas installée"
            if info.category == "dlc":
                if base and info.dlc_label and info.dlc_label in base.get("dlc", []):
                    return "installed", "Installé"
                return "not_installed", "Pas installé"
            return "not_installed", "Pas installé"
        if info.platform == "PS3":
            installed = ps3_versions.get(info.title_id)
            if info.category == "game":
                return ("installed", "Installé") if installed is not None else ("not_installed", "Pas installé")
            if info.category == "update" and installed is not None:
                if version_key(installed) >= version_key(info.version):
                    return "installed", "Installée"
                return "not_installed", f"Plus récente que l'installée (v{installed})"
            return "unknown", "À installer dans RPCS3"
        return "unsupported", "Non pris en charge"

    def _find(self, section: str, field: str, value: str) -> dict | None:
        for attempt in range(2):
            if attempt:
                self.invalidate()
            for item in self.library()[section]:
                if item[field] == value:
                    return item
        return None

    def _find_pkg(self, pkg_id: str) -> dict:
        item = self._find("pkgs", "id", pkg_id)
        if item is None:
            raise ActionError("Ce fichier PKG n'est plus dans la bibliothèque. Actualise la liste.")
        return item

    def _find_game(self, key: str) -> dict:
        game = self._find("games", "key", key)
        if game is None:
            raise ActionError("Ce jeu n'est plus dans la bibliothèque. Actualise la liste.")
        return game

    # ------------------------------------------------------------------ installation

    def install(self, pkg_id: str, confirm: bool = False) -> dict:
        item = self._find_pkg(pkg_id)
        if item["installing"]:
            raise ActionError("Ce PKG est déjà en cours d'installation.")
        if item["truncated"]:
            raise ActionError("Ce PKG est incomplet (téléchargement interrompu ?). Retélécharge-le avant de l'installer.")
        if not item["supported"]:
            raise ActionError(item["notes"][0] if item["notes"] else "Ce PKG n'est pas pris en charge.")
        if item["status"] == "installed" and not confirm:
            raise ActionError(f"{item['title']} est déjà installé. Le réinstaller ?", fix="confirm_install")
        try:
            info = read_pkg(item["path"], self.settings.get("prefer_french_titles"))
        except PkgError as exc:
            raise ActionError(str(exc)) from exc
        tools = self.tools(fresh=True)
        subtitle = f"{item['kind_label']} · {item['platform']}"
        if info.platform == "PS4":
            extractor = tools["pkg_extractor"]["path"]
            if not extractor:
                raise ActionError("pkg_extractor.exe est introuvable. Il est livré avec le shadPS4 Qt Launcher : "
                                  "indique son emplacement.", fix="pick_pkg_extractor")
            games_dir = tools["shadps4"]["games_dir"]
            if not games_dir:
                raise ActionError("Choisis d'abord le dossier où installer les jeux PS4.", fix="pick_games_dir")
            addons = tools["shadps4"]["addons_dir"]
            try:  # le dossier de jeux doit aussi figurer dans la liste de shadPS4 (et du Qt Launcher)
                Path(games_dir).mkdir(parents=True, exist_ok=True)
                shadps4.ensure_install_dir(shadps4.EmuConfig(Path(tools["shadps4"]["user_dir"])), games_dir)
            except OSError as exc:
                raise ActionError(f"Dossier des jeux inutilisable : {exc}") from exc
            job = Job("install", item["title"], subtitle, ref=info.path)
            self.jobs.submit(job, lambda j: installer.install_ps4(j, info, extractor, games_dir, addons))
        elif info.platform == "PS3":
            rpcs3_exe = tools["rpcs3"]["path"]
            if not rpcs3_exe:
                raise ActionError("RPCS3 est introuvable. Installe-le, puis indique rpcs3.exe.", fix="pick_rpcs3_exe")
            job = Job("install", item["title"], subtitle, ref=info.path)
            self.jobs.submit(job, lambda j: installer.install_ps3(j, info, rpcs3_exe))
        else:
            raise ActionError("Ce PKG n'est pas pris en charge.")
        self.invalidate()
        return job.to_dict()

    def cancel_job(self, job_id: str) -> bool:
        return self.jobs.cancel(job_id)

    def _job_finished(self, job: Job) -> None:
        self.invalidate()
        if job.status == "done":
            if job.kind == "install":
                self.notify("success", f"{job.title} : installation terminée.")
            elif job.kind == "update":
                self.notify("success", f"shadPS4 {job.result.get('version', '')} est installé et sélectionné.")
        elif job.status == "error":
            self.notify("error", f"{job.title} : {job.error}")
        elif job.status == "cancelled":
            self.notify("info", f"{job.title} : annulé.")

    # ------------------------------------------------------------------ lancement

    def launch(self, key: str) -> dict:
        game = self._find_game(key)
        if not game["launchable"]:
            raise ActionError(game["note"] or "Ce jeu ne peut pas être lancé.")
        s = self.settings.snapshot()
        tools = self.tools(fresh=True)
        if game["platform"] == "PS4":
            build = tools["shadps4"]["build"]
            if not build:
                raise ActionError("shadPS4 est introuvable. Indique shadPS4.exe ou installe la dernière version.",
                                  fix="pick_shadps4_exe")
            data = shadps4.EmuConfig(Path(tools["shadps4"]["user_dir"])).load()
            if shadps4.cfg_get(data, "GPU", "null_gpu") is True:
                raise ActionError("Le rendu graphique de shadPS4 est coupé (null GPU) : le jeu n'afficherait rien.",
                                  fix="null_gpu")
            command = shadps4.build_command(build["path"], game["launch_target"], s["fullscreen"], s["show_fps"])
        else:
            rpcs3_exe = tools["rpcs3"]["path"]
            if not rpcs3_exe:
                raise ActionError("RPCS3 est introuvable.", fix="pick_rpcs3_exe")
            if not tools["rpcs3"]["firmware"]:
                raise ActionError("Le firmware PS3 n'est pas installé dans RPCS3. Installe le fichier PS3UPDAT.PUP.",
                                  fix="install_firmware")
            command = rpcs3.launch_command(rpcs3_exe, game["launch_target"], s["fullscreen"])
        try:
            running = self.runner.launch(game, command, s["show_console"])
        except RunnerError as exc:
            raise ActionError(str(exc), exc.fix) from exc
        self.notify("info", f"{game['title']} démarre dans {game['emulator']}.")
        return running

    def stop(self) -> bool:
        return self.runner.stop()

    def _game_exited(self, record: dict) -> None:
        level = "warn" if record.get("crashed") else "info"
        text = f"{record['title']} : {record['summary']}"
        if record.get("crashed") and record.get("platform") == "PS4":
            reason = self._crash_reason(record)
            if reason:
                text = f"{record['title']} s'est arrêté. {reason}"
        self.notify(level, text)

    def _crash_reason(self, record: dict) -> str:
        """Cause probable d'un plantage, lue dans le journal de shadPS4 écrit pendant ce lancement."""
        try:
            info = shadps4.diagnose_log(Path(self.tools()["shadps4"]["user_dir"]), max_errors=0)
        except Exception:
            return ""
        if not info.get("exists") or info.get("modified", 0) < record.get("started", 0) - 1:
            return ""
        for hint in info["hints"]:
            if hint["level"] == "error":
                return f"{hint['title']} : {hint['text']}"
        return ""

    # ------------------------------------------------------------------ fichiers et dossiers

    def open_location(self, kind: str, ref: str = "") -> None:
        tools = self.tools()
        if kind == "pkg":
            path = self._find_pkg(ref)["path"]
            subprocess.Popen(f'explorer /select,"{path}"')  # un chemin Windows ne contient jamais de guillemet
            return
        if kind == "game":
            folder = self._find_game(ref)["folder"]
        elif kind == "shadps4_config":
            folder = tools["shadps4"]["user_dir"]
        elif kind == "shadps4_log":
            folder = str(Path(tools["shadps4"]["user_dir"]) / "log")
        elif kind == "games_dir":
            folder = tools["shadps4"]["games_dir"]
        elif kind == "data":
            folder = str(data_dir())
        else:
            raise ActionError("Emplacement inconnu.")
        if not folder or not Path(folder).is_dir():
            raise ActionError("Ce dossier n'existe pas (encore).")
        os.startfile(folder)  # type: ignore[attr-defined]

    @staticmethod
    def open_url(url: str) -> None:
        if not url.startswith(OPENABLE_URL_PREFIXES):
            raise ActionError("Adresse non autorisée.")
        webbrowser.open(url)

    def pick(self, kind: str) -> dict:
        if not self._dialog_lock.acquire(blocking=False):
            raise ActionError("Une fenêtre de choix est déjà ouverte.")
        try:
            return self._pick(kind)
        finally:
            self._dialog_lock.release()

    def _pick(self, kind: str) -> dict:
        downloads = str(downloads_dir())
        exe_filter = "Programmes (*.exe)|*.exe"
        if kind == "pkg_files":
            files = dialogs.pick_files("Choisir des fichiers PKG", "Fichiers PKG (*.pkg)|*.pkg", downloads)
            if files:
                self.settings.add_to_list("pkg_files", files)
                self.invalidate()
            return {"picked": files}
        if kind == "pkg_dir":
            folder = dialogs.pick_folder("Choisis un dossier qui contient des fichiers PKG", downloads)
            if folder:
                self.settings.add_to_list("pkg_dirs", [folder])
                self.invalidate()
            return {"picked": folder}
        if kind == "games_dir":
            current = self.tools()["shadps4"]["games_dir"]
            folder = dialogs.pick_folder("Choisis où installer les jeux PS4 (prévois beaucoup de place)", current)
            if folder:
                self.set_settings({"ps4_games_dir": folder})
            return {"picked": folder}
        if kind in ("shadps4_exe", "pkg_extractor", "rpcs3_exe"):
            names = {"shadps4_exe": ("Choisir shadPS4.exe", "shadPS4.exe"),
                     "pkg_extractor": ("Choisir pkg_extractor.exe", "pkg_extractor.exe"),
                     "rpcs3_exe": ("Choisir rpcs3.exe", "rpcs3.exe")}
            title, name = names[kind]
            files = dialogs.pick_files(title, f"{name}|{name}|{exe_filter}", downloads, multiple=False)
            if files:
                self.set_settings({kind: files[0]})
            return {"picked": files[0] if files else ""}
        if kind == "firmware":
            return self.install_firmware()
        raise ActionError("Choix inconnu.")

    def install_firmware(self) -> dict:
        tools = self.tools(fresh=True)
        rpcs3_exe = tools["rpcs3"]["path"]
        if not rpcs3_exe:
            raise ActionError("Installe et indique d'abord RPCS3.", fix="pick_rpcs3_exe")
        files = dialogs.pick_files("Choisir le firmware PS3 (PS3UPDAT.PUP)", "Firmware PS3 (*.PUP)|*.PUP",
                                   str(downloads_dir()), multiple=False)
        if not files:
            return {"picked": ""}
        subprocess.Popen(rpcs3.firmware_command(rpcs3_exe, files[0]), cwd=str(Path(rpcs3_exe).parent))
        self.notify("info", "RPCS3 installe le firmware : suis la progression dans sa fenêtre.")
        return {"picked": files[0]}

    # ------------------------------------------------------------------ réglages

    def get_settings(self) -> dict:
        return {"settings": self.settings.snapshot(), "tools": self.tools(fresh=True), "app_version": __version__}

    def set_settings(self, changes: dict) -> dict:
        rejected = self.settings.update(changes)
        if changes.get("ps4_games_dir"):
            folder = changes["ps4_games_dir"]
            try:
                Path(folder).mkdir(parents=True, exist_ok=True)
                tools = self.tools(fresh=True)
                shadps4.ensure_install_dir(shadps4.EmuConfig(Path(tools["shadps4"]["user_dir"])), folder)
            except OSError as exc:
                raise ActionError(f"Dossier inutilisable : {exc}") from exc
        self.invalidate()
        return {"rejected": rejected, **self.get_settings()}

    def remove_source(self, kind: str, path: str) -> dict:
        if kind not in ("pkg_dirs", "pkg_files"):
            raise ActionError("Source inconnue.")
        self.settings.remove_from_list(kind, path)
        self.invalidate()
        return self.get_settings()

    # ------------------------------------------------------------------ configuration de shadPS4

    def _emu_config(self) -> shadps4.EmuConfig:
        return shadps4.EmuConfig(Path(self.tools()["shadps4"]["user_dir"]))

    def emu_settings(self) -> dict:
        config = self._emu_config()
        data = config.load()
        last = shadps4.last_run_summary(config.user_dir)
        return {
            "available": bool(data),
            "path": str(config.path),
            "backup": config.backup_path.is_file(),
            "items": shadps4.settings_view(data, shadps4.list_gpus(), last) if data else [],
            "warnings": shadps4.config_warnings(data),
            "game_running": self.runner.state()["running"] is not None,
        }

    def set_emu_settings(self, changes: dict) -> dict:
        config = self._emu_config()
        data = config.load()
        if not data:
            raise ActionError("config.json de shadPS4 introuvable : lance shadPS4 une fois pour le créer.")
        changed = shadps4.apply_changes(data, changes)
        if changed:
            config.write(data)
            self.invalidate()
        return {"changed": changed, **self.emu_settings()}

    def light_preset(self) -> dict:
        config = self._emu_config()
        data = config.load()
        if not data:
            raise ActionError("config.json de shadPS4 introuvable : lance shadPS4 une fois pour le créer.")
        changed = shadps4.apply_light_preset(data)
        if changed:
            config.write(data)
            self.invalidate()
        return {"changed": changed, **self.emu_settings()}

    def restore_emu_backup(self) -> dict:
        if not self._emu_config().restore_backup():
            raise ActionError("Aucune sauvegarde à restaurer.")
        self.invalidate()
        return self.emu_settings()

    # ------------------------------------------------------------------ mises à jour de shadPS4

    def updates(self, force: bool = False) -> dict:
        release = updater.check_latest(force=force)
        self._release = release
        build = self.tools()["shadps4"]["build"]
        current = (build or {}).get("version", "")
        latest = release.get("version", "")
        available = bool(latest and release.get("asset") and
                         (not current or version_key(latest) > version_key(current)))
        installed = any(b["version"] == latest for b in self.tools()["shadps4"]["builds"]) if latest else False
        return {"release": release, "current": current, "current_label": (build or {}).get("label", ""),
                "update_available": available and not installed, "latest_installed": installed}

    def install_update(self) -> dict:
        release = self._release or updater.check_latest()
        if not release.get("asset"):
            raise ActionError(release.get("error") or "Aucune archive Windows trouvée pour la dernière version.")

        def run(job: Job) -> None:
            def progress(done: int, total: int) -> None:
                job.progress = done / total if total else None
                job.detail = f"Téléchargement : {installer.human_size(done)}" + (
                    f" sur {installer.human_size(total)}" if total else "")

            try:
                exe = updater.install_release(release, progress, job.cancel_event.is_set)
            except updater.Cancelled as exc:
                raise JobCancelled() from exc
            except updater.UpdateError as exc:
                raise JobError(str(exc)) from exc
            self.settings.update({"shadps4_exe": exe})
            job.progress = 1.0
            job.detail = f"Installé : {exe}"
            job.result = {"exe": exe, "version": release.get("version", "")}

        job = Job("update", f"shadPS4 {release.get('version', '')}", "Mise à jour de l'émulateur",
                  ref="shadps4-update")
        self.jobs.submit(job, run)
        return job.to_dict()

    # ------------------------------------------------------------------ diagnostic

    def diagnostics(self) -> dict:
        tools = self.tools(fresh=True)
        sh = tools["shadps4"]
        checks: list[dict] = []

        def add(level: str, title: str, text: str = "", fix: str | None = None) -> None:
            checks.append({"level": level, "title": title, "text": text, "fix": fix})

        if sh["found"]:
            build = sh["build"]
            add("ok", "shadPS4 trouvé", f"{build['label']} · {build['path']}")
        else:
            add("error", "shadPS4 introuvable", "Installe la dernière version ou indique shadPS4.exe.", "pick_shadps4_exe")
        release = self._release
        if release and release.get("version") and sh["build"]:
            current = sh["build"].get("version", "")
            if current and version_key(release["version"]) > version_key(current):
                add("warn", f"shadPS4 {release['version']} est disponible",
                    f"Tu utilises la {current}. Chaque version fait mieux tourner les jeux.", "update_shadps4")
        if tools["pkg_extractor"]["found"]:
            add("ok", "pkg_extractor trouvé", tools["pkg_extractor"]["path"])
        else:
            add("error", "pkg_extractor introuvable",
                "Il sert à installer les PKG PS4. Il est fourni avec le shadPS4 Qt Launcher.", "pick_pkg_extractor")
        games_dir = sh["games_dir"]
        if not games_dir:
            add("error", "Aucun dossier pour les jeux PS4", "Choisis où installer les jeux.", "pick_games_dir")
        elif not Path(games_dir).is_dir():
            add("warn", "Dossier des jeux PS4 absent", games_dir, "pick_games_dir")
        else:
            free = shutil.disk_usage(games_dir).free
            level = "ok" if free > 50 * 1024 ** 3 else "warn"
            add(level, f"Espace libre : {installer.human_size(free)}", f"Dossier des jeux PS4 : {games_dir}")
        for warning in sh["warnings"]:
            add("error" if warning["level"] == "error" else "warn", warning["text"], "",
                "null_gpu" if "null GPU" in warning["text"] else "light_preset")
        fonts = Path(sh["user_dir"]) / "fonts"
        has_fonts = fonts.is_dir() and any(p.is_file() for p in fonts.rglob("*"))
        if not has_fonts:
            add("info", "Polices système PS4 absentes",
                "Certains jeux peuvent afficher des textes manquants. Elles se récupèrent sur ta propre PS4.")
        gpus = shadps4.list_gpus()
        weak = [g for g in gpus if any(marker in g for marker in WEAK_GPU_MARKERS)]
        if gpus and len(weak) == len(gpus):
            add("warn", "Carte graphique d'entrée de gamme", ", ".join(gpus) +
                ". Les jeux PS4 lourds tourneront mal : vise les jeux notés « Jouable » et légers.")
        elif gpus:
            add("info", "Cartes graphiques", ", ".join(gpus))
        rp = tools["rpcs3"]
        if rp["found"]:
            add("ok" if rp["firmware"] else "warn",
                "RPCS3 trouvé" + (f" · firmware {rp['firmware']}" if rp["firmware"] else " · firmware PS3 manquant"),
                rp["path"], None if rp["firmware"] else "install_firmware")
        else:
            add("info", "RPCS3 non installé", "Nécessaire seulement pour les PKG PS3.", "pick_rpcs3_exe")
        compat = self.compat.status()
        if compat["count"]:
            add("ok", f"Base de compatibilité : {compat['count']} jeux",
                "Mise à jour le " + time.strftime("%d/%m/%Y", time.localtime(compat["updated"])))
        elif compat["refreshing"]:
            add("info", "Base de compatibilité en cours de téléchargement",
                "Relance l'analyse dans quelques secondes.")
        else:
            add("warn", "Base de compatibilité indisponible", compat["error"] or "Pas encore téléchargée.")
        return {"checks": checks, "log": shadps4.diagnose_log(Path(sh["user_dir"])),
                "last_run": self.runner.state()["last"], "python": sys.version.split()[0],
                "app_version": __version__}

    def fix(self, name: str) -> dict:
        """Corrections en un clic proposées par l'interface."""
        if name == "null_gpu":
            return self.set_emu_settings({"null_gpu": False})
        if name == "light_preset":
            return self.light_preset()
        raise ActionError("Correction inconnue.")
