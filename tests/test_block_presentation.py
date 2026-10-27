"""Block-diagram presentation controls (rtl-buddy-sch#196).

Three controls for ``--block-diagram`` on integration tops:

1. parameter rows — ``--params {all,none}`` and ``// rbsch: params=…``;
2. root boundary flags — ``--prune-flags``, and ``hide`` / ``bundle=``
   on root ports;
3. clock colours — covered by ``test_clock_colors.py``; the
   ``clock-colors`` overlay and the ``color=`` pragma end to end here.

The hint-layer tests are Verible-free; the fixture and CLI tests parse
``tests/fixtures/block_presentation`` (the issue's repro plus a top
using every control) and skip without Verible.
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.annotations import Clock, Crossing, DomainMap, load_domain_map
from rtl_buddy_view.connectivity import NetEdge
from rtl_buddy_view.extractor import (
    Instance,
    Module,
    ModuleTable,
    NetDecl,
    Parameter,
    ParameterOverride,
    Port,
    SourceLocation,
)
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import HierNode, build_hierarchy
from rtl_buddy_view.hints import (
    HintMap,
    HintsError,
    InstanceHints,
    ModuleHints,
    NetHints,
    apply_hints,
    hide_all_params,
    merge_hint_maps,
    parse_hint_sidecar,
    parse_params_value,
    resolve_hints,
    scan_pragmas,
)
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.render import mermaid as mermaid_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "block_presentation"
FILELIST = FIXTURE_DIR / "files.f"
FILE = "/design/top.sv"

needs_verible = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

_FLAG_RE = re.compile(r'^\s*"(_(?:in|out)_[^"]+)" \[shape=plaintext', re.M)
_EDGE_RE = re.compile(r'^\s*"([^"]+)" -> "([^"]+)" \[([^\n]*)\];', re.M)


def _loc(line: int) -> SourceLocation:
    return SourceLocation(file=FILE, start_line=line)


def _ov(name: str | None, value: str) -> ParameterOverride:
    return ParameterOverride(param_name=name, value_text=value, location=None)


# --- hint layer: params= ------------------------------------------------------------


def _params_table() -> ModuleTable:
    child = Module(
        name="child",
        ports=(),
        parameters=(
            Parameter(name="W", default_text="8", location=None),
            Parameter(name="DEPTH", default_text="4", location=None),
        ),
        instances=(),
        location=_loc(2),
    )
    top = Module(
        name="top",
        ports=(),
        parameters=(),
        instances=(
            Instance(
                name="u_a",
                module_name="child",
                param_overrides=(_ov("W", "8"), _ov("DEPTH", "16")),
                port_connections=(),
                location=_loc(10),
            ),
            Instance(
                name="u_b",
                module_name="child",
                param_overrides=(_ov("W", "4"), _ov(None, "2")),
                port_connections=(),
                location=_loc(11),
            ),
        ),
        location=_loc(9),
    )
    return ModuleTable(modules_by_name={"child": child, "top": top})


def _resolve(source: str, table: ModuleTable) -> HintMap:
    return resolve_hints(scan_pragmas({FILE: source}), table)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("none", "none"),
        ("all", "all"),
        ("W", ("W",)),
        ("DEPTH, W,W", ("DEPTH", "W")),
        (",", None),
        ("", None),
    ],
)
def test_parse_params_value(text: str, expected: object) -> None:
    assert parse_params_value(text) == expected


def test_params_pragma_on_module_and_instance() -> None:
    lines = [""] * 12
    lines[0] = "// rbsch: params=DEPTH"  # line 1 → module child (line 2)
    lines[9] = "child u_a ();  // rbsch: params=none"  # line 10
    lines[10] = "child u_b ();  // rbsch: params=all"  # line 11
    hints = _resolve("\n".join(lines), _params_table())
    assert hints.modules["child"].params == ("DEPTH",)
    assert hints.instances[("top", "u_a")].params == "none"
    assert hints.instances[("top", "u_b")].params == "all"
    assert not hints.warnings


def test_params_pragma_warns_on_unknown_name_and_empty_value() -> None:
    lines = [""] * 12
    lines[0] = "// rbsch: params=DEPHT"
    lines[9] = "child u_a ();  // rbsch: params=,"
    lines[10] = "child u_b ();  // rbsch: params=W,NOPE"
    hints = _resolve("\n".join(lines), _params_table())
    assert hints.modules["child"].params == ("DEPHT",)
    assert ("top", "u_a") not in hints.instances
    assert hints.instances[("top", "u_b")].params == ("NOPE", "W")
    text = "\n".join(hints.warnings)
    assert "params names DEPHT, not a parameter of module 'child'" in text
    assert "params names NOPE" in text
    assert "params needs all, none or a parameter list" in text


def _params_tree(hints: HintMap) -> HierNode:
    table = _params_table()
    return apply_hints(build_hierarchy(table, "top"), hints)


def test_instance_params_beat_module_params_and_filter_the_box() -> None:
    hints = HintMap(
        modules={"child": ModuleHints(params=("DEPTH",))},
        instances={("top", "u_b"): InstanceHints(params="all")},
    )
    root = _params_tree(hints)
    u_a, u_b = root.children
    assert u_a.display_params == ("DEPTH",)
    assert [ov.param_name for ov in u_a.shown_param_overrides] == ["DEPTH"]
    assert u_b.display_params is None
    assert len(u_b.shown_param_overrides) == 2
    # The data model keeps every override: only the box narrows.
    assert len(u_a.instance.param_overrides) == 2  # type: ignore[union-attr]


def test_a_name_list_drops_positional_overrides() -> None:
    root = _params_tree(
        HintMap(instances={("top", "u_b"): InstanceHints(params=("W",))})
    )
    assert [ov.value_text for ov in root.children[1].shown_param_overrides] == ["4"]


def test_hide_all_params_overrides_every_pragma() -> None:
    root = hide_all_params(
        _params_tree(HintMap(instances={("top", "u_b"): InstanceHints(params="all")}))
    )
    assert all(c.shown_param_overrides == () for c in root.children)
    # The top has no overrides of its own, and a second pass is free.
    assert root.display_params is None
    assert hide_all_params(root) is root


def test_params_none_drops_the_rows_from_every_dot_and_mermaid_label() -> None:
    root = hide_all_params(_params_tree(HintMap()))
    for kwargs in ({}, {"as_cluster_tree": True}, {"block_diagram": True}):
        out = io.StringIO()
        dot_render.render(root, out, **kwargs)
        assert "DEPTH" not in out.getvalue(), kwargs
        assert ".W(" not in out.getvalue(), kwargs
    out = io.StringIO()
    mermaid_render.render(root, out)
    assert "DEPTH" not in out.getvalue()


def test_sidecar_params_and_merge() -> None:
    sidecar = parse_hint_sidecar(
        {
            "schema_version": "1.1",
            "modules": {"child": {"params": ["W"]}},
            "instances": {"top.u_a": {"params": "none"}, "top.u_b": {"params": []}},
        },
        source="s.json",
    )
    assert sidecar.modules["child"].params == ("W",)
    assert sidecar.instances[("top", "u_a")].params == "none"
    # ``[]`` keeps nothing: the same as "none".
    assert sidecar.instances[("top", "u_b")].params == "none"
    base = HintMap(
        modules={"child": ModuleHints(label="C", params="none")},
        instances={("top", "u_a"): InstanceHints(params=("W",), rank=1)},
    )
    merged = merge_hint_maps(base, sidecar)
    assert merged.modules["child"] == ModuleHints(label="C", params=("W",))
    assert merged.instances[("top", "u_a")] == InstanceHints(params="none", rank=1)


@pytest.mark.parametrize("value", ["some", 3, [1]])
def test_sidecar_rejects_a_bad_params_value(value: object) -> None:
    with pytest.raises(HintsError, match="'params' must be"):
        parse_hint_sidecar(
            {"schema_version": "1.1", "modules": {"child": {"params": value}}},
            source="s.json",
        )


# --- hint layer: hide / color on ports ---------------------------------------------


def _port_table() -> ModuleTable:
    """``top`` with ports on lines 2-4, a net on 7, an instance on 8."""
    leaf = Module(name="leaf", ports=(), parameters=(), instances=(), location=_loc(20))
    top = Module(
        name="top",
        ports=(
            Port(name="clk", direction="input", type_text="logic", location=_loc(2)),
            Port(name="dbg", direction="input", type_text="logic", location=_loc(3)),
            Port(name="d", direction="input", type_text="logic", location=_loc(4)),
        ),
        parameters=(),
        instances=(
            Instance(
                name="u_leaf",
                module_name="leaf",
                param_overrides=(),
                port_connections=(),
                location=_loc(8),
            ),
        ),
        location=_loc(1),
        net_decls=(NetDecl(name="w", type_text="logic", location=_loc(7)),),
    )
    return ModuleTable(modules_by_name={"top": top, "leaf": leaf})


def _src(**lines: str) -> str:
    out = [""] * 22
    for key, text in lines.items():
        out[int(key[1:]) - 1] = text
    return "\n".join(out)


def test_trailing_hide_on_a_port_hides_the_port() -> None:
    hints = _resolve(_src(l3="input logic dbg,  // rbsch: hide"), _port_table())
    assert hints.nets[("top", "dbg")] == NetHints(hide=True)
    assert not hints.instances
    assert not hints.warnings


def test_standalone_hide_above_a_port_hides_the_port_not_an_instance() -> None:
    hints = _resolve(_src(l2="input logic clk,", l3="// rbsch: hide"), _port_table())
    # Line 3 holds the pragma; the next declaration is the port on 4.
    assert hints.nets[("top", "d")].hide is True
    assert not hints.instances


def test_standalone_hide_above_a_plain_net_still_reaches_the_instance() -> None:
    """The documented phase-1 rule survives: a ``logic`` between the
    pragma and the instance doesn't capture a box word."""
    hints = _resolve(_src(l6="// rbsch: hide"), _port_table())
    assert hints.instances[("top", "u_leaf")].hide is True
    assert ("top", "w") not in hints.nets


