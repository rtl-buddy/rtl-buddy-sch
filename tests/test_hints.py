"""Unit tests for the ``rbsch`` pragma layer (epic #159, phase 1).

Everything here is Verible-free: the payload parser takes a string,
the scanner takes text, and association takes a hand-built
:class:`ModuleTable`. The end-to-end proof that ``location.file``
really matches the scanner's key lives in ``test_cli_hints.py``,
which is Verible-gated.
"""

from __future__ import annotations

import json

import pytest

from rtl_buddy_view import hints as hints_mod
from rtl_buddy_view.extractor import Instance, Module, ModuleTable, SourceLocation
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.hints import (
    HintMap,
    HintsError,
    InstanceHints,
    ModuleHints,
    apply_hints,
    load_hint_sidecar,
    merge_hint_maps,
    parse_hint_sidecar,
    parse_pragma_payload,
    resolve_hints,
    scan_pragmas,
)

FILE = "/design/top.sv"


def _instance(name: str, module_name: str, line: int) -> Instance:
    return Instance(
        name=name,
        module_name=module_name,
        param_overrides=(),
        port_connections=(),
        location=SourceLocation(file=FILE, start_line=line),
    )


def _module(
    name: str, line: int, instances: tuple[Instance, ...] = (), file: str = FILE
) -> Module:
    return Module(
        name=name,
        ports=(),
        parameters=(),
        instances=instances,
        location=SourceLocation(file=file, start_line=line),
    )


def _table(*modules: Module) -> ModuleTable:
    return ModuleTable(modules_by_name={m.name: m for m in modules})


def _node(
    instance_path: str,
    module_name: str,
    *,
    instance: Instance | None = None,
    children: tuple[HierNode, ...] = (),
) -> HierNode:
    return HierNode(
        instance_path=instance_path,
        module_name=module_name,
        instance=instance,
        module=None,
        is_blackbox=False,
        children=children,
    )


# --- payload parsing --------------------------------------------------------


def test_parse_bare_flags() -> None:
    payload = parse_pragma_payload(" collapse hide ")
    assert payload.flags == ("collapse", "hide")
    assert payload.options == ()
    assert payload.warnings == ()


def test_parse_quoted_value_keeps_spaces() -> None:
    payload = parse_pragma_payload(' label="CSR block (PeakRDL)" leaf')
    assert payload.flags == ("leaf",)
    assert payload.option("label") == "CSR block (PeakRDL)"


def test_parse_single_quotes_and_bare_value() -> None:
    assert parse_pragma_payload("label='CSR block'").option("label") == "CSR block"
    assert parse_pragma_payload("label=csr").option("label") == "csr"


def test_parse_flags_are_sorted_and_deduplicated() -> None:
    """Two spellings of the same intent must compare equal."""
    assert parse_pragma_payload("hide collapse hide").flags == ("collapse", "hide")


def test_parse_unknown_key_warns_with_known_set() -> None:
    payload = parse_pragma_payload("collpase")
    assert payload.flags == ()
    assert len(payload.warnings) == 1
    warning = payload.warnings[0]
    assert "collpase" in warning
    assert "clock, collapse, data, hide, leaf, main, reset, side" in warning
    assert "label=…" in warning


def test_parse_unknown_valued_key_warns() -> None:
    payload = parse_pragma_payload('titel="x"')
    assert payload.options == ()
    assert "titel" in payload.warnings[0]


def test_parse_flag_with_value_warns() -> None:
    payload = parse_pragma_payload("leaf=true")
    assert payload.flags == ()
    assert "takes no value" in payload.warnings[0]


def test_parse_key_without_value_warns() -> None:
    payload = parse_pragma_payload("label")
    assert "needs a value" in payload.warnings[0]


def test_parse_value_without_a_key_warns() -> None:
    payload = parse_pragma_payload("=orphan")
    assert payload.is_empty
    assert "no key before" in payload.warnings[0]


def test_parse_empty_payload_warns() -> None:
    assert "empty rbsch pragma" in parse_pragma_payload("   ").warnings[0]


def test_parse_unterminated_quote_warns_instead_of_raising() -> None:
    payload = parse_pragma_payload('label="unclosed')
    assert payload.is_empty
    assert "malformed" in payload.warnings[0]


# --- scanning ---------------------------------------------------------------


def test_scan_finds_own_line_and_trailing_pragmas() -> None:
    sources = {
        FILE: (
            "module top;\n"
            '  // rbsch: label="CSR"\n'
            "  sub u_sub ();  // rbsch: collapse\n"
            "endmodule\n"
        )
    }
    pragmas = scan_pragmas(sources)
    assert [(p.line, p.own_line) for p in pragmas] == [(2, True), (3, False)]
    assert pragmas[0].option("label") == "CSR"
    assert pragmas[1].flags == ("collapse",)


def test_scan_ignores_non_rbsch_comments() -> None:
    sources = {FILE: "// rbcdc: leaf\n// plain comment\n/* rbsch: leaf */\n"}
    assert scan_pragmas(sources) == ()


