"""Intégration avec shadPS4 : versions installées, configuration, jeux, journal."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import appdata, data_dir, downloads_dir, same_path
from .sfo import SfoError, format_ps4_system_ver, pick_title, read_sfo, text

CREATE_NO_WINDOW = 0x08000000
OVERLAY_SUFFIXES = ("-UPDATE", "-patch", "-mods")


def qt_launcher_dir() -> Path:
    return appdata() / "shadPS4QtLauncher"


# --------------------------------------------------------------------------- versions

@dataclass
class Build:
    path: str
    label: str
    version: str
    source: str
    nightly: bool = False
    date: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _version_in(value: str) -> str:
    match = re.search(r"(\d+\.\d+\.\d+)", value or "")
    return match.group(1) if match else ""


def find_builds(custom: str = "") -> list[Build]:
    """Toutes les copies de shadPS4.exe trouvées sur la machine."""
    builds: list[Build] = []
    seen: set[str] = set()

    def add(path, label: str, source: str, nightly: bool = False, date: str = "") -> None:
        exe = Path(path)
        if not exe.is_file():
            return
        key = os.path.normcase(str(exe.resolve()))
        if key in seen:
            return
        seen.add(key)
        label = label or exe.parent.name
        version = _version_in(label) or _version_in(exe.parent.name)
        builds.append(Build(str(exe), label, version, source, nightly, date))

    # Les versions installées par Cartouche d'abord : elles gardent leur libellé même une fois choisies.
    for exe in sorted((data_dir() / "shadps4").glob("*/shadPS4.exe"), reverse=True):
        add(exe, f"v{exe.parent.name.lstrip('v')}", "Installé par Cartouche")
    if custom:
        add(custom, Path(custom).parent.name, "Choisi à la main")
    launcher = qt_launcher_dir()
    try:
        entries = json.loads((launcher / "versions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entries = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("path"):
            name = str(entry.get("name") or "").replace("v.", "v")
            codename = str(entry.get("codename") or "")
            nightly = entry.get("type") == 1
            label = name if nightly or not codename else f"{name} « {codename} »"
            add(entry["path"], label, "shadPS4 Qt Launcher", nightly, str(entry.get("date") or ""))
    for exe in sorted((launcher / "versions").glob("*/shadPS4.exe")):
        add(exe, exe.parent.name, "shadPS4 Qt Launcher")
    downloads = downloads_dir()
    for pattern in ("shadPS4.exe", "*/shadPS4.exe", "*/*/shadPS4.exe"):
        for exe in sorted(downloads.glob(pattern)):
            add(exe, exe.parent.name, "Téléchargements")
    return builds


def qt_selected_build() -> str:
    """Version choisie dans le shadPS4 Qt Launcher, s'il est installé."""
    try:
        content = (qt_launcher_dir() / "qt_ui.ini").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"^versionSelected=(.+)$", content, re.M)
    return match.group(1).strip().replace("\\\\", "\\") if match else ""


def choose_build(builds: list[Build], preferred: str = "") -> Build | None:
    if not builds:
        return None
    for wanted in (preferred, qt_selected_build()):
        if wanted:
            for build in builds:
                if same_path(build.path, wanted):
                    return build

    def recency(build: Build):
        try:
            return os.path.getmtime(build.path)
        except OSError:
            return 0

    return max(builds, key=recency)


def find_pkg_extractor(custom: str = "", near: list[str] | None = None) -> str:
    """Chemin de pkg_extractor.exe (fourni avec le shadPS4 Qt Launcher)."""
    if custom and Path(custom).is_file():
        return custom
    candidates: list[Path] = [data_dir() / "tools" / "pkg_extractor.exe"]
    for exe in near or []:
        candidates.append(Path(exe).parent / "pkg_extractor.exe")
    downloads = downloads_dir()
    for pattern in ("pkg_extractor.exe", "*/pkg_extractor.exe", "*/*/pkg_extractor.exe"):
        candidates.extend(downloads.glob(pattern))
    candidates.extend(qt_launcher_dir().glob("**/pkg_extractor.exe"))
    existing = [c for c in candidates if c.is_file()]
    if not existing:
        return ""
    return str(max(existing, key=lambda c: c.stat().st_mtime))


