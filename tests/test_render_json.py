"""Tests for the JSON renderer.

The JSON output is what ``rtl_buddy`` will consume via ``rb hier`` —
the keys in :data:`JSON_CONTRACT` are the cross-repo coupling
surface. The contract test below is the one that fails CI when
someone accidentally renames or retypes a public key.
"""

from __future__ import annotations

import io
import json

from rtl_buddy_view.annotations import (
    Clock,
    Crossing,
    DomainMap,
    FlopDomain,
)
from rtl_buddy_view.extractor import Instance, ParameterOverride, PortConnection
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.render import json_render
from rtl_buddy_view.render.json_render import JSON_CONTRACT


def _node(
    path: str,
    module: str,
    *,
    inst_name: str | None = None,
    is_blackbox: bool = False,
    children: tuple[HierNode, ...] = (),
    overrides: tuple[ParameterOverride, ...] = (),
    connections: tuple[PortConnection, ...] = (),
) -> HierNode:
    inst = (
        Instance(
            name=inst_name,
            module_name=module,
            param_overrides=overrides,
            port_connections=connections,
            location=None,
        )
        if inst_name is not None
        else None
    )
    return HierNode(
        instance_path=path,
        module_name=module,
        instance=inst,
        module=None,
        is_blackbox=is_blackbox,
        children=children,
    )


def _render(root: HierNode, **kwargs) -> dict:
    buf = io.StringIO()
    json_render.render(root, buf, **kwargs)
    return json.loads(buf.getvalue())


# --- contract --------------------------------------------------------------


def test_json_contract_keys_present_and_typed() -> None:
    """Every contract key must exist with the documented type.

    This is the cross-repo coupling guard — rtl_buddy parses these
    keys; renaming or retyping any of them is a downstream-breaking
    change. New optional keys can be added freely.
    """
    root = _node("top", "top")
    payload = _render(root)
    for dotted, expected_type in JSON_CONTRACT.items():
        value = payload
        for segment in dotted.split("."):
            assert segment in value, f"missing contract key: {dotted}"
            value = value[segment]
        assert isinstance(value, expected_type), (
            f"contract key {dotted} has wrong type: "
            f"expected {expected_type.__name__}, got {type(value).__name__}"
        )


def test_output_is_deterministic() -> None:
    """Two renders over the same graph must produce identical bytes.

    Catches accidental dict-ordering or set-ordering leaks into the
    payload — golden tests downstream rely on this.
    """
    child = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    buf_a, buf_b = io.StringIO(), io.StringIO()
    json_render.render(root, buf_a)
    json_render.render(root, buf_b)
    assert buf_a.getvalue() == buf_b.getvalue()


# --- shape -----------------------------------------------------------------


def test_nodes_sorted_by_instance_path() -> None:
    inner = _node("top.u_a.u_z", "leaf", inst_name="u_z")
    outer = _node("top.u_a", "mid", inst_name="u_a", children=(inner,))
    root = _node("top", "top", children=(outer,))
    payload = _render(root)
    paths = [n["instance_path"] for n in payload["nodes"]]
    assert paths == sorted(paths)
    assert paths == ["top", "top.u_a", "top.u_a.u_z"]


def test_edges_record_parent_child_pairs() -> None:
    inner = _node("top.u_a.u_z", "leaf", inst_name="u_z")
    outer = _node("top.u_a", "mid", inst_name="u_a", children=(inner,))
    root = _node("top", "top", children=(outer,))
    payload = _render(root)
    assert payload["edges"] == [
        {"parent": "top", "child": "top.u_a"},
        {"parent": "top.u_a", "child": "top.u_a.u_z"},
    ]


def test_node_carries_param_overrides_and_port_connections() -> None:
    inst = Instance(
        name="u_x",
        module_name="ff",
        param_overrides=(
            ParameterOverride(param_name="W", value_text="16", location=None),
        ),
        port_connections=(
            PortConnection(port_name="clk", net_expr_text="clk_a", location=None),
        ),
        location=None,
    )
    child = HierNode(
        instance_path="top.u_x",
        module_name="ff",
        instance=inst,
        module=None,
        is_blackbox=True,
        children=(),
    )
    root = _node("top", "top", children=(child,))
    payload = _render(root)
    n = next(n for n in payload["nodes"] if n["instance_path"] == "top.u_x")
    assert n["param_overrides"] == [{"param_name": "W", "value_text": "16"}]
    assert n["port_connections"] == [{"port_name": "clk", "net_expr_text": "clk_a"}]
    assert n["is_blackbox"] is True
    assert n["instance_name"] == "u_x"


def test_no_annotations_yields_null_clock_and_empty_crossings() -> None:
    root = _node("top", "top")
    payload = _render(root)
    n = payload["nodes"][0]
    assert n["clock"] is None
    assert n["crossings_in"] == []


def test_annotations_populate_clock_and_crossings() -> None:
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    m = DomainMap(
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
    payload = _render(root, domain_map=m)
    dst_node = next(n for n in payload["nodes"] if n["instance_path"] == "top.u_dst")
    assert dst_node["clock"] == "clk_b"
    assert dst_node["crossings_in"] == [
        {
            "src_clock": "clk_a",
            "dst_clock": "clk_b",
            "min_hops": 0,
            "width": 1,
            "async_per_sdc": True,
            "src_flop": "top.src",
        }
    ]


def test_empty_map_does_not_populate_clock_keys() -> None:
    root = _node("top", "top")
    empty = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
    )
    payload = _render(root, domain_map=empty)
    assert payload["nodes"][0]["clock"] is None
    assert payload["nodes"][0]["crossings_in"] == []
