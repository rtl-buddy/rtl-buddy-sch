"""Phase 7b physical overlay tests (rtl-buddy/rtl-buddy-sch#22).

Four layers covered:

* :mod:`rtl_buddy_view.phys_annotations` — the loader (manifest
  pointer, model directly, directory, schema rejection, half-filled
  documents) and the two joins. The joins are the trust boundary, so
  every figure the tests assert is a hand-checked sum against
  ``tests/fixtures/phys_design/phys-model.json``.
* The Liberty/RTL **name collision**, which is why the power channel
  joins on instance paths and never on the instance rows' ``module``
  field. The fixture design contains an RTL module called ``DFF_X1``
  and the model's leaf rows carry ``module: "DFF_X1"`` for flops that
  live elsewhere; a naive join would pile every flop's power onto the
  one node.
* The **wrapper top**: the same model rendered under a testbench, where
  every rootless row has to be rooted at ``tb_phys_top.u_dut``, and a
  design the model's top is nowhere in, where the overlay must attach
  nothing and say so.
* Renderer emission — ``node.overlays.phys`` + ``overlay_meta.phys``
  in ``view.json``, the ASCII tree suffix, byte-identical tree output
  without the overlay, and the CLI flag end to end.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from rtl_buddy_view.extractor import Instance
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.overlays import default_registry
from rtl_buddy_view.overlays.phys import PhysOverlay
from rtl_buddy_view.phys_annotations import (
    MODEL_SCHEMA_VERSION,
    PhysAnnotationsError,
    join_hierarchy,
    level_path,
    load_phys_model,
    rooted_model_path,
    tree_suffix,
)
from rtl_buddy_view.render import json_render
from rtl_buddy_view.render import tree as tree_render

FIXTURE = Path(__file__).parent / "fixtures" / "phys_design"
MODEL = FIXTURE / "phys-model.json"
MANIFEST = FIXTURE / "phys-manifest.json"
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "view-v1.json"

#: 0.5% is the acceptance tolerance from the issue. The producer's
#: ``totals`` come from a different scrape of the same run than the
#: rows do, so they are close but not equal by construction — that
#: non-tautology is the point of comparing them at all.
TOLERANCE = 0.005


# ---------------------------------------------------------------------------
# synthetic hierarchies (no Verible needed)
# ---------------------------------------------------------------------------


def _node(
    path: str,
    module: str,
    *,
    inst_name: str | None = None,
    children: tuple[HierNode, ...] = (),
    is_blackbox: bool = False,
) -> HierNode:
    inst = (
        Instance(
            name=inst_name,
            module_name=module,
            param_overrides=(),
            port_connections=(),
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


def _design() -> HierNode:
    """The fixture design, as ``build_hierarchy`` would produce it."""
    leaf = _node("phys_top.u_sub.u_leaf", "phys_leaf", inst_name="u_leaf")
    sub = _node("phys_top.u_sub", "phys_sub", inst_name="u_sub", children=(leaf,))
    dff = _node("phys_top.u_dff", "DFF_X1", inst_name="u_dff")
    return _node("phys_top", "phys_top", children=(sub, dff))


def _tb_design() -> HierNode:
    """The same design under a testbench wrapper."""
    dut = _design()
    rooted = _reroot(dut, "tb_phys_top.u_dut", inst_name="u_dut")
    return _node("tb_phys_top", "tb_phys_top", children=(rooted,))


def _reroot(node: HierNode, path: str, *, inst_name: str | None = None) -> HierNode:
    tail = node.instance_path
    children = tuple(
        _reroot(
            child,
            path + child.instance_path[len(tail) :],
            inst_name=child.instance.name if child.instance else None,
        )
        for child in node.children
    )
    return _node(path, node.module_name, inst_name=inst_name, children=children)


# ---------------------------------------------------------------------------
# loader
# ---------------------------------------------------------------------------


def test_load_via_manifest_follows_the_model_pointer():
    model = load_phys_model(MANIFEST)
    assert model.top == "phys_top"
    assert model.manifest == str(MANIFEST)
    assert model.source.endswith("phys-model.json")
    assert sorted(model.modules) == ["DFF_X1", "phys_leaf", "phys_sub", "phys_top"]
    assert len(model.instances) == 4
    assert model.notes == ()
    assert not model.is_empty


def test_load_model_directly_matches_the_manifest_route():
    direct = load_phys_model(MODEL)
    via = load_phys_model(MANIFEST)
    assert direct.modules == via.modules
    assert direct.instances == via.instances
    # The only difference is provenance: a model read directly knows
    # no manifest.
    assert direct.manifest is None


def test_load_directory_prefers_the_manifest():
    model = load_phys_model(FIXTURE)
    assert model.manifest == str(FIXTURE / "phys-manifest.json")


def test_load_directory_without_either_document_errors(tmp_path):
    with pytest.raises(PhysAnnotationsError, match="no phys-manifest.json"):
        load_phys_model(tmp_path)


def test_instance_rows_are_sorted_and_scaled_as_written():
    rows = load_phys_model(MODEL).instances
    assert [r.instance_path for r in rows] == [
        "_9_",
        "u_dff/_18_",
        "u_sub/_31_",
        "u_sub/u_leaf/_42_",
    ]
    hot = rows[-1]
    assert hot.module == "DFF_X1"
    assert hot.total_uw == pytest.approx(2.4266)
    assert hot.dynamic_uw == pytest.approx(2.28 + 0.0675)


# ---------------------------------------------------------------------------
# schema rejection
# ---------------------------------------------------------------------------


def test_rejects_a_model_schema_version_it_does_not_speak(tmp_path):
    doc = json.loads(MODEL.read_text())
    doc["schema_version"] = MODEL_SCHEMA_VERSION + 1
    path = tmp_path / "phys-model.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(PhysAnnotationsError) as exc:
        load_phys_model(path)
    message = str(exc.value)
    assert "unsupported phys-model schema_version 2" in message
    # A complete sentence naming the way out, per docs/overlays.md.
    assert "regenerate the artefacts" in message


def test_rejects_a_manifest_schema_version_it_does_not_speak(tmp_path):
    doc = json.loads(MANIFEST.read_text())
    doc["schema_version"] = 99
    path = tmp_path / "phys-manifest.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(PhysAnnotationsError, match="unsupported phys-manifest"):
        load_phys_model(path)


def test_rejects_a_document_with_no_schema_version(tmp_path):
    path = tmp_path / "phys-model.json"
    path.write_text('{"modules": [], "instances": []}')
    with pytest.raises(PhysAnnotationsError, match="no 'schema_version' key"):
        load_phys_model(path)


def test_rejects_malformed_documents(tmp_path):
    bad_json = tmp_path / "phys-model.json"
    bad_json.write_text("{not json")
    with pytest.raises(PhysAnnotationsError, match="not valid JSON"):
        load_phys_model(bad_json)

    not_object = tmp_path / "a.json"
    not_object.write_text("[]")
    with pytest.raises(PhysAnnotationsError, match="expected a JSON object"):
        load_phys_model(not_object)

    bad_half = tmp_path / "phys-model.json"
    bad_half.write_text(
        json.dumps({"schema_version": 1, "modules": {}, "instances": None})
    )
    with pytest.raises(PhysAnnotationsError, match="'modules' must be an array"):
        load_phys_model(bad_half)

    bad_row = tmp_path / "b.json"
    bad_row.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "instances": [{"instance_path": "u_a", "total_uw": "hot"}],
            }
        )
    )
    with pytest.raises(PhysAnnotationsError, match="non-numeric measurement"):
        load_phys_model(bad_row)


def test_manifest_whose_model_pointer_resolves_to_nothing_errors(tmp_path):
    doc = json.loads(MANIFEST.read_text())
    doc["model"] = "nowhere/phys-model.json"
    path = tmp_path / "phys-manifest.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(PhysAnnotationsError, match="the model it points at"):
        load_phys_model(path)


def test_model_beside_the_manifest_is_the_last_fallback(tmp_path):
    """A manifest read from somewhere the project root can't be counted
    back to still finds its model — every producer writes the two
    documents into one directory."""
    doc = json.loads(MANIFEST.read_text())
    doc["model"] = "some/other/project/phys-model.json"
    (tmp_path / "phys-manifest.json").write_text(json.dumps(doc))
    (tmp_path / "phys-model.json").write_text(MODEL.read_text())
    model = load_phys_model(tmp_path / "phys-manifest.json")
    assert model.top == "phys_top"


# ---------------------------------------------------------------------------
# half-filled models + units: warn, never fail
# ---------------------------------------------------------------------------


def test_a_synth_only_model_warns_and_contributes_area(tmp_path):
    doc = json.loads(MODEL.read_text())
    doc["instances"] = None
    path = tmp_path / "phys-model.json"
    path.write_text(json.dumps(doc))
    model = load_phys_model(path)
    assert any("rb power" in note for note in model.notes)
    join = join_hierarchy(model, _design())
    block = join.blocks["phys_top"]
    assert block["area_um2"] == pytest.approx(11.172)
    # No power half → no power keys at all, rather than a block of
    # zeroes a viewer would paint as "this design burns nothing".
    assert "total_uw" not in block
    assert join.meta["halves"] == {"modules": True, "instances": False}


def test_a_power_only_model_warns_and_contributes_power(tmp_path):
    doc = json.loads(MODEL.read_text())
    doc["modules"] = None
    path = tmp_path / "phys-model.json"
    path.write_text(json.dumps(doc))
    model = load_phys_model(path)
    assert any("rb synth" in note for note in model.notes)
    join = join_hierarchy(model, _design())
    block = join.blocks["phys_top"]
    assert "area_um2" not in block
    assert block["subtree_total_uw"] == pytest.approx(2.8565)


def test_unexpected_units_warn_rather_than_fail(tmp_path):
    doc = json.loads(MODEL.read_text())
    doc["units"] = {"area": "nm2", "power": "mW"}
    path = tmp_path / "phys-model.json"
    path.write_text(json.dumps(doc))
    model = load_phys_model(path)
    assert len(model.notes) == 2
    assert any("'nm2'" in note for note in model.notes)
    assert any("'mW'" in note for note in model.notes)


def test_a_mismatched_publication_pair_is_noted(tmp_path):
    doc = json.loads(MANIFEST.read_text())
    doc["publication"] = "0" * 32
    (tmp_path / "phys-manifest.json").write_text(json.dumps(doc))
    (tmp_path / "phys-model.json").write_text(MODEL.read_text())
    model = load_phys_model(tmp_path / "phys-manifest.json")
    assert any("publication tokens" in note for note in model.notes)


# ---------------------------------------------------------------------------
# path levelling
# ---------------------------------------------------------------------------


def test_level_path_treats_both_separators_as_levels():
    assert level_path("u_sub.u_leaf") == "u_sub/u_leaf"
    assert level_path("u_sub/u_leaf") == "u_sub/u_leaf"


def test_level_path_keeps_an_escaped_identifier_whole():
    # ``\gen[0].u_x`` is one name: the ``.`` inside names no level, so
    # levelling it would split one leaf into two and hang the row off a
    # parent that does not exist.
    assert level_path("u_top/\\gen[0].u_x") == "u_top/\\gen[0].u_x"
    assert level_path("u_top/\\gen[0].u_x ") == "u_top/\\gen[0].u_x"


def test_rooting_is_unconditional_even_when_the_row_starts_with_the_top():
    # The trap the /phy pane documents: under a top named ``cpu``, the
    # rootless row ``cpu/alu`` is a ``cpu`` INSTANCE inside the top,
    # and a prefix test would read it as already rooted.
    assert rooted_model_path("cpu/alu", "cpu") == "cpu/cpu/alu"
    assert rooted_model_path("u_a", "") == "u_a"


# ---------------------------------------------------------------------------
# the join
# ---------------------------------------------------------------------------


def test_leaf_rows_land_on_their_nearest_enclosing_scope():
    join = join_hierarchy(load_phys_model(MODEL), _design())
    assert join.attached
    # ``_9_`` is a cell in the top's own scope; the module-level view
    # has no node for a cell, so the row belongs to the top.
    assert join.blocks["phys_top"]["total_uw"] == pytest.approx(0.075)
    assert join.blocks["phys_top"]["leaf_instances"] == 1
    # ``u_sub/_31_`` → u_sub; ``u_sub/u_leaf/_42_`` → u_leaf, not u_sub.
    assert join.blocks["phys_top.u_sub"]["total_uw"] == pytest.approx(0.0888)
    assert join.blocks["phys_top.u_sub.u_leaf"]["total_uw"] == pytest.approx(2.4266)
    assert join.meta["matched_instance_rows"] == 4
    assert join.meta["unmatched_instance_rows"] == 0


def test_parents_carry_the_subtree_sum():
    join = join_hierarchy(load_phys_model(MODEL), _design())
    sub = join.blocks["phys_top.u_sub"]
    # 0.0888 (its own cell) + 2.4266 (the leaf below it).
    assert sub["subtree_total_uw"] == pytest.approx(0.0888 + 2.4266)
    assert sub["subtree_leaf_instances"] == 2
    top = join.blocks["phys_top"]
    assert top["subtree_leaf_instances"] == 4
    assert top["subtree_dynamic_uw"] == pytest.approx(
        top["subtree_internal_uw"] + top["subtree_switching_uw"]
    )


def test_rolled_up_totals_match_the_models_own_within_half_a_percent():
    """The issue's acceptance criterion, and the reason the roll-up is
    worth making at all: ``totals`` is a different scrape of the same
    run, so agreement is evidence rather than a tautology."""
    model = load_phys_model(MODEL)
    rollup = join_hierarchy(model, _design()).meta["rollup"]
    for key in ("leakage_uw", "internal_uw", "switching_uw", "total_uw"):
        expected = model.totals[key]
        assert expected is not None
        assert abs(rollup[key] - expected) <= TOLERANCE * abs(expected), key
    # Area is not summed — the producer's module area already rolls the
    # submodules up — so the top module's row IS the design total.
    assert rollup["area_um2"] == pytest.approx(model.totals["area_um2"])


def test_area_comes_from_the_module_row_and_self_area_is_derived():
    join = join_hierarchy(load_phys_model(MODEL), _design())
    top = join.blocks["phys_top"]
    assert top["cell_count"] == 3
    assert top["area_um2"] == pytest.approx(11.172)
    # 11.172 − (5.586 + 4.522): what the top's own gates cost.
    assert top["self_area_um2"] == pytest.approx(1.064)
    # A leaf node's self area is its whole area.
    leaf = join.blocks["phys_top.u_sub.u_leaf"]
    assert leaf["self_area_um2"] == pytest.approx(leaf["area_um2"])


def test_self_area_is_omitted_when_a_child_has_no_row():
    """Rather than subtracting nothing and reporting the subtree area
    as the self area, which would read as a block that costs its
    children's silicon."""
    model = load_phys_model(MODEL)
    orphan = _node("phys_top.u_ip", "unmapped_ip", inst_name="u_ip")
    design = _node("phys_top", "phys_top", children=(orphan,))
    join = join_hierarchy(model, design)
    block = join.blocks["phys_top"]
    assert block["area_um2"] == pytest.approx(11.172)
    assert "self_area_um2" not in block