def user_dir_for(exe: str | None) -> Path:
    """Dossier utilisateur de shadPS4 : 'user' à côté de l'exe (portable) ou %APPDATA%\\shadPS4."""
    if exe:
        portable = Path(exe).parent / "user"
        if portable.is_dir():
            return portable
    return appdata() / "shadPS4"


# --------------------------------------------------------------------------- configuration

class EmuConfig:
    """Lecture et écriture prudente de config.json (une sauvegarde est faite au premier changement)."""

    def __init__(self, user_dir: Path):
        self.user_dir = Path(user_dir)
        self.path = self.user_dir / "config.json"
        self.backup_path = self.user_dir / "config.json.cartouche-backup"

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, data: dict) -> None:
        if self.path.is_file() and not self.backup_path.exists():
            shutil.copy2(self.path, self.backup_path)
        ending = "\n"
        try:
            ending = "\n" if self.path.read_bytes().endswith(b"\n") else ""
        except OSError:
            pass
        self.user_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name("config.json.cartouche-tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + ending, encoding="utf-8")
        os.replace(tmp, self.path)

    def restore_backup(self) -> bool:
        if not self.backup_path.is_file():
            return False
        shutil.copy2(self.backup_path, self.path)
        return True


def cfg_get(data: dict, section: str, key: str, default=None):
    group = data.get(section)
    if isinstance(group, dict) and key in group:
        return group[key]
    return default


def cfg_has(data: dict, section: str, key: str) -> bool:
    group = data.get(section)
    return isinstance(group, dict) and key in group


def install_dirs(data: dict) -> list[str]:
    dirs = []
    for entry in cfg_get(data, "General", "install_dirs", []) or []:
        if isinstance(entry, dict) and entry.get("path") and entry.get("enabled", True):
            dirs.append(str(entry["path"]))
        elif isinstance(entry, str) and entry:
            dirs.append(entry)
    return dirs


def addon_dir(data: dict, user_dir: Path) -> str:
    return str(cfg_get(data, "General", "addon_install_dir", "") or (Path(user_dir) / "addcont"))


def ensure_install_dir(config: EmuConfig, folder: str) -> bool:
    """Ajoute un dossier de jeux à la liste de shadPS4 s'il n'y est pas déjà."""
    data = config.load()
    if not data:
        return False
    general = data.setdefault("General", {})
    entries = general.setdefault("install_dirs", [])
    for entry in entries:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if same_path(path, folder):
            return False
    entries.append({"enabled": True, "path": folder})
    config.write(data)
    return True


LANGUAGES = [
    (2, "Français"), (1, "Anglais (US)"), (18, "Anglais (UK)"), (3, "Espagnol"),
    (4, "Allemand"), (5, "Italien"), (17, "Portugais (Brésil)"), (0, "Japonais"),
]
RESOLUTIONS = [("1280x720", "1280 × 720 (léger)"), ("1600x900", "1600 × 900"), ("1920x1080", "1920 × 1080 (lourd)")]
READBACKS = [(0, "Désactivées (le plus rapide)"), (1, "Assouplies"), (2, "Précises (lent)")]

SPEC = [
    {"id": "null_gpu", "section": "GPU", "key": "null_gpu", "type": "bool",
     "label": "Couper le rendu graphique (null GPU)",
     "help": "Si c'est activé, aucune image ne s'affiche. Laisse-le désactivé.", "bad": True},
    {"id": "gpu_id", "section": "Vulkan", "key": "gpu_id", "type": "choice",
     "label": "Carte graphique utilisée", "help": ""},
    {"id": "resolution", "section": "GPU", "key": "internal_screen_width", "type": "choice",
     "label": "Résolution de l'écran émulé",
     "help": "Une résolution plus basse soulage les petites cartes graphiques."},
    {"id": "readbacks_mode", "section": "GPU", "key": "readbacks_mode", "type": "choice",
     "label": "Relectures GPU",
     "help": "Désactivées, c'est plus rapide. Certains jeux ont besoin du mode assoupli pour afficher correctement."},
    {"id": "pipeline_cache_enabled", "section": "Vulkan", "key": "pipeline_cache_enabled", "type": "bool",
     "label": "Garder les shaders compilés",
     "help": "Moins de saccades à partir du deuxième lancement d'un jeu.", "good": True},
    {"id": "fsr_enabled", "section": "GPU", "key": "fsr_enabled", "type": "bool",
     "label": "Agrandissement FSR",
     "help": "Image plus nette quand la fenêtre est plus grande que la résolution émulée. Coûte un peu de puissance."},
    {"id": "log_sync", "section": "Log", "key": "sync", "type": "bool",
     "label": "Journal écrit en direct",
     "help": "Plus lent. À activer seulement pour comprendre un plantage."},
    {"id": "vkvalidation_enabled", "section": "Vulkan", "key": "vkvalidation_enabled", "type": "bool",
     "label": "Validation Vulkan (débogage)", "help": "Ralentit énormément. À laisser désactivé.", "bad": True},
    {"id": "vkcrash_diagnostic_enabled", "section": "Vulkan", "key": "vkcrash_diagnostic_enabled", "type": "bool",
     "label": "Diagnostic de plantage GPU", "help": "Ralentit. À laisser désactivé.", "bad": True},
    {"id": "show_fps_counter", "section": "General", "key": "show_fps_counter", "type": "bool",
     "label": "Compteur d'images par seconde", "help": "Affiche les FPS en jeu."},
    {"id": "console_language", "section": "General", "key": "console_language", "type": "choice",
     "label": "Langue de la console", "help": "Les jeux qui la proposent s'affichent dans cette langue."},
]

LIGHT_PRESET = [
    ("GPU", "null_gpu", False), ("GPU", "readbacks_mode", 0), ("GPU", "fsr_enabled", False),
    ("GPU", "internal_screen_width", 1280), ("GPU", "internal_screen_height", 720),
    ("GPU", "dump_shaders", False), ("GPU", "copy_gpu_buffers", False),
    ("Vulkan", "pipeline_cache_enabled", True), ("Vulkan", "vkvalidation_enabled", False),
    ("Vulkan", "vkvalidation_sync_enabled", False), ("Vulkan", "vkvalidation_gpu_enabled", False),
    ("Vulkan", "vkcrash_diagnostic_enabled", False), ("Vulkan", "renderdoc_enabled", False),
    ("Vulkan", "vkhost_markers", False), ("Vulkan", "vkguest_markers", False),
    ("Debug", "debug_dump", False), ("Debug", "shader_collect", False), ("Log", "sync", False),
]


def settings_view(data: dict, gpus: list[str], last_run: dict | None) -> list[dict]:
    """Réglages choisis de config.json, prêts à afficher."""
    view = []
    for spec in SPEC:
        item = {k: spec[k] for k in ("id", "type", "label", "help")}
        item["available"] = cfg_has(data, spec["section"], spec["key"])
        if spec["id"] == "resolution":
            width = cfg_get(data, "GPU", "internal_screen_width")
            height = cfg_get(data, "GPU", "internal_screen_height")
            item["value"] = f"{width}x{height}"
            choices = list(RESOLUTIONS)
            if item["value"] not in {c[0] for c in choices}:
                choices.insert(0, (item["value"], f"{width} × {height} (actuel)"))
            item["choices"] = [{"value": v, "label": l} for v, l in choices]
        elif spec["id"] == "gpu_id":
            current = cfg_get(data, "Vulkan", "gpu_id", -1)
            item["value"] = current
            used_index = (last_run or {}).get("gpu_id")
            used_model = (last_run or {}).get("gpu")
            count = max(len(gpus), 2, (current if isinstance(current, int) else 0) + 1)
            choices = [{"value": -1, "label": "Automatique"}]
            for index in range(count):
                label = f"GPU n°{index}"
                if used_index == index and used_model:
                    label += f" : {used_model}"
                choices.append({"value": index, "label": label})
            item["choices"] = choices
            if gpus:
                item["help"] = "Cartes détectées : " + ", ".join(gpus) + "."
            if used_model:
                item["help"] += f" Dernier lancement : {used_model}."
        elif spec["id"] == "readbacks_mode":
            item["value"] = cfg_get(data, "GPU", "readbacks_mode")
            item["choices"] = [{"value": v, "label": l} for v, l in READBACKS]
        elif spec["id"] == "console_language":
            item["value"] = cfg_get(data, "General", "console_language")
            item["choices"] = [{"value": v, "label": l} for v, l in LANGUAGES]
        else:
            item["value"] = cfg_get(data, spec["section"], spec["key"])
            if spec.get("bad"):
                item["warn_when"] = True
            if spec.get("good"):
                item["warn_when"] = False
        view.append(item)
    return view


def apply_changes(data: dict, changes: dict) -> list[str]:
    """Applique des changements (id -> valeur) sur les clés existantes. Renvoie les libellés modifiés."""
    by_id = {spec["id"]: spec for spec in SPEC}
    changed = []
    for setting_id, value in changes.items():
        spec = by_id.get(setting_id)
        if not spec or not cfg_has(data, spec["section"], spec["key"]):
            continue
        if setting_id == "resolution":
            match = re.fullmatch(r"(\d{3,4})x(\d{3,4})", str(value))
            if not match:
                continue
            data["GPU"]["internal_screen_width"] = int(match.group(1))
            if cfg_has(data, "GPU", "internal_screen_height"):
                data["GPU"]["internal_screen_height"] = int(match.group(2))
        elif spec["type"] == "bool":
            if not isinstance(value, bool):
                continue
            data[spec["section"]][spec["key"]] = value
        else:
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            data[spec["section"]][spec["key"]] = value
        changed.append(spec["label"])
    return changed


def apply_light_preset(data: dict) -> list[str]:
    changed = []
    for section, key, value in LIGHT_PRESET:
        if cfg_has(data, section, key) and data[section][key] != value:
            data[section][key] = value
            changed.append(f"{section}.{key}")
    return changed


def config_warnings(data: dict) -> list[dict]:
    warnings = []
    if cfg_get(data, "GPU", "null_gpu") is True:
        warnings.append({"level": "error", "text": "Le rendu graphique est coupé (null GPU) : aucun jeu ne pourra s'afficher."})
    if cfg_get(data, "Vulkan", "vkvalidation_enabled") is True:
        warnings.append({"level": "warn", "text": "La validation Vulkan est active : tout tourne beaucoup plus lentement."})
    if cfg_get(data, "Vulkan", "vkcrash_diagnostic_enabled") is True:
        warnings.append({"level": "warn", "text": "Le diagnostic de plantage GPU est actif : il ralentit l'émulation."})
    if cfg_get(data, "GPU", "readbacks_mode") not in (None, 0):
        warnings.append({"level": "info", "text": "Les relectures GPU sont actives : plus fidèle, mais plus lent."})
    return warnings


# --------------------------------------------------------------------------- jeux installés

def game_key(folder: str | Path) -> str:
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8")).hexdigest()
    return "ps4-" + digest[:12]


def _overlay_suffix(name: str) -> str:
    for suffix in OVERLAY_SUFFIXES:
        if name.lower().endswith(suffix.lower()) and len(name) > len(suffix):
            return suffix
    return ""


def _collect_game_folders(root: Path, max_depth: int = 2) -> list[Path]:
    found: list[Path] = []

    def walk(folder: Path, depth: int) -> None:
        try:
            children = sorted(c for c in folder.iterdir() if c.is_dir())
        except OSError:
            return
        for child in children:
            if (child / "sce_sys" / "param.sfo").is_file():
                found.append(child)
            elif depth + 1 < max_depth:
                walk(child, depth + 1)

    walk(root, 0)
    return found


def scan_games(dirs: list[str], addons: str, user_dir: Path, prefer_french: bool = True) -> list[dict]:
    folders: list[Path] = []
    for directory in dirs:
        root = Path(directory)
        if root.is_dir():
            folders.extend(_collect_game_folders(root))

    bases: dict[str, dict] = {}
    overlays: list[tuple[Path, str, dict]] = []
    for folder in folders:
        try:
            sfo = read_sfo(folder / "sce_sys" / "param.sfo")
        except (OSError, SfoError):
            continue
        suffix = _overlay_suffix(folder.name)
        if suffix:
            overlays.append((folder, suffix, sfo))
            continue
        eboot = folder / "eboot.bin"
        title_id = text(sfo, "TITLE_ID")
        category = text(sfo, "CATEGORY").lower()
        game = {
            "key": game_key(folder),
            "platform": "PS4",
            "emulator": "shadPS4",
            "title": pick_title(sfo, prefer_french) or folder.name,
            "title_id": title_id,
            "content_id": text(sfo, "CONTENT_ID"),
            "category": category,
            "version": text(sfo, "APP_VER") or text(sfo, "VERSION"),
            "system_version": format_ps4_system_ver(sfo.get("SYSTEM_VER")),
            "folder": str(folder),
            "launch_target": str(eboot),
            "launchable": eboot.is_file(),
            "icon": _first_file(folder / "sce_sys", ["icon0.png"]),
            "background": _first_file(folder / "sce_sys", ["pic1.png", "pic0.png"]),
            "update": None,
            "mods": False,
            "dlc": [],
            "custom_config": bool(title_id) and (Path(user_dir) / "custom_configs" / f"{title_id}.json").is_file(),
            "note": "",
        }
        if not game["launchable"]:
            game["note"] = "Pas d'eboot.bin dans ce dossier : ce n'est pas un jeu lançable."
        bases[os.path.normcase(str(folder))] = game

    orphans = []
    for folder, suffix, sfo in overlays:
        base_path = folder.parent / folder.name[: -len(suffix)]
        base = bases.get(os.path.normcase(str(base_path)))
        if base is None:
            orphans.append({"folder": str(folder), "title_id": text(sfo, "TITLE_ID"),
                            "title": pick_title(sfo, prefer_french), "version": text(sfo, "APP_VER")})
            continue
        if suffix == "-mods":
            base["mods"] = True
        elif base["update"] is None or suffix == "-UPDATE":
            base["update"] = {"folder": str(folder), "version": text(sfo, "APP_VER") or text(sfo, "VERSION")}

    addon_root = Path(addons)
    for game in bases.values():
        if game["title_id"]:
            dlc_dir = addon_root / game["title_id"]
            try:
                game["dlc"] = sorted(c.name for c in dlc_dir.iterdir() if c.is_dir())
            except OSError:
                pass
        if orphans:
            for orphan in orphans:
                if orphan["title_id"] and orphan["title_id"] == game["title_id"] and game["update"] is None:
                    game["update"] = {"folder": orphan["folder"], "version": orphan["version"]}
                    orphan["attached"] = True

    games = sorted(bases.values(), key=lambda g: (not g["launchable"], g["title"].lower()))
    for orphan in orphans:
        if not orphan.get("attached"):
            games.append({
                "key": game_key(orphan["folder"]), "platform": "PS4", "emulator": "shadPS4",
                "title": orphan["title"] or Path(orphan["folder"]).name, "title_id": orphan["title_id"],
                "content_id": "", "category": "gp", "version": orphan["version"], "system_version": "",
                "folder": orphan["folder"], "launch_target": "", "launchable": False,
                "icon": _first_file(Path(orphan["folder"]) / "sce_sys", ["icon0.png"]), "background": "",
                "update": None, "mods": False, "dlc": [], "custom_config": False,
                "note": "Mise à jour installée sans le jeu de base.",
            })
    return games


def _first_file(folder: Path, names: list[str]) -> str:
    for name in names:
        candidate = folder / name
        if candidate.is_file():
            return str(candidate)
    return ""


def build_command(exe: str, eboot: str, fullscreen: bool = False, show_fps: bool = False) -> list[str]:
    command = [exe, "--game", eboot]
    if fullscreen:
        command += ["--fullscreen", "true"]
    if show_fps:
        command.append("--show-fps")
    return command


# --------------------------------------------------------------------------- matériel et journal

_gpu_cache: list[str] | None = None


def list_gpus() -> list[str]:
    """Noms des cartes graphiques (WMI)."""
    global _gpu_cache
    if _gpu_cache is not None:
        return _gpu_cache
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"],
            capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW,
        )
        _gpu_cache = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        _gpu_cache = []
    return _gpu_cache