def test_scan_is_sorted_by_file_then_line() -> None:
    """Warning order must not depend on the caller's mapping order."""
    sources = {
        "/b.sv": "// rbsch: leaf\n",
        "/a.sv": "\n// rbsch: hide\n",
    }
    assert [(p.file, p.line) for p in scan_pragmas(sources)] == [
        ("/a.sv", 2),
        ("/b.sv", 1),
    ]


# --- association ------------------------------------------------------------


def test_same_line_pragma_binds_to_that_instance() -> None:
    table = _table(_module("top", 1, (_instance("u_sub", "sub", 5),)))
    sources = {FILE: "\n" * 4 + "  sub u_sub ();  // rbsch: collapse\n"}
    hints = resolve_hints(scan_pragmas(sources), table)
    assert hints.for_instance("top", "u_sub") == InstanceHints(collapse=True)
    assert hints.warnings == ()


def test_own_line_pragma_binds_to_the_next_declaration() -> None:
    table = _table(
        _module("top", 3, (_instance("u_a", "sub", 5), _instance("u_b", "sub", 9)))
    )
    sources = {FILE: "\n" * 3 + "// rbsch: hide\n" + "\n" * 10}
    hints = resolve_hints(scan_pragmas(sources), table)
    # Line 4's pragma reaches u_a (line 5), not u_b (line 9).
    assert hints.for_instance("top", "u_a") == InstanceHints(hide=True)
    assert hints.for_instance("top", "u_b") is None


def test_own_line_pragma_binds_to_a_following_module_declaration() -> None:
    table = _table(_module("sub", 4))
    sources = {FILE: "\n" * 2 + "// rbsch: leaf\n" + "\n" * 5}
    hints = resolve_hints(scan_pragmas(sources), table)
    assert hints.for_module("sub") == ModuleHints(leaf=True)


def test_trailing_pragma_on_a_non_declaration_line_warns() -> None:
    """Falling through would let a stray comment reshape a distant block."""
    table = _table(_module("top", 1, (_instance("u_sub", "sub", 9),)))
    sources = {FILE: "\n" * 4 + "  logic x;  // rbsch: hide\n"}
    hints = resolve_hints(scan_pragmas(sources), table)
    assert hints.is_empty
    assert "matched no module or instance declaration" in hints.warnings[0]
    assert "same line" in hints.warnings[0]


def test_own_line_pragma_with_nothing_below_warns() -> None:
    table = _table(_module("top", 1, (_instance("u_sub", "sub", 2),)))
    sources = {FILE: "\n" * 20 + "// rbsch: hide\n"}
    hints = resolve_hints(scan_pragmas(sources), table)
    assert hints.is_empty
    assert "next declaration below it" in hints.warnings[0]


def test_leaf_on_an_instance_warns_and_is_dropped() -> None:
    table = _table(_module("top", 1, (_instance("u_sub", "sub", 5),)))
    sources = {FILE: "\n" * 4 + '  sub u_sub ();  // rbsch: leaf label="S"\n'}
    hints = resolve_hints(scan_pragmas(sources), table)
    assert hints.for_instance("top", "u_sub") == InstanceHints(label="S")
    assert "'leaf' applies to a module definition" in hints.warnings[0]


def test_collapse_on_a_module_warns_and_is_dropped() -> None:
    table = _table(_module("sub", 4))
    sources = {FILE: "\n" * 2 + '// rbsch: collapse label="S"\n'}
    hints = resolve_hints(scan_pragmas(sources), table)
    assert hints.for_module("sub") == ModuleHints(label="S")
    assert "'collapse' applies to an instance" in hints.warnings[0]


def test_payload_warnings_are_prefixed_with_file_and_line() -> None:
    table = _table(_module("sub", 4))
    sources = {FILE: "\n" * 2 + "// rbsch: nonsense\n"}
    hints = resolve_hints(scan_pragmas(sources), table)
    assert hints.warnings[0].startswith(f"{FILE}:3: ")


def test_two_pragmas_on_one_target_merge() -> None:
    table = _table(_module("top", 1, (_instance("u_sub", "sub", 6),)))
    sources = {FILE: "\n" * 3 + '// rbsch: hide\n// rbsch: label="S"\n'}
    hints = resolve_hints(scan_pragmas(sources), table)
    assert hints.for_instance("top", "u_sub") == InstanceHints(hide=True, label="S")


def test_instances_from_different_parents_do_not_collide() -> None:
    table = _table(
        _module("top", 1, (_instance("u_sub", "sub", 3),)),
        _module("other", 10, (_instance("u_sub", "sub", 11),)),
    )
    sources = {FILE: "\n" * 10 + "  sub u_sub ();  // rbsch: hide\n"}
    hints = resolve_hints(scan_pragmas(sources), table)
    assert hints.for_instance("other", "u_sub") == InstanceHints(hide=True)
    assert hints.for_instance("top", "u_sub") is None