def test_hide_on_an_instance_line_is_still_an_instance_hide() -> None:
    hints = _resolve(_src(l8="leaf u_leaf ();  // rbsch: hide"), _port_table())
    assert hints.instances[("top", "u_leaf")].hide is True


def test_color_pragma_on_a_clock_port() -> None:
    hints = _resolve(
        _src(l2="input logic clk,  // rbsch: clock color=Pink"), _port_table()
    )
    assert hints.nets[("top", "clk")] == NetHints(
        classification="clock", color="#fce7f3"
    )


def test_color_pragma_rejects_a_bad_colour_and_a_non_clock() -> None:
    hints = _resolve(
        _src(
            l2="input logic clk,  // rbsch: color=teal",
            l4="input logic d,  // rbsch: data color=blue",
        ),
        _port_table(),
    )
    assert ("top", "clk") not in hints.nets
    assert hints.nets[("top", "d")] == NetHints(classification="data")
    text = "\n".join(hints.warnings)
    assert "color 'teal' is not a colour" in text
    assert "color applies to clocks" in text


def test_sidecar_hide_and_color_and_merge() -> None:
    sidecar = parse_hint_sidecar(
        {
            "schema_version": "1.1",
            "nets": {
                "top.dbg": {"hide": True},
                "top.clk": {"color": "#ABCDEF"},
                "top.aux": {"color": ""},
            },
        },
        source="s.json",
    )
    assert sidecar.nets[("top", "dbg")] == NetHints(hide=True)
    assert sidecar.nets[("top", "clk")] == NetHints(color="#abcdef")
    base = HintMap(
        nets={
            ("top", "aux"): NetHints(classification="clock", color="#111111"),
            ("top", "dbg"): NetHints(bundle="x"),
        }
    )
    merged = merge_hint_maps(base, sidecar)
    # ``""`` revokes the in-source colour.
    assert merged.nets[("top", "aux")] == NetHints(classification="clock", color="")
    assert merged.nets[("top", "dbg")] == NetHints(bundle="x", hide=True)


