"""Build one listening page from a spec file.

The spec is the page manifest (see ``README.md``), except that every ``src``
is a path to an audio file, relative to the spec file. The build copies the
page template, copies each clip to ``audio/NN.<ext>`` so file names give
nothing away, and writes ``manifest.json`` with the new paths.

    python -m listen.make_page spec.json out/
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
from pathlib import Path
from typing import Any

TEMPLATE = Path(__file__).with_name("page") / "index.html"


def _clips(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every object in the spec that carries a ``src``, in page order."""
    found: list[dict[str, Any]] = []
    if spec.get("reference"):
        found.append(spec["reference"])
    for line in spec.get("lines", []):
        found.extend(line.get("refs", []))
        found.extend(line.get("takes", []))
    return found


def build(spec_path: Path, out: Path) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(spec_path.read_text(encoding="utf-8"))
    if not spec.get("lines"):
        raise SystemExit("spec has no lines")
    out.mkdir(parents=True, exist_ok=True)
    (out / "audio").mkdir(exist_ok=True)
    for n, clip in enumerate(_clips(spec), start=1):
        source = (spec_path.parent / clip["src"]).resolve()
        if not source.is_file():
            raise SystemExit(f"missing audio: {source}")
        name = f"audio/{n:02d}{source.suffix.lower()}"
        shutil.copyfile(source, out / name)
        clip["src"] = name
    text = json.dumps(spec, ensure_ascii=False, indent=1)
    # Embed the manifest so the page needs no extra request to render; escape "</" for the script tag.
    page = TEMPLATE.read_text(encoding="utf-8")
    slot = '<script id="manifest" type="application/json">null</script>'
    if slot not in page:
        raise SystemExit("template has no manifest slot")
    embedded = text.replace("</", "<\\/")
    page = page.replace(slot, f'<script id="manifest" type="application/json">{embedded}</script>')
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "manifest.json").write_text(text, encoding="utf-8")
    return spec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    spec = build(args.spec, args.out)
    print(f"built {args.out} with {len(_clips(spec))} clips")
    print(f"suggested slug: {secrets.token_urlsafe(18)}")


if __name__ == "__main__":
    main()