def test_declarations_without_locations_are_skipped() -> None:
    module = Module(
        name="top",
        ports=(),
        parameters=(),
        instances=(
            Instance(
                name="u_sub",
                module_name="sub",
                param_overrides=(),
                port_connections=(),
                location=None,
            ),
        ),
        location=None,
    )
    hints = resolve_hints(scan_pragmas({FILE: "// rbsch: hide\n"}), _table(module))
    assert hints.is_empty


# --- apply_hints ------------------------------------------------------------


def _demo_tree() -> HierNode:
    return _node(
        "top",
        "top",
        children=(
            _node(
                "top.u_csr",
                "csr",
                instance=_instance("u_csr", "csr", 5),
                children=(
                    _node(
                        "top.u_csr.u_sync",
                        "sync",
                        instance=_instance("u_sync", "sync", 7),
                        children=(
                            _node(
                                "top.u_csr.u_sync.u_ff",
                                "ff",
                                instance=_instance("u_ff", "ff", 9),
                            ),
                        ),
                    ),
                ),
            ),
            _node(
                "top.u_tie",
                "tie",
                instance=_instance("u_tie", "tie", 11),
            ),
        ),
    )


def _paths(node: HierNode) -> list[str]:
    out = [node.instance_path]
    for child in node.children:
        out.extend(_paths(child))
    return out


def test_empty_map_returns_the_same_object() -> None:
    """Graceful degradation: no hints must cost nothing and change nothing."""
    root = _demo_tree()
    assert apply_hints(root, HintMap()) is root


def test_hide_removes_the_node_and_its_subtree() -> None:
    root = _demo_tree()
    hints = HintMap(instances={("top", "u_csr"): InstanceHints(hide=True)})
    assert _paths(apply_hints(root, hints)) == ["top", "top.u_tie"]


def test_collapse_drops_children_but_keeps_the_node() -> None:
    root = _demo_tree()
    hints = HintMap(instances={("csr", "u_sync"): InstanceHints(collapse=True)})
    assert _paths(apply_hints(root, hints)) == [
        "top",
        "top.u_csr",
        "top.u_csr.u_sync",
        "top.u_tie",
    ]


def test_module_leaf_drops_children_of_every_instance() -> None:
    root = _demo_tree()
    hints = HintMap(modules={"sync": ModuleHints(leaf=True)})
    result = apply_hints(root, hints)
    assert "top.u_csr.u_sync.u_ff" not in _paths(result)
    assert "top.u_csr.u_sync" in _paths(result)


def test_label_lands_on_display_label_and_instance_beats_module() -> None:
    root = _demo_tree()
    hints = HintMap(
        modules={"csr": ModuleHints(label="module label")},
        instances={("top", "u_csr"): InstanceHints(label="instance label")},
    )
    result = apply_hints(root, hints)
    assert result.children[0].display_label == "instance label"


def test_module_label_applies_when_the_instance_says_nothing() -> None:
    root = _demo_tree()
    hints = HintMap(modules={"tie": ModuleHints(label="tie-off")})
    assert apply_hints(root, hints).children[1].display_label == "tie-off"


def test_label_on_the_top_module_applies() -> None:
    root = _demo_tree()
    hints = HintMap(modules={"top": ModuleHints(label="subsystem")})
    assert apply_hints(root, hints).display_label == "subsystem"


def test_leaf_on_the_top_module_is_refused_with_a_warning() -> None:
    """An empty figure is never what the author meant."""
    root = _demo_tree()
    warnings: list[str] = []
    result = apply_hints(
        root, HintMap(modules={"top": ModuleHints(leaf=True)}), warnings=warnings
    )
    assert result.children  # the whole design survives
    assert "would empty the diagram" in warnings[0]


def test_apply_hints_accepts_no_warning_sink() -> None:
    root = _demo_tree()
    result = apply_hints(root, HintMap(modules={"top": ModuleHints(leaf=True)}))
    assert result.children


def test_untouched_subtrees_are_returned_by_identity() -> None:
    root = _demo_tree()
    hints = HintMap(instances={("top", "u_tie"): InstanceHints(label="tie")})
    result = apply_hints(root, hints)
    assert result.children[0] is root.children[0]


def test_apply_hints_is_deterministic_and_preserves_order() -> None:
    root = _demo_tree()
    hints = HintMap(
        modules={"sync": ModuleHints(leaf=True, label="sync")},
        instances={("top", "u_tie"): InstanceHints(hide=True)},
    )
    first = apply_hints(root, hints)
    second = apply_hints(root, hints)
    assert _paths(first) == _paths(second) == ["top", "top.u_csr", "top.u_csr.u_sync"]


# --- sidecar ----------------------------------------------------------------


def _sidecar(**payload: object) -> dict[str, object]:
    return {"schema_version": "1.0", **payload}