@pytest.mark.parametrize(
    ("entry", "match"),
    [({"hide": "yes"}, "must be a boolean"), ({"color": "teal"}, "not a colour")],
)
def test_sidecar_rejects_bad_hide_and_color(entry: dict, match: str) -> None:
    with pytest.raises(HintsError, match=match):
        parse_hint_sidecar(
            {"schema_version": "1.1", "nets": {"top.clk": entry}}, source="s.json"
        )


# --- dot: bundle flag helpers ------------------------------------------------------


def _port(name: str, direction: str | None, type_text: str = "logic") -> Port:
    return Port(name=name, direction=direction, type_text=type_text, location=None)  # type: ignore[arg-type]


def test_bundle_flag_direction_weighs_bits_then_count() -> None:
    members = [_port("a", "input", "logic [31:0]"), _port("b", "output", "logic")]
    dirs = {"a": "input", "b": "output"}
    assert dot_render._bundle_flag_direction(members, dirs, {}) == "input"
    # An unknown width falls back to counting ports: 1 vs 1 ties.
    members[0] = _port("a", "input", "my_t")
    assert dot_render._bundle_flag_direction(members, dirs, {}) is None
    two_out = [*members, _port("c", "inout")]
    assert (
        dot_render._bundle_flag_direction(two_out, {**dirs, "c": "inout"}, {})
        == "output"
    )
    assert dot_render._bundle_flag_direction(members, {}, {}) is None


