"""Tests for the design-tier ``graph.json`` export (#126).

``graph.json`` is a cross-repo contract: rtl_buddy's config tier
(rtl_buddy#376) and binding tier (rtl_buddy#378) merge onto the node
ids emitted here, so a rename ripples through three repos.
``test_graph_contract_keys_present_and_typed`` is the tripwire (the
analogue of the view.json contract pin); the schema test validates
real Verible-parsed designs against ``schemas/graph-v1.json``; the
parity test keeps the graph honest against ``--format json``.

The shape tests build a synthetic :class:`ModuleTable` so they run
without Verible — the fixtures cover the end-to-end path.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from rtl_buddy_view import graph_export
from rtl_buddy_view.cli import app
from rtl_buddy_view.extractor import (
    Instance,
    Interface,
    InterfaceSignal,
    Modport,
    Module,
    ModuleTable,
    Parameter,
    ParameterOverride,
    Port,
    PortConnection,
    SourceLocation,
)
from rtl_buddy_view.graph import build_hierarchy

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "graph-v1.json"
PROJECT_ROOT = Path("/proj")


def _require_verible() -> None:
    from rtl_buddy_view._verible_install import find_binary

    if find_binary("verible-verilog-syntax") is None:
        pytest.skip("verible binary not on PATH / vendor/")


# --- synthetic design -------------------------------------------------------
#
#   top
#   ├── u_child : child       named connections + named override
#   ├── u_pos   : child       positional connections + positional override
#   ├── u_bb    : bb          named connections on a blackbox
#   ├── u_bbpos : bb          positional connections on a blackbox (unresolvable)
#   ├── u_user  : iface_user  module with a test_if.sub interface port
#   └── u_if    : test_if     an interface *instance*


def _loc(file: str, line: int) -> SourceLocation:
    return SourceLocation(file=f"/proj/{file}", start_line=line, start_column=1)


def _synthetic_table() -> ModuleTable:
    child = Module(
        name="child",
        ports=(
            Port("clk", "input", "logic", _loc("child.sv", 3)),
            Port("d", "input", "logic [7:0]", _loc("child.sv", 4)),
            Port("q", "output", "logic [WIDTH-1:0]", _loc("child.sv", 5)),
        ),
        parameters=(
            Parameter("WIDTH", "8", _loc("child.sv", 2)),
            Parameter("DEPTH", "16", _loc("child.sv", 2)),
        ),
        instances=(),
        location=_loc("child.sv", 1),
        leading_doc="// a child",
    )
    iface_user = Module(
        name="iface_user",
        ports=(
            Port(
                "m",
                None,
                None,
                _loc("iface_user.sv", 3),
                port_kind="interface",
                interface_type="test_if",
                modport="sub",
            ),
        ),
        parameters=(),
        instances=(),
        location=_loc("iface_user.sv", 1),
    )
    top = Module(
        name="top",
        ports=(),
        parameters=(),
        instances=(
            Instance(
                name="u_child",
                module_name="child",
                param_overrides=(ParameterOverride("WIDTH", "4", _loc("top.sv", 3)),),
                port_connections=(
                    PortConnection("clk", "clk", _loc("top.sv", 3)),
                    PortConnection("d", "data", _loc("top.sv", 3)),
                ),
                location=_loc("top.sv", 3),
            ),
            Instance(
                name="u_pos",
                module_name="child",
                param_overrides=(ParameterOverride(None, "2", _loc("top.sv", 4)),),
                port_connections=(
                    PortConnection(None, "clk", _loc("top.sv", 4)),
                    PortConnection(None, "data", _loc("top.sv", 4)),
                ),
                location=_loc("top.sv", 4),
            ),
            Instance(
                name="u_bb",
                module_name="bb",
                param_overrides=(ParameterOverride("MODE", "1", _loc("top.sv", 5)),),
                port_connections=(PortConnection("clk", "clk", _loc("top.sv", 5)),),
                location=_loc("top.sv", 5),
            ),
            Instance(
                name="u_bbpos",
                module_name="bb",
                param_overrides=(),
                port_connections=(PortConnection(None, "clk", _loc("top.sv", 6)),),
                location=_loc("top.sv", 6),
            ),
            Instance(
                name="u_user",
                module_name="iface_user",
                param_overrides=(),
                port_connections=(PortConnection("m", "u_if.sub", _loc("top.sv", 7)),),
                location=_loc("top.sv", 7),
            ),
            Instance(
                name="u_if",
                module_name="test_if",
                param_overrides=(),
                port_connections=(),
                location=_loc("top.sv", 8),
            ),
        ),
        location=_loc("top.sv", 1),
    )
    test_if = Interface(
        name="test_if",
        parameters=(Parameter("AW", "32", _loc("test_if.sv", 2)),),
        signals=(
            InterfaceSignal("req", "logic", _loc("test_if.sv", 3)),
            InterfaceSignal("ack", "logic", _loc("test_if.sv", 4)),
        ),
        modports=(
            Modport(
                "sub", inputs=("req",), outputs=("ack",), location=_loc("test_if.sv", 6)
            ),
            Modport(
                "master",
                inputs=("ack",),
                outputs=("req",),
                location=_loc("test_if.sv", 7),
            ),
        ),
        location=_loc("test_if.sv", 1),
    )
    return ModuleTable(
        modules_by_name={m.name: m for m in (top, child, iface_user)},
        unresolved=set(),
        interfaces_by_name={"test_if": test_if},
    )


@pytest.fixture(scope="module")
def synthetic() -> dict:
    table = _synthetic_table()
    root = build_hierarchy(table, "top")
    return graph_export.build_graph(
        root,
        table,
        project_root=PROJECT_ROOT,
        dut_top="top",
        frontend="verible",
        tool_version="9.9.9",
    )


def _nodes_by_id(payload: dict) -> dict[str, dict]:
    return {node["id"]: node for node in payload["nodes"]}


def _links(payload: dict, link_type: str) -> list[dict]:
    return [link for link in payload["links"] if link["type"] == link_type]


# --- the contract -----------------------------------------------------------


def test_graph_contract_keys_present_and_typed(synthetic: dict) -> None:
    """Every pinned key exists with the pinned type.

    Renaming or retyping anything in ``GRAPH_CONTRACT`` /
    ``NODE_CONTRACT`` / ``LINK_CONTRACT`` breaks rtl_buddy's config
    and binding tiers, which merge onto these ids. This test is the
    tripwire; ``docs/graph-json-v1.md`` and
    ``schemas/graph-v1.json`` are the prose and machine forms of the
    same promise.
    """
    for dotted, expected in graph_export.GRAPH_CONTRACT.items():
        cursor: object = synthetic
        for part in dotted.split("."):
            assert isinstance(cursor, dict), f"{dotted}: {part} not under a dict"
            assert part in cursor, f"contract key missing: {dotted}"
            cursor = cursor[part]
        assert isinstance(cursor, expected), (
            f"{dotted}: expected {expected}, got {type(cursor)}"
        )

    assert synthetic["nodes"], "contract test needs a non-empty graph"
    assert synthetic["links"], "contract test needs a non-empty graph"
    for node in synthetic["nodes"]:
        for key, expected in graph_export.NODE_CONTRACT.items():
            assert key in node, f"node {node.get('id')} missing {key}"
            assert isinstance(node[key], expected), f"node {node['id']}.{key}"
        assert node["type"] in graph_export.NODE_TYPES
    for link in synthetic["links"]:
        for key, expected in graph_export.LINK_CONTRACT.items():
            assert key in link, f"link missing {key}: {link}"
            assert isinstance(link[key], expected), f"link {key}: {link}"
        assert link["type"] in graph_export.EDGE_TYPES
        assert link["confidence"] == graph_export.CONFIDENCE_EXTRACTED


def test_envelope_is_networkx_node_link(synthetic: dict) -> None:
    assert synthetic["directed"] is True
    assert synthetic["multigraph"] is True
    assert synthetic["graph"]["schema_version"] == graph_export.SCHEMA_VERSION == 1
    assert synthetic["graph"]["generator"] == {
        "tool": "rtl-buddy-view",
        "version": "9.9.9",
        "tier": "design",
    }
    assert synthetic["graph"]["project_root_rel"] == "."
    assert synthetic["graph"]["design"] == {
        "top": "top",
        "dut_top": "top",
        "tb_top": None,
        "frontend": "verible",
    }


def test_graph_is_closed(synthetic: dict) -> None:
    """Every link endpoint resolves to a node — no dangling ids."""
    ids = set(_nodes_by_id(synthetic))
    for link in synthetic["links"]:
        assert link["source"] in ids, link
        assert link["target"] in ids, link


# --- nodes ------------------------------------------------------------------


def test_node_ids_follow_the_id_grammar(synthetic: dict) -> None:
    nodes = _nodes_by_id(synthetic)
    # module / instance / port / parameter / interface / modport
    assert nodes["module:child"]["type"] == "module"
    assert nodes["inst:top/top.u_child"]["type"] == "instance"
    assert nodes["port:child.clk"]["type"] == "port"
    assert nodes["param:child.WIDTH"]["type"] == "parameter"
    assert nodes["iface:test_if"]["type"] == "interface"
    assert nodes["modport:test_if.sub"]["type"] == "modport"
    # labels are the bare names, ids carry the scope
    assert nodes["port:child.clk"]["label"] == "clk"
    assert nodes["modport:test_if.sub"]["label"] == "sub"
    # every node is tagged with its tier so a merged graph splits back apart
    assert {n["tier"] for n in synthetic["nodes"]} == {"design"}


def test_instance_id_embeds_the_hub_resolver_path(synthetic: dict) -> None:
    """``inst:<top>/<instance path>`` with the full dot path.

    The path half must stay byte-identical to view.json's node id
    (dot-separated *including* the top segment) — that identity is
    what lets the hub resolver, the results overlay, and the binding
    tier all name the same instance.
    """
    node = _nodes_by_id(synthetic)["inst:top/top.u_child"]
    assert node["instance_path"] == "top.u_child"
    assert node["instance_name"] == "u_child"
    assert node["module"] == "child"
    assert node["depth"] == 1
    assert node["is_top"] is False
    root = _nodes_by_id(synthetic)["inst:top/top"]
    assert root["is_top"] is True
    assert root["instance_name"] is None
    assert root["depth"] == 0


def test_source_anchors_are_project_relative(synthetic: dict) -> None:
    nodes = _nodes_by_id(synthetic)
    assert nodes["module:child"]["file"] == "child.sv"
    assert nodes["module:child"]["line"] == 1
    # The root instance has no instantiation site, so it falls back
    # to its module declaration rather than emitting a null anchor.
    assert nodes["inst:top/top"]["file"] == "top.sv"
    assert nodes["inst:top/top"]["line"] == 1
    # Instance nodes anchor at the instantiation, not the definition.
    assert nodes["inst:top/top.u_child"]["file"] == "top.sv"
    assert nodes["inst:top/top.u_child"]["line"] == 3
    # Blackboxes have no anchor at all — null, never a fabricated path.
    assert nodes["module:bb"]["file"] is None
    assert nodes["module:bb"]["line"] is None


def test_paths_outside_the_project_root_stay_absolute() -> None:
    """Rather than growing a ``../..`` prefix that resolves
    differently under a symlinked checkout."""
    assert (
        graph_export._rel_path("/elsewhere/ip.sv", PROJECT_ROOT) == "/elsewhere/ip.sv"
    )
    assert graph_export._rel_path("/proj/a/b.sv", None) == "/proj/a/b.sv"


def test_port_attributes(synthetic: dict) -> None:
    nodes = _nodes_by_id(synthetic)
    clk = nodes["port:child.clk"]
    assert (clk["owner"], clk["dir"], clk["width"]) == ("child", "input", 1)
    assert clk["port_kind"] == "wire"
    d = nodes["port:child.d"]
    assert (d["dir"], d["width"], d["type_text"]) == ("input", 8, "logic [7:0]")
    # A parameterized range is left unknown on purpose: a wrong width
    # is worse than a missing one.
    assert nodes["port:child.q"]["width"] is None


@pytest.mark.parametrize(
    "type_text,expected",
    [
        (None, None),
        ("logic", 1),
        ("wire", 1),
        ("reg", 1),
        ("bit", 1),
        ("logic signed", 1),
        ("wire unsigned", 1),
        ("logic [7:0]", 8),
        ("logic [0:7]", 8),
        ("logic [ 31 : 0 ]", 32),
        ("[3:0]", 4),
        ("logic [WIDTH-1:0]", None),
        ("logic [$clog2(N)-1:0]", None),
        # Not a scalar keyword ⇒ unknown, never a confident 1. These
        # are the shapes that used to be reported as 1-bit ports.
        ("int", None),
        ("byte", None),
        ("shortint", None),
        ("chandle", None),
        ("cfg_t", None),
        ("some_pkg::cfg_t", None),
        ("apb_intf.subordinate", None),
        ("", None),
    ],
)
def test_width_of(type_text: str | None, expected: int | None) -> None:
    assert graph_export._width_of(type_text) == expected


def test_width_is_unknown_for_a_non_wire_port_kind() -> None:
    """An interface bundle is a set of signals, not a 1-bit port."""
    assert graph_export._width_of("apb_intf.subordinate", "interface") is None
    assert graph_export._width_of("logic", "interface") is None
    assert graph_export._width_of("logic", "interface_signal") is None


def test_interface_bundle_port_reports_no_width() -> None:
    module = Module(
        name="user",
        ports=(
            Port(
                "apb",
                None,
                "apb_intf.subordinate",
                None,
                port_kind="interface",
                interface_type="apb_intf",
                modport="subordinate",
            ),
            Port("cfg", "input", "csr_pkg::cfg_t", None),
        ),
        parameters=(),
        instances=(),
        location=None,
    )
    table = ModuleTable(modules_by_name={"user": module})
    nodes = _nodes_by_id(
        graph_export.build_graph(build_hierarchy(table, "user"), table)
    )
    assert nodes["port:user.apb"]["width"] is None
    assert nodes["port:user.cfg"]["width"] is None


def test_parameter_and_interface_attributes(synthetic: dict) -> None:
    nodes = _nodes_by_id(synthetic)
    assert nodes["param:child.WIDTH"]["default"] == "8"
    assert nodes["param:child.WIDTH"]["owner"] == "child"
    # Interface parameters use the same param: namespace, owned by
    # the interface.
    assert nodes["param:test_if.AW"]["default"] == "32"
    iface = nodes["iface:test_if"]
    assert iface["signals"] == ["req", "ack"]
    assert iface["is_blackbox"] is False
    sub = nodes["modport:test_if.sub"]
    assert (sub["interface"], sub["inputs"], sub["outputs"]) == (
        "test_if",
        ["req"],
        ["ack"],
    )


def test_interface_instance_is_not_reported_as_a_blackbox(synthetic: dict) -> None:
    """``test_if u_if()`` isn't in the module table, so the hierarchy
    flags it blackbox; ``is_interface`` is what tells a consumer it's
    a known entity of a different kind."""
    node = _nodes_by_id(synthetic)["inst:top/top.u_if"]
    assert node["is_blackbox"] is True  # parity with view.json
    assert node["is_interface"] is True
    assert "module:test_if" not in _nodes_by_id(synthetic)
    assert {
        link["target"]
        for link in _links(synthetic, "instance_of")
        if link["source"] == node["id"]
    } == {"iface:test_if"}


def test_blackbox_signature_recovered_from_use_sites(synthetic: dict) -> None:
    """A vendor macro still needs port + parameter nodes: the
    binding tier's ``drives`` edges land on ``port:`` ids."""
    nodes = _nodes_by_id(synthetic)
    assert nodes["module:bb"]["is_blackbox"] is True
    assert nodes["port:bb.clk"]["dir"] is None
    assert nodes["port:bb.clk"]["width"] is None
    assert nodes["param:bb.MODE"]["default"] is None


