"""Tests for the ``view.json`` v1 renderer (Phase 4 — #17).

The JSON output is the locked v1 contract that the Phase 5
interactive web viewer (#18) and ``rb hier`` parse. The schema file
at ``schemas/view-v1.json`` is the authoritative constraint; the
last test in this file validates every Phase 1/2/3 fixture against
it so the schema stays a live document rather than a stale
docstring.

Renaming or retyping any field documented in the schema is a
downstream-breaking change — the schema test fails first, the
shape tests below pin individual fields, and the producer-side
goldens in ``test_reset_overlay.py`` catch the byte-level drift.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import jsonschema
import pytest

from rtl_buddy_view.annotations import (
    Clock,
    Crossing,
    DomainMap,
    FlopDomain,
)
from rtl_buddy_view.extractor import (
    Instance,
    Module,
    ModuleTable,
    ParameterOverride,
    Port,
    PortConnection,
    SourceLocation,
)
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.render import json_render

SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "view-v1.json"


def _node(
    path: str,
    module: str,
    *,
    inst_name: str | None = None,
    is_blackbox: bool = False,
    children: tuple[HierNode, ...] = (),
    overrides: tuple[ParameterOverride, ...] = (),
    connections: tuple[PortConnection, ...] = (),
    module_obj: Module | None = None,
    instance_location: SourceLocation | None = None,
) -> HierNode:
    inst = (
        Instance(
            name=inst_name,
            module_name=module,
            param_overrides=overrides,
            port_connections=connections,
            location=instance_location,
        )
        if inst_name is not None
        else None
    )
    return HierNode(
        instance_path=path,
        module_name=module,
        instance=inst,
        module=module_obj,
        is_blackbox=is_blackbox,
        children=children,
    )


def _render(root: HierNode, **kwargs) -> dict:
    buf = io.StringIO()
    json_render.render(root, buf, **kwargs)
    return json.loads(buf.getvalue())


# --- envelope ---------------------------------------------------------------


def test_envelope_required_keys_present() -> None:
    root = _node("top", "top")
    payload = _render(root)
    assert payload["schema_version"] == "1.1"
    assert payload["top"] == "top"
    # dut_top / tb_top default to null when the caller (the test
    # harness) doesn't supply them — covers direct json_render.render
    # invocations that don't know the CLI surface.
    assert payload["dut_top"] is None
    assert payload["tb_top"] is None
    assert payload["tool"]["name"] == "rtl-buddy-view"
    assert isinstance(payload["tool"]["version"], str)
    assert payload["nodes"]
    assert payload["edges"] == []
    assert payload["overlays_present"] == []


def test_envelope_records_dut_top_and_tb_top_when_supplied() -> None:
    """When the CLI passes through --top / --tb-top values, the
    renderer surfaces them at the envelope level for downstream
    consumers (SPA DUT-boundary rendering, hub wave↔view mapping).
    Either may be null independently of the other."""
    root = _node("tb_top", "tb_top")
    payload = _render(root, dut_top="dut", tb_top="tb_top")
    assert payload["top"] == "tb_top"
    assert payload["dut_top"] == "dut"
    assert payload["tb_top"] == "tb_top"

    # --top alone
    dut_only = _render(_node("dut", "dut"), dut_top="dut")
    assert dut_only["dut_top"] == "dut"
    assert dut_only["tb_top"] is None

    # --tb-top alone
    tb_only = _render(_node("tb_top", "tb_top"), tb_top="tb_top")
    assert tb_only["dut_top"] is None
    assert tb_only["tb_top"] == "tb_top"


def test_output_is_deterministic() -> None:
    """Two renders over the same graph must produce identical bytes."""
    child = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    buf_a, buf_b = io.StringIO(), io.StringIO()
    json_render.render(root, buf_a)
    json_render.render(root, buf_b)
    assert buf_a.getvalue() == buf_b.getvalue()


# --- nodes ------------------------------------------------------------------


def test_nodes_sorted_by_id() -> None:
    inner = _node("top.u_a.u_z", "leaf", inst_name="u_z")
    outer = _node("top.u_a", "mid", inst_name="u_a", children=(inner,))
    root = _node("top", "top", children=(outer,))
    payload = _render(root)
    ids = [n["id"] for n in payload["nodes"]]
    assert ids == ["top", "top.u_a", "top.u_a.u_z"]


def test_node_carries_parameters_dict() -> None:
    inst_overrides = (
        ParameterOverride(param_name="W", value_text="16", location=None),
        ParameterOverride(param_name="DEPTH", value_text="32", location=None),
    )
    child = _node("top.u_x", "ff", inst_name="u_x", overrides=inst_overrides)
    root = _node("top", "top", children=(child,))
    payload = _render(root)
    n = next(n for n in payload["nodes"] if n["id"] == "top.u_x")
    assert n["parameters"] == {"W": "16", "DEPTH": "32"}


def test_node_ports_joined_from_module_and_parent_connections() -> None:
    """Ports come from the child's *module* declaration; expr/anchor
    come from the parent-side connection at this instantiation."""
    ff_module = Module(
        name="ff",
        ports=(
            Port(name="clk", direction="input", type_text=None, location=None),
            Port(name="q", direction="output", type_text=None, location=None),
        ),
        parameters=(),
        instances=(),
        location=SourceLocation(file="/abs/ff.sv", start_line=1, start_column=1),
    )
    conn = PortConnection(
        port_name="clk",
        net_expr_text="clk_a",
        location=SourceLocation(file="/abs/top.sv", start_line=12, start_column=24),
    )
    child = HierNode(
        instance_path="top.u_x",
        module_name="ff",
        instance=Instance(
            name="u_x",
            module_name="ff",
            param_overrides=(),
            port_connections=(conn,),
            location=None,
        ),
        module=ff_module,
        is_blackbox=False,
        children=(),
    )
    root = _node("top", "top", children=(child,))
    payload = _render(root)
    n = next(n for n in payload["nodes"] if n["id"] == "top.u_x")
    ports = {p["name"]: p for p in n["ports"]}
    assert ports["clk"]["dir"] == "input"
    assert ports["clk"]["expr"] == "clk_a"
    assert ports["clk"]["anchor"] == {"line": 12, "col": 24}
    # Unconnected port renders with null expr.
    assert ports["q"]["expr"] is None
    assert ports["q"]["anchor"] is None


def test_blackbox_node_renders_ports_from_parent_connections() -> None:
    """Module wasn't found — fall back to the parent's named bindings."""
    inst = Instance(
        name="u_bb",
        module_name="mystery",
        param_overrides=(),
        port_connections=(
            PortConnection(port_name="a", net_expr_text="net_a", location=None),
            PortConnection(port_name="b", net_expr_text="net_b", location=None),
        ),
        location=None,
    )
    child = HierNode(
        instance_path="top.u_bb",
        module_name="mystery",
        instance=inst,
        module=None,
        is_blackbox=True,
        children=(),
    )
    root = _node("top", "top", children=(child,))
    payload = _render(root)
    n = next(n for n in payload["nodes"] if n["id"] == "top.u_bb")
    assert n["is_blackbox"] is True
    names = {p["name"] for p in n["ports"]}
    assert names == {"a", "b"}
    a_port = next(p for p in n["ports"] if p["name"] == "a")
    assert a_port["dir"] is None  # unknown — module never resolved
    assert a_port["expr"] == "net_a"


def test_source_block_carries_decl_line_col() -> None:
    """``source.decl_line`` / ``decl_col`` mark the module declaration —
    distinct from ``start_line``/``start_column`` which mark *this*
    instantiation."""
    module_loc = SourceLocation(file="/abs/ff.sv", start_line=5, start_column=1)
    ff_module = Module(
        name="ff",
        ports=(),
        parameters=(),
        instances=(),
        location=module_loc,
    )
    inst_loc = SourceLocation(file="/abs/top.sv", start_line=12, start_column=8)
    child = HierNode(
        instance_path="top.u_x",
        module_name="ff",
        instance=Instance(
            name="u_x",
            module_name="ff",
            param_overrides=(),
            port_connections=(),
            location=inst_loc,
        ),
        module=ff_module,
        is_blackbox=False,
        children=(),
    )
    root = _node("top", "top", children=(child,))
    payload = _render(root)
    n = next(n for n in payload["nodes"] if n["id"] == "top.u_x")
    assert n["source"]["file"] == "/abs/top.sv"  # the instantiation site
    assert n["source"]["start_line"] == 12
    assert n["source"]["start_column"] == 8
    assert n["source"]["decl_line"] == 5  # module declaration
    assert n["source"]["decl_col"] == 1


def test_link_uri_format() -> None:
    inst_loc = SourceLocation(file="/abs/top.sv", start_line=12, start_column=8)
    child = HierNode(
        instance_path="top.u_x",
        module_name="ff",
        instance=Instance(
            name="u_x",
            module_name="ff",
            param_overrides=(),
            port_connections=(),
            location=inst_loc,
        ),
        module=None,
        is_blackbox=False,
        children=(),
    )
    root = _node("top", "top", children=(child,))
    payload = _render(root)
    n = next(n for n in payload["nodes"] if n["id"] == "top.u_x")
    assert n["link"] == "rtlbuddy://open?file=/abs/top.sv&line=12&col=8"


def test_link_uri_null_when_no_location() -> None:
    root = _node("top", "top")  # no module location either
    payload = _render(root)
    assert payload["nodes"][0]["link"] is None


def test_link_uri_encodes_special_chars() -> None:
    inst_loc = SourceLocation(
        file="/abs/path with space/top file.sv",
        start_line=1,
        start_column=1,
    )
    child = HierNode(
        instance_path="top.u_x",
        module_name="ff",
        instance=Instance(
            name="u_x",
            module_name="ff",
            param_overrides=(),
            port_connections=(),
            location=inst_loc,
        ),
        module=None,
        is_blackbox=False,
        children=(),
    )
    root = _node("top", "top", children=(child,))
    payload = _render(root)
    link = (
        payload["nodes"][0]["link"]
        if False
        else next(n["link"] for n in payload["nodes"] if n["id"] == "top.u_x")
    )
    # Spaces in the file path must be percent-encoded so the URI
    # is dispatchable; the path separators (/) stay readable.
    assert "%20" in link
    assert "/abs/path%20with%20space/top%20file.sv" in link


# --- edges ------------------------------------------------------------------


def test_edges_record_parent_child_pairs() -> None:
    inner = _node("top.u_a.u_z", "leaf", inst_name="u_z")
    outer = _node("top.u_a", "mid", inst_name="u_a", children=(inner,))
    root = _node("top", "top", children=(outer,))
    payload = _render(root)
    edges = [(e["from"], e["to"]) for e in payload["edges"]]
    assert edges == [("top", "top.u_a"), ("top.u_a", "top.u_a.u_z")]


def test_edge_port_pairs_carry_net_expr_and_child_port() -> None:
    conn_clk = PortConnection(port_name="clk", net_expr_text="core_clk", location=None)
    conn_q = PortConnection(port_name="q", net_expr_text="out_net", location=None)
    inst = Instance(
        name="u_alu",
        module_name="alu",
        param_overrides=(),
        port_connections=(conn_clk, conn_q),
        location=None,
    )
    child = HierNode(
        instance_path="top.u_alu",
        module_name="alu",
        instance=inst,
        module=None,
        is_blackbox=False,
        children=(),
    )
    root = _node("top", "top", children=(child,))
    payload = _render(root)
    edge = next(e for e in payload["edges"] if e["to"] == "top.u_alu")
    assert edge["port_pairs"] == [
        ["core_clk", "clk"],
        ["out_net", "q"],
    ]


# --- overlays --------------------------------------------------------------


def test_overlays_present_lists_active_overlays() -> None:
    cm = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
    )
    root = _node("top", "top")
    payload = _render(root, domain_map=cm)
    assert payload["overlays_present"] == ["clock"]


def test_empty_overlay_payload_not_listed_as_present() -> None:
    """An empty clock map (no SDC supplied) shouldn't surface as an
    active overlay — the viewer would render a toggle for nothing."""
    empty = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
    )
    root = _node("top", "top")
    payload = _render(root, domain_map=empty)
    assert payload["overlays_present"] == []


def test_clock_overlay_per_node_uses_flop_clock_when_known() -> None:
    """A flop with an explicit ``flop_domains`` entry surfaces with
    that exact clock — distinguishes from the predominant-clock
    fallback used on container modules."""
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    cm = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        flop_domains=(
            FlopDomain(instance_path="top.u_dst", clock="clk_b", location=None),
        ),
    )
    payload = _render(root, domain_map=cm)
    dst_node = next(n for n in payload["nodes"] if n["id"] == "top.u_dst")
    assert dst_node["overlays"]["clock"] == {"clock": "clk_b"}


def test_clock_crossing_marks_edge() -> None:
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    cm = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_dst",
                min_hops=0,
                width=1,
                async_per_sdc=True,
                src_flop="top.src",
            ),
        ),
    )
    payload = _render(root, domain_map=cm)
    edge = next(e for e in payload["edges"] if e["to"] == "top.u_dst")
    # The clock overlay now carries the deduped (src_clock, dst_clock)
    # pairs alongside the crossing flag so the SPA's EdgeDetail panel
    # can surface the specific clocks without re-reading the domain
    # map. One flop in this fixture → flops=1.
    assert edge["overlays"]["clock"] == {
        "crossing": True,
        "pairs": [{"src_clock": "clk_a", "dst_clock": "clk_b", "flops": 1}],
    }


def test_no_overlays_yields_empty_overlays_dict() -> None:
    root = _node("top", "top")
    payload = _render(root)
    assert payload["nodes"][0]["overlays"] == {}


# --- JSON Schema validation -------------------------------------------------


@pytest.fixture(scope="module")
def view_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def test_simple_payload_validates_against_v1_schema(view_schema: dict) -> None:
    """Renderer output for a minimal hierarchy validates against the
    locked v1 schema. The schema is the authoritative constraint;
    this test ensures it stays in sync with the renderer."""
    child = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    payload = _render(root)
    jsonschema.validate(instance=payload, schema=view_schema)


# --- layout block -----------------------------------------------------------


def test_layout_block_present_by_default() -> None:
    """Default render embeds the dot string so the SPA can use the
    same layout as the desktop ``--format dot`` output. Producers can
    opt out via ``embed_layout=False`` (asserted below)."""
    child = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    payload = _render(root)
    layout = payload["layout"]
    assert layout["engine"] == "dot"
    assert layout["dot"].startswith("digraph hierarchy {")
    # Round-trip: every node id in nodes[] is referenced verbatim in
    # the embedded dot so the SPA's <title>-keyed selector can find
    # the SVG group for every node.
    for node in payload["nodes"]:
        assert f'"{node["id"]}"' in layout["dot"], node["id"]


def test_layout_block_omitted_when_disabled() -> None:
    """``embed_layout=False`` keeps the JSON renderer free of the
    dot dependency for producers that can't afford the layout cost.
    """
    root = _node("top", "top")
    buf = io.StringIO()
    json_render.render(root, buf, embed_layout=False)
    payload = json.loads(buf.getvalue())
    assert "layout" not in payload


def test_layout_block_deterministic() -> None:
    """Two renders over the same graph produce identical layout
    bytes — the dot renderer is already golden-stable, so the
    embedded form inherits that property."""
    child = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    a = _render(root)["layout"]["dot"]
    b = _render(root)["layout"]["dot"]
    assert a == b


def test_layout_block_carries_elk_payload_when_module_table_supplied() -> None:
    """``layout.elk`` is the SPA schematic canvas's input (epic #163
    P2): the same payload ``--format elk`` writes, embedded so the
    canvas needs one fetch, not two.

    Additive by design — ``JSON_CONTRACT`` does not pin it and the
    schema's ``additionalProperties: true`` already admits it, so an
    older consumer reading a newer view.json is unaffected.
    """
    child = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    table = ModuleTable(
        modules_by_name={
            "top": Module(
                name="top", location=None, ports=(), parameters=(), instances=()
            ),
            "child": Module(
                name="child", location=None, ports=(), parameters=(), instances=()
            ),
        }
    )
    layout = _render(root, module_table=table)["layout"]
    elk = layout["elk"]
    # The ELK root IS the design top — node ids are instance paths,
    # which is what makes a schematic selection join straight onto
    # ``nodes[].id`` without a lookup table (unlike ``cluster_lookup``).
    assert elk["id"] == "top"
    assert [c["id"] for c in elk["children"]] == ["top.u_a"]
    assert elk["rb"]["export"]["design"]["top"] == "top"
    # The dot half is untouched — this is a second key, not a swap.
    assert layout["engine"] == "dot"
    assert layout["dot"].startswith("digraph hierarchy {")


def test_layout_block_omits_elk_without_a_module_table() -> None:
    """Formal pin names and declared widths live on the
    ``ModuleTable``, not on ``HierNode`` — without one the payload
    would claim a pinout it never saw. Degrade to the dot-only block
    instead of emitting an empty-pinout schematic.
    """
    root = _node("top", "top")
    layout = _render(root)["layout"]
    assert "elk" not in layout


def test_layout_elk_is_deterministic() -> None:
    """Same determinism contract as the dot half: two renders over
    the same graph produce identical bytes."""
    child = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    table = ModuleTable(
        modules_by_name={
            "top": Module(
                name="top", location=None, ports=(), parameters=(), instances=()
            ),
            "child": Module(
                name="child", location=None, ports=(), parameters=(), instances=()
            ),
        }
    )
    a = json.dumps(_render(root, module_table=table)["layout"]["elk"], sort_keys=True)
    b = json.dumps(_render(root, module_table=table)["layout"]["elk"], sort_keys=True)
    assert a == b


def test_layout_block_carries_no_per_node_fillcolor_when_overlays_supplied() -> None:
    """The embedded DOT is structure-only; per-node clock/reset
    fills come from ``nodes[].overlays`` so the SPA can toggle them
    on and off. If we bake palette colors into the DOT, the
    polygon's ``fill=`` attribute survives the SPA's overlay-clear
    (which only touches inline ``style.fill``) and the user sees
    colors persist after unchecking the overlay (rtl-buddy-view live
    demo, 2026-05-20).
    """
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    cm = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        flop_domains=(
            FlopDomain(instance_path="top.u_dst", clock="clk_a", location=None),
        ),
        crossings=(),
    )
    payload = _render(root, domain_map=cm)
    dot = payload["layout"]["dot"]
    # No per-node fillcolor — the global ``node []`` statement still
    # sets the default neutral fill, but individual nodes must not
    # override it with palette swatches.
    lines = [
        line
        for line in dot.splitlines()
        if "fillcolor=" in line and not line.lstrip().startswith("node [")
    ]
    assert lines == [], f"unexpected per-node fillcolor lines: {lines}"
    # The per-node clock label tag (e.g. ``[clk_a]``) must also be
    # absent — it duplicates what the overlay surfaces and bakes a
    # clock name into the layout where it can't be cleared.
    assert "[clk_a]" not in dot


def test_combined_overlay_payload_validates_against_v1_schema(
    view_schema: dict,
) -> None:
    """A payload with both clock + reset overlays + crossings on
    both still satisfies the schema's per-node ``overlays`` and
    ``overlays_present`` shapes."""
    from rtl_buddy_view.reset_annotations import (
        FlopReset,
        ResetCrossing,
        ResetDomainMap,
    )

    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    cm = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        flop_domains=(
            FlopDomain(instance_path="top.u_dst", clock="clk_b", location=None),
        ),
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_dst",
                min_hops=0,
                width=1,
                async_per_sdc=True,
                src_flop="top.src",
            ),
        ),
    )
    rm = ResetDomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        flop_resets=(
            FlopReset(
                instance_path="top.u_dst",
                clock="clk_b",
                reset="rst_n",
                reset_kind="port",
                polarity="low",
                type="async",
                location=None,
            ),
        ),
        reset_crossings=(
            ResetCrossing(
                instance_path="top.u_dst",
                kind="async-deassert",
                flop_clock="clk_b",
                reset="rst_n",
                reset_kind="port",
                polarity="low",
                type="async",
                location=None,
            ),
        ),
    )
    payload = _render(root, domain_map=cm, reset_map=rm)
    jsonschema.validate(instance=payload, schema=view_schema)
    assert payload["overlays_present"] == ["clock", "reset"]
    dst_node = next(n for n in payload["nodes"] if n["id"] == "top.u_dst")
    assert dst_node["overlays"]["clock"] == {"clock": "clk_b"}
    assert dst_node["overlays"]["reset"]["reset"] == "rst_n"
    assert dst_node["overlays"]["reset"]["polarity"] == "low"


# --- axi_perf source metadata (Phase 2.5 of the marimo umbrella) -----------


def test_axi_perf_block_absent_by_default() -> None:
    """No ``axi_perf`` overlay supplied → no top-level ``axi_perf``
    block. The SPA's "Open in marimo" button falls back to prompts."""
    root = _node("top", "top")
    payload = _render(root)
    assert "axi_perf" not in payload