def test_bundle_flag_keys_never_collide() -> None:
    keys = dot_render._bundle_flag_keys(["a-b", "a_b", "apb"])
    assert keys == {"a-b": "$a_b", "a_b": "$a_b_2", "apb": "$apb"}


def test_merge_parallel_unions_and_keeps_main() -> None:
    a = NetEdge(src=("port", "$x"), dst=("inst", "u"), nets=("p",), bits=3, bundle="x")
    b = NetEdge(
        src=("port", "$x"),
        dst=("inst", "u"),
        nets=("q",),
        bits=None,
        emphasis="main",
        bundle="y",
    )
    merged = dot_render._merge_parallel([a, b])
    assert merged.nets == ("p", "q")
    assert merged.bits is None
    assert merged.emphasis == "main"
    assert merged.bundle is None
    side = dot_render._merge_parallel(
        [a, NetEdge(a.src, a.dst, ("r",), bits=2, emphasis="side", bundle="x")]
    )
    assert (side.bits, side.emphasis, side.bundle) == (5, "side", "x")


# --- the fixture -----------------------------------------------------------------


def _table() -> ModuleTable:
    return parse_to_modules(parse_filelist(FILELIST), frontend=Frontend.verible)


def _hints(table: ModuleTable) -> HintMap:
    files = parse_filelist(FILELIST)
    return resolve_hints(scan_pragmas({str(p): p.read_text() for p in files}), table)


def _dot(top: str, *, hints: bool = True, **kwargs) -> str:
    table = _table()
    hint_map = _hints(table) if hints else None
    root = build_hierarchy(table, top)
    if hint_map is not None:
        root = apply_hints(root, hint_map)
    out = io.StringIO()
    dot_render.render(
        root, out, block_diagram=True, module_table=table, hints=hint_map, **kwargs
    )
    return out.getvalue()


#: ``rtl-buddy-view --top top --format dot --block-diagram`` on the
#: issue's repro, as it rendered before #196 — the default must not move.
REPRO_BEFORE_FLAGS = (
    '      "_in_clk" [shape=plaintext, label="clk ▶", fontsize=9];\n'
    '      "_in_d" [shape=plaintext, label="  d ▶", fontsize=9];\n'
)


@needs_verible
def test_repro_default_output_is_unchanged() -> None:
    text = _dot("top")
    assert REPRO_BEFORE_FLAGS in text
    assert "W&nbsp;&nbsp;" in text and "DEPTH&nbsp;&nbsp;" in text
    assert [(s, d) for s, d, _ in _EDGE_RE.findall(text)] == [("_in_d", "top.u_c")]


@needs_verible
def test_repro_prune_flags_drops_the_edgeless_clock_flag() -> None:
    text = _dot("top", prune_flags=True)
    assert _FLAG_RE.findall(text) == ["_in_d"]
    assert '"_in_d" [shape=plaintext, label="d ▶"' in text


