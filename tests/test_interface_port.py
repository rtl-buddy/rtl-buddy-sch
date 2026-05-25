"""End-to-end tests for SystemVerilog interface-port extraction.

Covers the four-layer change set from rtl-buddy/rtl-buddy-view#102:

1. ``extractor.Port`` carries ``port_kind`` / ``interface_type`` /
   ``modport`` for ``test_mem_if.sub`` style declarations.
2. ``frontend.verible._port_from_decl`` detects the
   ``kInterfacePortHeader`` CST signature.
3. ``render.json_render`` surfaces the new fields only on interface
   ports (the wire-port emit shape is unchanged for back-compat).
4. ``schemas/view-v1.json`` accepts the new fields.

The SPA-layer rendering (``blockFlow.js`` distinct styling, wave
overlay skip) is covered by ``viewer/tests/blockFlow.spec.js`` +
``viewer/tests/overlays.spec.js``.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import json_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "interface_port_module"


def _have_verible() -> bool:
    return find_binary("verible-verilog-syntax") is not None


pytestmark = pytest.mark.skipif(
    not _have_verible(),
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


def _table():
    return parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )


# --- extractor / verible CST signature --------------------------------------


def test_interface_port_is_extracted_with_kind_type_modport() -> None:
    mod = _table().modules_by_name["test_module_3"]
    ports_by_name = {p.name: p for p in mod.ports}
    m = ports_by_name["m"]
    assert m.port_kind == "interface"
    assert m.interface_type == "test_mem_if"
    assert m.modport == "sub"
    # Interface ports don't have a direction — that's a property of
    # the modport, not the port declaration itself.
    assert m.direction is None


def test_wire_ports_alongside_interface_keep_default_kind() -> None:
    """Mixed port lists: scalar ports stay ``port_kind="wire"`` so the
    new fields are opt-in. This is the back-compat guarantee."""
    mod = _table().modules_by_name["test_module_3"]
    wires = [p for p in mod.ports if p.name in ("clk", "rst", "z")]
    assert len(wires) == 3
    for p in wires:
        assert p.port_kind == "wire"
        assert p.interface_type is None
        assert p.modport is None
    # Direction is still extracted normally for the wire ports.
    assert {p.name: p.direction for p in wires} == {
        "clk": "input",
        "rst": "input",
        "z": "output",
    }


def test_port_order_preserves_source_order() -> None:
    """The mixed list should stay in declaration order — the SPA's
    block-flow renderer uses index order to pair the input column."""
    mod = _table().modules_by_name["test_module_3"]
    assert [p.name for p in mod.ports] == ["clk", "rst", "m", "z"]


# --- json_render contract ---------------------------------------------------


def test_view_json_emits_bundle_port_when_no_module_table() -> None:
    """Without ``module_table`` (back-compat), the interface port
    surfaces as the #102-style bundle row. The flatten happens only
    when the producer supplies the interface table."""
    table = _table()
    root = build_hierarchy(table, "tb_top")
    buf = io.StringIO()
    json_render.render(root, buf, embed_layout=False)
    payload = json.loads(buf.getvalue())
    dut = next(n for n in payload["nodes"] if n["id"] == "tb_top.dut")
    m = next(p for p in dut["ports"] if p["name"] == "m")
    assert m["port_kind"] == "interface"
    assert m["interface_type"] == "test_mem_if"
    assert m["modport"] == "sub"
    assert m["expr"] == "u_if.sub"


def test_view_json_flattens_interface_ports_when_table_supplied() -> None:
    """With ``module_table=table``, interface ports flatten to
    per-signal scalar ports (m.req / m.addr) with synthesized
    direction from the modport. The flattened ports carry
    ``port_kind == "interface_signal"`` and the expr/anchor of the
    owning interface port for connectivity purposes. (#105.)"""
    table = _table()
    root = build_hierarchy(table, "tb_top")
    buf = io.StringIO()
    json_render.render(root, buf, embed_layout=False, module_table=table)
    payload = json.loads(buf.getvalue())
    dut = next(n for n in payload["nodes"] if n["id"] == "tb_top.dut")
    names = [p["name"] for p in dut["ports"]]
    # ``m`` is gone, replaced by per-signal entries in source order.
    assert "m" not in names
    assert "m.req" in names
    assert "m.addr" in names
    m_req = next(p for p in dut["ports"] if p["name"] == "m.req")
    assert m_req["port_kind"] == "interface_signal"
    assert m_req["dir"] == "input"  # sub modport pins inputs
    assert m_req["interface_type"] == "test_mem_if"
    assert m_req["modport"] == "sub"
    # The owning interface port's expr ("u_if.sub") rides along so
    # block-flow can still wire connectivity through the bundle.
    assert m_req["expr"] == "u_if.sub"


def test_view_json_omits_new_fields_on_wire_ports() -> None:
    """Wire ports must NOT carry the new keys — keeps view.json
    payload minimal and avoids confusing producers that grep the
    schema for "interface" markers."""
    table = _table()
    root = build_hierarchy(table, "tb_top")
    buf = io.StringIO()
    json_render.render(root, buf, embed_layout=False, module_table=table)
    payload = json.loads(buf.getvalue())
    dut = next(n for n in payload["nodes"] if n["id"] == "tb_top.dut")
    for p in dut["ports"]:
        if p["name"].startswith("m"):  # m.req / m.addr opted in
            continue
        assert "port_kind" not in p, p
        assert "interface_type" not in p, p
        assert "modport" not in p, p


def test_flatten_interface_ports_unresolved_falls_back_to_bundle() -> None:
    """If the interface body isn't in the table, the helper keeps
    the original bundle port unchanged."""
    from rtl_buddy_view.extractor import flatten_interface_ports

    table = _table()
    mod = table.modules_by_name["test_module_3"]
    # Pretend the interface table is empty — same effect as a
    # blackbox interface or a build that didn't see the .sv.
    empty = type(table)()
    flat = flatten_interface_ports(mod, empty)
    assert any(p.name == "m" and p.port_kind == "interface" for p in flat)
    # Confirm we didn't lose the wire ports either.
    assert {p.name for p in flat if p.port_kind == "wire"} == {"clk", "rst", "z"}


def test_interface_signals_and_modports_extracted() -> None:
    """Producer-side: the verible walker captures the interface
    body so json_render has a table to flatten against."""
    table = _table()
    iface = table.interfaces_by_name["test_mem_if"]
    assert [s.name for s in iface.signals] == ["req", "addr"]
    by_name = {mp.name: mp for mp in iface.modports}
    assert by_name["master"].outputs == ("req", "addr")
    assert by_name["master"].inputs == ()
    assert by_name["sub"].inputs == ("req", "addr")
    assert by_name["sub"].outputs == ()
