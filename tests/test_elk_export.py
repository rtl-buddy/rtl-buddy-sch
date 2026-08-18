"""Tests for the ELK schematic payload (``--format elk``, epic #163 P1).

``test_elk_contract_keys_present_and_typed`` is the tripwire — P2's
elkjs canvas keys on every id and ``rb`` field pinned in
:data:`rtl_buddy_view.elk_export.ELK_CONTRACT`, the same way rtl_buddy
keys on ``GRAPH_CONTRACT``. The shape tests build a synthetic
:class:`ModuleTable` so they run without Verible; one fixture test
covers the end-to-end CLI path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rtl_buddy_view import elk_export
from rtl_buddy_view.annotations import Clock, DomainMap, FlopDomain
from rtl_buddy_view.cli import app
from rtl_buddy_view.extractor import (
    Instance,
    Interface,
    Module,
    ModuleTable,
    Parameter,
    ParameterOverride,
    Port,
    PortConnection,
)
from rtl_buddy_view.graph import build_hierarchy

FIXTURES_ROOT = Path(__file__).parent / "fixtures"


def _require_verible() -> None:
    from rtl_buddy_view._verible_install import find_binary

    if find_binary("verible-verilog-syntax") is None:
        pytest.skip("verible binary not on PATH / vendor/")


# --- synthetic design -------------------------------------------------------
#
#   top
#   ├── u_src  : src          drives w (8b) and v (1b); `en` left dangling
#   ├── u_sink : sink         one net in  → a single-pin edge
#   ├── u_dual : dual         two nets in → a multi-pin (node-level) edge
#   └── u_bb   : missing_bb   blackbox: pinout known only from the binding


def _port(
    name: str, direction: str | None, type_text: str | None, **kw: object
) -> Port:
    return Port(
        name=name,
        direction=direction,  # type: ignore[arg-type]
        type_text=type_text,
        location=None,
        **kw,  # type: ignore[arg-type]
    )


def _conn(port_name: str | None, net: str = "") -> PortConnection:
    return PortConnection(port_name=port_name, net_expr_text=net, location=None)


def _synthetic_table() -> ModuleTable:
    src = Module(
        name="src",
        ports=(
            _port("clk", "input", "logic"),
            _port("d", "input", "logic [7:0]"),
            _port("q", "output", "logic [7:0]"),
            _port("q2", "output", "logic"),
            _port("en", "input", "logic"),
        ),
        parameters=(
            Parameter("WIDTH", "8", None),
            Parameter("DEPTH", "4", None),
        ),
        instances=(),
        location=None,
    )
    sink = Module(
        name="sink",
        ports=(
            _port("clk", "input", "logic"),
            _port("a", "input", "logic [7:0]"),
            _port("y", "output", "logic [7:0]"),
        ),
        parameters=(),
        instances=(),
        location=None,
    )
    dual = Module(
        name="dual",
        ports=(
            _port("x", "input", "logic [7:0]"),
            _port("z", "input", "logic"),
        ),
        parameters=(),
        instances=(),
        location=None,
    )
    top = Module(
        name="top",
        ports=(
            _port("clk", "input", "logic"),
            _port("rst_n", "input", "logic"),
            _port("din", "input", "logic [7:0]"),
            _port("dout", "output", "logic [7:0]"),
            _port("bus", None, None, port_kind="interface", interface_type="apb_intf"),
        ),
        parameters=(),
        instances=(
            Instance(
                name="u_src",
                module_name="src",
                param_overrides=(
                    ParameterOverride("WIDTH", "8", None),
                    ParameterOverride(None, "16", None),
                ),
                port_connections=(
                    _conn("clk", "clk"),
                    _conn("d", "din"),
                    _conn("q", "w"),
                    _conn("q2", "v"),
                ),
                location=None,
            ),
            Instance(
                name="u_sink",
                module_name="sink",
                param_overrides=(),
                port_connections=(
                    _conn("clk", "clk"),
                    _conn("a", "w"),
                    _conn("y", "dout"),
                ),
                location=None,
            ),
            Instance(
                name="u_dual",
                module_name="dual",
                param_overrides=(),
                port_connections=(_conn("x", "w"), _conn("z", "v")),
                location=None,
            ),
            Instance(
                name="u_bb",
                module_name="missing_bb",
                param_overrides=(),
                port_connections=(_conn("probe", "w"),),
                location=None,
            ),
        ),
        location=None,
    )
    return ModuleTable(
        modules_by_name={m.name: m for m in (top, src, sink, dual)},
    )


@pytest.fixture
def payload() -> dict:
    table = _synthetic_table()
    return elk_export.export_elk(build_hierarchy(table, "top"), table)


def _by_id(payload: dict) -> dict[str, dict]:
    out = {payload["id"]: payload}
    for child in payload["children"]:
        out.update(_by_id(child))
    return out


def _edges_by_id(payload: dict) -> dict[str, dict]:
    out = {edge["id"]: edge for edge in payload["edges"]}
    for child in payload["children"]:
        out.update(_edges_by_id(child))
    return out


# --- the contract -----------------------------------------------------------


def test_elk_contract_keys_present_and_typed(payload: dict) -> None:
    """Every pinned key exists with the pinned type.

    P2's canvas reads these ids and ``rb`` fields directly, so a
    rename here is a breaking change for the SPA — this test is the
    tripwire and ``docs/elk-json-v1.md`` is its prose form.
    """
    for dotted, expected in elk_export.ELK_CONTRACT.items():
        cursor: object = payload
        for part in dotted.split("."):
            assert isinstance(cursor, dict), f"{dotted}: {part} not under a dict"
            assert part in cursor, f"contract key missing: {dotted}"
            cursor = cursor[part]
        assert isinstance(cursor, expected), (
            f"{dotted}: expected {expected}, got {type(cursor)}"
        )

    nodes = _by_id(payload)
    assert len(nodes) > 1, "contract test needs a non-empty hierarchy"
    for node in nodes.values():
        for key, expected in elk_export.NODE_CONTRACT.items():
            assert key in node, f"node {node.get('id')} missing {key}"
            assert isinstance(node[key], expected), f"node {node['id']}.{key}"
        for key, expected in elk_export.NODE_RB_CONTRACT.items():
            assert key in node["rb"], f"node {node['id']} rb missing {key}"
            assert isinstance(node["rb"][key], expected), f"node {node['id']}.rb.{key}"
        assert node["ports"], f"node {node['id']} has no ports"
        for port in node["ports"]:
            for key, expected in elk_export.PORT_CONTRACT.items():
                assert key in port and isinstance(port[key], expected)
            for key, expected in elk_export.PORT_RB_CONTRACT.items():
                assert key in port["rb"], f"port {port['id']} rb missing {key}"
                assert isinstance(port["rb"][key], expected), f"{port['id']}.rb.{key}"

    edges = _edges_by_id(payload)
    assert edges, "contract test needs a non-empty edge set"
    for edge in edges.values():
        for key, expected in elk_export.EDGE_CONTRACT.items():
            assert key in edge and isinstance(edge[key], expected)
        for key, expected in elk_export.EDGE_RB_CONTRACT.items():
            assert key in edge["rb"], f"edge {edge['id']} rb missing {key}"
            assert isinstance(edge["rb"][key], expected), f"{edge['id']}.rb.{key}"


def test_export_is_json_serialisable(payload: dict) -> None:
    """No tuples, no dataclasses, no sets leak into the payload."""
    json.dumps(payload)


def test_payload_carries_no_layout_options(payload: dict) -> None:
    """Side/size/algorithm choices belong to the consumer, not to us.

    Baking ``elk.port.side: WEST`` here would freeze a presentation
    decision into an analyzer artifact — and the consumer is the only
    party that knows the font it measures labels in.
    """
    blob = json.dumps(payload)
    assert "layoutOptions" not in blob
    assert "elk." not in blob
    for side in ("WEST", "EAST", "NORTH", "SOUTH"):
        assert side not in blob


# --- nodes ------------------------------------------------------------------


def test_node_ids_are_instance_paths(payload: dict) -> None:
    assert set(_by_id(payload)) == {
        "top",
        "top.u_bb",
        "top.u_dual",
        "top.u_sink",
        "top.u_src",
    }


def test_children_are_sorted_by_instance_path(payload: dict) -> None:
    paths = [child["id"] for child in payload["children"]]
    assert paths == sorted(paths)


def test_root_node_carries_the_design_provenance(payload: dict) -> None:
    export = payload["rb"]["export"]
    assert export["schema_version"] == elk_export.SCHEMA_VERSION
    assert export["generator"]["tool"] == "rtl-buddy-view"
    assert export["design"]["top"] == "top"
    # ...and only the root: every other node is a plain node object.
    assert all("export" not in n["rb"] for n in payload["children"])


def test_param_overrides_are_name_value_pairs(payload: dict) -> None:
    """Positional overrides resolve through the child's declaration."""
    assert _by_id(payload)["top.u_src"]["rb"]["param_overrides"] == [
        ["WIDTH", "8"],
        ["DEPTH", "16"],
    ]


