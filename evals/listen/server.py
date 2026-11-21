"""Listening-feedback server for the owner's blind tests.

Standard library only. It serves each listening page from ``pages/<slug>/``
and keeps the owner's answers in ``data/<slug>/``. Run it behind a reverse
proxy that terminates HTTPS; it only binds to localhost by default.

    python -m listen.server --root /srv/amadeus-listen --port 8080

Routes:

* ``GET /l/<slug>/...``            static files of one page
* ``GET /l/<slug>/api/feedback``   the latest saved answers, or ``{}``
* ``PUT /l/<slug>/api/feedback``   replace the answers (JSON object)
* ``GET /robots.txt``              disallow everything
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

SLUG = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
MAX_BODY = 256 * 1024
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}
CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; media-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


def resolve_static(pages: Path, slug: str, rel: str) -> Path | None:
    """Return the file for ``rel`` inside one page, or None if it must not be served."""
    if not SLUG.match(slug):
        return None
    base = (pages / slug).resolve()
    target = (base / (rel or "index.html")).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    if target.suffix.lower() not in TYPES:
        return None
    return target


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single ``bytes=a-b`` range. Return inclusive bounds, or None to send it all."""
    if not header or not header.startswith("bytes=") or "," in header or size == 0:
        return None
    start_s, _, end_s = header[len("bytes=") :].strip().partition("-")
    try:
        if start_s == "":
            length = int(end_s)
            if length <= 0:
                return None
            return max(0, size - length), size - 1
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start >= size or end < start:
        return None
    return start, min(end, size - 1)


def load_feedback(data: Path, slug: str) -> dict[str, Any]:
    path = data / slug / "feedback.json"
    if not path.is_file():
        return {}
    record = json.loads(path.read_text(encoding="utf-8"))
    return record if isinstance(record, dict) else {}


def save_feedback(data: Path, slug: str, feedback: dict[str, Any]) -> dict[str, Any]:
    """Replace the latest answers atomically and append them to the history."""
    folder = data / slug
    folder.mkdir(parents=True, exist_ok=True)
    record = {"savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "feedback": feedback}
    text = json.dumps(record, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, folder / "feedback.json")
    with (folder / "history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")
    return record


def make_handler(root: Path) -> type[BaseHTTPRequestHandler]:
    pages = root / "pages"
    data = root / "data"

    class Handler(BaseHTTPRequestHandler):
        server_version = "amadeus-listen"
        sys_version = ""

        def _headers(self, status: int, ctype: str, length: int, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self._headers(status, ctype, len(body), extra)
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj: object) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send(status, body, TYPES[".json"], {"Cache-Control": "no-store"})

        def _not_found(self) -> None:
            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")

        def _route(self) -> tuple[str, str] | None:
            path = unquote(urlsplit(self.path).path)
            parts = path.split("/", 3)
            if len(parts) < 3 or parts[0] != "" or parts[1] != "l" or not SLUG.match(parts[2]):
                return None
            if not (pages / parts[2]).is_dir():
                return None
            return parts[2], parts[3] if len(parts) == 4 else ""

        def do_GET(self) -> None:
            if urlsplit(self.path).path == "/robots.txt":
                self._send(HTTPStatus.OK, b"User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8")
                return
            route = self._route()
            if route is None:
                self._not_found()
                return
            slug, rel = route
            if rel == "" and not urlsplit(self.path).path.endswith("/"):
                self._send(HTTPStatus.MOVED_PERMANENTLY, b"", "text/plain", {"Location": f"/l/{slug}/"})
                return
            if rel == "api/feedback":
                self._json(HTTPStatus.OK, load_feedback(data, slug))
                return
            target = resolve_static(pages, slug, rel)
            if target is None:
                self._not_found()
                return
            self._file(target)

        do_HEAD = do_GET

        def _file(self, target: Path) -> None:
            ctype = TYPES[target.suffix.lower()]
            size = target.stat().st_size
            extra = {"Accept-Ranges": "bytes"}
            if target.suffix.lower() == ".html":
                extra |= {"Cache-Control": "no-store", "Content-Security-Policy": CSP}
            else:
                extra["Cache-Control"] = "private, max-age=3600"
            requested = self.headers.get("Range")
            span = parse_range(requested, size)
            if span is None and requested and requested.startswith("bytes=") and "," not in requested and size > 0:
                self._send(
                    HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", "text/plain", {"Content-Range": f"bytes */{size}"}
                )
                return
            with target.open("rb") as handle:
                if span is None:
                    self._headers(HTTPStatus.OK, ctype, size, extra)
                    if self.command != "HEAD":
                        self.wfile.write(handle.read())
                    return
                start, end = span
                handle.seek(start)
                chunk = handle.read(end - start + 1)
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._headers(HTTPStatus.PARTIAL_CONTENT, ctype, len(chunk), extra)
            if self.command != "HEAD":
                self.wfile.write(chunk)

        def do_PUT(self) -> None:
            route = self._route()
            if route is None or route[1] != "api/feedback":
                self._not_found()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length <= 0 or length > MAX_BODY:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body must be 1 byte to 256 KiB"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "body must be JSON"})
                return
            if not isinstance(payload, dict):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "body must be a JSON object"})
                return
            record = save_feedback(data, route[0], payload)
            self._json(HTTPStatus.OK, {"savedAt": record["savedAt"]})

        do_POST = do_PUT

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            # Keep request lines only; no client addresses in the journal.
            print(format % args, flush=True)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, required=True, help="folder holding pages/ and data/")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    (args.root / "pages").mkdir(parents=True, exist_ok=True)
    (args.root / "data").mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.root))
    print(f"listening on {args.host}:{args.port}, root {args.root}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
