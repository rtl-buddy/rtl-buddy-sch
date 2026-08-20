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
    Assign,
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


def test_tokenless_clock_and_reset_pins_are_marked() -> None:
    """The pin flag must agree with the widened edge-filter
    classification: ``wclk`` routing is suppressed as a clock tree, so
    its pin carries the chevron too — while domain-suffixed data pins
    (``src_sel_cclk``) stay unflagged."""
    from rtl_buddy_view.elk_export import _port_entry

    def rb(name: str) -> dict:
        return _port_entry("top.u_x", name, "input", 1, connected=True)["rb"]

    assert rb("wclk")["is_clock"] is True
    assert rb("cclk")["is_clock"] is True
    assert rb("crst_n")["is_reset"] is True
    assert rb("aresetn")["is_reset"] is True
    assert rb("src_sel_cclk")["is_clock"] is False
    assert rb("burst")["is_reset"] is False


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
        "bits_expr": None,
        "src_pins": ["q"],
        "dst_pins": ["a"],
        "emphasis": None,
        "bundle": None,
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


# --- algebraic widths -------------------------------------------------------
#
# The shape the demo design has: a synchroniser whose pins are typed
# ``[WIDTH-1:0]`` and bound with ``#(.WIDTH(PTR_W))``, next to one
# bound with a literal. The first is knowable only in symbols, the
# second is knowable as a number.


def _param_width_table() -> ModuleTable:
    sync = Module(
        name="ip_cdc_sync",
        ports=(
            _port("d", "input", "logic [WIDTH-1:0]"),
            _port("q", "output", "logic [WIDTH-1:0]"),
        ),
        parameters=(Parameter("WIDTH", "1", None),),
        instances=(),
        location=None,
    )
    sink = Module(
        name="sink",
        ports=(
            _port("a", "input", None),
            _port("b", "input", None),
            # Literally declared, for the "a number is not algebra" case.
            _port("c", "input", "logic [7:0]"),
        ),
        parameters=(),
        instances=(),
        location=None,
    )
    top = Module(
        name="top",
        ports=(_port("din", "input", "logic"),),
        parameters=(),
        instances=(
            Instance(
                name="u_sym",
                module_name="ip_cdc_sync",
                param_overrides=(ParameterOverride("WIDTH", "PTR_W", None),),
                port_connections=(_conn("d", "din"), _conn("q", "gray")),
                location=None,
            ),
            Instance(
                name="u_num",
                module_name="ip_cdc_sync",
                param_overrides=(ParameterOverride("WIDTH", "19", None),),
                port_connections=(_conn("d", "din"), _conn("q", "payload")),
                location=None,
            ),
            Instance(
                name="u_sink",
                module_name="sink",
                param_overrides=(),
                port_connections=(_conn("a", "gray"), _conn("b", "payload")),
                location=None,
            ),
        ),
        location=None,
    )
    return ModuleTable(modules_by_name={m.name: m for m in (top, sync, sink)})


@pytest.fixture
def param_payload() -> dict:
    table = _param_width_table()
    return elk_export.export_elk(build_hierarchy(table, "top"), table)


def test_a_literal_override_carries_both_the_number_and_the_name(
    param_payload: dict,
) -> None:
    """19 bits *and* ``WIDTH``-wide — neither answer suppresses the other."""
    ports = {
        p["rb"]["name"]: p["rb"] for p in _by_id(param_payload)["top.u_num"]["ports"]
    }
    assert ports["q"]["width"] == 19
    assert ports["q"]["width_expr"] == "WIDTH"


def test_a_symbolic_override_leaves_only_the_name(param_payload: dict) -> None:
    """``#(.WIDTH(PTR_W))``: no integer exists, so only the name does."""
    ports = {
        p["rb"]["name"]: p["rb"] for p in _by_id(param_payload)["top.u_sym"]["ports"]
    }
    assert ports["q"]["width"] is None
    assert ports["q"]["width_expr"] == "WIDTH"
    assert ports["d"]["width_expr"] == "WIDTH"


def test_a_literally_declared_pin_has_no_expression(param_payload: dict) -> None:
    """``[7:0]`` is a number, and a number is not algebra."""
    ports = {
        p["rb"]["name"]: p["rb"] for p in _by_id(param_payload)["top.u_sink"]["ports"]
    }
    assert ports["c"]["width"] == 8
    assert ports["c"]["width_expr"] is None


def test_edges_carry_the_name_whether_or_not_a_number_resolved(
    param_payload: dict,
) -> None:
    edges = _edges_by_id(param_payload)
    symbolic = edges["top.u_sym:q->top.u_sink:a"]["rb"]
    assert (symbolic["bits"], symbolic["bits_expr"]) == (None, "WIDTH")
    both = edges["top.u_num:q->top.u_sink:b"]["rb"]
    assert (both["bits"], both["bits_expr"]) == (19, "WIDTH")