# --- links ------------------------------------------------------------------


def test_instantiates_and_child_of_and_instance_of(synthetic: dict) -> None:
    instantiates = {
        (link["source"], link["target"], link["instance"])
        for link in _links(synthetic, "instantiates")
    }
    assert ("module:top", "module:child", "u_child") in instantiates
    assert ("module:top", "module:child", "u_pos") in instantiates
    assert ("module:top", "iface:test_if", "u_if") in instantiates

    child_of = {
        (link["source"], link["target"]) for link in _links(synthetic, "child_of")
    }
    assert ("inst:top/top.u_child", "inst:top/top") in child_of
    # The root has no parent.
    assert not [
        link
        for link in _links(synthetic, "child_of")
        if link["source"] == "inst:top/top"
    ]

    instance_of = {
        (link["source"], link["target"]) for link in _links(synthetic, "instance_of")
    }
    assert ("inst:top/top.u_child", "module:child") in instance_of
    assert ("inst:top/top", "module:top") in instance_of


def test_connects_points_at_the_instantiated_modules_port(synthetic: dict) -> None:
    named = [
        link
        for link in _links(synthetic, "connects")
        if link["source"] == "inst:top/top.u_child"
    ]
    assert {(link["target"], link["actual"]) for link in named} == {
        ("port:child.clk", "clk"),
        ("port:child.d", "data"),
    }
    assert all(link["positional"] is False for link in named)
    assert all(link["file"] == "top.sv" and link["line"] == 3 for link in named)


