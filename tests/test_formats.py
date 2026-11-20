import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from builders import PNG_1PX, build_ps3_pkg, build_ps4_pkg, build_sfo, ps4_sfo  # noqa: E402
from cartouche.pkg import PkgError, PkgInfo, read_pkg, read_region, sniff  # noqa: E402
from cartouche.sfo import (  # noqa: E402
    SfoError, format_ps3_system_ver, format_ps4_system_ver, parse_sfo, pick_title, version_key)

REAL_SFO = Path.home() / "Downloads/shadPS4QtLauncher-win64-qt-2026-09-12-864dc30/games/NPXS39041/sce_sys/param.sfo"
REAL_MAC_PKG = Path.home() / "Downloads/oc4vm-3.0.1/packages/VoodooHDA-Tahoe.pkg"


class SfoTests(unittest.TestCase):
    def test_roundtrip(self):
        data = build_sfo({"TITLE": "Mon jeu", "APP_VER": "01.05", "SYSTEM_VER": 0x05050000})
        sfo = parse_sfo(data)
        self.assertEqual(sfo["TITLE"], "Mon jeu")
        self.assertEqual(sfo["APP_VER"], "01.05")
        self.assertEqual(sfo["SYSTEM_VER"], 0x05050000)

    def test_bad_magic(self):
        with self.assertRaises(SfoError):
            parse_sfo(b"not an sfo at all, sorry")

    def test_truncated(self):
        data = build_sfo({"TITLE": "x"})
        with self.assertRaises(SfoError):
            parse_sfo(data[:24])

    def test_french_title_preferred(self):
        sfo = {"TITLE": "The Game", "TITLE_02": "Le Jeu"}
        self.assertEqual(pick_title(sfo), "Le Jeu")
        self.assertEqual(pick_title(sfo, prefer_french=False), "The Game")
        self.assertEqual(pick_title({"TITLE": "Ligne 1\nLigne 2"}), "Ligne 1 Ligne 2")

    def test_versions(self):
        self.assertEqual(format_ps4_system_ver(0x01700000), "1.70")
        self.assertEqual(format_ps4_system_ver(0x05050000), "5.05")
        self.assertEqual(format_ps4_system_ver(0), "")
        self.assertEqual(format_ps3_system_ver("04.4100"), "4.41")
        self.assertGreater(version_key("01.10"), version_key("01.09"))
        self.assertEqual(version_key(""), ())

    @unittest.skipUnless(REAL_SFO.is_file(), "PARAM.SFO réel absent")
    def test_real_ps4_sfo(self):
        sfo = parse_sfo(REAL_SFO.read_bytes())
        self.assertEqual(sfo["TITLE_ID"], "NPXS39041")
        self.assertEqual(sfo["CATEGORY"], "gde")


