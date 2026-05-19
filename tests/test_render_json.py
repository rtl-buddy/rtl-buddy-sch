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
    assert payload["schema_version"] == "1.0"
    assert payload["top"] == "top"
    assert payload["tool"]["name"] == "rtl-buddy-view"
    assert isinstance(payload["tool"]["version"], str)
    assert payload["nodes"]
    assert payload["edges"] == []
    assert payload["overlays_present"] == []


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
    assert edge["overlays"]["clock"] == {"crossing": True}


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