def test_positional_bindings_resolve_through_the_declaration(synthetic: dict) -> None:
    connects = {
        (link["target"], link["index"], link["positional"])
        for link in _links(synthetic, "connects")
        if link["source"] == "inst:top/top.u_pos"
    }
    assert connects == {("port:child.clk", 0, True), ("port:child.d", 1, True)}
    overrides = [
        link
        for link in _links(synthetic, "overrides")
        if link["source"] == "inst:top/top.u_pos"
    ]
    assert [(link["target"], link["value"]) for link in overrides] == [
        ("param:child.WIDTH", "2")
    ]


def test_positional_bindings_on_a_blackbox_are_dropped_not_guessed(
    synthetic: dict,
) -> None:
    """No declaration ⇒ no port order ⇒ no formal name. Inventing
    one would be inference, and this tier only emits EXTRACTED."""
    assert not [
        link
        for link in _links(synthetic, "connects")
        if link["source"] == "inst:top/top.u_bbpos"
    ]
    # The instance itself is still in the graph.
    assert "inst:top/top.u_bbpos" in _nodes_by_id(synthetic)
    # Named bindings on the same blackbox module do survive.
    assert [
        link["target"]
        for link in _links(synthetic, "connects")
        if link["source"] == "inst:top/top.u_bb"
    ] == ["port:bb.clk"]


