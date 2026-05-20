"""Copy the built Vue SPA into the Python package tree so the wheel ships it.

The Vue/Vite build produces ``viewer/dist/``. uv_build's default package
discovery picks up everything under ``src/rtl_buddy_view/``, so we stage
the bundle at ``src/rtl_buddy_view/_viewer_bundle/`` right before
``uv build`` runs. Source maps and incidental test artefacts are filtered
out — they're 4× the size of the actual JS and useless to end users.

This script is the canonical pre-build step. Both the release workflow
and local ``uv build`` invocations run it; idempotent re-runs are fine.

Usage:
    python scripts/prebuild_viewer.py [--skip-npm]

``--skip-npm`` assumes ``viewer/dist/`` is already up to date (CI uses
this after the viewer.yml workflow's build step).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWER_SRC = REPO_ROOT / "viewer"
DIST = VIEWER_SRC / "dist"
BUNDLE_DEST = REPO_ROOT / "src" / "rtl_buddy_view" / "_viewer_bundle"

# Anything matching these is dropped during stage. .js.map files are by far
# the biggest contributor (~3× the bundle); view.json is a dev-only leftover
# that we don't want to ship as part of the SPA distribution.
EXCLUDE_SUFFIXES = (".map",)
EXCLUDE_NAMES = frozenset({"view.json"})


def _npm_build() -> None:
    if not (VIEWER_SRC / "package.json").is_file():
        sys.exit(f"prebuild_viewer: no package.json under {VIEWER_SRC}")
    print(f"prebuild_viewer: npm ci && npm run build in {VIEWER_SRC}", flush=True)
    subprocess.run(["npm", "ci"], cwd=VIEWER_SRC, check=True)
    subprocess.run(["npm", "run", "build"], cwd=VIEWER_SRC, check=True)


def _stage_bundle() -> None:
    if not DIST.is_dir():
        sys.exit(
            f"prebuild_viewer: {DIST} not found — run without --skip-npm "
            "or `npm run build` in viewer/ first"
        )
    if BUNDLE_DEST.exists():
        shutil.rmtree(BUNDLE_DEST)
    BUNDLE_DEST.mkdir(parents=True)

    copied = 0
    skipped = 0
    for src in DIST.rglob("*"):
        if not src.is_file():
            continue
        if src.suffix in EXCLUDE_SUFFIXES or src.name in EXCLUDE_NAMES:
            skipped += 1
            continue
        rel = src.relative_to(DIST)
        dst = BUNDLE_DEST / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    # Sanity-check: a bundle without index.html is useless — fail loud so
    # we don't ship a wheel that the hub will then 404 on.
    if not (BUNDLE_DEST / "index.html").is_file():
        sys.exit("prebuild_viewer: staged bundle is missing index.html")

    print(
        f"prebuild_viewer: staged {copied} files into {BUNDLE_DEST} "
        f"(skipped {skipped} sourcemap/dev files)",
        flush=True,
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--skip-npm",
        action="store_true",
        help="Skip `npm ci && npm run build`; assume viewer/dist/ is current.",
    )
    args = p.parse_args()
    if not args.skip_npm:
        _npm_build()
    _stage_bundle()


if __name__ == "__main__":
    main()