def test_sidecar_parses_modules_and_instances() -> None:
    hints = parse_hint_sidecar(
        _sidecar(
            modules={"sync": {"leaf": True, "label": "CDC sync"}},
            instances={"top.u_tie": {"hide": True, "collapse": False}},
        ),
        source="sidecar.json",
    )
    assert hints.for_module("sync") == ModuleHints(leaf=True, label="CDC sync")
    assert hints.for_instance("top", "u_tie") == InstanceHints(
        hide=True, collapse=False
    )


def test_sidecar_unknown_field_warns_but_loads() -> None:
    hints = parse_hint_sidecar(
        _sidecar(modules={"sync": {"leaf": True, "colour": "red"}}),
        source="sidecar.json",
    )
    assert hints.for_module("sync") == ModuleHints(leaf=True)
    assert "colour" in hints.warnings[0]


def test_sidecar_unknown_instance_field_warns_but_loads() -> None:
    hints = parse_hint_sidecar(
        _sidecar(instances={"top.u_a": {"hide": True, "colapse": True}}),
        source="sidecar.json",
    )
    assert hints.for_instance("top", "u_a") == InstanceHints(hide=True)
    assert "colapse" in hints.warnings[0]


def test_sidecar_empty_entry_is_dropped() -> None:
    hints = parse_hint_sidecar(_sidecar(modules={"sync": {}}), source="s.json")
    assert hints.is_empty


@pytest.mark.parametrize(
    "payload, message",
    [
        ([], "top-level must be a JSON object"),
        ({}, "schema_version missing"),
        ({"schema_version": 1}, "schema_version missing"),
        ({"schema_version": "2.0"}, "is not supported"),
        ({"schema_version": "x.0"}, "not a dotted number"),
        (_sidecar(modules=[]), "'modules' must be a JSON object"),
        (_sidecar(instances=[]), "'instances' must be a JSON object"),
        (_sidecar(modules={"a": 1}), "entry must be a JSON object"),
        (_sidecar(modules={"a": {"leaf": "yes"}}), "must be a boolean"),
        (_sidecar(modules={"a": {"label": 3}}), "must be a string"),
        (_sidecar(instances={"a": {"hide": True}}), "<parent_module>.<instance_name>"),
        (_sidecar(instances={"a.b.c": {}}), "<parent_module>.<instance_name>"),
        (_sidecar(instances={"a.b": 1}), "entry must be a JSON object"),
        (_sidecar(instances={"a.b": {"hide": 1}}), "must be a boolean"),
        (_sidecar(instances={"a.b": {"label": 1}}), "must be a string"),
    ],
)
def test_sidecar_rejects_malformed_payloads(payload: object, message: str) -> None:
    with pytest.raises(HintsError, match=message):
        parse_hint_sidecar(payload, source="sidecar.json")


def test_load_sidecar_reads_json(tmp_path) -> None:
    path = tmp_path / "hints.json"
    path.write_text(json.dumps(_sidecar(modules={"sync": {"leaf": True}})))
    assert load_hint_sidecar(path).for_module("sync") == ModuleHints(leaf=True)


def test_load_sidecar_wraps_missing_file(tmp_path) -> None:
    with pytest.raises(HintsError, match="could not read"):
        load_hint_sidecar(tmp_path / "absent.json")


def test_load_sidecar_wraps_invalid_json(tmp_path) -> None:
    path = tmp_path / "hints.json"
    path.write_text("{not json")
    with pytest.raises(HintsError, match="invalid JSON"):
        load_hint_sidecar(path)


# --- precedence -------------------------------------------------------------


def test_sidecar_wins_field_by_field() -> None:
    pragma = HintMap(
        modules={"sync": ModuleHints(leaf=True, label="from pragma")},
        instances={("top", "u_tie"): InstanceHints(hide=True)},
    )
    sidecar = HintMap(
        modules={"sync": ModuleHints(label="from sidecar")},
        instances={("top", "u_tie"): InstanceHints(collapse=True)},
    )
    merged = merge_hint_maps(pragma, sidecar)
    # Overridden where the sidecar spoke, preserved where it didn't.
    assert merged.for_module("sync") == ModuleHints(leaf=True, label="from sidecar")
    assert merged.for_instance("top", "u_tie") == InstanceHints(
        hide=True, collapse=True
    )


def test_sidecar_false_revokes_an_in_source_flag() -> None:
    pragma = HintMap(instances={("top", "u_a"): InstanceHints(collapse=True)})
    sidecar = HintMap(instances={("top", "u_a"): InstanceHints(collapse=False)})
    merged = merge_hint_maps(pragma, sidecar)
    assert merged.for_instance("top", "u_a") == InstanceHints(collapse=False)
    assert (
        apply_hints(
            _node(
                "top",
                "top",
                children=(
                    _node(
                        "top.u_a",
                        "a",
                        instance=_instance("u_a", "a", 3),
                        children=(_node("top.u_a.u_b", "b"),),
                    ),
                ),
            ),
            merged,
        )
        .children[0]
        .children
    )