_LOG_LINE = re.compile(
    r"^\[(?P<channel>[^\]]+)\] <(?P<level>\w+)> (?:\([^)]*\) )?(?P<source>\S+:\d+) (?P<func>[^:]+): (?P<message>.*)$"
)

_HINTS = [
    (re.compile(r"isNullGpu: true"), "error", "Rendu graphique coupé",
     "Le réglage « null GPU » était actif : le jeu tournait sans rien afficher. Désactive-le dans Réglages."),
    (re.compile(r"OutOfDeviceMemory|ERROR_OUT_OF_DEVICE_MEMORY", re.I), "error", "Mémoire vidéo saturée",
     "La carte graphique n'a plus assez de mémoire. Baisse la résolution émulée ou ferme les autres programmes."),
    (re.compile(r"OutOfHostMemory|bad_alloc|Failed to allocate", re.I), "error", "Mémoire vive saturée",
     "Le PC a manqué de mémoire. Ferme les programmes ouverts (navigateur, jeux) avant de lancer."),
    (re.compile(r"DEVICE_LOST|DeviceLost", re.I), "error", "Le pilote graphique a lâché",
     "La carte graphique a cessé de répondre. Mets à jour le pilote, ou le jeu est trop lourd pour cette carte."),
    (re.compile(r"Unhandled Exception code 0xc000001d|PatchesIllegalInstructionHandler: Failed", re.I), "error",
     "Code que shadPS4 ne sait pas encore exécuter",
     "Ce jeu n'est pas encore compatible avec cette version de shadPS4. Ce n'est ni ton PC ni Cartouche : "
     "seule une future version de shadPS4 pourra le lancer."),
    (re.compile(r"Unresolved|not implemented|Unimplemented|UNREACHABLE", re.I), "warn",
     "Fonction PS4 pas encore émulée",
     "Le jeu appelle quelque chose que shadPS4 ne sait pas encore faire. Une version plus récente de shadPS4 peut aider."),
    (re.compile(r"Access violation|EXCEPTION_ACCESS_VIOLATION|Unhandled exception|SIGSEGV", re.I), "error",
     "Plantage de l'émulateur", "shadPS4 s'est arrêté brutalement sur ce jeu."),
    (re.compile(r"No dumped system fonts"), "info", "Polices système absentes",
     "Certains textes peuvent manquer. Ces polices se récupèrent sur ta propre PS4."),
    (re.compile(r"Couldn't extract trophy"), "info", "Trophées non extraits",
     "Sans conséquence pour le jeu."),
]


