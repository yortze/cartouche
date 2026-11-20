import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from builders import PNG_1PX, ps4_sfo  # noqa: E402
from cartouche import shadps4  # noqa: E402
from cartouche.runner import describe_exit  # noqa: E402

REAL_CONFIG = Path.home() / "AppData/Roaming/shadPS4/config.json"
REAL_LOG = Path.home() / "AppData/Roaming/shadPS4/log/shad_log.txt"

SAMPLE_CONFIG = {
    "GPU": {"null_gpu": True, "readbacks_mode": 1, "fsr_enabled": True, "internal_screen_width": 1920,
            "internal_screen_height": 1080, "dump_shaders": False, "copy_gpu_buffers": False},
    "General": {"console_language": 1, "show_fps_counter": False,
                "install_dirs": [{"enabled": True, "path": "C:\\Jeux"}, {"enabled": False, "path": "D:\\Off"}]},
    "Log": {"sync": True, "type": "wincolor"},
    "Vulkan": {"gpu_id": 1, "pipeline_cache_enabled": False, "vkvalidation_enabled": True,
               "vkcrash_diagnostic_enabled": False},
}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user = Path(self.tmp.name)
        (self.user / "config.json").write_text(json.dumps(SAMPLE_CONFIG, indent=2) + "\n", encoding="utf-8")
        self.config = shadps4.EmuConfig(self.user)

    def tearDown(self):
        self.tmp.cleanup()

    def test_view_and_warnings(self):
        data = self.config.load()
        view = {item["id"]: item for item in shadps4.settings_view(data, ["Intel", "NVIDIA"], {"gpu": "NVIDIA", "gpu_id": 1})}
        self.assertTrue(view["null_gpu"]["value"])
        self.assertEqual(view["resolution"]["value"], "1920x1080")
        self.assertIn("NVIDIA", [c["label"] for c in view["gpu_id"]["choices"]][2])
        levels = [w["level"] for w in shadps4.config_warnings(data)]
        self.assertIn("error", levels)
        self.assertEqual(shadps4.install_dirs(data), ["C:\\Jeux"])

    def test_apply_changes_and_backup(self):
        data = self.config.load()
        changed = shadps4.apply_changes(data, {"null_gpu": False, "resolution": "1280x720", "console_language": 2,
                                               "gpu_id": -1, "bogus": True, "fsr_enabled": "oui"})
        self.assertEqual(len(changed), 4)
        self.config.write(data)
        written = json.loads((self.user / "config.json").read_text(encoding="utf-8"))
        self.assertFalse(written["GPU"]["null_gpu"])
        self.assertEqual(written["GPU"]["internal_screen_width"], 1280)
        self.assertEqual(written["GPU"]["internal_screen_height"], 720)
        self.assertEqual(written["General"]["console_language"], 2)
        self.assertTrue(written["GPU"]["fsr_enabled"], "une valeur invalide ne doit rien changer")
        self.assertEqual(written["Log"]["type"], "wincolor", "les autres clés sont conservées")
        self.assertTrue((self.user / "config.json").read_text(encoding="utf-8").endswith("\n"))
        backup = json.loads(self.config.backup_path.read_text(encoding="utf-8"))
        self.assertTrue(backup["GPU"]["null_gpu"])
        self.assertTrue(self.config.restore_backup())
        self.assertTrue(self.config.load()["GPU"]["null_gpu"])

    def test_light_preset_only_touches_existing_keys(self):
        data = self.config.load()
        changed = shadps4.apply_light_preset(data)
        self.assertIn("GPU.null_gpu", changed)
        self.assertIn("Log.sync", changed)
        self.assertNotIn("Vulkan.renderdoc_enabled", changed)
        self.assertNotIn("renderdoc_enabled", data["Vulkan"])
        self.assertTrue(data["Vulkan"]["pipeline_cache_enabled"])
        self.assertEqual(shadps4.config_warnings(data), [])

    def test_ensure_install_dir(self):
        self.assertTrue(shadps4.ensure_install_dir(self.config, "E:\\NouveauxJeux"))
        self.assertFalse(shadps4.ensure_install_dir(self.config, "e:\\nouveauxjeux"))
        self.assertIn("E:\\NouveauxJeux", shadps4.install_dirs(self.config.load()))

    @unittest.skipUnless(REAL_CONFIG.is_file(), "config.json réel absent")
    def test_real_config_is_understood(self):
        tmp_user = self.user / "reel"
        tmp_user.mkdir()
        shutil.copy2(REAL_CONFIG, tmp_user / "config.json")
        data = shadps4.EmuConfig(tmp_user).load()
        view = shadps4.settings_view(data, [], None)
        missing = [item["id"] for item in view if not item["available"]]
        self.assertEqual(missing, [], "toutes les options choisies existent dans la vraie config")
        before = (tmp_user / "config.json").read_text(encoding="utf-8")
        shadps4.EmuConfig(tmp_user).write(data)
        self.assertEqual(json.loads((tmp_user / "config.json").read_text(encoding="utf-8")), json.loads(before))