def test_overrides_carry_the_value(synthetic: dict) -> None:
    override = [
        link
        for link in _links(synthetic, "overrides")
        if link["source"] == "inst:top/top.u_child"
    ]
    assert len(override) == 1
    assert override[0]["target"] == "param:child.WIDTH"
    assert override[0]["value"] == "4"
    assert override[0]["parameter"] == "WIDTH"
    # DEPTH is left at its default — no edge, the default lives on
    # the parameter node.
    assert _nodes_by_id(synthetic)["param:child.DEPTH"]["default"] == "16"


def test_implements_links_module_to_modport(synthetic: dict) -> None:
    implements = _links(synthetic, "implements")
    assert [(link["source"], link["target"], link["port"]) for link in implements] == [
        ("module:iface_user", "modport:test_if.sub", "m")
    ]
    port = _nodes_by_id(synthetic)["port:iface_user.m"]
    assert (port["port_kind"], port["interface_type"], port["modport"]) == (
        "interface",
        "test_if",
        "sub",
    )


def test_bare_interface_port_pins_nothing() -> None:
    """No ``.modport`` suffix ⇒ no direction pinned ⇒ no
    ``implements`` edge (but the port node still records the type)."""
    module = Module(
        name="user",
        ports=(
            Port(
                "m",
                None,
                None,
                None,
                port_kind="interface",
                interface_type="test_if",
                modport=None,
            ),
        ),
        parameters=(),
        instances=(),
        location=None,
    )
    table = ModuleTable(modules_by_name={"user": module})
    payload = graph_export.build_graph(build_hierarchy(table, "user"), table)
    assert _links(payload, "implements") == []
    assert _nodes_by_id(payload)["port:user.m"]["interface_type"] == "test_if"


