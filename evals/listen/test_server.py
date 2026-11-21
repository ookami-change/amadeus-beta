"""Unit tests for the listening-feedback server."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from listen.server import make_handler, parse_range, resolve_static

SLUG = "abcdefghijklmnop1234"


class RangeTests(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertEqual(parse_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(parse_range("bytes=90-", 100), (90, 99))
        self.assertEqual(parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(parse_range("bytes=50-500", 100), (50, 99))
        self.assertIsNone(parse_range("bytes=100-", 100))
        self.assertIsNone(parse_range("bytes=0-1,5-6", 100))
        self.assertIsNone(parse_range(None, 100))


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        page = root / "pages" / SLUG
        (page / "audio").mkdir(parents=True)
        (page / "index.html").write_text("<p>hi</p>", encoding="utf-8")
        (page / "audio" / "01.wav").write_bytes(bytes(range(256)))
        (page / "secret.txt").write_text("no", encoding="utf-8")
        (root / "outside.html").write_text("no", encoding="utf-8")
        self.root = root
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(root))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, res.read(), dict(res.headers)
        except urllib.error.HTTPError as err:
            return err.code, err.read(), dict(err.headers)

    def put(self, path: str, body: bytes, method: str = "PUT") -> int:
        req = urllib.request.Request(
            self.base + path, data=body, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req) as res:
                return int(res.status)
        except urllib.error.HTTPError as err:
            return int(err.code)

    def test_static_and_noindex(self) -> None:
        status, body, headers = self.get(f"/l/{SLUG}/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<p>hi</p>")
        self.assertIn("noindex", headers["X-Robots-Tag"])
        self.assertIn("Content-Security-Policy", headers)

    def test_unknown_and_traversal_are_404(self) -> None:
        self.assertEqual(self.get("/")[0], 404)
        self.assertEqual(self.get("/l/short/")[0], 404)
        self.assertEqual(self.get(f"/l/{'z' * 20}/")[0], 404)
        self.assertEqual(self.get(f"/l/{SLUG}/secret.txt")[0], 404)
        self.assertEqual(self.get(f"/l/{SLUG}/../../outside.html")[0], 404)
        self.assertIsNone(resolve_static(self.root / "pages", SLUG, "../../outside.html"))

    def test_audio_range(self) -> None:
        status, body, headers = self.get(f"/l/{SLUG}/audio/01.wav", {"Range": "bytes=10-19"})
        self.assertEqual(status, 206)
        self.assertEqual(body, bytes(range(10, 20)))
        self.assertEqual(headers["Content-Range"], "bytes 10-19/256")
        self.assertEqual(self.get(f"/l/{SLUG}/audio/01.wav", {"Range": "bytes=300-"})[0], 416)

    def test_feedback_round_trip(self) -> None:
        self.assertEqual(json.loads(self.get(f"/l/{SLUG}/api/feedback")[1]), {})
        self.assertEqual(self.put(f"/l/{SLUG}/api/feedback", json.dumps({"a": 1}).encode()), 200)
        self.assertEqual(self.put(f"/l/{SLUG}/api/feedback", json.dumps({"a": 2}).encode()), 200)
        record = json.loads(self.get(f"/l/{SLUG}/api/feedback")[1])
        self.assertEqual(record["feedback"], {"a": 2})
        history = (self.root / "data" / SLUG / "history.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(history), 2)

    def test_feedback_rejects_bad_bodies(self) -> None:
        self.assertEqual(self.put(f"/l/{SLUG}/api/feedback", b"[1, 2]"), 400)
        self.assertEqual(self.put(f"/l/{SLUG}/api/feedback", b"not json"), 400)
        self.assertEqual(self.put(f"/l/{SLUG}/api/feedback", b"{" + b" " * 300_000 + b"}"), 413)
        self.assertEqual(self.put("/l/short/api/feedback", b"{}"), 404)

    def test_reports_append(self) -> None:
        url = f"/l/{SLUG}/api/report"
        self.assertEqual(json.loads(self.get(url)[1]), [])
        self.assertEqual(self.put(url, json.dumps({"device": "a"}).encode(), "POST"), 200)
        self.assertEqual(self.put(url, json.dumps({"device": "b"}).encode(), "POST"), 200)
        self.assertEqual(self.put(url, json.dumps({"device": "c"}).encode(), "PUT"), 405)
        self.assertEqual(self.put(url, b"[1]", "POST"), 400)
        reports = json.loads(self.get(url)[1])
        self.assertEqual([r["report"]["device"] for r in reports], ["a", "b"])
        self.assertEqual([r["n"] for r in reports], [1, 2])

    def test_reports_cap(self) -> None:
        from listen import server

        folder = self.root / "data" / SLUG
        folder.mkdir(parents=True)
        (folder / "reports.jsonl").write_text('{"n": 0, "report": {}}\n' * server.MAX_REPORTS, encoding="utf-8")
        self.assertEqual(self.put(f"/l/{SLUG}/api/report", b"{}", "POST"), 507)

    def test_model_and_blob_csp(self) -> None:
        (self.root / "pages" / SLUG / "avatar.vrm").write_bytes(b"glTF")
        status, _, headers = self.get(f"/l/{SLUG}/avatar.vrm")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "model/gltf-binary")
        csp = self.get(f"/l/{SLUG}/")[2]["Content-Security-Policy"]
        self.assertIn("connect-src 'self' blob:", csp)


if __name__ == "__main__":
    unittest.main()