def diagnose_log(user_dir: Path, max_errors: int = 25) -> dict:
    """Résumé du dernier journal de shadPS4."""
    path = Path(user_dir) / "log" / "shad_log.txt"
    result: dict = {"exists": False, "path": str(path)}
    try:
        stat = path.stat()
    except OSError:
        return result
    result.update(exists=True, modified=stat.st_mtime, size=stat.st_size)
    try:
        with open(path, "rb") as handle:
            if stat.st_size <= 3 << 20:
                raw = handle.read()
            else:
                head = handle.read(512 << 10)
                handle.seek(-(2 << 20), os.SEEK_END)
                raw = head + b"\n" + handle.read()
    except OSError:
        return result
    content = raw.decode("utf-8", "replace")
    lines = content.splitlines()

    def find(pattern: str) -> str:
        match = re.search(pattern, content, re.M)
        return match.group(1).strip() if match else ""

    result["emulator_version"] = find(r"Starting shadps4 emulator (\S+)")
    result["game_id"] = find(r"Game id: (\S+)")
    result["game_title"] = find(r"Game id: \S+ Title: (.+)$")
    result["gpu"] = find(r"GPU_Model: (.+)$")
    result["vulkan"] = find(r"GPU_Vulkan_Version: (.+)$")
    result["driver"] = find(r"GPU_Vulkan_Driver: (.+)$")
    result["cpu"] = find(r"CPU Model: (.+)$")
    result["ram"] = find(r"Total RAM: (.+)$")
    gpu_id = find(r"Vulkan gpuId: (-?\d+)")
    result["gpu_id"] = int(gpu_id) if gpu_id else None

    errors: dict[str, dict] = {}
    for line in lines:
        match = _LOG_LINE.match(line)
        if not match or match.group("level") not in ("Error", "Critical"):
            continue
        message = match.group("message").strip()
        key = match.group("level") + message
        if key in errors:
            errors[key]["count"] += 1
        elif len(errors) < max_errors:
            errors[key] = {"level": match.group("level"), "channel": match.group("channel"),
                           "message": message, "count": 1}
    result["errors"] = list(errors.values())
    result["hints"] = [
        {"level": level, "title": title, "text": hint}
        for pattern, level, title, hint in _HINTS if pattern.search(content)
    ]
    result["tail"] = [line[:400] for line in lines[-40:]]
    return result


def last_run_summary(user_dir: Path) -> dict:
    """GPU utilisé au dernier lancement (pour étiqueter le choix de carte graphique)."""
    info = diagnose_log(user_dir, max_errors=0)
    return {"gpu": info.get("gpu", ""), "gpu_id": info.get("gpu_id")}
