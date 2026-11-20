import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from builders import build_ps4_pkg, ps4_sfo  # noqa: E402
from cartouche.installer import expected_folders, folder_size, human_size, install_ps4  # noqa: E402
from cartouche.jobs import Job, JobError  # noqa: E402
from cartouche.pkg import PkgInfo, read_pkg  # noqa: E402


class InstallerTests(unittest.TestCase):
    def test_folder_size_counts_files_being_written(self):
        # Sous Windows, os.scandir donne 0 octet pour un fichier encore ouvert en écriture.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "sous").mkdir()
            (folder / "sous" / "fini.bin").write_bytes(b"\0" * 1000)
            with open(folder / "en_cours.bin", "wb") as out:
                out.write(b"\0" * 250_000)
                out.flush()
                self.assertEqual(folder_size(folder), 251_000)

    def test_expected_folders(self):
        info = PkgInfo(path="x.pkg", file_size=1, platform="PS4", title_id="CUSA00001", dlc_label="LABEL")
        games, addons = Path("G"), Path("A")
        self.assertEqual(expected_folders("game", info, games, addons), [games / "CUSA00001"])
        self.assertEqual(expected_folders("update", info, games, addons),
                         [games / "CUSA00001-patch", games / "CUSA00001-UPDATE"])
        self.assertEqual(expected_folders("dlc", info, games, addons)[0], addons / "CUSA00001" / "LABEL")

    def test_human_size(self):
        self.assertEqual(human_size(512), "512 o")
        self.assertEqual(human_size(3 * 1024 ** 3), "3,0 Go")


class ExtractionTests(unittest.TestCase):
    """install_ps4 avec le faux pkg_extractor, qui imite les messages du vrai."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.games = root / "jeux"
        self.addons = root / "addcont"
        self.extractor = str(Path(__file__).resolve().parent / "fake_extractor.py")
        cid = "EP0001-CUSA12345_00-TESTGAME00000000"
        self.game_pkg = root / "jeu.pkg"
        self.game_pkg.write_bytes(build_ps4_pkg(ps4_sfo(), cid))
        self.update_pkg = root / "maj.pkg"
        self.update_pkg.write_bytes(build_ps4_pkg(ps4_sfo(category="gp", app_ver="01.09"), cid))

    def tearDown(self):
        os.environ.pop("FAKE_EXTRACTOR_FAIL", None)
        self.tmp.cleanup()

    def run_install(self, pkg_path):
        job = Job("install", "Test")
        install_ps4(job, read_pkg(pkg_path), self.extractor, str(self.games), str(self.addons))
        return job

    def test_game_then_patch(self):
        job = self.run_install(self.game_pkg)
        self.assertEqual(Path(job.result["folder"]), self.games / "CUSA12345")
        self.assertTrue(any("Extracting file 1 of 10" in line for line in job.to_dict()["log"]))
        self.assertEqual(job.progress, 1.0)
        job = self.run_install(self.update_pkg)
        self.assertEqual(Path(job.result["folder"]), self.games / "CUSA12345-patch")
        self.assertEqual(job.result["kind"], "update")

    def test_failure_message_is_reported(self):
        os.environ["FAKE_EXTRACTOR_FAIL"] = "1"
        with self.assertRaises(JobError) as ctx:
            self.run_install(self.game_pkg)
        self.assertIn("failed to decrypt PFS image", str(ctx.exception))
        self.assertFalse((self.games / "CUSA12345").exists())


if __name__ == "__main__":
    unittest.main()