def test_blackbox_is_flagged_and_keeps_its_observed_pinout(payload: dict) -> None:
    node = _by_id(payload)["top.u_bb"]
    assert node["rb"]["is_blackbox"] is True
    assert node["rb"]["module_name"] == "missing_bb"
    (probe,) = node["ports"]
    assert probe["id"] == "top.u_bb:probe"
    # Nothing was declared, so nothing is claimed beyond the binding.
    assert probe["rb"]["direction"] is None
    assert probe["rb"]["width"] is None
    assert probe["rb"]["connected"] is True


# --- ports ------------------------------------------------------------------


def test_all_declared_ports_are_exported_in_declaration_order(payload: dict) -> None:
    node = _by_id(payload)["top.u_src"]
    assert [p["rb"]["name"] for p in node["ports"]] == [
        "clk",
        "d",
        "q",
        "q2",
        "en",
    ]


def test_unconnected_ports_are_kept_and_flagged(payload: dict) -> None:
    """An unbound ``en`` pin is information, not noise."""
    ports = {p["rb"]["name"]: p["rb"] for p in _by_id(payload)["top.u_src"]["ports"]}
    assert ports["en"]["connected"] is False
    assert ports["d"]["connected"] is True


def test_clock_and_reset_pins_are_marked(payload: dict) -> None:
    """Clock *routing* is filtered out of the edges; the pin remains."""
    ports = {p["rb"]["name"]: p["rb"] for p in payload["ports"]}
    assert ports["clk"]["is_clock"] is True
    assert ports["clk"]["is_reset"] is False
    assert ports["rst_n"]["is_reset"] is True
    assert ports["din"]["is_clock"] is False