def test_axi_perf_block_carries_source_and_derived_test_when_canonical(
    tmp_path: Path,
) -> None:
    """Canonical layout ``<suite>/artefacts/axi/<test>/axi-perf.json``
    → the SPA gets ``test`` + ``suite_dir`` for free and can skip
    prompts entirely."""
    src = (
        tmp_path
        / "verif"
        / "demo_axi_2x2"
        / "artefacts"
        / "axi"
        / "basic_traffic"
        / "axi-perf.json"
    )
    src.parent.mkdir(parents=True)
    src.write_text("{}")  # contents don't matter for the source-block test

    root = _node("top", "top")
    payload = _render(root, axi_perf_source=src)

    block = payload["axi_perf"]
    assert block["source"] == str(src.resolve())
    assert block["test"] == "basic_traffic"
    assert block["suite_dir"] == str((tmp_path / "verif" / "demo_axi_2x2").resolve())


def test_axi_perf_block_omits_derived_keys_when_layout_nonstandard(
    tmp_path: Path,
) -> None:
    """Non-canonical layout (e.g. user pointed at ``perf.json`` in a
    random directory) → only ``source`` is emitted; SPA falls back to
    prompts so it doesn't act on a wrong derivation."""
    src = tmp_path / "elsewhere" / "perf.json"
    src.parent.mkdir(parents=True)
    src.write_text("{}")

    root = _node("top", "top")
    payload = _render(root, axi_perf_source=src)

    block = payload["axi_perf"]
    assert block["source"] == str(src.resolve())
    assert "test" not in block
    assert "suite_dir" not in block
