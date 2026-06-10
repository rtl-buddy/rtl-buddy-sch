"""Tests for the ``rtl-buddy-view query`` CLI surface (rtl_buddy#198).

The subcommands are thin wrappers over :mod:`rtl_buddy_view.query`
(whose semantics are pinned by ``test_query.py``), so these tests
focus on the CLI layer: argument plumbing, JSON shapes, exit codes,
stderr diagnostics, and the non-regression of the original
single-command render surface after the subcommand split.

All design-loading tests are Verible-gated and run in-process via
``CliRunner`` (mirrors ``test_coverage_overlay.py``) so the new CLI
code counts toward the coverage gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rtl_buddy_view.cli import app

DESIGN = Path(__file__).parent / "fixtures" / "counter_with_subs"
# counter_with_subs hierarchy:
#   counter
#   ├── u_ff : counter_ff
#   └── u_x  : sub_x (blackbox)


def _require_verible() -> None:
    try:
        from rtl_buddy_view._verible_install import find_binary
    except ImportError:
        pytest.skip("verible not available")
    if find_binary("verible-verilog-syntax") is None:
        pytest.skip("verible binary not on PATH / vendor/")


def _query(*args: str):
    runner = CliRunner()
    return runner.invoke(
        app,
        ["query", *args, "--top", "counter", "--filelist", str(DESIGN / "files.f")],
    )


# --- surface ---------------------------------------------------------------


def test_query_help_lists_all_verbs() -> None:
    """No Verible needed — pure typer surface."""
    result = CliRunner().invoke(app, ["query", "--help"])
    assert result.exit_code == 0
    for verb in (
        "find-module",
        "subtree",
        "instances-of",
        "port-connections",
        "source-snippet",
    ):
        assert verb in result.output


def test_render_path_still_validates_required_options() -> None:
    """The subcommand guard must not swallow the legacy validation:
    invoking with no subcommand and no --filelist still exits 2."""
    result = CliRunner().invoke(app, ["--top", "counter"])
    assert result.exit_code == 2
    assert "--filelist is required" in result.output


# --- find-module -----------------------------------------------------------


def test_find_module_emits_full_definition() -> None:
    _require_verible()
    result = _query("find-module", "counter_ff")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["name"] == "counter_ff"
    assert [p["name"] for p in payload["ports"]] == ["clk", "q"]
    assert payload["location"]["file"].endswith("counter_ff.sv")
    # Tuples serialize as lists; instances of a leaf module are empty.
    assert payload["instances"] == []


def test_find_module_miss_exits_one_with_message() -> None:
    _require_verible()
    result = _query("find-module", "nope")
    assert result.exit_code == 1
    assert "module 'nope' not found" in result.output


# --- subtree ---------------------------------------------------------------


def test_subtree_nested_json() -> None:
    _require_verible()
    result = _query("subtree", "counter")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["instance_path"] == "counter"
    assert payload["instance_name"] is None  # top has no instantiation
    children = {c["instance_path"]: c for c in payload["children"]}
    assert set(children) == {"counter.u_ff", "counter.u_x"}
    assert children["counter.u_ff"]["module_name"] == "counter_ff"
    assert children["counter.u_ff"]["instance_name"] == "u_ff"
    assert children["counter.u_x"]["is_blackbox"] is True
    assert children["counter.u_x"]["location"] is None  # blackbox: no definition
    assert children["counter.u_ff"]["children"] == []


def test_subtree_tree_format_matches_renderer() -> None:
    _require_verible()
    result = _query("subtree", "counter", "--format", "tree")
    assert result.exit_code == 0, result.output
    # The ASCII renderer, rooted at the matched node (labelled by its
    # module name, as for any tree root).
    assert result.stdout.splitlines() == [
        "counter",
        "├── u_ff : counter_ff",
        "└── u_x : sub_x (blackbox)",
    ]
    # And rooting at a leaf renders just that node.
    leaf = _query("subtree", "counter.u_ff", "--format", "tree")
    assert leaf.stdout == "counter_ff\n"


def test_subtree_unresolved_path_exits_one() -> None:
    _require_verible()
    result = _query("subtree", "counter.u_missing")
    assert result.exit_code == 1
    assert "'counter.u_missing' not found" in result.output
    assert "rooted at 'counter'" in result.output


def test_subtree_bad_top_reports_hierarchy_error() -> None:
    _require_verible()
    result = CliRunner().invoke(
        app,
        [
            "query",
            "subtree",
            "counter",
            "--top",
            "not_a_module",
            "--filelist",
            str(DESIGN / "files.f"),
        ],
    )
    assert result.exit_code == 1
    assert "hierarchy:" in result.output
    assert "not_a_module" in result.output


# --- instances-of ----------------------------------------------------------


def test_instances_of_lists_flat_nodes_without_children() -> None:
    _require_verible()
    result = _query("instances-of", "counter_ff")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    (node,) = payload
    assert node["instance_path"] == "counter.u_ff"
    assert "children" not in node


def test_instances_of_includes_blackboxes() -> None:
    _require_verible()
    result = _query("instances-of", "sub_x")
    payload = json.loads(result.stdout)
    assert [n["instance_path"] for n in payload] == ["counter.u_x"]
    assert payload[0]["is_blackbox"] is True


def test_instances_of_unknown_module_is_empty_list_exit_zero() -> None:
    _require_verible()
    result = _query("instances-of", "nope")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


# --- port-connections ------------------------------------------------------


def test_port_connections_emits_connection_list() -> None:
    _require_verible()
    result = _query("port-connections", "counter.u_ff")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [(c["port_name"], c["net_expr_text"]) for c in payload] == [
        ("clk", "clk"),
        ("q", "q"),
    ]
    # Connection anchors point at the instantiation site in the parent.
    assert payload[0]["location"]["file"].endswith("counter.sv")


def test_port_connections_top_node_is_empty_list() -> None:
    _require_verible()
    result = _query("port-connections", "counter")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


def test_port_connections_unresolved_path_exits_one() -> None:
    _require_verible()
    result = _query("port-connections", "counter.nope")
    assert result.exit_code == 1


# --- source-snippet --------------------------------------------------------


def test_source_snippet_line_numbered_by_default() -> None:
    _require_verible()
    result = _query("source-snippet", "counter.u_ff")
    assert result.exit_code == 0, result.output
    # Module body spans lines 2-6; default context 2 clamps to 1..6
    # (the file is 6 lines long). Line-number prefixes are the
    # LLM-citation contract.
    assert "2 | module counter_ff (" in result.stdout
    assert result.stdout.splitlines()[0].startswith("1 |")


def test_source_snippet_no_line_numbers_and_context() -> None:
    _require_verible()
    result = _query(
        "source-snippet", "counter.u_ff", "--context", "0", "--no-line-numbers"
    )
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "module counter_ff ("
    assert lines[-1] == "endmodule"


def test_source_snippet_blackbox_exits_one() -> None:
    _require_verible()
    result = _query("source-snippet", "counter.u_x")
    assert result.exit_code == 1
    assert "blackbox" in result.output