def test_unknown_interface_type_gets_a_blackbox_stub() -> None:
    """An interface the filelist never defined still anchors the
    ``implements`` edge rather than dangling it."""
    module = Module(
        name="user",
        ports=(
            Port(
                "m",
                None,
                None,
                None,
                port_kind="interface",
                interface_type="missing_if",
                modport="sub",
            ),
        ),
        parameters=(),
        instances=(),
        location=None,
    )
    table = ModuleTable(modules_by_name={"user": module})
    payload = graph_export.build_graph(build_hierarchy(table, "user"), table)
    nodes = _nodes_by_id(payload)
    assert nodes["iface:missing_if"]["is_blackbox"] is True
    assert nodes["modport:missing_if.sub"]["is_blackbox"] is True
    assert _links(payload, "implements")[0]["target"] == "modport:missing_if.sub"


# --- closure ----------------------------------------------------------------


def _closure_table() -> ModuleTable:
    """``holder`` binds names ``child``'s declaration doesn't supply.

    ``.qq``/``.WITDH`` stand in for every way a formal can go
    missing: an interface's header ports (``Interface`` has no
    ``ports`` field), a multi-declarator ``#(parameter AW = 8, DW =
    32)`` header the Verible frontend collapses to one
    ``<unknown>``, and a plain source-level typo.
    """
    child = Module(
        name="child",
        ports=(Port("q", "output", "logic", None),),
        parameters=(Parameter("WIDTH", "8", None),),
        instances=(),
        location=None,
    )
    holder = Module(
        name="holder",
        ports=(),
        parameters=(),
        instances=(
            Instance(
                name="u_child",
                module_name="child",
                param_overrides=(ParameterOverride("WITDH", "4", None),),
                port_connections=(PortConnection("qq", "net", None),),
                location=None,
            ),
            # An interface instance with named header-port bindings.
            Instance(
                name="u_if",
                module_name="test_if",
                param_overrides=(),
                port_connections=(PortConnection("clk", "clk", None),),
                location=None,
            ),
        ),
        location=None,
    )
    test_if = Interface(
        name="test_if", parameters=(), signals=(), modports=(), location=None
    )
    return ModuleTable(
        modules_by_name={m.name: m for m in (holder, child)},
        unresolved=set(),
        interfaces_by_name={"test_if": test_if},
    )


def test_named_bindings_to_undeclared_formals_stay_closed() -> None:
    """A ``connects``/``overrides`` target the declaration never
    produced is stubbed in, not left dangling.

    A dangling id is not cosmetic: ``networkx.node_link_graph``
    silently invents an attribute-less node for it, and that node —
    with no ``type``/``label``/``tier`` — then propagates into every
    merged cross-tier graph, violating ``NODE_CONTRACT``.
    """
    table = _closure_table()
    payload = graph_export.build_graph(build_hierarchy(table, "holder"), table)
    ids = set(_nodes_by_id(payload))
    for link in payload["links"]:
        assert link["source"] in ids, link
        assert link["target"] in ids, link
    # The edges survive — dropping them would lose the binding the
    # binding tier's ``drives`` edge lands on.
    assert _links(payload, "connects")[0]["target"] == "port:child.qq"
    assert _links(payload, "overrides")[0]["target"] == "param:child.WITDH"