def test_merge_keeps_targets_from_both_sides_and_concatenates_warnings() -> None:
    merged = merge_hint_maps(
        HintMap(modules={"a": ModuleHints(leaf=True)}, warnings=("one",)),
        HintMap(modules={"b": ModuleHints(leaf=True)}, warnings=("two",)),
    )
    assert sorted(merged.modules) == ["a", "b"]
    assert merged.warnings == ("one", "two")


def test_hints_overlay_loads_a_sidecar_and_no_ops_the_other_hooks(tmp_path) -> None:
    """The sidecar rides the standard overlay protocol; the graph
    rewrite happens once in the CLI over the *merged* map, so join
    and contribute are deliberately empty."""
    from rtl_buddy_view.overlays.hints import HintsOverlay

    path = tmp_path / "hints.json"
    path.write_text(json.dumps(_sidecar(modules={"sync": {"leaf": True}})))
    overlay = HintsOverlay()
    assert overlay.name == "hints"
    loaded = overlay.load(path)
    assert loaded.for_module("sync") == ModuleHints(leaf=True)
    assert overlay.join(_demo_tree(), loaded) is None
    assert overlay.contribute(None) is None


def test_public_constants_document_the_vocabulary() -> None:
    """The docs table and the warning text both derive from these."""
    assert hints_mod.PRAGMA_PREFIX == "rbsch"
    assert hints_mod.KNOWN_FLAGS == ("collapse", "hide", "leaf")
    assert hints_mod.KNOWN_KEYS == ("group", "in", "label", "out", "rank")
    assert hints_mod.KNOWN_NET_FLAGS == ("clock", "data", "main", "reset", "side")
    assert hints_mod.KNOWN_NET_KEYS == ("bundle",)


# --- phase 2: net vocabulary ------------------------------------------------


def _net_decl(name: str, line: int, file: str = FILE):
    from rtl_buddy_view.extractor import NetDecl

    return NetDecl(
        name=name,
        type_text="logic",
        location=SourceLocation(file=file, start_line=line),
    )


def _port_decl(name: str, line: int, file: str = FILE):
    from rtl_buddy_view.extractor import Port

    return Port(
        name=name,
        direction="input",
        type_text="logic",
        location=SourceLocation(file=file, start_line=line),
    )


def _net_module(
    name: str,
    line: int,
    *,
    nets: tuple = (),
    ports: tuple = (),
    instances: tuple[Instance, ...] = (),
) -> Module:
    return Module(
        name=name,
        ports=ports,
        parameters=(),
        instances=instances,
        location=SourceLocation(file=FILE, start_line=line),
        net_decls=nets,
    )


def test_parse_accepts_net_flags() -> None:
    payload = parse_pragma_payload("clock main")
    assert payload.flags == ("clock", "main")
    assert payload.warnings == ()


def test_parse_net_flag_with_value_warns() -> None:
    payload = parse_pragma_payload("clock=false")
    assert payload.flags == ()
    assert "takes no value" in payload.warnings[0]


def test_trailing_net_pragma_flags_every_declarator_on_the_line() -> None:
    """``logic a, b;  // rbsch: clock`` classifies both declarators."""
    table = _table(_net_module("m", 1, nets=(_net_decl("a", 5), _net_decl("b", 5))))
    text = "\n" * 4 + "logic a, b;  // rbsch: clock\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets[("m", "a")].classification == "clock"
    assert hints.nets[("m", "b")].classification == "clock"
    assert hints.warnings == ()


def test_standalone_net_pragma_covers_one_declaration_line_only() -> None:
    """The next declaration *line*, never a run — one comment must not
    silently reclassify half a module."""
    table = _table(_net_module("m", 1, nets=(_net_decl("a", 5), _net_decl("b", 6))))
    text = "\n" * 3 + "// rbsch: side\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets[("m", "a")].emphasis == "side"
    assert ("m", "b") not in hints.nets


def test_net_pragma_binds_to_a_port_declaration() -> None:
    """A clock is very often a port of the scope being drawn."""
    table = _table(_net_module("m", 1, ports=(_port_decl("tick", 3),)))
    text = "\n" * 2 + "input logic tick,  // rbsch: clock\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets[("m", "tick")].classification == "clock"


def test_box_pragma_skips_net_declarations() -> None:
    """Phase-1 stability: teaching the index about nets must not
    re-bind ``// rbsch: collapse`` sitting above a net declaration."""
    table = _table(
        _net_module(
            "m",
            1,
            nets=(_net_decl("w", 4),),
            instances=(_instance("u_x", "x", 5),),
        )
    )
    text = "\n" * 2 + "// rbsch: collapse\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.instances[("m", "u_x")].collapse is True
    assert hints.nets == {}
    assert hints.warnings == ()