def test_bits_stays_numeric_only_across_the_whole_payload(
    param_payload: dict, payload: dict
) -> None:
    """``bits`` never becomes a string; the two are independent."""
    both_seen = False
    for candidate in (param_payload, payload):
        for edge in _edges_by_id(candidate).values():
            bits, expr = edge["rb"]["bits"], edge["rb"]["bits_expr"]
            assert bits is None or isinstance(bits, int)
            assert expr is None or isinstance(expr, str)
            both_seen = both_seen or (bits is not None and expr is not None)
        for node in _by_id(candidate).values():
            for port in node["ports"]:
                width, expr = port["rb"]["width"], port["rb"]["width_expr"]
                assert width is None or isinstance(width, int)
                assert expr is None or isinstance(expr, str)
    # And the coexistence is real, not merely permitted.
    assert both_seen


# --- nested self-port feed-throughs -----------------------------------------


def _feedthrough_table() -> ModuleTable:
    """``top.u_mid`` whose input reaches its output through an assign.

    Inside ``mid`` the only port-to-port dataflow is
    ``assign dout = din;`` — a pure feed-through with no instance to
    land a wire on. ``mid`` also instantiates a leaf so it is a
    compound whose scope edges actually get emitted.
    """
    leaf = Module(
        name="leaf",
        ports=(_port("p", "input", "logic"),),
        parameters=(),
        instances=(),
        location=None,
    )
    mid = Module(
        name="mid",
        ports=(
            _port("din", "input", "logic"),
            _port("dout", "output", "logic"),
        ),
        parameters=(),
        instances=(
            Instance(
                name="u_leaf",
                module_name="leaf",
                param_overrides=(),
                port_connections=(_conn("p", "din"),),
                location=None,
            ),
        ),
        location=None,
        assigns=(Assign(lhs_text="dout", rhs_text="din", location=None),),
    )
    top = Module(
        name="top",
        ports=(_port("ti", "input", "logic"), _port("to", "output", "logic")),
        parameters=(),
        instances=(
            Instance(
                name="u_mid",
                module_name="mid",
                param_overrides=(),
                port_connections=(_conn("din", "ti"), _conn("dout", "to")),
                location=None,
            ),
        ),
        location=None,
    )
    return ModuleTable(modules_by_name={"top": top, "mid": mid, "leaf": leaf})


def test_nested_self_port_feedthrough_edges_are_dropped() -> None:
    """ELK cannot route an edge whose both ends are the laid-out
    node's own ports — elkjs emits null coordinates, which drew stray
    lines outside the sheet. The exporter keeps them out."""
    table = _feedthrough_table()
    root = build_hierarchy(table, "top")
    payload = elk_export.export_elk(root, table)
    mid = payload["children"][0]
    own_port = mid["id"] + ":"
    for edge in mid["edges"]:
        assert not (
            edge["sources"][0].startswith(own_port)
            and edge["targets"][0].startswith(own_port)
        ), f"self-port edge leaked: {edge['id']}"
    # The child edge (din -> u_leaf:p) survives — only the pure
    # port-to-port feed-through is undrawable.
    assert any("u_leaf" in e["targets"][0] for e in mid["edges"])


def test_root_port_to_port_edges_are_kept() -> None:
    """At the root the consumer re-points port endpoints at off-page
    flag nodes, which routes fine — a top-level passthrough is a
    legitimate wire between two flags."""
    leaf = Module(
        name="leaf",
        ports=(_port("p", "input", "logic"),),
        parameters=(),
        instances=(),
        location=None,
    )
    top = Module(
        name="top",
        ports=(_port("ti", "input", "logic"), _port("to", "output", "logic")),
        parameters=(),
        instances=(
            Instance(
                name="u_leaf",
                module_name="leaf",
                param_overrides=(),
                port_connections=(_conn("p", "ti"),),
                location=None,
            ),
        ),
        location=None,
        assigns=(Assign(lhs_text="to", rhs_text="ti", location=None),),
    )
    table = ModuleTable(modules_by_name={"top": top, "leaf": leaf})
    root = build_hierarchy(table, "top")
    payload = elk_export.export_elk(root, table)
    pairs = [(e["sources"][0], e["targets"][0]) for e in payload["edges"]]
    assert ("top:ti", "top:to") in pairs


# --- rb.net: the binding a pin carries (#184) --------------------------------


def _ports_by_name(payload: dict, instance_path: str) -> dict:
    child = next(c for c in payload["children"] if c["id"] == instance_path)
    return {p["rb"]["name"]: p["rb"] for p in child["ports"]}


def test_a_pin_carries_the_net_bound_at_the_binding_site(payload: dict) -> None:
    """A pin whose net no sibling pin drives — because the parent's
    own ``always_ff`` drives it — has no edge to attach to and
    renders as a bare stub. The net name is what keeps it traceable,
    and it is read off the binding site rather than inferred."""
    ports = _ports_by_name(payload, "top.u_src")
    assert ports["d"]["net"] == "din"
    assert ports["q"]["net"] == "w"


def test_an_unbound_pin_has_no_net(payload: dict) -> None:
    """``en`` is left dangling at the binding site: absent, not
    bound-to-nothing."""
    ports = _ports_by_name(payload, "top.u_src")
    assert ports["en"]["net"] is None
    assert ports["en"]["connected"] is False


def test_root_ports_bind_their_own_name(payload: dict) -> None:
    """The root is the sheet boundary: its pins *are* the nets."""
    root = {p["rb"]["name"]: p["rb"] for p in payload["ports"]}
    assert root["din"]["net"] == "din"