def test_recovered_stubs_are_marked_by_their_empty_attributes() -> None:
    """No source anchor and no declared facts is what says
    "recovered from a use site, never seen declared"."""
    table = _closure_table()
    nodes = _nodes_by_id(
        graph_export.build_graph(build_hierarchy(table, "holder"), table)
    )
    stub = nodes["port:child.qq"]
    assert (stub["owner"], stub["dir"], stub["width"], stub["type_text"]) == (
        "child",
        None,
        None,
        None,
    )
    assert (stub["file"], stub["line"]) == (None, None)
    assert nodes["param:child.WITDH"]["default"] is None
    # An interface's header ports get the same treatment — the
    # extractor models signals/params/modports, not the port list.
    assert nodes["port:test_if.clk"]["owner"] == "test_if"
    # The declared port keeps its real facts; the stub never wins.
    assert nodes["port:child.q"]["width"] == 1


# --- determinism ------------------------------------------------------------


def test_output_is_byte_stable_and_sorted() -> None:
    table = _synthetic_table()
    root = build_hierarchy(table, "top")

    def _emit() -> str:
        buf = io.StringIO()
        graph_export.render(
            root, buf, module_table=table, project_root=PROJECT_ROOT, tool_version="1"
        )
        return buf.getvalue()

    first = _emit()
    assert first == _emit()
    assert first.endswith("\n")
    payload = json.loads(first)
    ids = [node["id"] for node in payload["nodes"]]
    assert ids == sorted(ids)
    keys = [(link["source"], link["target"], link["type"]) for link in payload["links"]]
    assert keys == sorted(keys)


def test_parallel_edges_survive_and_stay_ordered(synthetic: dict) -> None:
    """``multigraph: true`` isn't decorative — ``top`` instantiates
    ``child`` twice, and both edges must be present."""
    pairs = [
        link
        for link in _links(synthetic, "instantiates")
        if (link["source"], link["target"]) == ("module:top", "module:child")
    ]
    assert [link["instance"] for link in pairs] == ["u_child", "u_pos"]


# --- meta sidecar -----------------------------------------------------------


def test_build_meta_records_input_hashes_and_provenance(synthetic: dict) -> None:
    files = [Path("/proj/top.sv"), Path("/proj/child.sv")]
    meta = graph_export.build_meta(
        synthetic,
        files,
        project_root=PROJECT_ROOT,
        hasher=lambda path: f"hash-of-{path.name}",
    )
    assert meta["schema_version"] == graph_export.META_SCHEMA_VERSION
    design = meta["tiers"]["design"]
    assert design["generator"] == synthetic["graph"]["generator"]
    assert design["design"] == synthetic["graph"]["design"]
    # Inputs sorted so the sidecar is diffable too.
    assert [i["path"] for i in design["inputs"]] == ["child.sv", "top.sv"]
    assert design["inputs"][0]["sha256"] == "hash-of-child.sv"
    assert design["node_count"] == len(synthetic["nodes"])
    assert design["link_count"] == len(synthetic["links"])


def test_build_meta_tolerates_a_vanished_input(synthetic: dict) -> None:
    """The export already succeeded; a file that disappeared between
    parse and hash is recorded as unhashed, not raised."""

    def _boom(path: Path) -> str:
        raise OSError("gone")

    meta = graph_export.build_meta(
        synthetic, [Path("/proj/top.sv")], project_root=PROJECT_ROOT, hasher=_boom
    )
    entry = meta["tiers"]["design"]["inputs"][0]
    assert entry == {"path": "top.sv", "sha256": None, "bytes": None}


# --- schema + real designs --------------------------------------------------


@pytest.fixture(scope="module")
def graph_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def test_synthetic_graph_validates(graph_schema: dict, synthetic: dict) -> None:
    jsonschema.validate(instance=synthetic, schema=graph_schema)


_FIXTURE_CASES = [
    ("empty_module", "empty"),
    ("counter_with_subs", "counter"),
    ("parameterized_fifo", "fifo"),
    ("connection_shapes", "top"),
    ("interface_port_module", "tb_top"),
    ("tb_over_dut", "tb_top"),
    ("graph_closure", "closure_top"),
]


@pytest.mark.parametrize(
    "fixture_dir,top", _FIXTURE_CASES, ids=[case[0] for case in _FIXTURE_CASES]
)
def test_fixture_graph_validates(
    graph_schema: dict, fixture_dir: str, top: str
) -> None:
    payload = _export_fixture(fixture_dir, top)
    jsonschema.validate(instance=payload, schema=graph_schema)
    assert payload["graph"]["design"]["top"] == top


@pytest.mark.parametrize(
    "fixture_dir,top", _FIXTURE_CASES, ids=[case[0] for case in _FIXTURE_CASES]
)
def test_fixture_graph_is_closed(fixture_dir: str, top: str) -> None:
    """Closure over real Verible-parsed designs, not just the
    synthetic table — the frontend's own gaps (an interface's header
    ports, a multi-declarator parameter header) are exactly what
    used to dangle."""
    payload = _export_fixture(fixture_dir, top)
    ids = set(_nodes_by_id(payload))
    for link in payload["links"]:
        assert link["source"] in ids, link
        assert link["target"] in ids, link