def test_net_pragma_skips_box_declarations() -> None:
    """The mirror image: a net word reaches past an instance to the
    next net declaration."""
    table = _table(
        _net_module(
            "m",
            1,
            nets=(_net_decl("w", 6),),
            instances=(_instance("u_x", "x", 4),),
        )
    )
    text = "\n" * 2 + "// rbsch: main\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets[("m", "w")].emphasis == "main"
    assert hints.instances == {}


def test_mixed_payload_binds_each_family_independently() -> None:
    table = _table(
        _net_module(
            "m",
            1,
            nets=(_net_decl("w", 4),),
            instances=(_instance("u_x", "x", 6),),
        )
    )
    text = "\n" * 2 + "// rbsch: collapse main\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.instances[("m", "u_x")].collapse is True
    assert hints.nets[("m", "w")].emphasis == "main"


def test_net_pragma_with_no_net_target_warns() -> None:
    table = _table(_net_module("m", 1, instances=(_instance("u_x", "x", 3),)))
    text = "\n" * 2 + "u_x x ();  // rbsch: clock\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets == {}
    assert any("matched no net or port declaration" in w for w in hints.warnings)


def test_conflicting_classifications_warn_and_drop() -> None:
    table = _table(_net_module("m", 1, nets=(_net_decl("w", 3),)))
    text = "\n" * 2 + "logic w;  // rbsch: clock reset main\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    # The emphasis half of the payload still lands.
    assert hints.nets[("m", "w")] == hints_mod.NetHints(emphasis="main")
    assert any("mutually exclusive" in w for w in hints.warnings)


def test_conflicting_emphasis_warns_and_drops() -> None:
    table = _table(_net_module("m", 1, nets=(_net_decl("w", 3),)))
    text = "\n" * 2 + "logic w;  // rbsch: main side data\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets[("m", "w")] == hints_mod.NetHints(classification="data")
    assert any("mutually exclusive" in w for w in hints.warnings)


def test_later_net_pragma_wins_per_field() -> None:
    table = _table(_net_module("m", 1, nets=(_net_decl("w", 4),)))
    text = "\n" * 2 + "// rbsch: clock\nlogic w;  // rbsch: data main\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets[("m", "w")] == hints_mod.NetHints(
        classification="data", emphasis="main"
    )


def test_net_only_map_is_not_empty_but_leaves_the_tree_alone() -> None:
    hints = HintMap(nets={("m", "w"): hints_mod.NetHints(emphasis="main")})
    assert not hints.is_empty
    root = _node("top", "m")
    assert apply_hints(root, hints) is root


def test_nets_for_module_filters_to_one_scope() -> None:
    hints = HintMap(
        nets={
            ("m", "w"): hints_mod.NetHints(emphasis="main"),
            ("other", "w"): hints_mod.NetHints(classification="clock"),
        }
    )
    assert hints.nets_for_module("m") == {"w": hints_mod.NetHints(emphasis="main")}


def test_sidecar_parses_nets() -> None:
    hints = parse_hint_sidecar(
        {
            "schema_version": "1.1",
            "nets": {
                "m.tick": {"classification": "clock"},
                "m.stage": {"emphasis": "main"},
            },
        },
        source="hints.json",
    )
    assert hints.nets[("m", "tick")].classification == "clock"
    assert hints.nets[("m", "stage")].emphasis == "main"
    assert hints.warnings == ()


def test_sidecar_bad_classification_value_raises() -> None:
    with pytest.raises(HintsError, match="classification must be one of"):
        parse_hint_sidecar(
            {"schema_version": "1.1", "nets": {"m.w": {"classification": "clokc"}}},
            source="hints.json",
        )


def test_sidecar_bad_emphasis_value_raises() -> None:
    with pytest.raises(HintsError, match="emphasis must be one of"):
        parse_hint_sidecar(
            {"schema_version": "1.1", "nets": {"m.w": {"emphasis": "primary"}}},
            source="hints.json",
        )


def test_sidecar_bad_net_key_raises() -> None:
    with pytest.raises(HintsError, match="'<module>.<net_name>'"):
        parse_hint_sidecar(
            {"schema_version": "1.1", "nets": {"w": {"emphasis": "main"}}},
            source="hints.json",
        )


def test_sidecar_unknown_net_field_warns_but_loads() -> None:
    hints = parse_hint_sidecar(
        {
            "schema_version": "1.1",
            "nets": {"m.w": {"emphasis": "side", "bundel": "x"}},
        },
        source="hints.json",
    )
    assert hints.nets[("m", "w")].emphasis == "side"
    assert any("bundel" in w for w in hints.warnings)


def test_merge_sidecar_overrides_net_fields_independently() -> None:
    base = HintMap(
        nets={("m", "w"): hints_mod.NetHints(classification="clock", emphasis="main")}
    )
    override = HintMap(nets={("m", "w"): hints_mod.NetHints(classification="data")})
    merged = merge_hint_maps(base, override)
    # Classification overridden, in-source emphasis kept.
    assert merged.nets[("m", "w")] == hints_mod.NetHints(
        classification="data", emphasis="main"
    )


