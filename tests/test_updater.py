import io
import sys
import tempfile
import threading
import unittest
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cartouche import updater  # noqa: E402


def make_zip(members: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.www = self.root / "www"
        self.www.mkdir()
        (self.www / "bon.zip").write_bytes(make_zip({"shadPS4.exe": b"MZ" + b"\0" * 2048, "LISEZ.txt": b"ok"}))
        (self.www / "piege.zip").write_bytes(make_zip({"../../evade.exe": b"MZ"}))
        (self.www / "vide.zip").write_bytes(make_zip({"rien.txt": b""}))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(self.www)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def release(self, name):
        return {"version": "9.9.9", "asset": {"name": name, "url": self.base + name, "size": 0}}

    def test_install(self):
        seen = []
        exe = updater.install_release(self.release("bon.zip"), lambda d, t: seen.append(d),
                                      root=self.root / "emu", check_url=lambda url: None)
        self.assertTrue(Path(exe).is_file())
        self.assertEqual(Path(exe).parent.name, "9.9.9")
        self.assertTrue(seen)
        again = updater.install_release(self.release("bon.zip"), root=self.root / "emu", check_url=lambda url: None)
        self.assertEqual(again, exe)

    def test_path_traversal_is_refused(self):
        with self.assertRaises(updater.UpdateError):
            updater.install_release(self.release("piege.zip"), root=self.root / "emu", check_url=lambda url: None)
        self.assertFalse((self.root / "evade.exe").exists())

    def test_archive_without_exe(self):
        with self.assertRaisesRegex(updater.UpdateError, "introuvable"):
            updater.install_release(self.release("vide.zip"), root=self.root / "emu", check_url=lambda url: None)

    def test_cancel(self):
        with self.assertRaises(updater.Cancelled):
            updater.install_release(self.release("bon.zip"), cancelled=lambda: True,
                                    root=self.root / "emu", check_url=lambda url: None)

    def test_url_check(self):
        updater._check_url("https://github.com/shadps4-emu/shadPS4/releases/download/v1/x.zip")
        updater._check_url("https://objects.githubusercontent.com/abc")
        for bad in ("http://github.com/x.zip", "https://evil.example/x.zip", "https://github.com.evil.io/x"):
            with self.assertRaises(updater.UpdateError):
                updater._check_url(bad)

    def test_pick_asset(self):
        assets = [{"name": "shadps4-linux-sdl-0.21.0.zip"}, {"name": "shadps4-win64-qt-0.21.0.zip"},
                  {"name": "shadps4-win64-sdl-0.21.0.zip"}, {"name": "shadps4-macos-sdl.zip"}]
        self.assertEqual(updater.pick_asset(assets)["name"], "shadps4-win64-sdl-0.21.0.zip")
        self.assertIsNone(updater.pick_asset([{"name": "shadps4-win64-qt-0.21.0.zip"}]))


if __name__ == "__main__":
    unittest.main()