@needs_verible
def test_prune_flags_keeps_a_flag_only_an_overlay_arrow_names() -> None:
    """A CDC arrow out of ``clk`` is an edge too: never prune its flag."""
    dm = DomainMap(
        schema_version="1.0",
        generator_name="t",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk", period=None, source="create_clock", ports=("clk",)),),
        crossings=(
            Crossing(
                src_clock="clk",
                dst_clock="other",
                dst_flop="top.u_c",
                min_hops=0,
                width=1,
                async_per_sdc=True,
                src_flop="x",
            ),
        ),
    )
    text = _dot("top", prune_flags=True, domain_map=dm)
    assert set(_FLAG_RE.findall(text)) == {"_in_clk", "_in_d"}


@needs_verible
def test_root_port_bundles_hide_and_params_on_the_rich_top() -> None:
    text = _dot("bp_top")
    flags = _FLAG_RE.findall(text)
    assert flags == ["_in_$s_dom", "_in_$f_dom", "_in_$apb", "_in_din", "_out_dout"]
    assert '"_in_$apb" [shape=plaintext, label="  apb ▶"' in text
    edges = {(s, d): attrs for s, d, attrs in _EDGE_RE.findall(text)}
    # The APB group is one flag and one folded edge, labelled apb.
    assert set(edges) == {
        ("_in_$apb", "bp_top.u_regs"),
        ("_in_din", "bp_top.u_fifo"),
        ("bp_top.u_fifo", "_out_dout"),
    }
    assert 'xlabel="apb\\l"' in edges[("_in_$apb", "bp_top.u_regs")]
    # dbg_en is hidden: no flag, no edge into u_dbg.
    assert "dbg_en" not in text
    # params=none on u_regs, params=DEPTH on every bp_fifo.
    regs = re.search(r'"bp_top\.u_regs" \[label=<(.*?)>,', text)
    fifo = re.search(r'"bp_top\.u_fifo" \[label=<(.*?)>,', text)
    assert regs is not None and "POINT-SIZE" not in regs.group(1)
    assert fifo is not None and "DEPTH" in fifo.group(1) and ">W&" not in fifo.group(1)


@needs_verible
def test_without_hints_the_rich_top_draws_every_port() -> None:
    text = _dot("bp_top", hints=False)
    assert len(_FLAG_RE.findall(text)) == 14


@needs_verible
def test_prune_flags_drops_the_edgeless_clock_reset_bundles() -> None:
    flags = _FLAG_RE.findall(_dot("bp_top", prune_flags=True))
    assert flags == ["_in_$apb", "_in_din", "_out_dout"]


@needs_verible
def test_a_bundled_or_hidden_clock_port_moves_or_drops_its_cdc_arrow() -> None:
    dm = DomainMap(
        schema_version="1.0",
        generator_name="t",
        generator_version="0",
        design_top="bp_top",
        design_frontend="yosys",
        clocks=(
            Clock(name="s_clk", period=None, source="create_clock", ports=("s_clk",)),
        ),
        crossings=tuple(
            Crossing(
                src_clock=src,
                dst_clock="f_clk",
                dst_flop="bp_top.u_fifo",
                min_hops=0,
                width=1,
                async_per_sdc=True,
                src_flop="x",
            )
            for src in ("s_clk", "dbg_en")
        ),
    )
    text = _dot("bp_top", domain_map=dm, prune_flags=True)
    tails = {s for s, d, _ in _EDGE_RE.findall(text) if d == "bp_top.u_fifo"}
    assert "_in_$s_dom" in tails
    assert "_in_s_clk" not in tails and "_in_dbg_en" not in tails
    assert "_in_$s_dom" in _FLAG_RE.findall(text)


@needs_verible
def test_rdc_arrows_follow_the_same_flags() -> None:
    from rtl_buddy_view.reset_annotations import ResetCrossing, ResetDomainMap

    rm = ResetDomainMap(
        schema_version="1.0",
        generator_name="t",
        generator_version="0",
        design_top="bp_top",
        design_frontend="yosys",
        reset_crossings=(
            ResetCrossing(
                instance_path="bp_top.u_regs",
                kind="async-deassert",
                flop_clock="s_clk",
                reset="s_rst_n",
                reset_kind="port",
                polarity="low",
                type="async",
                location=None,
            ),
        ),
    )
    text = _dot("bp_top", reset_map=rm)
    assert any(
        s == "_in_$s_dom" and d == "bp_top.u_regs" and "RDC" in a
        for s, d, a in _EDGE_RE.findall(text)
    )