def test_frontend_gaps_are_stubbed_rather_than_dangled() -> None:
    """The ``graph_closure`` fixture pins the two real-world shapes.

    ``closure_bus_if``'s ``clk``/``rst_n`` are interface *header*
    ports, which ``extractor.Interface`` doesn't model;
    ``closure_leaf``'s ``#(parameter AW = 8, DW = 32)`` reaches the
    module table as one ``<unknown>`` parameter. Both are named at
    the instantiation site, so both need a node to point at.
    """
    payload = _export_fixture("graph_closure", "closure_top")
    nodes = _nodes_by_id(payload)
    assert nodes["port:closure_bus_if.clk"]["type"] == "port"
    assert nodes["port:closure_bus_if.rst_n"]["owner"] == "closure_bus_if"
    assert nodes["param:closure_leaf.AW"]["default"] is None
    assert nodes["param:closure_leaf.DW"]["default"] is None
    # ... and a non-scalar port type stays unknown rather than 1.
    assert nodes["port:closure_leaf.budget"]["type_text"] == "int"
    assert nodes["port:closure_leaf.budget"]["width"] is None
    assert nodes["port:closure_leaf.clk"]["width"] == 1


def _export_fixture(fixture_dir: str, top: str, *, tb_top: str | None = None) -> dict:
    _require_verible()
    from rtl_buddy_view._filelist import parse_filelist
    from rtl_buddy_view.frontend import Frontend, parse_to_modules

    fix = FIXTURES_ROOT / fixture_dir
    table = parse_to_modules(parse_filelist(fix / "files.f"), frontend=Frontend.verible)
    root = build_hierarchy(table, tb_top or top)
    return graph_export.build_graph(
        root,
        table,
        project_root=FIXTURES_ROOT,
        dut_top=top,
        tb_top=tb_top,
        frontend="verible",
    )


@pytest.mark.parametrize(
    "fixture_dir,top", _FIXTURE_CASES, ids=[case[0] for case in _FIXTURE_CASES]
)
def test_parity_with_view_json(fixture_dir: str, top: str) -> None:
    """#126 acceptance: every module and instance in ``--format
    json`` has a node here."""
    _require_verible()
    from rtl_buddy_view._filelist import parse_filelist
    from rtl_buddy_view.frontend import Frontend, parse_to_modules
    from rtl_buddy_view.render import json_render

    fix = FIXTURES_ROOT / fixture_dir
    table = parse_to_modules(parse_filelist(fix / "files.f"), frontend=Frontend.verible)
    root = build_hierarchy(table, top)

    buf = io.StringIO()
    json_render.render(root, buf, module_table=table)
    view = json.loads(buf.getvalue())

    payload = graph_export.build_graph(root, table, project_root=FIXTURES_ROOT)
    ids = set(_nodes_by_id(payload))
    for node in view["nodes"]:
        assert graph_export.instance_id(top, node["id"]) in ids
        assert (
            graph_export.module_id(node["module"]) in ids
            or graph_export.interface_id(node["module"]) in ids
        )
    # And the reverse: no invented instances.
    graph_paths = {
        n["instance_path"] for n in payload["nodes"] if n["type"] == "instance"
    }
    assert graph_paths == {n["id"] for n in view["nodes"]}


def test_tb_rooted_export_carries_the_testbench_hierarchy() -> None:
    """#126 acceptance: the SV testbench scope lands in the graph."""
    payload = _export_fixture("tb_over_dut", "dut", tb_top="tb_top")
    nodes = _nodes_by_id(payload)
    assert payload["graph"]["design"] == {
        "top": "tb_top",
        "dut_top": "dut",
        "tb_top": "tb_top",
        "frontend": "verible",
    }
    # TB-only scopes (clock generator, stimulus driver) that a
    # DUT-rooted export never sees.
    assert "module:clkgen" in nodes
    assert "module:driver" in nodes
    assert "inst:tb_top/tb_top.u_driver" in nodes
    # ...and the DUT subtree underneath, keyed by the TB-rooted path.
    assert "inst:tb_top/tb_top.u_dut.u_a" in nodes

    dut_rooted = _export_fixture("tb_over_dut", "dut")
    assert "module:clkgen" not in _nodes_by_id(dut_rooted)
    # Same instance, different root ⇒ different id: the <top>/ prefix
    # plus the path keeps the two exports mergeable side by side.
    assert "inst:dut/dut.u_a" in _nodes_by_id(dut_rooted)


# --- optional consumers -----------------------------------------------------


def test_networkx_round_trip(synthetic: dict) -> None:
    """The envelope is genuine NetworkX node-link JSON, which is what
    makes it Graphify-compatible."""
    nx = pytest.importorskip("networkx")
    graph = nx.node_link_graph(synthetic, edges="links")
    assert graph.is_directed() and graph.is_multigraph()
    assert graph.number_of_nodes() == len(synthetic["nodes"])
    assert graph.number_of_edges() == len(synthetic["links"])
    assert graph.graph["schema_version"] == 1
    assert graph.nodes["module:child"]["label"] == "child"
    round_tripped = nx.node_link_data(graph, edges="links")
    assert {n["id"] for n in round_tripped["nodes"]} == set(_nodes_by_id(synthetic))


