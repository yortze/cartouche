"""Vérification et installation de la dernière version officielle de shadPS4."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .paths import cache_dir, data_dir

API_URL = "https://api.github.com/repos/shadps4-emu/shadPS4/releases/latest"
RELEASES_PAGE = "https://github.com/shadps4-emu/shadPS4/releases/latest"
USER_AGENT = "Cartouche-launcher"
ALLOWED_HOSTS = ("github.com", "githubusercontent.com")


class UpdateError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def _version(value: str) -> str:
    match = re.search(r"(\d+\.\d+\.\d+)", value or "")
    return match.group(1) if match else ""


def pick_asset(assets: list[dict]) -> dict | None:
    """Archive Windows de l'émulateur (build SDL, sans interface Qt)."""
    names = [(a, str(a.get("name", ""))) for a in assets if isinstance(a, dict)]
    for pattern in (r"win64.*sdl.*\.zip$", r"win64(?!.*qt).*\.zip$"):
        for asset, name in names:
            if re.search(pattern, name, re.I):
                return asset
    return None


def check_latest(force: bool = False, max_age: float = 6 * 3600, api_url: str = API_URL) -> dict:
    cache = cache_dir() / "shadps4_release.json"
    if not force:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - cached.get("checked", 0) < max_age and not cached.get("error"):
                return cached
        except (OSError, ValueError):
            pass
    try:
        request = urllib.request.Request(api_url, headers={
            "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.loads(response.read(4 << 20).decode("utf-8"))
        asset = pick_asset(release.get("assets") or [])
        info = {
            "checked": time.time(),
            "tag": release.get("tag_name", ""),
            "name": release.get("name", ""),
            "version": _version(release.get("tag_name", "")) or _version(release.get("name", "")),
            "published": str(release.get("published_at") or "")[:10],
            "page": release.get("html_url") or RELEASES_PAGE,
            "asset": {"name": asset.get("name"), "url": asset.get("browser_download_url"),
                      "size": asset.get("size", 0)} if asset else None,
            "error": "",
        }
    except Exception as exc:
        info = {"checked": time.time(), "error": f"Impossible de joindre GitHub : {exc}", "asset": None,
                "page": RELEASES_PAGE, "version": "", "tag": "", "name": "", "published": ""}
    try:
        cache.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return info


def _check_url(url: str) -> None:
    parts = urllib.parse.urlparse(url or "")
    host = parts.hostname or ""
    if parts.scheme != "https" or not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        raise UpdateError(f"Adresse de téléchargement refusée : {url}")


def install_release(release: dict, progress=None, cancelled=None, root: Path | None = None,
                    check_url=_check_url) -> str:
    """Télécharge l'archive, l'extrait dans %APPDATA%\\Cartouche\\shadps4\\<version> et renvoie l'exe."""
    asset = release.get("asset") or {}
    url = asset.get("url") or ""
    check_url(url)
    version = release.get("version") or "derniere"
    root = root or data_dir() / "shadps4"
    target = root / version
    existing = next(target.rglob("shadPS4.exe"), None) if target.is_dir() else None
    if existing:
        return str(existing)

    downloads = root / "_telechargements"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / (asset.get("name") or f"shadps4-{version}.zip")
    partial = archive.with_name(archive.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(partial, "wb") as out:
            total = int(response.headers.get("Content-Length") or asset.get("size") or 0)
            done = 0
            while True:
                if cancelled and cancelled():
                    raise Cancelled()
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if total and done < total:
            raise UpdateError("Téléchargement incomplet, réessaie.")
        os.replace(partial, archive)
    except Cancelled:
        partial.unlink(missing_ok=True)
        raise
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Téléchargement impossible : {exc}") from exc

    staging = root / f"_{version}.extraction"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as zipped:
            base = staging.resolve()
            for member in zipped.infolist():
                destination = (staging / member.filename).resolve()
                if destination != base and base not in destination.parents:
                    raise UpdateError(f"Archive suspecte : chemin {member.filename!r} refusé")
            zipped.extractall(staging)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("L'archive téléchargée est abîmée.") from exc
    exe = next(staging.rglob("shadPS4.exe"), None)
    if exe is None:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("shadPS4.exe introuvable dans l'archive.")
    relative = exe.relative_to(staging)
    if target.exists():
        shutil.rmtree(target)
    os.replace(staging, target)
    archive.unlink(missing_ok=True)
    return str(target / relative)