def test_all_instances_of_one_module_share_its_area_but_not_its_power():
    """Two instances of ``phys_sub`` get the same module row (area is a
    property of the module) and different power (the leaf rows under
    each are different rows)."""
    model = load_phys_model(MODEL)
    leaf = _node("phys_top.u_sub.u_leaf", "phys_leaf", inst_name="u_leaf")
    sub_a = _node("phys_top.u_sub", "phys_sub", inst_name="u_sub", children=(leaf,))
    sub_b = _node("phys_top.u_sub2", "phys_sub", inst_name="u_sub2")
    design = _node("phys_top", "phys_top", children=(sub_a, sub_b))
    join = join_hierarchy(model, design)
    assert join.blocks["phys_top.u_sub"]["area_um2"] == pytest.approx(5.586)
    assert join.blocks["phys_top.u_sub2"]["area_um2"] == pytest.approx(5.586)
    assert join.blocks["phys_top.u_sub2"]["subtree_total_uw"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# the Liberty / RTL name collision
# ---------------------------------------------------------------------------


def test_liberty_cell_names_are_joined_to_nothing():
    """The fixture's ``DFF_X1`` is an RTL module AND the Liberty cell
    three of the model's leaf rows are instances of. The RTL↔RTL join
    gives the node its area; the power channel joins on paths only, so
    the node's power is exactly what sits under ``u_dff`` — not the sum
    of every ``module: "DFF_X1"`` row in the design."""
    model = load_phys_model(MODEL)
    liberty_dff_rows = [r for r in model.instances if r.module == "DFF_X1"]
    assert len(liberty_dff_rows) == 2
    naive_sum = sum(r.total_uw for r in liberty_dff_rows)

    block = join_hierarchy(model, _design()).blocks["phys_top.u_dff"]
    # Area: the RTL module row of the same name. This join is correct.
    assert block["area_um2"] == pytest.approx(4.522)
    # Power: only ``u_dff/_18_``. The other DFF_X1 row lives under
    # u_leaf and stays there.
    assert block["total_uw"] == pytest.approx(0.2661)
    assert block["subtree_total_uw"] == pytest.approx(0.2661)
    assert not block["total_uw"] == pytest.approx(naive_sum)


# ---------------------------------------------------------------------------
# wrapper tops
# ---------------------------------------------------------------------------


def test_a_wrapper_top_roots_the_join_at_the_models_own_top():
    join = join_hierarchy(load_phys_model(MODEL), _tb_design())
    assert join.attached
    assert join.meta["view_root"] == "tb_phys_top"
    assert join.meta["join_root"] == "tb_phys_top.u_dut"
    assert join.meta["matched_instance_rows"] == 4
    # Every figure is the same as the DUT-rooted render, one level down.
    assert join.blocks["tb_phys_top.u_dut.u_sub.u_leaf"]["total_uw"] == pytest.approx(
        2.4266
    )
    # The TB scope above has no module row (the synthesis never saw it)
    # but does carry the roll-up of everything below it.
    tb = join.blocks["tb_phys_top"]
    assert "area_um2" not in tb
    assert tb["subtree_total_uw"] == pytest.approx(2.8565)


def test_a_model_of_another_design_attaches_nothing_and_says_so():
    model = load_phys_model(MODEL)
    other = _node(
        "counter", "counter", children=(_node("counter.u_ff", "ff", inst_name="u_ff"),)
    )
    join = join_hierarchy(model, other)
    assert not join.attached
    assert join.blocks == {}
    assert join.meta["join_root"] is None
    assert join.meta["attached"] is False
    assert any("is not this design" in note for note in join.meta["notes"])


def test_a_twice_instantiated_model_top_picks_the_shallowest_and_warns():
    model = load_phys_model(MODEL)
    first = _reroot(_design(), "tb_phys_top.u_a", inst_name="u_a")
    second = _reroot(_design(), "tb_phys_top.u_b", inst_name="u_b")
    design = _node("tb_phys_top", "tb_phys_top", children=(first, second))
    join = join_hierarchy(model, design)
    assert join.meta["join_root"] == "tb_phys_top.u_a"
    assert any("instantiated 2 times" in note for note in join.meta["notes"])
    # The second copy carries area (a module property) but no power —
    # the model describes one of the two, and guessing which would be
    # worse than saying nothing.
    assert join.blocks["tb_phys_top.u_b"]["area_um2"] == pytest.approx(11.172)
    assert join.blocks["tb_phys_top.u_b"]["subtree_total_uw"] == pytest.approx(0.0)


def test_rows_that_match_no_scope_are_reported_not_silently_dropped():
    """A netlist whose hierarchy differs from the RTL as rendered —
    a collapse pragma, a flattened partition — leaves rows with
    nowhere to go. Counting them is what keeps the roll-up honest."""
    model = load_phys_model(MODEL)
    bare = _node("phys_top", "phys_top")  # no children at all
    join = join_hierarchy(model, bare)
    # Every row resolves to the top by walking up, so nothing is lost
    # here — the roll-up still equals the design total.
    assert join.meta["unmatched_instance_rows"] == 0
    assert join.blocks["phys_top"]["subtree_total_uw"] == pytest.approx(2.8565)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_phys_overlay_registered_by_default():
    registry = default_registry()
    assert "phys" in registry.names()
    overlay = registry.get("phys")
    assert isinstance(overlay, PhysOverlay)
    assert overlay.schema_version == "1.0"
    assert registry.source_of("phys") == "built-in"


def test_overlay_load_and_hooks():
    overlay = PhysOverlay()
    model = overlay.load(MANIFEST)
    assert model.top == "phys_top"
    # The protocol's two hooks stay no-ops; the real join is
    # ``join_hierarchy``, called once by the CLI (see the module's
    # docstring for why it cannot land on the hook).
    assert overlay.join(_design(), model) is None
    assert overlay.contribute(None) is None


# ---------------------------------------------------------------------------
# tree renderer
# ---------------------------------------------------------------------------


def test_tree_suffix_reports_subtree_figures():
    join = join_hierarchy(load_phys_model(MODEL), _design())
    design = _design()
    assert tree_suffix(join, design) == "  [area=11.17µm² P=2.86µW]"
    sub = design.children[0]
    assert tree_suffix(join, sub) == "  [area=5.59µm² P=2.52µW]"
    # No join, or a node the overlay said nothing about: no suffix.
    assert tree_suffix(None, design) == ""
    assert tree_suffix(join, _node("elsewhere", "elsewhere")) == ""


def test_tree_render_appends_the_suffix_to_every_measured_line():
    join = join_hierarchy(load_phys_model(MODEL), _design())
    buf = io.StringIO()
    tree_render.render(_design(), buf, phys_join=join, links=False)
    assert buf.getvalue() == (
        "phys_top  [area=11.17µm² P=2.86µW]\n"
        "├── u_sub : phys_sub  [area=5.59µm² P=2.52µW]\n"
        "│   └── u_leaf : phys_leaf  [area=4.52µm² P=2.43µW]\n"
        "└── u_dff : DFF_X1  [area=4.52µm² P=0.27µW]\n"
    )


def test_tree_render_is_byte_identical_without_the_overlay():
    """The graceful-degradation contract: ``phys_join=None`` must
    reproduce the Phase-1 baseline exactly."""
    baseline = io.StringIO()
    tree_render.render(_design(), baseline, links=False)
    explicit = io.StringIO()
    tree_render.render(_design(), explicit, phys_join=None, links=False)
    assert explicit.getvalue() == baseline.getvalue()
    assert "area=" not in baseline.getvalue()


def test_tree_suffix_keeps_a_tiny_figure_legible():
    """A real-but-tiny leakage must not render as ``0`` — three
    significant digits below 0.01."""
    model = load_phys_model(MODEL)
    join = join_hierarchy(model, _design())
    join.blocks["phys_top"]["subtree_total_uw"] = 0.000123
    join.blocks["phys_top"]["area_um2"] = 1240.37
    assert tree_suffix(join, _design()) == "  [area=1240µm² P=0.000123µW]"


# ---------------------------------------------------------------------------
# JSON renderer
# ---------------------------------------------------------------------------


def _render_json(root: HierNode, **kwargs) -> dict:
    buf = io.StringIO()
    json_render.render(root, buf, embed_layout=False, **kwargs)
    return json.loads(buf.getvalue())


def test_json_render_emits_per_node_blocks_and_the_meta_envelope():
    join = join_hierarchy(load_phys_model(MODEL), _design())
    payload = _render_json(_design(), phys_join=join)
    assert payload["overlays_present"] == ["phys"]
    nodes = {n["id"]: n for n in payload["nodes"]}
    block = nodes["phys_top.u_sub"]["overlays"]["phys"]
    assert block["cell_count"] == 2
    assert block["area_um2"] == pytest.approx(5.586)
    assert block["self_area_um2"] == pytest.approx(1.064)
    assert block["subtree_total_uw"] == pytest.approx(2.5154)
    meta = payload["overlay_meta"]["phys"]
    assert meta["model_top"] == "phys_top"
    assert meta["join_root"] == "phys_top"
    assert meta["units"] == {"area": "um2", "power": "uW"}
    assert meta["halves"] == {"modules": True, "instances": True}
    assert meta["notes"] == []


def test_json_render_keeps_the_meta_envelope_when_nothing_attached():
    """ "The model's top is not this design" is exactly what the viewer
    has to be able to say, so the envelope survives an empty join."""
    join = join_hierarchy(load_phys_model(MODEL), _node("counter", "counter"))
    payload = _render_json(_node("counter", "counter"), phys_join=join)
    assert payload["overlays_present"] == ["phys"]
    assert payload["nodes"][0]["overlays"] == {}
    assert payload["overlay_meta"]["phys"]["attached"] is False


def test_json_render_without_the_overlay_is_unchanged():
    baseline = _render_json(_design())
    assert baseline["overlays_present"] == []
    assert all(n["overlays"] == {} for n in baseline["nodes"])
    assert "overlay_meta" not in baseline


def test_json_render_is_deterministic_with_the_overlay():
    join = join_hierarchy(load_phys_model(MODEL), _design())
    first, second = io.StringIO(), io.StringIO()
    json_render.render(_design(), first, phys_join=join, embed_layout=False)
    json_render.render(_design(), second, phys_join=join, embed_layout=False)
    assert first.getvalue() == second.getvalue()


def test_phys_payload_validates_against_the_v1_schema():
    """``node.overlays.<name>`` is an open object in the pinned schema
    (``docs/view-json-v1.md`` §6), so the phys block is additive — but
    it still has to satisfy the envelope."""
    schema = json.loads(SCHEMA_PATH.read_text())
    join = join_hierarchy(load_phys_model(MODEL), _design())
    payload = _render_json(_design(), phys_join=join)
    jsonschema.validate(instance=payload, schema=schema)


def test_the_documented_block_keys_are_the_emitted_ones():
    """Contract pin for ``docs/view-json-v1.md`` §6 and
    ``docs/phys-overlay.md``: the key set a consumer may rely on.
    Adding a key here is additive and fine; renaming or dropping one
    is a breaking change to the viewer."""
    join = join_hierarchy(load_phys_model(MODEL), _design())
    payload = _render_json(_design(), phys_join=join)
    nodes = {n["id"]: n for n in payload["nodes"]}
    assert sorted(nodes["phys_top"]["overlays"]["phys"]) == [
        "area_um2",
        "cell_count",
        "dynamic_uw",
        "internal_uw",
        "leaf_instances",
        "leakage_uw",
        "self_area_um2",
        "subtree_dynamic_uw",
        "subtree_internal_uw",
        "subtree_leaf_instances",
        "subtree_leakage_uw",
        "subtree_switching_uw",
        "subtree_total_uw",
        "switching_uw",
        "total_uw",
    ]
    assert sorted(payload["overlay_meta"]["phys"]) == [
        "attached",
        "halves",
        "join_root",
        "manifest",
        "matched_instance_rows",
        "model_top",
        "notes",
        "rollup",
        "source",
        "totals",
        "units",
        "unmatched_instance_rows",
        "view_root",
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _require_verible():
    try:
        from rtl_buddy_view._verible_install import find_binary
    except ImportError:  # pragma: no cover - packaging accident
        pytest.skip("verible not available")
    if find_binary("verible-verilog-syntax") is None:
        pytest.skip("verible binary not on PATH / vendor/")


def test_cli_renders_the_phys_overlay_into_view_json(tmp_path):
    _require_verible()
    from rtl_buddy_view.cli import app

    out_path = tmp_path / "view.json"
    result = CliRunner().invoke(
        app,
        [
            "--top",
            "phys_top",
            "--filelist",
            str(FIXTURE / "files.f"),
            "--overlay",
            f"phys={MANIFEST}",
            "--format",
            "json",
            "--output",
            str(out_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out_path.read_text())
    assert "phys" in payload["overlays_present"]
    nodes = {n["id"]: n for n in payload["nodes"]}
    assert nodes["phys_top"]["overlays"]["phys"]["subtree_total_uw"] == pytest.approx(
        2.8565
    )
    assert nodes["phys_top.u_dff"]["overlays"]["phys"]["total_uw"] == pytest.approx(
        0.2661
    )
    assert payload["overlay_meta"]["phys"]["join_root"] == "phys_top"


def test_cli_roots_the_join_under_a_tb_top(tmp_path):
    _require_verible()
    from rtl_buddy_view.cli import app

    out_path = tmp_path / "view.json"
    result = CliRunner().invoke(
        app,
        [
            "--top",
            "phys_top",
            "--tb-top",
            "tb_phys_top",
            "--filelist",
            str(FIXTURE / "files_tb.f"),
            "--overlay",
            f"phys={MODEL}",
            "--format",
            "json",
            "--output",
            str(out_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out_path.read_text())
    assert payload["overlay_meta"]["phys"]["join_root"] == "tb_phys_top.u_dut"
    nodes = {n["id"]: n for n in payload["nodes"]}
    assert nodes["tb_phys_top"]["overlays"]["phys"][
        "subtree_total_uw"
    ] == pytest.approx(2.8565)


def test_cli_tree_gains_the_suffix_only_with_the_overlay():
    _require_verible()
    from rtl_buddy_view.cli import app

    runner = CliRunner()
    base = [
        "--top",
        "phys_top",
        "--filelist",
        str(FIXTURE / "files.f"),
        "--format",
        "tree",
    ]
    without = runner.invoke(app, base)
    with_phys = runner.invoke(app, base + ["--overlay", f"phys={MANIFEST}"])
    assert without.exit_code == 0 and with_phys.exit_code == 0, with_phys.output
    assert "area=" not in without.output
    assert "[area=11.17µm² P=2.86µW]" in with_phys.output
    # Every other byte is the baseline's.
    stripped = "\n".join(
        line.split("  [area=")[0] for line in with_phys.output.splitlines()
    )
    assert stripped + "\n" == without.output


def test_cli_dot_output_is_untouched_by_the_overlay():
    _require_verible()
    from rtl_buddy_view.cli import app

    runner = CliRunner()
    base = [
        "--top",
        "phys_top",
        "--filelist",
        str(FIXTURE / "files.f"),
        "--format",
        "dot",
    ]
    without = runner.invoke(app, base)
    with_phys = runner.invoke(app, base + ["--overlay", f"phys={MANIFEST}"])
    assert without.exit_code == 0 and with_phys.exit_code == 0
    assert with_phys.output == without.output


def test_cli_reports_a_model_of_another_design_without_failing():
    _require_verible()
    from rtl_buddy_view.cli import app

    result = CliRunner().invoke(
        app,
        [
            "--top",
            "counter",
            "--filelist",
            str(Path(__file__).parent / "fixtures" / "counter_with_subs" / "files.f"),
            "--overlay",
            f"phys={MODEL}",
            "--format",
            "tree",
        ],
    )
    assert result.exit_code == 0
    assert "overlay phys:" in result.output
    assert "is not this design" in result.output
    assert "area=" not in result.output


def test_cli_rejects_a_bad_phys_payload(tmp_path):
    _require_verible()
    from rtl_buddy_view.cli import app

    bad = tmp_path / "phys-model.json"
    bad.write_text('{"schema_version": 7}')
    result = CliRunner().invoke(
        app,
        [
            "--top",
            "phys_top",
            "--filelist",
            str(FIXTURE / "files.f"),
            "--overlay",
            f"phys={bad}",
            "--format",
            "tree",
        ],
    )
    assert result.exit_code == 1
    assert "overlay phys:" in result.output
    assert "unsupported phys-model schema_version 7" in result.output
