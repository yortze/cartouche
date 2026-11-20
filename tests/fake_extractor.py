"""Faux pkg_extractor.exe pour les tests, calqué sur le vrai (codes, messages, dossiers).

  fake_extractor.py <pkg> --check-type  -> 101 jeu, 102 mise à jour, 103 DLC, 0 erreur
  fake_extractor.py <pkg> <dossier>     -> <dossier>/<ID>, <ID>-patch ou <ID>/<libellé DLC>

Variables d'environnement :
  FAKE_EXTRACTOR_DELAY  durée de l'extraction en secondes (0,3 par défaut)
  FAKE_EXTRACTOR_FAIL   si définie, imite un échec (« Cannot extract PKG file »)
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cartouche.pkg import PkgError, read_pkg  # noqa: E402

CODES = {"game": 101, "app": 101, "update": 102, "dlc": 103}  # le vrai outil traite une appli « gde » en jeu


def main() -> int:
    pkg_path = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        info = read_pkg(pkg_path)
    except PkgError:
        print(f'"{pkg_path}" doesn\'t appear to be a valid PKG file\n\nTHE END "{pkg_path}"')
        return 0
    print(f'"{pkg_path}" is a valid PKG\n\nopen PKG file success\nopen PSF success', flush=True)
    print(f"PSF category = {info.sfo.get('CATEGORY', '')}", flush=True)
    if len(sys.argv) < 3 or sys.argv[2] == "--check-type":
        return CODES.get(info.category, 0)
    if os.environ.get("FAKE_EXTRACTOR_FAIL"):
        print("Cannot extract PKG file : failed to decrypt PFS image", flush=True)
        print(f'THE END "{pkg_path}"')
        return 0

    dest = Path(sys.argv[2])
    if info.category in ("game", "app"):
        folder = dest / info.title_id
    elif info.category == "update":
        folder = dest / f"{info.title_id}-patch"
    else:
        folder = dest / info.title_id / info.dlc_label
    steps = 8
    total = steps + 2
    (folder / "sce_sys").mkdir(parents=True, exist_ok=True)
    with open(pkg_path, "rb") as handle:
        data = handle.read()
    sfo_start = data.find(b"\0PSF")
    print(f'Extracting file 1 of {total} to "{folder}"', flush=True)
    (folder / "sce_sys" / "param.sfo").write_bytes(data[sfo_start:sfo_start + 4096])
    print(f'Extracting file 2 of {total} to "{folder}"', flush=True)
    if info.category in ("game", "app"):
        (folder / "eboot.bin").write_bytes(b"\x7fELF" + b"\0" * 1024)
    delay = float(os.environ.get("FAKE_EXTRACTOR_DELAY", "0.3"))
    chunk = max(1, info.content_size // steps)
    for step in range(steps):  # écriture progressive, pour voir la progression
        print(f'Extracting file {step + 3} of {total} to "{folder}"', flush=True)
        (folder / f"data_{step}.bin").write_bytes(b"\0" * chunk)
        time.sleep(delay / steps)
    print(f'THE END "{pkg_path}"', flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