# --- phase 3: bundles -------------------------------------------------------


def test_parse_bundle_key() -> None:
    payload = parse_pragma_payload("bundle=cmd_bus")
    assert payload.options == (("bundle", "cmd_bus"),)
    assert payload.warnings == ()


def test_parse_bare_bundle_warns() -> None:
    assert "needs a value" in parse_pragma_payload("bundle").warnings[0]


def test_standalone_bundle_covers_the_contiguous_run() -> None:
    """One comment above the group names the whole visually grouped
    block — and stops at the first gap."""
    table = _table(
        _net_module(
            "m",
            1,
            nets=(
                _net_decl("cmd_valid", 5),
                _net_decl("cmd_data", 6),
                _net_decl("cmd_ready", 7),
                _net_decl("plain", 9),  # after a blank line — not covered
            ),
        )
    )
    text = "\n" * 3 + "// rbsch: bundle=cmd_bus\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets[("m", "cmd_valid")].bundle == "cmd_bus"
    assert hints.nets[("m", "cmd_data")].bundle == "cmd_bus"
    assert hints.nets[("m", "cmd_ready")].bundle == "cmd_bus"
    assert ("m", "plain") not in hints.nets


def test_other_net_words_ride_the_bundle_run() -> None:
    table = _table(_net_module("m", 1, nets=(_net_decl("a", 5), _net_decl("b", 6))))
    text = "\n" * 3 + "// rbsch: bundle=cmd_bus main\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets[("m", "b")] == hints_mod.NetHints(
        emphasis="main", bundle="cmd_bus"
    )


def test_trailing_bundle_covers_its_line_only() -> None:
    table = _table(_net_module("m", 1, nets=(_net_decl("a", 5), _net_decl("b", 6))))
    text = "\n" * 4 + "logic a;  // rbsch: bundle=x\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets[("m", "a")].bundle == "x"
    assert ("m", "b") not in hints.nets


def test_classification_run_is_still_one_line_without_bundle() -> None:
    """The run rule belongs to ``bundle=`` alone."""
    table = _table(_net_module("m", 1, nets=(_net_decl("a", 5), _net_decl("b", 6))))
    text = "\n" * 3 + "// rbsch: clock\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert ("m", "b") not in hints.nets


def test_empty_bundle_name_warns_and_is_ignored() -> None:
    table = _table(_net_module("m", 1, nets=(_net_decl("a", 4),)))
    text = "\n" * 2 + "// rbsch: bundle=\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.nets == {}
    assert any("bundle needs a name" in w for w in hints.warnings)


def test_sidecar_parses_and_revokes_bundle() -> None:
    hints = parse_hint_sidecar(
        {
            "schema_version": "1.1",
            "nets": {
                "m.a": {"bundle": "cmd_bus"},
                "m.b": {"bundle": ""},
            },
        },
        source="hints.json",
    )
    assert hints.nets[("m", "a")].bundle == "cmd_bus"
    assert hints.nets[("m", "b")].bundle == ""


def test_merge_sidecar_empty_bundle_revokes_in_source_membership() -> None:
    base = HintMap(nets={("m", "a"): hints_mod.NetHints(bundle="cmd_bus")})
    override = HintMap(nets={("m", "a"): hints_mod.NetHints(bundle="")})
    merged = merge_hint_maps(base, override)
    assert merged.nets[("m", "a")].bundle == ""


# --- phase 4: layout hints --------------------------------------------------


def test_rank_and_group_parse_and_land_on_the_instance() -> None:
    table = _table(_module("m", 1, instances=(_instance("u_a", "a", 3),)))
    text = "\n" * 2 + "a u_a ();  // rbsch: rank=2 group=frontend\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.instances[("m", "u_a")] == InstanceHints(rank=2, group="frontend")
    assert hints.warnings == ()


def test_non_integer_rank_warns_and_is_ignored() -> None:
    table = _table(_module("m", 1, instances=(_instance("u_a", "a", 3),)))
    text = "\n" * 2 + "a u_a ();  // rbsch: rank=first\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.instances == {}
    assert any("rank must be an integer" in w for w in hints.warnings)


def test_empty_group_name_warns_and_is_ignored() -> None:
    table = _table(_module("m", 1, instances=(_instance("u_a", "a", 3),)))
    text = "\n" * 2 + "a u_a ();  // rbsch: group=\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.instances == {}
    assert any("group needs a name" in w for w in hints.warnings)


def test_rank_on_a_module_warns() -> None:
    table = _table(_module("m", 3))
    text = "\n// rbsch: rank=1\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.modules == {}
    assert any("'rank' applies to an instance" in w for w in hints.warnings)


def test_sidecar_parses_rank_and_group() -> None:
    hints = parse_hint_sidecar(
        {
            "schema_version": "1.1",
            "instances": {"m.u_a": {"rank": 3, "group": "backend"}},
        },
        source="hints.json",
    )
    assert hints.instances[("m", "u_a")] == InstanceHints(rank=3, group="backend")


