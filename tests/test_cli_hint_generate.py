"""Hint keys reach generate-scoped instances and nets (rtl-buddy-sch#217).

Drives the real CLI over the issue's repro (``top8``) and a lanes
fixture (``ghk_top``) that adds a generate-local net, a conditional
arm, a symbolic loop, a non-generate control instance, and a placement
whose override grows the loop past the module's default elaboration.
Verible-gated like ``test_cli_hints.py``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._verible_install import find_binary

FIXTURES = Path(__file__).parent / "fixtures" / "generate_hint_keys"
FILELIST = FIXTURES / "files.f"

pytestmark = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


def _run(top: str, tmp_path: Path, sidecar: dict | None, *args: str):
    cmd = [sys.executable, "-m", "rtl_buddy_view", "--top", top]
    cmd += ["--filelist", str(FILELIST), *args]
    if sidecar is not None:
        path = tmp_path / "hints.json"
        path.write_text(json.dumps({"schema_version": "1.3", **sidecar}))
        cmd += ["--overlay", f"hints={path}"]
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _json_ids(stdout: str) -> list[str]:
    return [n["id"] for n in json.loads(stdout)["nodes"]]


def _elk_ids(stdout: str) -> list[str]:
    ids: list[str] = []

    def visit(node: dict) -> None:
        ids.append(node["id"])
        for child in node.get("children", ()):
            visit(child)

    visit(json.loads(stdout))
    return ids


def _hint_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("hints:")]


# --- the issue's repro ------------------------------------------------------


def test_issue_key_hides_one_iteration_in_every_renderer(tmp_path: Path) -> None:
    sidecar = {"instances": {"top8.g[0].i_lpb": {"hide": True}}}

    tree = _run("top8", tmp_path, sidecar, "--format", "tree")
    assert tree.returncode == 0, tree.stderr
    assert "g[0].i_lpb" not in tree.stdout
    assert "g[1].i_lpb" in tree.stdout
    assert _hint_lines(tree.stderr) == []

    dot = _run("top8", tmp_path, sidecar, "--format", "dot", "--block-diagram")
    assert dot.returncode == 0, dot.stderr
    assert '"top8.g[0].i_lpb"' not in dot.stdout
    assert '"top8.g[1].i_lpb"' in dot.stdout

    mermaid = _run("top8", tmp_path, sidecar, "--format", "mermaid")
    assert "g[0].i_lpb" not in mermaid.stdout
    assert "g[1].i_lpb" in mermaid.stdout

    ids = _json_ids(_run("top8", tmp_path, sidecar, "--format", "json").stdout)
    assert "top8.g[0].i_lpb" not in ids
    assert "top8.g[1].i_lpb" in ids

    elk = _elk_ids(_run("top8", tmp_path, sidecar, "--format", "elk").stdout)
    assert "top8.g[0].i_lpb" not in elk
    assert "top8.g[1].i_lpb" in elk


def test_star_index_hides_every_iteration(tmp_path: Path) -> None:
    result = _run(
        "top8",
        tmp_path,
        {"instances": {"top8.g[*].i_lpb": {"hide": True}}},
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    ids = _json_ids(result.stdout)
    assert not [i for i in ids if i.endswith("i_lpb")]
    assert "top8.g[0].i_mrg" in ids and "top8.g[1].i_mrg" in ids


def test_glob_hides_every_matching_instance(tmp_path: Path) -> None:
    result = _run(
        "top8",
        tmp_path,
        {"instances": {"top8.*.i_*": {"hide": True}}},
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    assert _json_ids(result.stdout) == ["top8", "top8.g[0].sub", "top8.g[1].sub"]


def test_label_reaches_a_generate_instance(tmp_path: Path) -> None:
    result = _run(
        "top8",
        tmp_path,
        {"instances": {"top8.g[1].i_mrg": {"label": "merge one"}}},
        "--format",
        "dot",
        "--block-diagram",
    )
    assert result.returncode == 0, result.stderr
    assert "<B>merge one</B>" in result.stdout


def test_malformed_generate_key_exits_1(tmp_path: Path) -> None:
    result = _run(
        "top8",
        tmp_path,
        {"instances": {"top8.g[0.i_lpb": {"hide": True}}},
        "--format",
        "tree",
    )
    assert result.returncode == 1
    assert "overlay hints:" in result.stderr
    assert "instances.top8.g[0.i_lpb" in result.stderr
    assert "unbalanced '['" in result.stderr


def test_key_naming_nothing_warns_and_changes_nothing(tmp_path: Path) -> None:
    plain = _run("top8", tmp_path, None, "--format", "tree")
    result = _run(
        "top8",
        tmp_path,
        {
            "instances": {
                "top8.g[0].i_lbp": {"hide": True},
                "top8.g[*].u_none": {"hide": True},
            },
            "nets": {"top8.g[*].nope": {"emphasis": "main"}},
        },
        "--format",
        "tree",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == plain.stdout
    warnings = _hint_lines(result.stderr)
    assert len(warnings) == 3
    assert any(
        "instances.top8.g[0].i_lbp: names no instance of module 'top8'" in w
        and "g[0].i_lpb" in w
        for w in warnings
    )
    assert any("instances.top8.g[*].u_none: names no instance" in w for w in warnings)
    assert any("nets.top8.g[*].nope: names no net" in w for w in warnings)


def test_generate_interface_net_keys_bind_without_warnings(tmp_path: Path) -> None:
    result = _run(
        "top8",
        tmp_path,
        {
            "nets": {
                "top8.g[0].sub": {"bundle": "lane0"},
                "top8.g[*].sub[1]": {"emphasis": "main"},
            }
        },
        "--format",
        "dot",
        "--block-diagram",
    )
    assert result.returncode == 0, result.stderr
    assert _hint_lines(result.stderr) == []


# --- lanes: placements, nets, pragmas, controls -----------------------------


def _edge_widths(dot: str) -> dict[str, str]:
    """``src -> dst`` → penwidth, for the lane edges of a block diagram."""
    widths: dict[str, str] = {}
    for line in dot.splitlines():
        m = re.search(r'"([^"]+)" -> "([^"]+)" \[.*penwidth=([0-9.]+)', line)
        if m and "u_src" in m.group(1):
            widths[f"{m.group(1)} -> {m.group(2)}"] = m.group(3)
    return widths


def test_wildcard_net_key_reaches_every_lane_of_every_placement(
    tmp_path: Path,
) -> None:
    plain = _edge_widths(
        _run("ghk_top", tmp_path, None, "--format", "dot", "--block-diagram").stdout
    )
    result = _run(
        "ghk_top",
        tmp_path,
        {
            "nets": {
                "ghk_lanes.lane[*].w": {"emphasis": "main"},
                # Exists only in the N=4 placement — no warning.
                "ghk_lanes.lane[3].w": {"emphasis": "side"},
            }
        },
        "--format",
        "dot",
        "--block-diagram",
    )
    assert result.returncode == 0, result.stderr
    assert _hint_lines(result.stderr) == []
    widths = _edge_widths(result.stdout)
    # 4 lanes under u_four + 2 under u_two.
    assert len(widths) == len(plain) == 6
    assert set(plain.values()) == {"1.0"}
    side = {k for k, v in widths.items() if v == "0.6"}
    assert side == {"ghk_top.u_four.lane[3].u_src -> ghk_top.u_four.lane[3].u_snk"}
    assert all(v == "2.2" for k, v in widths.items() if k not in side)


def test_exact_net_key_classifies_one_lane(tmp_path: Path) -> None:
    result = _run(
        "ghk_top",
        tmp_path,
        {"nets": {"ghk_lanes.lane[0].w": {"classification": "clock"}}},
        "--format",
        "dot",
        "--block-diagram",
    )
    assert result.returncode == 0, result.stderr
    edges = _edge_widths(result.stdout)
    # A clock net draws no dataflow edge: lane[0] loses its edge in
    # both placements, every other lane keeps it.
    assert len(edges) == 4
    assert not [e for e in edges if "lane[0]" in e]


def test_instance_key_past_the_default_elaboration(tmp_path: Path) -> None:
    """``lane[3]`` exists only where the override sets N=4."""
    result = _run(
        "ghk_top",
        tmp_path,
        {"instances": {"ghk_lanes.lane[3].u_snk": {"hide": True}}},
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    assert _hint_lines(result.stderr) == []
    ids = _json_ids(result.stdout)
    assert "ghk_top.u_four.lane[3].u_snk" not in ids
    assert "ghk_top.u_four.lane[2].u_snk" in ids


def test_conditional_and_symbolic_scopes(tmp_path: Path) -> None:
    result = _run(
        "ghk_top",
        tmp_path,
        {
            "instances": {
                "ghk_lanes.wide.u_extra": {"hide": True},
                "ghk_lanes.sym[*].u_s": {"hide": True},
            }
        },
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    assert _hint_lines(result.stderr) == []
    ids = _json_ids(result.stdout)
    assert not [i for i in ids if i.endswith((".wide.u_extra", ".sym[*].u_s"))]
    assert "ghk_top.u_two.lane[1].u_src" in ids


def test_non_generate_key_is_unchanged(tmp_path: Path) -> None:
    result = _run(
        "ghk_top",
        tmp_path,
        {"instances": {"ghk_lanes.u_plain": {"hide": True}}},
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    assert _hint_lines(result.stderr) == []
    ids = _json_ids(result.stdout)
    assert not [i for i in ids if i.endswith(".u_plain")]
    assert "ghk_top.u_four.lane[0].u_src" in ids


def test_pragma_in_a_loop_body_hides_every_iteration(tmp_path: Path) -> None:
    """``// rbsch: hide`` on ``u_tap`` used to bind ``lane[0]`` only."""
    result = _run("ghk_top", tmp_path, None, "--format", "tree")
    assert result.returncode == 0, result.stderr
    assert "u_tap" not in result.stdout
    assert _hint_lines(result.stderr) == []
    unhinted = _run("ghk_top", tmp_path, None, "--format", "tree", "--no-pragmas")
    assert unhinted.stdout.count("u_tap") == 6


# --- the names keys bind against --------------------------------------------


def test_design_names_cover_every_placement_and_scope() -> None:
    from rtl_buddy_view._filelist import parse_filelist
    from rtl_buddy_view.frontend import Frontend, parse_to_modules
    from rtl_buddy_view.graph import build_hierarchy
    from rtl_buddy_view.hints import design_names

    table = parse_to_modules(parse_filelist(FILELIST), frontend=Frontend.verible)
    root = build_hierarchy(table, "ghk_top")

    from_table = design_names(table)
    assert "lane[3].u_snk" not in from_table.instances["ghk_lanes"]
    assert {"lane[0].w", "lane[1].w", "wide.x", "sym[*].y"} <= from_table.nets[
        "ghk_lanes"
    ]
    # The excluded ``else`` arm contributes nothing.
    assert "narrow.z" not in from_table.nets["ghk_lanes"]
    assert "narrow.u_only" not in from_table.instances["ghk_lanes"]

    names = design_names(table, root)
    assert "lane[3].u_snk" in names.instances["ghk_lanes"]
    assert "lane[3].w" in names.nets["ghk_lanes"]
    assert {"g[0].sub", "g[1].sub"} <= names.nets["top8"]
