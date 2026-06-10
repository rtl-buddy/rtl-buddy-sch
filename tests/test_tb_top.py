"""End-to-end tests for the `--tb-top` CLI surface (issue #99 / 6a).

Covers the three valid CLI modes:

- ``--top X`` alone  → DUT view (today's behaviour, byte-identical).
  ``view.json::dut_top == X``, ``tb_top`` is null.
- ``--tb-top Y`` alone → TB view. ``view.json::tb_top == Y``,
  ``dut_top`` is null. Overlays anchor under their own ``design_top``
  at load time; the renderer doesn't need a DUT name to elaborate
  the TB tree.
- ``--top X --tb-top Y`` → TB view with DUT recorded.
  ``view.json::dut_top == X`` and ``tb_top == Y``; rendered root is
  the TB.

Plus the negative case: neither flag set → exit 2 with a clear
error. The renderer does NOT error when the DUT module isn't found
in the TB elaboration — that's surfaced at overlay load time, where
the overlay's ``design_top`` fails to resolve into any instance
path (more actionable than a render-time error).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._verible_install import find_binary

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tb_over_dut"


def _have_verible() -> bool:
    return find_binary("verible-verilog-syntax") is not None


pytestmark = pytest.mark.skipif(
    not _have_verible(),
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rtl_buddy_view", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _json_for(*args: str) -> dict:
    """Render the fixture as JSON with the given CLI flags."""
    result = _run(
        *args,
        "--filelist",
        str(FIXTURE_DIR / "files.f"),
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# --- mode 1: --top alone (DUT view, byte-identical to v1.0 behaviour) ------


def test_dut_only_mode_records_dut_top_only() -> None:
    payload = _json_for("--top", "dut")
    assert payload["top"] == "dut"
    assert payload["dut_top"] == "dut"
    assert payload["tb_top"] is None
    ids = {n["id"] for n in payload["nodes"]}
    # Rendered tree is rooted at the DUT — TB scopes are not present.
    assert "dut" in ids
    assert "dut.u_a" in ids
    assert "dut.u_b" in ids
    assert not any(i.startswith("tb_top") for i in ids)


# --- mode 2: --tb-top alone (TB view, no DUT marking) ----------------------


def test_tb_top_only_mode_records_tb_top_only() -> None:
    payload = _json_for("--tb-top", "tb_top")
    assert payload["top"] == "tb_top"
    assert payload["dut_top"] is None
    assert payload["tb_top"] == "tb_top"
    ids = {n["id"] for n in payload["nodes"]}
    # TB elaboration: the DUT instance + its leaves are reachable
    # under tb_top.u_dut, plus the TB-only scopes.
    assert "tb_top" in ids
    assert "tb_top.u_clkgen" in ids
    assert "tb_top.u_driver" in ids
    assert "tb_top.u_dut" in ids
    assert "tb_top.u_dut.u_a" in ids


# --- mode 3: --top + --tb-top (TB view + DUT recorded) ---------------------


def test_both_flags_render_tb_root_and_record_dut_top() -> None:
    payload = _json_for("--top", "dut", "--tb-top", "tb_top")
    assert payload["top"] == "tb_top"
    assert payload["dut_top"] == "dut"
    assert payload["tb_top"] == "tb_top"
    # SPA derives the DUT anchor by filtering nodes[] for
    # module == dut_top; the single DUT instance must surface that way.
    dut_anchors = [n["id"] for n in payload["nodes"] if n["module"] == "dut"]
    assert dut_anchors == ["tb_top.u_dut"]


# --- auto-detect: a --tb-top hint that isn't a real module ------------------


def test_tb_top_hint_not_a_module_auto_detects() -> None:
    """When ``--tb-top`` names something that isn't a module in the
    design (the common case: the testbench *config* name differs from
    the actual top module), the renderer recovers the real TB top from
    the elaborated design — the root containing the DUT — instead of
    erroring. Models the hub passing a best-effort testbench name."""
    result = _run(
        "--top",
        "dut",
        "--tb-top",
        "tb_apb",  # not a module here; real TB top is ``tb_top``
        "--filelist",
        str(FIXTURE_DIR / "files.f"),
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    assert "auto-detected 'tb_top'" in result.stderr
    payload = json.loads(result.stdout)
    # Rendered + recorded as the real TB top, DUT still recorded.
    assert payload["top"] == "tb_top"
    assert payload["tb_top"] == "tb_top"
    assert payload["dut_top"] == "dut"
    dut_anchors = [n["id"] for n in payload["nodes"] if n["module"] == "dut"]
    assert dut_anchors == ["tb_top.u_dut"]


# --- negative: neither flag set --------------------------------------------


def test_neither_top_nor_tb_top_errors() -> None:
    result = _run("--filelist", str(FIXTURE_DIR / "files.f"))
    assert result.returncode == 2
    assert "--top or --tb-top is required" in result.stderr


# --- schema bump ------------------------------------------------------------


def test_schema_version_bumped_to_1_1() -> None:
    payload = _json_for("--top", "dut")
    assert payload["schema_version"] == "1.1"
