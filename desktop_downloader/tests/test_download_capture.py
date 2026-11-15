"""Catching the browser's downloads: the extension's background script (run in Node with a fake
browser API) and the app's plain-file downloader (against a local HTTP server)."""

from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
import browser_extension  # noqa: E402
import file_download  # noqa: E402

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const scenario = JSON.parse(process.argv[3]);
const log = [];
let onCreated = null, onMessage = null;
const store = Object.assign({token: "t0k"}, scenario.storage || {});
function deep(path) {
  const fn = function () { return Promise.resolve(undefined); };
  return new Proxy(fn, {
    get(_t, key) {
      const name = path ? path + "." + String(key) : String(key);
      if (key === "then") return undefined;
      if (name === "storage.local.get") return async () => ({...store});
      if (name === "storage.local.set") return async (v) => { Object.assign(store, v); };
      if (name === "downloads.onCreated.addListener") return (fn) => { onCreated = fn; };
      if (name === "runtime.onMessage.addListener") return (fn) => { onMessage = fn; };
      if (name === "cookies.getAll") return async () => [];
      if (name === "runtime.getURL") return (file) => "ext://" + file;
      if (["downloads.pause", "downloads.cancel", "downloads.erase", "downloads.resume"].includes(name))
        return async (arg) => { log.push(name.split(".")[1]); };
      if (name === "notifications.create") return () => log.push("notify");
      return deep(name);
    },
  });
}
const sent = [];
async function fakeFetch(url, opts) {
  if (String(url).startsWith("ext://pairing.json")) {
    if (!scenario.bundled) return {ok: false, json: async () => ({})};
    return {ok: true, json: async () => ({token: scenario.bundled})};
  }
  if (scenario.appDown) throw new Error("connection refused");
  if (url.endsWith("/ping")) return {ok: true, json: async () => ({app: "Media Downloader"})};
  if (url.endsWith("/download")) { sent.push(JSON.parse(opts.body)); return {ok: true, json: async () => ({ok: true})}; }
  return {ok: true, json: async () => ({ok: true})};
}
const context = {chrome: deep(""), fetch: fakeFetch, console, URL, setTimeout, clearTimeout, setInterval,
                 clearInterval, Date, Promise, navigator: {userAgent: "node"}, TextDecoder, TextEncoder};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  await new Promise(r => setTimeout(r, 20));
  if (scenario.altClick) onMessage({type: "md-bypass"}, {}, () => {});
  await onCreated(scenario.item);
  await new Promise(r => setTimeout(r, 20));
  console.log(JSON.stringify({log, sent, token: store.token}));
})();
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is needed to run the extension script")
class ExtensionCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        folder = browser_extension.export_extension(Path(cls.tmp.name) / "ext")
        cls.background = folder / "background.js"
        cls.harness = Path(cls.tmp.name) / "harness.js"
        cls.harness.write_text(HARNESS, encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def run_scenario(self, **scenario) -> dict:
        item = {"id": 7, "url": "https://files.example.com/get/setup.exe", "finalUrl": "https://cdn.example.com/setup.exe",
                "referrer": "https://example.com/download", "filename": "", "mime": "application/x-msdownload",
                "totalBytes": 5_000_000, "state": "in_progress"}
        item.update(scenario.pop("item", {}))
        scenario["item"] = item
        out = subprocess.run(["node", str(self.harness), str(self.background), json.dumps(scenario)],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_manifest_asks_for_downloads(self) -> None:
        self.assertIn("downloads", browser_extension.MANIFEST["permissions"])

    def test_download_is_handed_to_the_app(self) -> None:
        result = self.run_scenario()
        self.assertEqual(result["log"][:3], ["pause", "cancel", "erase"])
        body = result["sent"][0]
        self.assertEqual((body["kind"], body["fileName"], body["url"]),
                         ("file", "setup.exe", "https://cdn.example.com/setup.exe"))
        self.assertEqual(body["referer"], "https://example.com/download")
        self.assertEqual(body["fileSize"], 5_000_000)

    def test_browser_keeps_the_download_when_the_app_is_closed(self) -> None:
        result = self.run_scenario(appDown=True)
        self.assertEqual(result["log"], ["pause", "resume"])
        self.assertEqual(result["sent"], [])

    def test_alt_click_lets_the_browser_download(self) -> None:
        result = self.run_scenario(altClick=True)
        self.assertEqual((result["log"], result["sent"]), ([], []))

    def test_pairs_itself_from_the_exported_token(self) -> None:
        # Never pasted a token: the one the app wrote into pairing.json is used.
        result = self.run_scenario(storage={"token": ""}, bundled="from-app")
        self.assertEqual(result["token"], "from-app")
        self.assertEqual(result["sent"][0]["kind"], "file")
        # A stale token from an older install is replaced too.
        self.assertEqual(self.run_scenario(storage={"token": "old"}, bundled="new")["token"], "new")

    def test_export_writes_the_pairing_file_and_refreshes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "ext"
            self.assertFalse(browser_extension.refresh_exported_extension(folder, "abc"))  # never installed
            browser_extension.export_extension(folder, "abc")
            self.assertEqual(json.loads((folder / "pairing.json").read_text(encoding="utf-8"))["token"], "abc")
            self.assertFalse(browser_extension.refresh_exported_extension(folder, "abc"))  # up to date
            (folder / "background.js").write_text("// old version", encoding="utf-8")
            self.assertTrue(browser_extension.refresh_exported_extension(folder, "abc"))
            self.assertIn("syncBundledPairing", (folder / "background.js").read_text(encoding="utf-8"))

    def test_not_caught(self) -> None:
        for scenario in (
            {"storage": {"token": ""}, "bundled": ""},
            {"item": {"url": "blob:https://x/1", "finalUrl": "blob:https://x/1"}},
            {"item": {"finalUrl": "https://x.com/photo.png", "mime": "image/png"}},
            {"storage": {"capture": False}},
            {"storage": {"minSizeKb": 10_000}},
            {"storage": {"blocked": "example.com"}},
        ):
            with self.subTest(scenario=scenario):
                self.assertEqual(self.run_scenario(**scenario)["sent"], [])

    def test_empty_type_list_catches_any_file(self) -> None:
        result = self.run_scenario(storage={"captureTypes": ""},
                                   item={"finalUrl": "https://x.com/data.parquet", "mime": "application/octet-stream"})
        self.assertEqual(result["sent"][0]["fileName"], "data.parquet")


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


class FileDownloadTests(unittest.TestCase):
    DATA = bytes(range(256)) * 4096  # 1 MiB

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.seen: list[dict] = []
        data, seen = self.DATA, self.seen

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                seen.append(dict(self.headers))
                if self.path.startswith("/page"):
                    body = b"<html>hi</html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                start = 0
                rng = self.headers.get("Range")
                if rng:
                    start = int(rng.split("=")[1].split("-")[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="server name.zip"')
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                self.wfile.write(data[start:])

        self.server = _Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_download_resume_and_names(self) -> None:
        cookie = self.dir / "c.txt"
        cookie.write_text("# Netscape HTTP Cookie File\n127.0.0.1\tFALSE\t/\tFALSE\t9999999999\tsid\tabc\n",
                          encoding="utf-8")
        # A partial file from a stopped download is continued with a Range request.
        (self.dir / "setup.zip.mdpart").write_bytes(self.DATA[:300_000])
        metrics: list[dict] = []
        path = file_download.direct_download(url=f"{self.base}/f", download_dir=self.dir, filename="setup.zip",
                                             cancel_event=threading.Event(), cookiefile=str(cookie),
                                             referer="https://example.com/", on_metrics=metrics.append)
        self.assertEqual(path.name, "setup.zip")
        self.assertEqual(path.read_bytes(), self.DATA)
        self.assertFalse((self.dir / "setup.zip.mdpart").exists())
        self.assertEqual(self.seen[-1]["Range"], "bytes=300000-")
        self.assertEqual(self.seen[-1]["Referer"], "https://example.com/")
        self.assertIn("sid=abc", self.seen[-1].get("Cookie", ""))
        self.assertEqual(metrics[-1]["total_bytes"], len(self.DATA))
        # Same name again: the finished file is kept and a numbered copy is made.
        again = file_download.direct_download(url=f"{self.base}/f", download_dir=self.dir, filename="setup.zip",
                                              cancel_event=threading.Event())
        self.assertEqual(again.name, "setup (2).zip")
        # No name from the browser: the server's Content-Disposition name is used.
        named = file_download.direct_download(url=f"{self.base}/dl.php?id=3", download_dir=self.dir,
                                              cancel_event=threading.Event())
        self.assertEqual(named.name, "server name.zip")

    def test_stop_keeps_the_partial_file(self) -> None:
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(file_download.Cancelled):
            file_download.direct_download(url=f"{self.base}/f", download_dir=self.dir, filename="a.bin",
                                          cancel_event=cancel)
        self.assertFalse((self.dir / "a.bin").exists())

    def test_probe_and_url_helpers(self) -> None:
        self.assertTrue(file_download.probe_is_file(f"{self.base}/dl.php?id=1"))
        self.assertFalse(file_download.probe_is_file(f"{self.base}/page"))
        self.assertTrue(file_download.looks_like_file("https://x.com/a/b/Setup%201.2.EXE?x=1"))
        self.assertFalse(file_download.looks_like_file("https://x.com/master.m3u8"))
        self.assertFalse(file_download.looks_like_file("https://www.youtube.com/watch?v=1"))
        self.assertEqual(file_download.name_from_url("https://x.com/a/Setup%201.2.exe?x=1"), "Setup 1.2.exe")
        self.assertEqual(file_download.clean_filename('bad:name?.zip'), "bad_name_.zip")
        self.assertTrue(file_download.is_plain_file_info({"direct": True, "ext": "unknown_video"}))
        self.assertFalse(file_download.is_plain_file_info({"direct": True, "ext": "mp4"}))
        self.assertFalse(file_download.is_plain_file_info({"ext": "unknown_video"}))
        self.assertEqual(file_download.probe_file(f"{self.base}/dl.php?id=1")["name"], "server name.zip")
        self.assertEqual(file_download.filename_from_disposition("attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf"),
                         "résumé.pdf")


class HandOverTests(unittest.TestCase):
    def fake(self) -> SimpleNamespace:
        calls: list[tuple] = []
        return SimpleNamespace(
            calls=calls, tray=None,
            _default_batch_options=lambda mode=None: {"mode": "Video", "video_quality": "720p",
                                                       "audio_format": "MP3", "audio_quality": "192"},
            _enqueue_batch_downloads=lambda text, options, extra=None: calls.append((text, options, extra)),
        )

    def test_browser_download_is_a_direct_file(self) -> None:
        fake = self.fake()
        app_module.DownloaderApp._handle_external_download(fake, {
            "url": "https://cdn.example.com/setup.exe", "kind": "file", "fileName": "My Setup.exe",
            "pageUrl": "https://example.com/download"})
        _url, _options, extra = fake.calls[0]
        self.assertTrue(extra["direct"])
        self.assertEqual(extra["file_name"], "My Setup.exe")

    def test_links_get_readable_names(self) -> None:
        self.assertEqual(app_module._link_label("https://www.youtube.com/watch?v=abc"), "youtube.com/watch?v=abc")
        self.assertEqual(app_module._link_label("https://cdn.x.com/a/b/file.mp4"), "cdn.x.com/file.mp4")
        self.assertEqual(app_module._link_label("https://x.com/"), "x.com")

    def test_right_clicked_links(self) -> None:
        fake = self.fake()
        app_module.DownloaderApp._handle_external_download(fake, {"url": "https://x.com/pack.zip", "kind": "link"})
        app_module.DownloaderApp._handle_external_download(fake, {"url": "https://www.youtube.com/watch?v=1",
                                                                  "kind": "link"})
        self.assertEqual(fake.calls[0][2]["file_name"], "pack.zip")
        self.assertNotIn("direct", fake.calls[1][2])


if __name__ == "__main__":
    unittest.main()
