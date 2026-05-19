"""Build a self-contained HTML bundle from the viewer's ``dist/`` output.

Run from the ``viewer/`` directory after ``npm run build``::

    python embed.py --inject-data PATH/TO/view.json --output viewer.html

Reads ``dist/index.html``, inlines every same-origin asset
referenced by ``<script src=…>`` / ``<link rel=stylesheet>`` /
``<img src=…>``, optionally embeds a ``view.json`` payload into
the ``window.__RTL_BUDDY_VIEW_DATA__`` injection point, and writes
the result as a single ``.html`` file that opens fully offline.

Mirrors the same pattern as ``coverview/embed.py``. Deliberately
kept dependency-free (stdlib only) so the embed step doesn't need
``uv``, ``pip``, or a Node toolchain — just ``python3``.

Bundle-size budget (issue #18 acceptance criterion): ≤2 MB
gzipped for the standalone HTML. ``viz.js`` WASM dominates; the
script logs the final size so CI can fail on regression.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import mimetypes
import re
import sys
from pathlib import Path

DIST = Path(__file__).parent / "dist"

# Asset references we inline. Anchors / external URLs (http://, https://,
# protocol-relative //) are left alone so external CDNs still work
# when present.
_TAG_RE = re.compile(
    r"""
    <(?P<tag>script|link|img)\b
    (?P<attrs>[^>]*?)
    \s*(?P<close>/?)>
    """,
    re.VERBOSE | re.IGNORECASE,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist",
        default=str(DIST),
        help="Path to the Vite build output (default: ./dist).",
    )
    parser.add_argument(
        "--inject-data",
        help="Path to a view.json to inline as window.__RTL_BUDDY_VIEW_DATA__.",
    )
    parser.add_argument(
        "--output",
        default="viewer.html",
        help="Output HTML path (default: viewer.html).",
    )
    args = parser.parse_args()

    dist = Path(args.dist).resolve()
    index = dist / "index.html"
    if not index.exists():
        print(
            f"error: {index} not found — run `npm run build` first.",
            file=sys.stderr,
        )
        return 1

    html = index.read_text(encoding="utf-8")
    html = _inline_assets(html, dist)

    if args.inject_data:
        payload_path = Path(args.inject_data).resolve()
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        injection = (
            "<script>window.__RTL_BUDDY_VIEW_DATA__ = "
            + json.dumps(payload, separators=(",", ":"))
            + ";</script>"
        )
        # Replace the placeholder declaration that the dev template
        # carries. If the build process stripped the comment, fall
        # back to inserting before </head>.
        if "window.__RTL_BUDDY_VIEW_DATA__ = null;" in html:
            html = html.replace(
                "<script>window.__RTL_BUDDY_VIEW_DATA__ = null;</script>",
                injection,
                1,
            )
        else:
            html = html.replace("</head>", injection + "</head>", 1)

    out_path = Path(args.output).resolve()
    out_path.write_text(html, encoding="utf-8")

    raw_size = out_path.stat().st_size
    gz_size = len(gzip.compress(html.encode("utf-8")))
    print(f"wrote {out_path} ({raw_size:,} bytes; ~{gz_size:,} bytes gzipped)")
    return 0


def _inline_assets(html: str, dist: Path) -> str:
    """Inline every same-origin asset reference in ``html``.

    Conservative URL classification: anything starting with
    ``http://`` / ``https://`` / ``//`` / ``data:`` is left alone.
    Everything else is treated as a path under ``dist/``.
    """

    def replace(match: re.Match[str]) -> str:
        tag = match.group("tag").lower()
        attrs = match.group("attrs")
        url_attr = "src" if tag in ("script", "img") else "href"
        m = re.search(rf'{url_attr}\s*=\s*["\']([^"\']+)["\']', attrs)
        if not m:
            return match.group(0)
        url = m.group(1)
        if _is_external(url):
            return match.group(0)
        asset = (dist / url.lstrip("/")).resolve()
        if not asset.is_file():
            return match.group(0)
        data = asset.read_bytes()
        if tag == "script":
            return f"<script>{data.decode('utf-8')}</script>"
        if tag == "link" and "stylesheet" in attrs:
            return f"<style>{data.decode('utf-8')}</style>"
        if tag == "img":
            mime, _ = mimetypes.guess_type(asset.name)
            mime = mime or "application/octet-stream"
            b64 = base64.b64encode(data).decode("ascii")
            new_attrs = re.sub(
                rf'{url_attr}\s*=\s*["\']{re.escape(url)}["\']',
                f'{url_attr}="data:{mime};base64,{b64}"',
                attrs,
            )
            return f"<img{new_attrs}/>"
        return match.group(0)

    return _TAG_RE.sub(replace, html)


def _is_external(url: str) -> bool:
    return (
        url.startswith("http://")
        or url.startswith("https://")
        or url.startswith("//")
        or url.startswith("data:")
    )


if __name__ == "__main__":
    sys.exit(main())