class BuildsTests(unittest.TestCase):
    def test_managed_build_keeps_its_label_when_chosen(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CARTOUCHE_DATA"] = tmp
            try:
                exe = Path(tmp) / "shadps4" / "0.21.0" / "shadPS4.exe"
                exe.parent.mkdir(parents=True)
                exe.write_bytes(b"MZ")
                builds = shadps4.find_builds(custom=str(exe))
                chosen = shadps4.choose_build(builds, str(exe))
            finally:
                os.environ.pop("CARTOUCHE_DATA", None)
        self.assertEqual((chosen.label, chosen.source, chosen.version), ("v0.21.0", "Installé par Cartouche", "0.21.0"))


class GamesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.games = root / "games"
        self.addons = root / "addcont"
        self.user = root / "user"
        self.make_game("CUSA00001", "gd", "01.00", "Alpha", eboot=True)
        self.make_game("CUSA00001-UPDATE", "gp", "01.05", "Alpha", title_id="CUSA00001")
        self.make_game("Rangement/CUSA00002", "gd", "01.00", "Beta", eboot=True)
        self.make_game("NPXS00009", "gde", "01.00", "Appli", eboot=False)
        self.make_game("CUSA00003-patch", "gp", "01.02", "Gamma", title_id="CUSA00003")
        (self.addons / "CUSA00001" / "DLCPACK000000001").mkdir(parents=True)
        (self.user / "custom_configs").mkdir(parents=True)
        (self.user / "custom_configs" / "CUSA00002.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def make_game(self, rel, category, version, title, eboot=False, title_id=None):
        folder = self.games / rel
        (folder / "sce_sys").mkdir(parents=True)
        tid = title_id or Path(rel).name
        (folder / "sce_sys" / "param.sfo").write_bytes(
            ps4_sfo(title_id=tid, category=category, app_ver=version, title=title))
        (folder / "sce_sys" / "icon0.png").write_bytes(PNG_1PX)
        if eboot:
            (folder / "eboot.bin").write_bytes(b"\x7fELF")

    def test_scan(self):
        games = shadps4.scan_games([str(self.games)], str(self.addons), self.user)
        by_title = {g["title"]: g for g in games}
        alpha = by_title["Alpha"]
        self.assertTrue(alpha["launchable"])
        self.assertEqual(alpha["update"]["version"], "01.05")
        self.assertEqual(alpha["dlc"], ["DLCPACK000000001"])
        self.assertTrue(alpha["icon"].endswith("icon0.png"))
        self.assertTrue(by_title["Beta"]["custom_config"], "jeu rangé dans un sous-dossier trouvé")
        self.assertFalse(by_title["Appli"]["launchable"])
        self.assertIn("eboot.bin", by_title["Appli"]["note"])
        orphan = by_title["Gamma"]
        self.assertFalse(orphan["launchable"])
        self.assertIn("sans le jeu de base", orphan["note"])
        self.assertEqual(games[-1]["title"], "Gamma", "les entrées non lançables passent en dernier")

    def test_command(self):
        self.assertEqual(shadps4.build_command("s.exe", "e.bin", True, True),
                         ["s.exe", "--game", "e.bin", "--fullscreen", "true", "--show-fps"])


class LogTests(unittest.TestCase):
    SAMPLE = "\n".join([
        "[Loader] <Info> (shadPS4:Main) emulator.cpp:473 Run: Starting shadps4 emulator v0.18.0 ",
        "[Config] <Info> (shadPS4:Main) emulator.cpp:491 Run: GPU isNullGpu: true",
        "[Config] <Info> (shadPS4:Main) emulator.cpp:499 Run: Vulkan gpuId: 1",
        "[Loader] <Info> (shadPS4:Main) emulator.cpp:524 Run: Game id: CUSA12345 Title: Jeu Test",
        "[Loader] <Error> (shadPS4:Main) emulator.cpp:241 ExtractTrophies: Couldn't extract trophy file trophy00.trp",
        "[Render.Vulkan] <Info> (shadPS4:Main) vk_instance.cpp:638 CollectDeviceParameters: GPU_Model: NVIDIA GeForce MX350",
        "[Render.Vulkan] <Critical> (shadPS4:Main) vk_scheduler.cpp:10 Submit: ErrorDeviceLost",
        "[Render.Vulkan] <Critical> (shadPS4:Main) vk_scheduler.cpp:10 Submit: ErrorDeviceLost",
    ])

    def test_diagnose(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "log"
            log_dir.mkdir()
            (log_dir / "shad_log.txt").write_text(self.SAMPLE, encoding="utf-8")
            info = shadps4.diagnose_log(Path(tmp))
        self.assertEqual(info["emulator_version"], "v0.18.0")
        self.assertEqual(info["game_id"], "CUSA12345")
        self.assertEqual(info["game_title"], "Jeu Test")
        self.assertEqual(info["gpu"], "NVIDIA GeForce MX350")
        self.assertEqual(info["gpu_id"], 1)
        self.assertEqual(len(info["errors"]), 2)
        self.assertEqual(info["errors"][1]["count"], 2)
        titles = {h["title"] for h in info["hints"]}
        self.assertIn("Rendu graphique coupé", titles)
        self.assertIn("Le pilote graphique a lâché", titles)
        self.assertIn("Trophées non extraits", titles)

    @unittest.skipUnless(REAL_LOG.is_file(), "journal réel absent")
    def test_real_log(self):
        info = shadps4.diagnose_log(REAL_LOG.parent.parent)
        self.assertTrue(info["exists"])
        self.assertTrue(info["emulator_version"].startswith("v"))

    def test_illegal_instruction_hint(self):
        # Lignes réelles du journal de shadPS4 0.18.0 au lancement d'Apollo Save Tool
        sample = "\n".join([
            "[Core] <Error> (Game:Main) cpu_patches.cpp:2164 PatchesIllegalInstructionHandler: Failed to patch address 80000000 -- mnemonic: (failed to decode)",
            "[Debug] <Critical> (Game:Main) signals.cpp:86 SignalHandler: Unhandled Exception code 0xc000001d at 0x80000000",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "log").mkdir()
            (Path(tmp) / "log" / "shad_log.txt").write_text(sample, encoding="utf-8")
            info = shadps4.diagnose_log(Path(tmp))
        self.assertEqual(info["hints"][0]["title"], "Code que shadPS4 ne sait pas encore exécuter")
        self.assertEqual([e["level"] for e in info["errors"]], ["Error", "Critical"])
        self.assertIn("pas su exécuter", describe_exit(-1073741795, 9))  # 0xC000001D

    def test_exit_codes(self):
        self.assertEqual(describe_exit(0, 100), "Fermé normalement.")
        text = describe_exit(-1073741819, 3)  # 0xC0000005
        self.assertIn("0xC0000005", text)
        self.assertIn("violation d'accès", text)
        self.assertIn("presque tout de suite", text)


if __name__ == "__main__":
    unittest.main()