# --- the CLI ------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rtl_buddy_view", "--filelist", str(FILELIST), *args],
        check=False,
        capture_output=True,
        text=True,
    )


@needs_verible
def test_cli_params_none_and_prune_flags_on_the_repro() -> None:
    result = _run(
        "--top",
        "top",
        "--format",
        "dot",
        "--block-diagram",
        "--params",
        "none",
        "--prune-flags",
    )
    assert result.returncode == 0, result.stderr
    assert "DEPTH" not in result.stdout
    assert _FLAG_RE.findall(result.stdout) == ["_in_d"]


@needs_verible
def test_cli_params_none_reaches_mermaid() -> None:
    result = _run("--top", "top", "--format", "mermaid", "--params", "none")
    assert result.returncode == 0, result.stderr
    assert "DEPTH" not in result.stdout


@needs_verible
@pytest.mark.parametrize(
    ("args", "warning"),
    [
        (("--format", "tree", "--params", "none"), "--params applies to --format dot"),
        (
            ("--format", "dot", "--prune-flags"),
            "--prune-flags applies to --block-diagram",
        ),
    ],
)
def test_cli_warns_and_ignores_misplaced_flags(
    args: tuple[str, ...], warning: str
) -> None:
    result = _run("--top", "top", *args)
    assert result.returncode == 0, result.stderr
    assert warning in result.stderr


@needs_verible
def test_cli_clock_colors_overlay_and_pragma() -> None:
    result = _run(
        "--top",
        "bp_top",
        "--format",
        "dot",
        "--block-diagram",
        "--clock-legend",
        "--overlay",
        f"clock={FIXTURE_DIR / 'domain_map.json'}",
        "--overlay",
        f"clock-colors={FIXTURE_DIR / 'clock_colors.json'}",
    )
    assert result.returncode == 0, result.stderr
    text = result.stdout
    # s_clk: the ``// rbsch: clock color=blue`` pragma on its port.
    # f_clk: the overlay's chosen hex.
    assert re.search(r'"bp_top\.u_regs" .*fillcolor="#dbeafe"', text)
    assert re.search(r'"_legend_s_clk" .*fillcolor="#dbeafe"', text)
    assert re.search(r'"bp_top\.u_fifo" .*fillcolor="#fde68a"', text)
    assert re.search(r'"_legend_f_clk" .*fillcolor="#fde68a"', text)


@needs_verible
def test_cli_clock_colors_overlay_without_one_deals_distinct_swatches() -> None:
    result = _run(
        "--top",
        "bp_top",
        "--format",
        "mermaid",
        "--no-pragmas",
        "--overlay",
        f"clock={FIXTURE_DIR / 'domain_map.json'}",
    )
    assert result.returncode == 0, result.stderr
    # f_clk / s_clk hash to one orange; sorted dealing gives blue / green.
    assert "style bp_top_u_fifo fill:#dbeafe" in result.stdout
    assert "style bp_top_u_regs fill:#dcfce7" in result.stdout
    assert load_domain_map(FIXTURE_DIR / "domain_map.json").design_top == "bp_top"


@needs_verible
def test_cli_bad_clock_colors_overlay_exits_1(tmp_path: Path) -> None:
    bad = tmp_path / "cc.json"
    bad.write_text('{"schema_version": "1.0", "clocks": {"s_clk": "teal"}}')
    result = _run("--top", "bp_top", "--overlay", f"clock-colors={bad}")
    assert result.returncode == 1
    assert "overlay clock-colors:" in result.stderr
    assert "not a colour" in result.stderr


def test_list_overlays_names_clock_colors() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "rtl_buddy_view", "--list-overlays"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "clock-colors\t1.0\t(built-in)" in result.stdout
    assert "hints\t1.3\t(built-in)" in result.stdout