def test_graphify_accepts_the_export(tmp_path: Path, synthetic: dict) -> None:
    """Acceptance criterion from #126, guarded as an optional
    dependency: ``graphify`` is not on PyPI as of this change, so
    this skips everywhere until it is."""
    import shutil
    import subprocess

    if shutil.which("graphify") is None:
        pytest.skip("graphify not installed")
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(synthetic))
    merged = tmp_path / "merged.json"
    result = subprocess.run(
        ["graphify", "merge-graphs", str(path), "-o", str(merged)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        ["graphify", "query", str(merged)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


# --- CLI --------------------------------------------------------------------


def test_graph_help_documents_the_verb(monkeypatch) -> None:
    # CI renders help styled and 80-column-wrapped, which can split an
    # option name mid-token; force plain wide output so the assertions
    # test the content, not the renderer.
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("COLUMNS", "200")
    result = CliRunner().invoke(app, ["graph", "--help"])
    assert result.exit_code == 0
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "knowledge graph" in plain
    for flag in ("--filelist", "--top", "--tb-top", "--output", "--project-root"):
        assert flag in plain


def test_graph_requires_a_top() -> None:
    result = CliRunner().invoke(
        app, ["graph", "-f", str(FIXTURES_ROOT / "counter_with_subs" / "files.f")]
    )
    assert result.exit_code == 2
    assert "--top or --tb-top is required" in result.output


def test_graph_unresolved_top_exits_one() -> None:
    _require_verible()
    result = CliRunner().invoke(
        app,
        [
            "graph",
            "-f",
            str(FIXTURES_ROOT / "counter_with_subs" / "files.f"),
            "--top",
            "nope",
        ],
    )
    assert result.exit_code == 1
    assert "hierarchy:" in result.output


def test_graph_slang_frontend_exits_two() -> None:
    """Same exit-code contract as the render surface: an
    unimplemented frontend is 2, not a traceback."""
    result = CliRunner().invoke(
        app,
        [
            "graph",
            "-f",
            str(FIXTURES_ROOT / "counter_with_subs" / "files.f"),
            "--top",
            "counter",
            "--frontend",
            "slang",
        ],
    )
    assert result.exit_code == 2
    assert "frontend:" in result.output


def test_graph_streams_to_stdout() -> None:
    _require_verible()
    result = CliRunner().invoke(
        app,
        [
            "graph",
            "-f",
            str(FIXTURES_ROOT / "counter_with_subs" / "files.f"),
            "--top",
            "counter",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["graph"]["project_root_rel"] == "."
    assert "module:counter_ff" in _nodes_by_id(payload)


def test_graph_writes_artefact_and_meta_sidecar(tmp_path: Path) -> None:
    _require_verible()
    design = FIXTURES_ROOT / "counter_with_subs"
    out = tmp_path / "artefacts" / "graph" / "graph.json"
    result = CliRunner().invoke(
        app,
        [
            "graph",
            "-f",
            str(design / "files.f"),
            "--top",
            "counter",
            "--project-root",
            str(tmp_path),
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    # Parent dirs created, and the root is two hops up from the
    # contract's artefacts/graph/ location.
    assert payload["graph"]["project_root_rel"] == "../.."
    meta = json.loads((out.parent / "graph-meta.json").read_text())
    inputs = meta["tiers"]["design"]["inputs"]
    assert {Path(i["path"]).name for i in inputs} == {"counter.sv", "counter_ff.sv"}
    assert all(len(i["sha256"]) == 64 for i in inputs)
    assert meta["tiers"]["design"]["node_count"] == len(payload["nodes"])


def test_graph_no_meta_skips_the_sidecar(tmp_path: Path) -> None:
    _require_verible()
    out = tmp_path / "graph.json"
    result = CliRunner().invoke(
        app,
        [
            "graph",
            "-f",
            str(FIXTURES_ROOT / "counter_with_subs" / "files.f"),
            "--top",
            "counter",
            "-o",
            str(out),
            "--no-meta",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert not (tmp_path / "graph-meta.json").exists()


def test_graph_recovers_a_wrong_tb_top_hint() -> None:
    """A ``--tb-top`` naming the testbench *config* rather than the
    top module is auto-corrected, exactly as the render path does."""
    _require_verible()
    result = CliRunner().invoke(
        app,
        [
            "graph",
            "-f",
            str(FIXTURES_ROOT / "tb_over_dut" / "files.f"),
            "--top",
            "dut",
            "--tb-top",
            "tb_dut_config",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["graph"]["design"]["tb_top"] == "tb_top"
    assert "inst:tb_top/tb_top.u_dut" in _nodes_by_id(payload)
