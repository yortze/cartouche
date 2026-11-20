"""Parcours complet : bibliothèque, installation PS4 via un faux pkg_extractor, lancement, serveur."""

import http.client
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from builders import build_ps3_pkg, build_ps4_pkg, ps4_sfo  # noqa: E402


class CoreFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        os.environ["CARTOUCHE_DATA"] = str(root / "data")
        cls.root = root
        emu = root / "emu"
        (emu / "user").mkdir(parents=True)  # dossier 'user' à côté de l'exe : mode portable
        (emu / "shadPS4.exe").write_bytes(b"MZ")
        cls.games = root / "jeux"
        config = {
            "GPU": {"null_gpu": True, "readbacks_mode": 0, "internal_screen_width": 1280,
                    "internal_screen_height": 720},
            "General": {"install_dirs": [], "addon_install_dir": str(root / "addcont"),
                        "console_language": 1, "show_fps_counter": False},
            "Log": {"sync": True},
            "Vulkan": {"gpu_id": -1, "pipeline_cache_enabled": False},
        }
        (emu / "user" / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        pkgs = root / "pkgs"
        (pkgs / "sous-dossier").mkdir(parents=True)
        cid = "EP0001-CUSA12345_00-TESTGAME00000000"
        (pkgs / "jeu.pkg").write_bytes(build_ps4_pkg(ps4_sfo(title="Jeu Test"), cid))
        (pkgs / "sous-dossier" / "maj.pkg").write_bytes(
            build_ps4_pkg(ps4_sfo(category="gp", app_ver="01.09"), cid))
        (pkgs / "ps3.pkg").write_bytes(build_ps3_pkg("UP0001-NPUB30001_00-0000000000000000"))
        app_cid = "IV0000-APPS00001_00-HOMEBREWAPP00001"
        (pkgs / "appli.pkg").write_bytes(build_ps4_pkg(
            ps4_sfo(title_id="APPS00001", category="gde", title="Appli maison", content_id=app_cid), app_cid))
        (pkgs / "macos.pkg").write_bytes(b"xar!" + b"\0" * 64)
        compat_path = root / "compat.json"
        compat_path.write_text(json.dumps({"CUSA12345": {"os-windows": {
            "status": "status-playable", "version": "v0.21.0", "last_tested": "2026-11-01T00:00:00Z",
            "issue_number": "42"}}}), encoding="utf-8")

        from cartouche.compat import CompatDb
        from cartouche.core import Core
        from cartouche.settings import Settings

        settings = Settings(root / "settings.json")
        settings.update({
            "first_run_done": True, "shadps4_exe": str(emu / "shadPS4.exe"),
            "pkg_extractor": str(HERE / "fake_extractor.py"), "ps4_games_dir": str(cls.games),
            "pkg_dirs": [str(pkgs)], "rpcs3_exe": str(root / "absent" / "rpcs3.exe"),
        })
        cls.core = Core(settings=settings, compat=CompatDb(path=compat_path, url="http://127.0.0.1:9/"))
        cls.emu = emu

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        os.environ.pop("CARTOUCHE_DATA", None)

    def wait_job(self, job_id, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.core.jobs.get(job_id)
            if job and not job.active:
                return job
            time.sleep(0.1)
        self.fail("tâche trop longue")

    def pkg(self, library, name):
        return next(p for p in library["pkgs"] if p["file_name"] == name)

    def test_1_library(self):
        library = self.core.library()
        names = sorted(p["file_name"] for p in library["pkgs"])
        self.assertEqual(names, ["appli.pkg", "jeu.pkg", "maj.pkg", "ps3.pkg"])
        self.assertEqual([i["name"] for i in library["ignored"]], ["macos.pkg"])
        game = self.pkg(library, "jeu.pkg")
        self.assertEqual(game["status"], "not_installed")
        self.assertEqual(game["title"], "Jeu Test")
        self.assertEqual(game["compat"]["label"], "Jouable")
        self.assertTrue(game["icon_url"].startswith("/img/"))
        self.assertIsNotNone(self.core.images.read(game["icon_url"][5:]))
        self.assertEqual(self.pkg(library, "maj.pkg")["status"], "needs_base")
        ps3 = self.pkg(library, "ps3.pkg")
        self.assertFalse(ps3["emulator_ready"])
        self.assertEqual(ps3["title"], "ps3", "titre PS3 inconnu avant installation : nom du fichier")

    def test_2_install_game_then_update(self):
        from cartouche.core import ActionError

        game = self.pkg(self.core.library(), "jeu.pkg")
        job = self.wait_job(self.core.install(game["id"])["id"])
        self.assertEqual(job.status, "done", job.error)
        self.assertTrue((self.games / "CUSA12345" / "eboot.bin").is_file())
        library = self.core.library()
        self.assertEqual(self.pkg(library, "jeu.pkg")["status"], "installed")
        installed = next(g for g in library["games"] if g["title_id"] == "CUSA12345")
        self.assertTrue(installed["launchable"])
        self.assertEqual(installed["compat"]["status"], "playable")
        with self.assertRaises(ActionError) as ctx:
            self.core.install(game["id"])
        self.assertEqual(ctx.exception.fix, "confirm_install")

        update = self.pkg(library, "maj.pkg")
        self.assertEqual(update["status"], "not_installed")
        job = self.wait_job(self.core.install(update["id"])["id"])
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(self.pkg(self.core.library(), "maj.pkg")["status"], "installed")
        # le dossier de jeux a été ajouté à la liste de shadPS4
        config = json.loads((self.emu / "user" / "config.json").read_text(encoding="utf-8"))
        self.assertIn(str(self.games), [d["path"] for d in config["General"]["install_dirs"]])

        # une appli (catégorie « gde », comme Apollo Save Tool) s'installe et se lance comme un jeu
        app = self.pkg(self.core.library(), "appli.pkg")
        self.assertEqual((app["category"], app["status"]), ("app", "not_installed"))
        job = self.wait_job(self.core.install(app["id"])["id"])
        self.assertEqual(job.status, "done", job.error)
        library = self.core.library()
        self.assertEqual(self.pkg(library, "appli.pkg")["status"], "installed")
        self.assertTrue(next(g for g in library["games"] if g["title_id"] == "APPS00001")["launchable"])

    def test_3_launch_checks_null_gpu_then_runs(self):
        from cartouche.core import ActionError

        game = next(g for g in self.core.library()["games"] if g["title_id"] == "CUSA12345")
        with self.assertRaises(ActionError) as ctx:
            self.core.launch(game["key"])
        self.assertEqual(ctx.exception.fix, "null_gpu")
        self.core.fix("null_gpu")
        captured = {}

        def fake_launch(game_dict, command, show_console):
            captured["command"] = command
            return {"key": game_dict["key"]}

        original = self.core.runner.launch
        self.core.runner.launch = fake_launch
        try:
            self.core.launch(game["key"])
        finally:
            self.core.runner.launch = original
        self.assertEqual(captured["command"][1:3], ["--game", str(self.games / "CUSA12345" / "eboot.bin")])

    def test_4_ps3_needs_rpcs3(self):
        from cartouche.core import ActionError

        ps3 = self.pkg(self.core.library(), "ps3.pkg")
        with self.assertRaises(ActionError) as ctx:
            self.core.install(ps3["id"])
        self.assertEqual(ctx.exception.fix, "pick_rpcs3_exe")

    def test_5_runner_records_crash(self):
        from cartouche.runner import Runner

        runner = Runner()
        game = {"key": "k", "title": "Test", "platform": "PS4", "emulator": "shadPS4"}
        runner.launch(game, [sys.executable, "-c", "import sys; sys.exit(3)"])
        deadline = time.time() + 15
        while runner.state()["running"] and time.time() < deadline:
            time.sleep(0.1)
        last = runner.state()["last"]
        self.assertEqual(last["exit_code"], 3)
        self.assertTrue(last["crashed"])

    def test_6_diagnostics_and_emu_settings(self):
        diag = self.core.diagnostics()
        titles = [c["title"] for c in diag["checks"]]
        self.assertTrue(any(t.startswith("shadPS4 trouvé") for t in titles))
        self.assertTrue(any(t.startswith("pkg_extractor trouvé") for t in titles))
        view = self.core.emu_settings()
        self.assertTrue(view["available"])
        result = self.core.light_preset()
        self.assertIn("Log.sync", result["changed"])

    def test_8_crash_notification_explains_cause(self):
        log_dir = self.emu / "user" / "log"
        log_dir.mkdir(exist_ok=True)
        (log_dir / "shad_log.txt").write_text("\n".join([
            "[Core] <Error> (Game:Main) cpu_patches.cpp:2684 PatchesIllegalInstructionHandler: Failed to patch address 80000000 -- mnemonic: (failed to decode)",
            "[Debug] <Critical> (Game:Main) signals.cpp:145 SignalHandler: Unhandled Exception code 0xc000001d at 0x80000000",
        ]), encoding="utf-8")
        self.core._game_exited({"title": "Appli maison", "platform": "PS4", "crashed": True,
                                "started": time.time() - 5, "summary": "Arrêt avec le code 0xC0000005."})
        last = self.core.status()["events"][-1]
        self.assertEqual(last["level"], "warn")
        self.assertIn("Appli maison s'est arrêté. Code que shadPS4 ne sait pas encore exécuter", last["text"])
        # un journal plus ancien que le lancement n'est pas utilisé
        self.core._game_exited({"title": "Autre", "platform": "PS4", "crashed": True,
                                "started": time.time() + 60, "summary": "Arrêt avec le code 3."})
        self.assertEqual(self.core.status()["events"][-1]["text"], "Autre : Arrêt avec le code 3.")

    def test_7_server(self):
        from cartouche.server import make_server, serve_in_thread

        server = make_server(self.core, 0)
        serve_in_thread(server)
        port = server.server_address[1]
        host = f"127.0.0.1:{port}"

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            payload = json.dumps(body).encode() if body is not None else None
            all_headers = {"Host": host, "Content-Type": "application/json"}
            all_headers.update(headers or {})
            connection.request(method, path, body=payload, headers=all_headers)
            response = connection.getresponse()
            data = response.read()
            connection.close()
            return response.status, data

        token = {"X-Cartouche-Token": server.token}
        try:
            status, html = request("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn(server.token.encode(), html)
            self.assertEqual(request("GET", "/api/status")[0], 403)
            self.assertEqual(request("GET", "/api/status", headers=token)[0], 200)
            self.assertEqual(request("GET", "/api/status", headers={**token, "Host": "evil.example:80"})[0], 403)
            status, data = request("GET", "/api/library", headers=token)
            self.assertEqual(status, 200)
            self.assertEqual(len(json.loads(data)["pkgs"]), 4)
            self.assertEqual(request("POST", "/api/stop", {}, {**token, "Origin": "https://evil.example"})[0], 403)
            self.assertEqual(request("POST", "/api/stop", {})[0], 403)
            self.assertEqual(request("POST", "/api/bye", {"token": server.token})[0], 200)
            status, data = request("POST", "/api/install", {"id": "inconnu"}, token)
            self.assertEqual(status, 400)
            self.assertIn("bibliothèque", json.loads(data)["error"])
            self.assertEqual(request("GET", "/static/../app.py")[0], 404)
            self.assertEqual(request("GET", "/img/0000")[0], 404)
            self.assertEqual(request("GET", "/api/hello")[0], 200)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