class Ps4PkgTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_game(self):
        content_id = "EP0001-CUSA12345_00-TESTGAME00000000"
        path = self.write("jeu.pkg", build_ps4_pkg(ps4_sfo(title_fr="Jeu en français"), content_id))
        self.assertEqual(sniff(path), "PS4")
        info = read_pkg(path)
        self.assertEqual(info.platform, "PS4")
        self.assertEqual(info.title_id, "CUSA12345")
        self.assertEqual(info.content_id, content_id)
        self.assertEqual(info.title, "Jeu en français")
        self.assertEqual(info.category, "game")
        self.assertEqual(info.version, "01.00")
        self.assertEqual(info.system_version, "5.05")
        self.assertTrue(info.supported)
        self.assertFalse(info.truncated)
        self.assertEqual(read_region(path, info.icon), PNG_1PX)
        self.assertIsNone(info.background)
        clone = PkgInfo.from_dict(info.to_dict())
        self.assertEqual(clone.icon, info.icon)

    def test_update_and_dlc(self):
        update = read_pkg(self.write("maj.pkg", build_ps4_pkg(
            ps4_sfo(category="gp", app_ver="01.09"), "EP0001-CUSA12345_00-TESTGAME00000000")))
        self.assertEqual((update.category, update.version), ("update", "01.09"))
        dlc_cid = "EP0001-CUSA12345_00-DLCPACK000000001"
        dlc = read_pkg(self.write("dlc.pkg", build_ps4_pkg(
            ps4_sfo(category="ac", content_id=dlc_cid, version="01.02"), dlc_cid)))
        self.assertEqual((dlc.category, dlc.version, dlc.dlc_label), ("dlc", "01.02", "DLCPACK000000001"))

    def test_encrypted_sfo_falls_back_to_header(self):
        info = read_pkg(self.write("chiffre.pkg", build_ps4_pkg(
            ps4_sfo(), "EP0001-CUSA99999_00-TESTGAME00000000", encrypted_sfo=True, content_type=0x1B)))
        self.assertEqual(info.title, "")
        self.assertEqual(info.title_id, "CUSA99999")
        self.assertEqual(info.category, "dlc")
        self.assertTrue(any("chiffré" in note for note in info.notes))

    def test_truncated(self):
        data = build_ps4_pkg(ps4_sfo(), "EP0001-CUSA12345_00-TESTGAME00000000", declared_size=10 << 30)
        info = read_pkg(self.write("coupe.pkg", data))
        self.assertTrue(info.truncated)
        self.assertFalse(info.supported)
        self.assertIn("incomplet", info.notes[0])

    def test_not_a_pkg(self):
        path = self.write("faux.pkg", b"bonjour" * 100)
        self.assertEqual(sniff(path), "")
        with self.assertRaises(PkgError):
            read_pkg(path)
        mac = self.write("mac.pkg", b"xar!" + b"\0" * 100)
        self.assertEqual(sniff(mac), "macOS")
        with self.assertRaisesRegex(PkgError, "macOS"):
            read_pkg(mac)

    @unittest.skipUnless(REAL_MAC_PKG.is_file(), "installeur macOS réel absent")
    def test_real_macos_installer_is_rejected(self):
        self.assertEqual(sniff(REAL_MAC_PKG), "macOS")
        with self.assertRaises(PkgError):
            read_pkg(REAL_MAC_PKG)


class Ps3PkgTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, data: bytes):
        path = self.dir / "ps3.pkg"
        path.write_bytes(data)
        return read_pkg(path)

    def test_game(self):
        info = self.read(build_ps3_pkg("UP0001-NPUB30001_00-0000000000000000"))
        self.assertEqual(info.platform, "PS3")
        self.assertEqual(info.title_id, "NPUB30001")
        self.assertEqual(info.category, "game")
        self.assertEqual(info.version, "01.02")
        self.assertEqual(info.system_version, "4.41")
        self.assertTrue(info.retail)
        self.assertTrue(info.supported)

    def test_patch_and_dlc(self):
        patch = self.read(build_ps3_pkg("UP0001-BLUS30001_00-PATCH00000000001", content_type=0x04, flags=0x10))
        self.assertEqual(patch.category, "update")
        dlc = self.read(build_ps3_pkg("UP0001-NPUB30001_00-DLC0000000000001", content_type=0x04, flags=0x80,
                                      install_dir="NPUB30001"))
        self.assertEqual(dlc.category, "dlc")
        self.assertEqual(dlc.install_dir, "NPUB30001")

    def test_vita_is_unsupported(self):
        info = self.read(build_ps3_pkg("EP0001-PCSB00001_00-0000000000000000", content_type=0x15, platform=2))
        self.assertEqual(info.platform, "VITA")
        self.assertFalse(info.supported)

    def test_package_version_used_when_no_app_version(self):
        info = self.read(build_ps3_pkg("UP0001-NPUB30001_00-0000000000000000", app_version=b"\x00\x00",
                                       package_version=b"\x01\x07"))
        self.assertEqual(info.version, "01.07")


if __name__ == "__main__":
    unittest.main()
