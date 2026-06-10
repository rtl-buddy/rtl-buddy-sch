"""Phase 6 coverage overlay tests (rtl-buddy-view#20).

Three layers covered:

* :mod:`rtl_buddy_view.coverage_annotations` — LCOV parser (single
  combined ``.info``, Coverview-typed directory, zip archive),
  cross-dataset merge semantics, suffix-based file matching, and the
  per-module range rollup. The aggregation is the trust boundary —
  the fixture values are hand-checked sums.
* :mod:`rtl_buddy_view.overlays.coverage` — registry registration.
* JSON renderer emission — ``node.overlays.coverage`` joined by the
  *defining module's* range (not the instance anchor), the
  ``overlay_meta.coverage`` block, and graceful degradation for
  blackboxes / files without LCOV data. End-to-end via the CLI
  against the ``counter_with_subs`` fixture design paired with the
  hand-written ``fixtures/coverage/coverview_regression`` dataset.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rtl_buddy_view.coverage_annotations import (
    DEFAULT_URL_BASE,
    CoverageAnnotationsError,
    CoverageMap,
    load_coverage_map,
)
from rtl_buddy_view.extractor import Module, SourceLocation
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.overlays import default_registry
from rtl_buddy_view.overlays.coverage import CoverageOverlay
from rtl_buddy_view.render import json_render

FIXTURE = Path(__file__).parent / "fixtures" / "coverage"
DESIGN = Path(__file__).parent / "fixtures" / "counter_with_subs"

COUNTER_SV = "tests/fixtures/counter_with_subs/counter.sv"
COUNTER_FF_SV = "tests/fixtures/counter_with_subs/counter_ff.sv"


# ---------------------------------------------------------------------------
# loader: input shapes
# ---------------------------------------------------------------------------


def test_load_combined_info_feeds_lines_and_branches():
    cmap = load_coverage_map(FIXTURE / "coverage_merged.info")
    assert not cmap.is_empty
    block = cmap.rollup(COUNTER_SV, 5, 12)
    assert block is not None
    assert block["lines"] == {"covered": 1, "total": 2, "pct": 50.0}
    assert block["branches"] == {"covered": 1, "total": 2, "pct": 50.0}
    # A combined LCOV has no toggle channel.
    assert "toggles" not in block


def test_load_coverview_directory_feeds_all_three_channels():
    cmap = load_coverage_map(FIXTURE / "coverview_regression")
    block = cmap.rollup(COUNTER_SV, 5, 12)
    assert block is not None
    # Hand-checked sums: DA:1 sits outside the module's 5..12 range
    # so the lines channel sees only lines 10 (hit) + 11 (miss);
    # the expression file's DA:10 must NOT inflate any channel.
    assert block["lines"] == {"covered": 1, "total": 2, "pct": 50.0}
    assert block["branches"] == {"covered": 1, "total": 2, "pct": 50.0}
    assert block["toggles"] == {"covered": 2, "total": 3, "pct": 66.7}

    ff = cmap.rollup(COUNTER_FF_SV, 2, 6)
    assert ff is not None
    assert ff["lines"] == {"covered": 2, "total": 2, "pct": 100.0}
    assert ff["toggles"] == {"covered": 1, "total": 2, "pct": 50.0}
    assert "branches" not in ff  # no BRDA records for this file


def test_load_zip_archive_matches_directory_load(tmp_path):
    zip_path = tmp_path / "coverview_regression.zip"
    src_dir = FIXTURE / "coverview_regression"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for member in sorted(src_dir.glob("*.info")):
            zf.write(member, arcname=f"coverview_regression/{member.name}")
    from_zip = load_coverage_map(zip_path)
    from_dir = load_coverage_map(src_dir)
    assert from_zip.rollup(COUNTER_SV, 5, 12) == from_dir.rollup(COUNTER_SV, 5, 12)
    assert from_zip.rollup(COUNTER_FF_SV, 2, 6) == from_dir.rollup(COUNTER_FF_SV, 2, 6)


def test_load_directory_without_info_files_errors(tmp_path):
    with pytest.raises(CoverageAnnotationsError, match="no .info files"):
        load_coverage_map(tmp_path)


def test_load_zip_without_info_members_errors(tmp_path):
    zip_path = tmp_path / "empty.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    with pytest.raises(CoverageAnnotationsError, match="no .info members"):
        load_coverage_map(zip_path)


def test_load_corrupt_zip_errors(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("this is not a zip")
    with pytest.raises(CoverageAnnotationsError, match="not a zip archive"):
        load_coverage_map(bad)


# ---------------------------------------------------------------------------
# loader: record-level semantics
# ---------------------------------------------------------------------------


def _single_info(tmp_path: Path, text: str, name: str = "some.info") -> CoverageMap:
    p = tmp_path / name
    p.write_text(text)
    return load_coverage_map(p)


def test_brda_dash_means_never_taken(tmp_path):
    cmap = _single_info(
        tmp_path,
        "SF:a.sv\nBRDA:3,0,0,-\nBRDA:3,0,1,-\nend_of_record\n",
    )
    block = cmap.rollup("a.sv", None, None)
    assert block is not None
    assert block["branches"] == {"covered": 0, "total": 2, "pct": 0.0}


def test_units_merge_across_datasets_any_hit_wins(tmp_path):
    # Same line missed in dataset 1, hit in dataset 2 → covered.
    (tmp_path / "coverage_line_t1.info").write_text("SF:a.sv\nDA:7,0\nend_of_record\n")
    (tmp_path / "coverage_line_t2.info").write_text("SF:a.sv\nDA:7,9\nend_of_record\n")
    cmap = load_coverage_map(tmp_path)
    block = cmap.rollup("a.sv", None, None)
    assert block is not None
    assert block["lines"] == {"covered": 1, "total": 1, "pct": 100.0}


def test_echo_da_in_branch_typed_file_not_counted_as_lines(tmp_path):
    # info-process keeps DA echo lines alongside BRDA in branch
    # extracts; they must not create line units.
    (tmp_path / "coverage_branch_t.info").write_text(
        "SF:a.sv\nDA:9,1\nBRDA:9,0,0,1\nend_of_record\n"
    )
    cmap = load_coverage_map(tmp_path)
    block = cmap.rollup("a.sv", None, None)
    assert block is not None
    assert "lines" not in block
    assert block["branches"] == {"covered": 1, "total": 1, "pct": 100.0}


def test_malformed_da_errors_with_context(tmp_path):
    with pytest.raises(CoverageAnnotationsError, match=r"some\.info:2.*DA"):
        _single_info(tmp_path, "SF:a.sv\nDA:nonsense\nend_of_record\n")


def test_malformed_brda_errors_with_context(tmp_path):
    with pytest.raises(CoverageAnnotationsError, match=r"some\.info:2.*BRDA"):
        _single_info(tmp_path, "SF:a.sv\nBRDA:1,2\nend_of_record\n")


def test_da_outside_sf_scope_errors(tmp_path):
    with pytest.raises(CoverageAnnotationsError, match="outside SF scope"):
        _single_info(tmp_path, "DA:1,1\n")


def test_brda_outside_sf_scope_errors(tmp_path):
    with pytest.raises(CoverageAnnotationsError, match="outside SF scope"):
        _single_info(tmp_path, "BRDA:1,0,0,1\n")


def test_empty_sf_record_errors(tmp_path):
    with pytest.raises(CoverageAnnotationsError, match="empty SF"):
        _single_info(tmp_path, "SF:\nDA:1,1\nend_of_record\n")


def test_unknown_record_types_are_skipped(tmp_path):
    cmap = _single_info(
        tmp_path,
        "TN:test\nSF:a.sv\nFN:1,foo\nFNDA:3,foo\nDA:1,1\nLH:1\nLF:1\nend_of_record\n",
    )
    block = cmap.rollup("a.sv", None, None)
    assert block is not None
    assert block["lines"] == {"covered": 1, "total": 1, "pct": 100.0}


# ---------------------------------------------------------------------------
# file matching + rollup
# ---------------------------------------------------------------------------


def test_relative_sf_suffix_matches_absolute_node_path(tmp_path):
    cmap = _single_info(tmp_path, "SF:design/blk/a.sv\nDA:1,1\nend_of_record\n")
    assert cmap.rollup("/repo/checkout/design/blk/a.sv", None, None) is not None
    # Same basename, different directory → no match.
    assert cmap.rollup("/repo/checkout/other/blk2/a.sv", None, None) is None


def test_absolute_sf_matches_relative_node_path(tmp_path):
    cmap = _single_info(tmp_path, "SF:/repo/design/a.sv\nDA:1,1\nend_of_record\n")
    assert cmap.rollup("design/a.sv", None, None) is not None


def test_ambiguous_suffix_match_resolves_to_no_data(tmp_path):
    # Two SF entries tie on the matchable suffix ("a.sv") for a node
    # path that disambiguates neither → no data, never a guess.
    cmap = _single_info(
        tmp_path,
        "SF:design/x/a.sv\nDA:1,1\nend_of_record\n"
        "SF:design/y/a.sv\nDA:2,1\nend_of_record\n",
    )
    assert cmap.rollup("a.sv", None, None) is None
    # A node path carrying the distinguishing component still matches.
    assert cmap.rollup("/abs/design/x/a.sv", None, None) is not None


def test_rollup_outside_range_returns_none(tmp_path):
    cmap = _single_info(tmp_path, "SF:a.sv\nDA:100,1\nend_of_record\n")
    assert cmap.rollup("a.sv", 1, 50) is None


def test_rollup_open_ended_bounds(tmp_path):
    cmap = _single_info(tmp_path, "SF:a.sv\nDA:5,1\nDA:50,0\nend_of_record\n")
    assert cmap.rollup("a.sv", None, 10)["lines"]["total"] == 1
    assert cmap.rollup("a.sv", 10, None)["lines"]["total"] == 1
    assert cmap.rollup("a.sv", None, None)["lines"]["total"] == 2


def test_coverview_link_uses_url_base_and_start_line(tmp_path):
    cmap = _single_info(tmp_path, "SF:design/a.sv\nDA:6,1\nend_of_record\n")
    assert cmap.url_base == DEFAULT_URL_BASE
    block = cmap.rollup("/x/design/a.sv", 5, 12)
    assert block["coverview_link"] == "http://localhost:5173/#/design%2Fa.sv?L=5"
    # Post-load reconfiguration (what --coverage-url-base does), with
    # or without trailing slash; None start_line drops the ?line=.
    cmap.url_base = "http://cov.example:9999"
    block = cmap.rollup("/x/design/a.sv", None, None)
    assert block["coverview_link"] == "http://cov.example:9999/#/design%2Fa.sv"


# ---------------------------------------------------------------------------
# registry + JSON renderer contribution
# ---------------------------------------------------------------------------


def test_coverage_overlay_registered_by_default():
    warn = io.StringIO()
    registry = default_registry(warn_stream=warn)
    overlay = registry.get("coverage")
    assert isinstance(overlay, CoverageOverlay)
    assert overlay.schema_version == "1.0"
    assert warn.getvalue() == ""
    # join/contribute are reserved no-op hooks on the Phase-4
    # protocol — the renderer queries the map directly.
    assert overlay.join(_synthetic_tree(), CoverageMap(source="x")) is None
    assert overlay.contribute(None) is None


def _synthetic_tree() -> HierNode:
    """``top`` (a.sv lines 5..12) with one blackbox child."""
    top_module = Module(
        name="top",
        ports=(),
        parameters=(),
        instances=(),
        location=SourceLocation(file="/repo/design/a.sv", start_line=5, end_line=12),
    )
    blackbox = HierNode(
        instance_path="top.u_bb",
        module_name="bb",
        instance=None,
        module=None,
        is_blackbox=True,
    )
    return HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=top_module,
        is_blackbox=False,
        children=(blackbox,),
    )


def test_json_render_emits_coverage_block_and_meta(tmp_path):
    cmap = _single_info(
        tmp_path,
        "SF:design/a.sv\nDA:10,4\nDA:11,0\nBRDA:10,0,0,1\nend_of_record\n",
    )
    out = io.StringIO()
    json_render.render(
        _synthetic_tree(),
        out,
        coverage_map=cmap,
        coverage_metric="branches",
        embed_layout=False,
    )
    payload = json.loads(out.getvalue())
    assert payload["overlays_present"] == ["coverage"]
    assert payload["overlay_meta"]["coverage"]["metric"] == "branches"
    assert payload["overlay_meta"]["coverage"]["url_base"] == DEFAULT_URL_BASE

    nodes = {n["id"]: n for n in payload["nodes"]}
    block = nodes["top"]["overlays"]["coverage"]
    assert block["lines"] == {"covered": 1, "total": 2, "pct": 50.0}
    assert block["branches"] == {"covered": 1, "total": 1, "pct": 100.0}
    # Blackbox: no module location → no coverage key, never an error.
    assert "coverage" not in nodes["top.u_bb"]["overlays"]


def test_json_render_empty_map_degrades_gracefully(tmp_path):
    # A coverage map whose data matched nothing in the design: nodes
    # carry no coverage key and overlays_present stays empty when the
    # map itself is empty.
    out = io.StringIO()
    json_render.render(
        _synthetic_tree(),
        out,
        coverage_map=CoverageMap(source="x"),
        embed_layout=False,
    )
    payload = json.loads(out.getvalue())
    assert payload["overlays_present"] == []
    assert "overlay_meta" not in payload
    assert "coverage" not in {n["id"]: n for n in payload["nodes"]}["top"]["overlays"]


# ---------------------------------------------------------------------------
# end-to-end CLI (Verible-gated, mirrors the wave overlay test)
# ---------------------------------------------------------------------------


def _require_verible() -> None:
    try:
        from rtl_buddy_view._verible_install import find_binary
    except ImportError:
        pytest.skip("verible not available")
    if find_binary("verible-verilog-syntax") is None:
        pytest.skip("verible binary not on PATH / vendor/")


def test_cli_renders_coverage_overlay_into_view_json(tmp_path):
    """Acceptance path from #20: ``--overlay coverage=DIR`` produces a
    view.json whose per-node rollups match the hand-checked sums for
    the two-module fixture."""
    _require_verible()
    from rtl_buddy_view.cli import app

    runner = CliRunner()
    out_path = tmp_path / "view.json"
    result = runner.invoke(
        app,
        [
            "--top",
            "counter",
            "--filelist",
            str(DESIGN / "files.f"),
            "--overlay",
            f"coverage={FIXTURE / 'coverview_regression'}",
            "--coverage-metric",
            "branches",
            "--coverage-url-base",
            "http://cov.example:9999/",
            "--format",
            "json",
            "--output",
            str(out_path),
        ],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(out_path.read_text())
    assert "coverage" in payload["overlays_present"]
    meta = payload["overlay_meta"]["coverage"]
    assert meta["metric"] == "branches"
    assert meta["url_base"] == "http://cov.example:9999/"

    nodes = {n["id"]: n for n in payload["nodes"]}
    counter = nodes["counter"]["overlays"]["coverage"]
    assert counter["lines"] == {"covered": 1, "total": 2, "pct": 50.0}
    assert counter["branches"] == {"covered": 1, "total": 2, "pct": 50.0}
    assert counter["toggles"] == {"covered": 2, "total": 3, "pct": 66.7}
    assert counter["coverview_link"] == (
        "http://cov.example:9999/"
        "#/tests%2Ffixtures%2Fcounter_with_subs%2Fcounter.sv?L=5"
    )

    # u_ff joins by its *defining module's* file (counter_ff.sv), not
    # the instantiation site in counter.sv.
    u_ff = nodes["counter.u_ff"]["overlays"]["coverage"]
    assert u_ff["lines"] == {"covered": 2, "total": 2, "pct": 100.0}
    assert u_ff["toggles"] == {"covered": 1, "total": 2, "pct": 50.0}
    assert "branches" not in u_ff
    assert "counter_ff.sv?L=2" in u_ff["coverview_link"]

    # Blackbox leaf: no coverage data, no crash.
    assert "coverage" not in nodes["counter.u_x"]["overlays"]


def test_cli_tree_output_unchanged_by_coverage_overlay():
    """The coverage overlay contributes to view.json only — desktop
    tree output stays byte-identical."""
    _require_verible()
    from rtl_buddy_view.cli import app

    runner = CliRunner()
    base_args = [
        "--top",
        "counter",
        "--filelist",
        str(DESIGN / "files.f"),
        "--format",
        "tree",
    ]
    without = runner.invoke(app, base_args)
    with_cov = runner.invoke(
        app,
        base_args + ["--overlay", f"coverage={FIXTURE / 'coverview_regression'}"],
    )
    assert without.exit_code == 0 and with_cov.exit_code == 0
    assert with_cov.output == without.output


def test_cli_rejects_bad_coverage_payload(tmp_path):
    """Loader failures surface as exit 1 with the overlay name."""
    _require_verible()
    from rtl_buddy_view.cli import app

    bad = tmp_path / "broken.info"
    bad.write_text("SF:a.sv\nDA:garbage\nend_of_record\n")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--top",
            "counter",
            "--filelist",
            str(DESIGN / "files.f"),
            "--overlay",
            f"coverage={bad}",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 1
    assert "overlay coverage:" in result.output
