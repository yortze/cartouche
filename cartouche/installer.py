"""Installation des PKG : PS4 avec pkg_extractor.exe, PS3 avec RPCS3."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .jobs import Job, JobCancelled, JobError
from .pkg import PkgInfo
from .runner import describe_launch_error
from . import rpcs3

CREATE_NO_WINDOW = 0x08000000
# Codes et messages vérifiés sur le vrai pkg_extractor.exe (install_pkg.bat et sortie réelle).
CHECK_TYPE_CODES = {101: "game", 102: "update", 103: "dlc"}
KIND_LABELS = {"game": "jeu de base", "update": "mise à jour", "dlc": "DLC"}
INVALID_MARKER = "doesn't appear to be a valid PKG"
FAILURE_MARKER = "Cannot extract PKG file"
FILE_COUNT = re.compile(r"Extracting file (\d+) of (\d+)")


def human_size(size: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.0f} {unit}" if unit in ("o", "Ko") else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return f"{size:.1f} To"


def decode_output(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("mbcs" if os.name == "nt" else "latin-1", "replace")


def extractor_command(extractor: str, *args: str) -> list[str]:
    # Un script .py est accepté pour les tests automatiques.
    if extractor.lower().endswith(".py"):
        return [sys.executable, extractor, *args]
    return [extractor, *args]


def check_type(extractor: str, pkg_path: str) -> tuple[str | None, str]:
    """Demande à pkg_extractor le type du PKG (codes du install_pkg.bat officiel)."""
    try:
        result = subprocess.run(
            extractor_command(extractor, pkg_path, "--check-type"),
            capture_output=True, timeout=300, cwd=str(Path(extractor).parent), creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise JobError("pkg_extractor ne répond pas (vérification du type).") from exc
    except OSError as exc:
        raise JobError(describe_launch_error("pkg_extractor", extractor, exc)) from exc
    output = decode_output(result.stdout + result.stderr)
    return CHECK_TYPE_CODES.get(result.returncode), output


def folder_size(path: Path) -> int:
    """Taille réelle d'un dossier, fichiers en cours d'écriture compris.

    Sous Windows, la taille donnée par le listage d'un dossier (os.scandir) reste figée tant
    qu'un fichier est ouvert en écriture : os.stat interroge le fichier lui-même.
    """
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += os.stat(entry.path).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _list_dirs(path: Path) -> set[str]:
    try:
        return {entry.name for entry in os.scandir(path) if entry.is_dir()}
    except OSError:
        return set()


def expected_folders(kind: str, info: PkgInfo, games_dir: Path, addons_dir: Path) -> list[Path]:
    title_id = info.title_id
    if kind == "game":
        return [games_dir / title_id]
    if kind == "update":  # pkg_extractor crée « -patch » ; le Qt Launcher utilisait « -UPDATE »
        return [games_dir / f"{title_id}-patch", games_dir / f"{title_id}-UPDATE"]
    if info.dlc_label:
        return [addons_dir / title_id / info.dlc_label, addons_dir / info.dlc_label]
    return [addons_dir / title_id]


def install_ps4(job: Job, info: PkgInfo, extractor: str, games_dir: str, addons_dir: str) -> None:
    if not extractor or not Path(extractor).is_file():
        raise JobError("pkg_extractor.exe est introuvable. Indique son emplacement dans Réglages.")
    if not games_dir:
        raise JobError("Aucun dossier de jeux PS4 n'est défini. Choisis-en un dans Réglages.")
    games = Path(games_dir)
    addons = Path(addons_dir)

    job.detail = "Vérification du PKG"
    kind, output = check_type(extractor, info.path)
    for line in output.splitlines():
        job.add_log(line)
    if kind is None:
        reason = "pkg_extractor ne reconnaît pas ce fichier comme un PKG PS4 installable."
        if INVALID_MARKER in output:
            reason = "Ce fichier n'est pas un PKG PS4 valide (ou il est chiffré pour une vraie console)."
        raise JobError(reason)
    if info.category in KIND_LABELS and info.category != kind:
        job.add_log(f"Note : l'en-tête annonce « {KIND_LABELS[info.category]} », "
                    f"pkg_extractor indique « {KIND_LABELS[kind]} ».")
    job.check_cancel()

    destination = addons if kind == "dlc" else games
    destination.mkdir(parents=True, exist_ok=True)
    needed = max(info.content_size, info.file_size)
    free = shutil.disk_usage(destination).free
    if free < needed * 1.05:
        raise JobError(f"Pas assez de place sur le disque : il faut environ {human_size(needed)}, "
                       f"il reste {human_size(free)}.")

    watch_roots = [destination] + ([addons / info.title_id] if kind == "dlc" and info.title_id else [])
    before = {str(root): _list_dirs(root) for root in watch_roots}
    expected = expected_folders(kind, info, games, addons)
    preexisting = {str(p) for p in expected if p.exists()}

    def candidates() -> list[Path]:
        found = [p for p in expected if p.exists()]
        for root in watch_roots:
            for name in _list_dirs(root) - before[str(root)]:
                path = root / name
                if path not in found and path not in watch_roots:
                    found.append(path)
        # Un dossier contenu dans un autre ne doit pas être compté deux fois.
        return [p for p in found if not any(q != p and q in p.parents for q in found)]

    command = extractor_command(extractor, info.path, str(destination))
    job.add_log("> " + " ".join(f'"{c}"' if " " in c else c for c in command))
    job.detail = f"Extraction ({KIND_LABELS[kind]})"
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(Path(extractor).parent), creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise JobError(describe_launch_error("pkg_extractor", extractor, exc)) from exc
    job.process = process
    output_lines: list[str] = []
    counts: list[tuple[int, int] | None] = [None]  # « Extracting file N of M » du vrai pkg_extractor

    def reader() -> None:
        for raw in iter(process.stdout.readline, b""):
            line = decode_output(raw).rstrip()
            output_lines.append(line)
            job.add_log(line)
            match = FILE_COUNT.search(line)
            if match:
                counts[0] = (int(match.group(1)), int(match.group(2)))

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    interval = 1.0
    started = time.time()
    while process.poll() is None:
        if job.cancel_event.is_set():
            process.terminate()
            break
        t0 = time.perf_counter()
        written = sum(folder_size(p) for p in candidates())
        cost = time.perf_counter() - t0
        interval = min(6.0, max(1.0, cost * 8))
        elapsed = time.time() - started
        by_size = written / needed if needed else 0.0
        if counts[0]:
            done, total = counts[0]
            by_count = done / total if total else 0.0
            # Les gros fichiers passent souvent en premier : le nombre de fichiers sous-estime alors
            # l'avancement. La taille écrite est plus juste, sauf si elle dépasse l'estimation
            # (contenu compressé) : là, seul le compteur de fichiers reste fiable.
            ratio = max(by_count, by_size) if by_size < 0.98 else by_count
            job.detail = f"Extraction : fichier {done} sur {total} · {human_size(written)} écrits"
        else:
            ratio = by_size
            job.detail = f"Extraction : {human_size(written)} sur environ {human_size(needed)}"
        job.progress = min(0.99, ratio)
        if elapsed > 5 and 0.02 < ratio < 0.99:
            remaining = elapsed * (1 - ratio) / ratio
            if remaining > 1:
                job.detail += f" (reste ~{_duration(remaining)})"
        job.cancel_event.wait(interval)
    code = process.wait()
    thread.join(timeout=5)
    process.stdout.close()

    if job.cancel_event.is_set():
        for path in candidates():
            if str(path) not in preexisting:
                shutil.rmtree(path, ignore_errors=True)
                job.add_log(f"Dossier partiel supprimé : {path}")
        raise JobCancelled()

    text = "\n".join(output_lines)
    for line in output_lines:
        if FAILURE_MARKER in line:
            reason = line.split(FAILURE_MARKER, 1)[1].strip(" :")
            raise JobError(f"pkg_extractor n'a pas pu extraire ce PKG{' : ' + reason if reason else '.'} "
                           "(un PKG chiffré pour une vraie console ne s'extrait pas).")
    target = None
    for path in expected + candidates():
        if path.is_dir() and (kind == "dlc" or (path / "sce_sys" / "param.sfo").is_file()):
            target = path
            break
    if INVALID_MARKER in text or target is None:
        raise JobError(f"L'extraction a échoué (code {code}). Le détail de pkg_extractor est ci-dessous.")
    if kind == "game" and not (target / "eboot.bin").is_file():
        job.add_log("Attention : aucun eboot.bin dans le dossier installé, le jeu ne pourra pas démarrer.")
    job.progress = 1.0
    job.detail = f"Installé dans {target}"
    job.result = {"folder": str(target), "kind": kind, "exit_code": code}


def install_ps3(job: Job, info: PkgInfo, rpcs3_exe: str) -> None:
    if not rpcs3_exe or not Path(rpcs3_exe).is_file():
        raise JobError("RPCS3 est introuvable. Installe-le puis indique rpcs3.exe dans Réglages.")
    command = rpcs3.install_command(rpcs3_exe, info.path)
    job.add_log("> " + " ".join(command))
    try:
        subprocess.Popen(command, cwd=str(Path(rpcs3_exe).parent))
    except OSError as exc:
        raise JobError(describe_launch_error("RPCS3", rpcs3_exe, exc)) from exc
    job.progress = 1.0
    job.detail = "RPCS3 s'ouvre et installe le paquet : suis la progression dans sa fenêtre."
    job.result = {"kind": info.category}


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds:02d}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d}"
