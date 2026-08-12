"""Lockstep guard for the vendored hub design-token sheet.

``viewer/src/theme.css`` is a byte-for-byte copy of
``src/rtl_buddy/hub/theme.css`` in the sibling `rtl_buddy
<https://github.com/rtl-buddy/rtl_buddy>`_ repo, which owns it. This
mirrors — in the opposite direction — the hub-protocol schema, which
*this* repo owns and rtl_buddy vendors with the same style of guard
(``tests/test_hub_protocol.py::test_vendored_schema_matches_source_when_view_repo_present``).

Two independent checks, because they fail for different reasons:

* :func:`test_vendored_theme_hash_is_pinned` catches an edit made
  *here*. It needs no sibling checkout, so it is the guard that
  actually runs in CI.
* :func:`test_vendored_theme_matches_sibling_source` catches upstream
  drift, and only fires when a sibling checkout carrying the sheet is
  present. It skips loudly otherwise: the sheet lands on rtl_buddy
  ``main`` with rtl-buddy/rtl_buddy#398, and until then a hard failure
  would be a red CI for a file nobody can fix from this repo.

Updating the sheet is a two-repo change: land it in rtl_buddy, copy the
exact bytes here, re-pin the hash below in the same commit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDORED = REPO_ROOT / "viewer" / "src" / "theme.css"

# sha256 of the vendored bytes. Bump together with the file.
EXPECTED_SHA256 = "952d37f19eae099caefd92937f33b6eb78712ac8051f09b490ced66a4cc16959"

# Where the source of truth lives inside a sibling rtl_buddy checkout.
SIBLING_RELPATH = Path("src") / "rtl_buddy" / "hub" / "theme.css"


def _sibling_sheet() -> Path | None:
    """Locate the upstream sheet in a sibling ``rtl_buddy`` checkout."""

    candidate = REPO_ROOT.parent / "rtl_buddy" / SIBLING_RELPATH
    return candidate if candidate.is_file() else None


def test_vendored_theme_present() -> None:
    assert VENDORED.is_file(), f"missing vendored token sheet at {VENDORED}"


def test_vendored_theme_hash_is_pinned() -> None:
    """The vendored bytes are exactly what we recorded when we copied them."""

    digest = hashlib.sha256(VENDORED.read_bytes()).hexdigest()
    assert digest == EXPECTED_SHA256, (
        "viewer/src/theme.css has been edited in this repo. It is vendored "
        "byte-for-byte from rtl_buddy (src/rtl_buddy/hub/theme.css), which "
        "owns it — make the change there, re-copy, and update "
        "EXPECTED_SHA256 in the same commit."
    )


def test_vendored_theme_defines_the_documented_tokens() -> None:
    """A copy that lost the tokens the SPA reads is worse than no copy."""

    text = VENDORED.read_text(encoding="utf-8")
    for name in (
        "--bg",
        "--panel",
        "--panel-2",
        "--line",
        "--line-strong",
        "--fg",
        "--fg-muted",
        "--fg-faint",
        "--accent",
        "--accent-contrast",
        "--ok",
        "--warn",
        "--err",
        "--info",
        "--cov-l",
        "--cov-none",
        "--font-mono",
        "--font-sans",
        "--radius-2",
        "--shadow-1",
    ):
        assert f"{name}:" in text, f"vendored theme.css is missing {name}"


def test_vendored_theme_carries_both_theme_pins() -> None:
    """Light and dark ``[data-theme]`` overrides must both be present.

    A pin has to win in *both* directions — a light pin beats the dark
    media query, a dark pin beats the ``:root`` defaults — so half a
    copy is a bug the SPA would only hit on one of the two paths.
    """

    text = VENDORED.read_text(encoding="utf-8")
    assert ':root[data-theme="light"]' in text
    assert ':root[data-theme="dark"]' in text
    assert "prefers-color-scheme: dark" in text


def test_vendored_theme_matches_sibling_source() -> None:
    """Byte-compare against a sibling rtl_buddy checkout when present."""

    source = _sibling_sheet()
    if source is None:
        pytest.skip(
            "no sibling rtl_buddy checkout carrying "
            f"{SIBLING_RELPATH} — upstream drift unchecked. The sheet lands "
            "on rtl_buddy main with rtl-buddy/rtl_buddy#398; until then this "
            "check can only run against a local side-by-side checkout, and "
            "test_vendored_theme_hash_is_pinned is the guard that runs in CI."
        )
    assert VENDORED.read_bytes() == source.read_bytes(), (
        "vendored token sheet has drifted from the rtl_buddy source at "
        f"{source}. Re-copy and commit; rtl_buddy owns the sheet."
    )