def test_port_widths_come_from_the_declared_type(payload: dict) -> None:
    ports = {p["rb"]["name"]: p["rb"] for p in payload["ports"]}
    assert ports["din"]["width"] == 8
    assert ports["clk"]["width"] == 1
    # An interface bundle is a set of signals, not a 1-bit port.
    assert ports["bus"]["width"] is None
    assert ports["bus"]["direction"] is None


def test_root_ports_are_the_sheet_boundary(payload: dict) -> None:
    """The top has no binding site, so its pins are connected by
    definition — P2 draws them as off-page connector flags."""
    assert [p["id"] for p in payload["ports"]] == [
        "top:clk",
        "top:rst_n",
        "top:din",
        "top:dout",
        "top:bus",
    ]
    assert all(p["rb"]["connected"] for p in payload["ports"])


# --- edges ------------------------------------------------------------------


def test_a_single_pin_edge_references_port_ids(payload: dict) -> None:
    edge = _edges_by_id(payload)["top.u_src:q->top.u_sink:a"]
    assert edge["sources"] == ["top.u_src:q"]
    assert edge["targets"] == ["top.u_sink:a"]
    assert edge["rb"] == {
        "nets": ["w"],
        "bits": 8,
        "src_pins": ["q"],
        "dst_pins": ["a"],
    }