def test_sidecar_boolean_rank_raises() -> None:
    """``bool`` is an ``int`` subclass; a JSON ``true`` is a mistake,
    not a column."""
    with pytest.raises(HintsError, match="must be an integer"):
        parse_hint_sidecar(
            {"schema_version": "1.1", "instances": {"m.u_a": {"rank": True}}},
            source="hints.json",
        )


def test_merge_sidecar_empty_group_revokes_membership() -> None:
    base = HintMap(instances={("m", "u_a"): InstanceHints(group="frontend", rank=1)})
    override = HintMap(instances={("m", "u_a"): InstanceHints(group="")})
    merged = merge_hint_maps(base, override)
    assert merged.instances[("m", "u_a")] == InstanceHints(group="", rank=1)


# --- phase 5: blackbox pin directions ---------------------------------------


def test_in_out_parse_split_strip_and_dedupe() -> None:
    table = _table(_module("m", 1, instances=(_instance("u_pll", "vendor_pll", 3),)))
    text = (
        "\n" * 2
        + 'vendor_pll u_pll ();  // rbsch: in="refclk, pd,refclk" out=clk_out\n'
    )
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.instances[("m", "u_pll")] == InstanceHints(
        in_pins=("pd", "refclk"), out_pins=("clk_out",)
    )
    assert hints.warnings == ()


def test_pin_in_both_lists_warns_and_drops_from_both() -> None:
    table = _table(_module("m", 1, instances=(_instance("u_x", "bb", 3),)))
    text = "\n" * 2 + "bb u_x ();  // rbsch: in=a,b out=b,c\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.instances[("m", "u_x")] == InstanceHints(
        in_pins=("a",), out_pins=("c",)
    )
    assert any("named in both in= and out=" in w for w in hints.warnings)


def test_empty_pin_list_warns_and_is_ignored() -> None:
    table = _table(_module("m", 1, instances=(_instance("u_x", "bb", 3),)))
    text = "\n" * 2 + "bb u_x ();  // rbsch: in=,\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.instances == {}
    assert any("comma-separated pin list" in w for w in hints.warnings)


def test_in_out_are_legal_on_a_module_declaration() -> None:
    table = _table(_module("m", 3))
    text = "\n" * 1 + "// rbsch: out=q\n"
    hints = resolve_hints(scan_pragmas({FILE: text}), table)
    assert hints.modules["m"] == ModuleHints(out_pins=("q",))


def test_pin_directions_for_applies_module_hints_to_every_placement() -> None:
    scope = _module(
        "top",
        1,
        instances=(
            _instance("u_a", "vendor_rom", 3),
            _instance("u_b", "vendor_rom", 4),
            _instance("u_c", "other", 5),
        ),
    )
    hints = HintMap(
        modules={"vendor_rom": ModuleHints(in_pins=("addr",), out_pins=("q",))}
    )
    assert hints.pin_directions_for(scope) == {
        "u_a": {"addr": "input", "q": "output"},
        "u_b": {"addr": "input", "q": "output"},
    }


def test_instance_pin_hints_replace_module_hints_wholesale() -> None:
    """One author statement per placement — never a per-pin interleave."""
    scope = _module("top", 1, instances=(_instance("u_a", "vendor_rom", 3),))
    hints = HintMap(
        modules={"vendor_rom": ModuleHints(in_pins=("addr",), out_pins=("q",))},
        instances={("top", "u_a"): InstanceHints(out_pins=("addr",))},
    )
    assert hints.pin_directions_for(scope) == {"u_a": {"addr": "output"}}


def test_sidecar_parses_pin_arrays_and_empty_list_revokes() -> None:
    hints = parse_hint_sidecar(
        {
            "schema_version": "1.1",
            "modules": {"vendor_rom": {"in": ["addr"], "out": ["q"]}},
            "instances": {"top.u_a": {"in": []}},
        },
        source="hints.json",
    )
    assert hints.modules["vendor_rom"] == ModuleHints(
        in_pins=("addr",), out_pins=("q",)
    )
    assert hints.instances[("top", "u_a")] == InstanceHints(in_pins=())


def test_sidecar_non_array_pin_list_raises() -> None:
    with pytest.raises(HintsError, match="array of pin-name strings"):
        parse_hint_sidecar(
            {"schema_version": "1.1", "modules": {"bb": {"in": "addr"}}},
            source="hints.json",
        )


def test_merge_sidecar_pin_lists_replace_in_source_ones() -> None:
    base = HintMap(modules={"bb": ModuleHints(in_pins=("a",), out_pins=("q",))})
    override = HintMap(modules={"bb": ModuleHints(out_pins=("clk_out",))})
    merged = merge_hint_maps(base, override)
    assert merged.modules["bb"] == ModuleHints(in_pins=("a",), out_pins=("clk_out",))