def test_a_multi_pin_bundle_references_node_ids(payload: dict) -> None:
    """Two pins, one bundle: there is no single pin to land on.

    The pin names survive in ``rb`` so the consumer can still label
    the wire; only the *attachment point* degrades to the box.
    """
    edge = _edges_by_id(payload)["top.u_src->top.u_dual"]
    assert edge["sources"] == ["top.u_src"]
    assert edge["targets"] == ["top.u_dual"]
    assert edge["rb"]["src_pins"] == ["q", "q2"]
    assert edge["rb"]["dst_pins"] == ["x", "z"]
    # 8-bit ``w`` + 1-bit ``v``.
    assert edge["rb"]["bits"] == 9


def test_scope_port_endpoints_reference_the_scopes_own_port(payload: dict) -> None:
    edge = _edges_by_id(payload)["top:din->top.u_src:d"]
    assert edge["sources"] == ["top:din"]
    assert _edges_by_id(payload)["top.u_sink:y->top:dout"]["targets"] == ["top:dout"]


def test_an_edge_onto_a_blackbox_still_resolves_its_pin(payload: dict) -> None:
    edge = _edges_by_id(payload)["top.u_src:q->top.u_bb:probe"]
    assert edge["targets"] == ["top.u_bb:probe"]
    assert edge["rb"]["dst_pins"] == ["probe"]


def test_every_edge_endpoint_resolves_to_something_in_the_payload(
    payload: dict,
) -> None:
    """A dangling reference makes elkjs throw, so it must be impossible."""
    known = set(_by_id(payload))
    for node in _by_id(payload).values():
        known.update(port["id"] for port in node["ports"])
    for edge in _edges_by_id(payload).values():
        for ref in edge["sources"] + edge["targets"]:
            assert ref in known, f"dangling endpoint {ref} on {edge['id']}"


def test_leaf_nodes_carry_empty_children_and_edges(payload: dict) -> None:
    leaf = _by_id(payload)["top.u_sink"]
    assert leaf["children"] == []
    assert leaf["edges"] == []


def test_edges_are_sorted_by_id(payload: dict) -> None:
    ids = [edge["id"] for edge in payload["edges"]]
    assert ids == sorted(ids)


# --- determinism + overlays -------------------------------------------------


def test_two_runs_are_byte_identical() -> None:
    """Set iteration must never reach the emitted bytes."""
    first_table = _synthetic_table()
    second_table = _synthetic_table()
    first = elk_export.export_elk(
        build_hierarchy(first_table, "top"), first_table, tool_version="1.2.3"
    )
    second = elk_export.export_elk(
        build_hierarchy(second_table, "top"), second_table, tool_version="1.2.3"
    )
    assert json.dumps(first, indent=2) == json.dumps(second, indent=2)


def test_clock_is_null_without_an_overlay(payload: dict) -> None:
    assert all(node["rb"]["clock"] is None for node in _by_id(payload).values())


def test_an_empty_domain_map_degrades_to_the_unannotated_payload(
    payload: dict,
) -> None:
    """The graceful-degradation contract: no SDC, no annotation."""
    table = _synthetic_table()
    empty = DomainMap(
        schema_version="1.0",
        generator_name="rtl-buddy-cdc",
        generator_version="0.0.0",
        design_top="top",
        design_frontend="verible",
    )
    annotated = elk_export.export_elk(
        build_hierarchy(table, "top"), table, domain_map=empty
    )
    assert annotated == payload


def test_a_clock_overlay_lands_on_the_node() -> None:
    table = _synthetic_table()
    domain_map = DomainMap(
        schema_version="1.0",
        generator_name="rtl-buddy-cdc",
        generator_version="0.0.0",
        design_top="top",
        design_frontend="verible",
        clocks=(Clock(name="clk", period=10.0, source="clk", ports=("clk",)),),
        flop_domains=(
            FlopDomain(instance_path="top.u_sink", clock="clk", location=None),
        ),
    )
    annotated = elk_export.export_elk(
        build_hierarchy(table, "top"), table, domain_map=domain_map
    )
    assert _by_id(annotated)["top.u_sink"]["rb"]["clock"] == "clk"


# --- CLI --------------------------------------------------------------------


def test_elk_is_offered_on_the_format_flag() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "elk" in result.output


def test_cli_renders_elk_for_the_block_diagram_fixture(tmp_path: Path) -> None:
    _require_verible()
    out = tmp_path / "elk.json"
    result = CliRunner().invoke(
        app,
        [
            "--top",
            "blk_top",
            "--filelist",
            str(FIXTURES_ROOT / "block_diagram_demo" / "files.f"),
            "--format",
            "elk",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())

    assert payload["id"] == "blk_top"
    nodes = _by_id(payload)
    # Nesting survives: the producer is a compound node with its own
    # scope-local edges, not a flattened leaf.
    assert "blk_top.u_prod.u_stage" in nodes
    assert nodes["blk_top.u_prod"]["edges"]

    ports = {p["rb"]["name"]: p["rb"] for p in payload["ports"]}
    assert ports["clk"]["is_clock"] is True
    assert ports["cmd_in"]["width"] == 16

    # At least one edge landed on a named pin rather than a box.
    assert any(
        ":" in ref
        for edge in _edges_by_id(payload).values()
        for ref in edge["sources"] + edge["targets"]
    )


def test_cli_elk_output_is_stable_across_two_runs(tmp_path: Path) -> None:
    _require_verible()
    outputs = []
    for name in ("first.json", "second.json"):
        out = tmp_path / name
        result = CliRunner().invoke(
            app,
            [
                "--top",
                "blk_top",
                "--filelist",
                str(FIXTURES_ROOT / "block_diagram_demo" / "files.f"),
                "--format",
                "elk",
                "--output",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1]


# --- binding-site corner cases ----------------------------------------------


def _positional_table() -> ModuleTable:
    """A design whose bindings and overrides are all positional.

    Also carries an interface *instance* (``test_if u_if()``), which
    SystemVerilog spells with module-instance syntax — the parameter
    lookup has to find its declaration under ``interfaces_by_name``.
    """
    leaf = Module(
        name="leaf",
        ports=(_port("a", "input", "logic"), _port("b", "output", "logic")),
        parameters=(Parameter("W", "1", None),),
        instances=(),
        location=None,
    )
    top = Module(
        name="top",
        ports=(_port("din", "input", "logic"),),
        parameters=(),
        instances=(
            Instance(
                name="u_leaf",
                module_name="leaf",
                param_overrides=(ParameterOverride(None, "4", None),),
                port_connections=(_conn(None, "din"), _conn(None, "w")),
                location=None,
            ),
            Instance(
                name="u_if",
                module_name="test_if",
                param_overrides=(ParameterOverride(None, "8", None),),
                port_connections=(),
                location=None,
            ),
        ),
        location=None,
    )
    table = ModuleTable(modules_by_name={m.name: m for m in (top, leaf)})
    table.interfaces_by_name["test_if"] = Interface(
        name="test_if",
        parameters=(Parameter("AW", "32", None),),
        signals=(),
        modports=(),
        location=None,
    )
    return table


def test_positional_connections_still_mark_a_port_connected() -> None:
    """Connectivity won't infer *dataflow* from port order; "this pin
    is bound" is a cheap enough claim to make from an index."""
    table = _positional_table()
    payload = elk_export.export_elk(build_hierarchy(table, "top"), table)
    ports = {p["rb"]["name"]: p["rb"] for p in _by_id(payload)["top.u_leaf"]["ports"]}
    assert ports["a"]["connected"] is True
    assert ports["b"]["connected"] is True


def test_positional_overrides_resolve_through_the_declaration() -> None:
    table = _positional_table()
    payload = elk_export.export_elk(build_hierarchy(table, "top"), table)
    nodes = _by_id(payload)
    assert nodes["top.u_leaf"]["rb"]["param_overrides"] == [["W", "4"]]
    # The interface's own parameter list is the declaration here.
    assert nodes["top.u_if"]["rb"]["param_overrides"] == [["AW", "8"]]
