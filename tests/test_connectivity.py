"""Unit tests for the scope-local connectivity analyzer.

Everything here builds a :class:`ModuleTable` by hand — the analyzer
is a pure function over the data model, so no Verible run is needed.
The Verible-backed end-to-end coverage lives in
``test_block_diagram.py``.
"""

from __future__ import annotations

from rtl_buddy_view.connectivity import NetEdge, root_identifiers, scope_connectivity
from rtl_buddy_view.extractor import (
    Assign,
    Instance,
    Module,
    ModuleTable,
    Port,
    PortConnection,
)


# --- builders ---------------------------------------------------------------


def _port(name: str, direction: str | None, **kw: object) -> Port:
    return Port(
        name=name,
        direction=direction,  # type: ignore[arg-type]
        type_text=None,
        location=None,
        **kw,  # type: ignore[arg-type]
    )


def _conn(port_name: str | None, net: str = "") -> PortConnection:
    return PortConnection(port_name=port_name, net_expr_text=net, location=None)


def _inst(name: str, module_name: str, *conns: PortConnection) -> Instance:
    return Instance(
        name=name,
        module_name=module_name,
        param_overrides=(),
        port_connections=conns,
        location=None,
    )


def _module(
    name: str,
    *,
    ports: tuple[Port, ...] = (),
    instances: tuple[Instance, ...] = (),
    assigns: tuple[Assign, ...] = (),
) -> Module:
    return Module(
        name=name,
        ports=ports,
        parameters=(),
        instances=instances,
        location=None,
        assigns=assigns,
    )


def _assign(lhs: str, rhs: str) -> Assign:
    return Assign(lhs_text=lhs, rhs_text=rhs, location=None)


def _table(*modules: Module) -> ModuleTable:
    return ModuleTable(modules_by_name={m.name: m for m in modules})


#: A driver/sink pair reused by most scope tests: ``src`` emits ``q``,
#: ``sink`` consumes ``d``.
_SRC = _module("src", ports=(_port("q", "output"),))
_SINK = _module("sink", ports=(_port("d", "input"),))


# --- root_identifiers -------------------------------------------------------


def test_root_identifiers_strips_field_selects() -> None:
    assert root_identifiers("hwif_out.ctrl.SRC.value") == ("hwif_out",)


def test_root_identifiers_strips_part_selects() -> None:
    assert root_identifiers("cmd_dst_data_cclk[18:16]") == ("cmd_dst_data_cclk",)


def test_root_identifiers_walks_concatenations() -> None:
    assert root_identifiers("{a, b[3:0], c.d}") == ("a", "b", "c")


def test_root_identifiers_skips_sized_literals() -> None:
    """``2'b00`` must not contribute a ``b00`` net."""
    assert root_identifiers("2'b00") == ()
    assert root_identifiers("{sel, 8'hFF, 1'b1}") == ("sel",)


def test_root_identifiers_skips_system_functions() -> None:
    assert root_identifiers("$clog2(DEPTH)") == ("DEPTH",)


def test_root_identifiers_dedupes_preserving_first_appearance() -> None:
    assert root_identifiers("a & b | a & c") == ("a", "b", "c")


def test_root_identifiers_on_empty_expression() -> None:
    assert root_identifiers("") == ()


def test_root_identifiers_keeps_index_expressions() -> None:
    """Documented over-match: an index is a real read of that net."""
    assert root_identifiers("mem[addr]") == ("mem", "addr")


# --- scope_connectivity -----------------------------------------------------


def test_direct_net_match_yields_one_edge() -> None:
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "w")),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
    )
    edges = scope_connectivity(top, _table(top, _SRC, _SINK))
    assert edges == (
        NetEdge(src=("inst", "u_src"), dst=("inst", "u_sink"), nets=("w",)),
    )


def test_implicit_named_connection_binds_to_same_name() -> None:
    """``.q`` with no parens is the elaborator's same-name binding."""
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q")),
            _inst("u_sink", "sink", _conn("d", "q")),
        ),
    )
    edges = scope_connectivity(top, _table(top, _SRC, _SINK))
    assert edges == (
        NetEdge(src=("inst", "u_src"), dst=("inst", "u_sink"), nets=("q",)),
    )


def test_assign_alias_hop_connects_renamed_nets() -> None:
    """``assign b = a;`` puts ``a`` and ``b`` in one group."""
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "a")),
            _inst("u_sink", "sink", _conn("d", "b[3:0]")),
        ),
        assigns=(_assign("b", "a"),),
    )
    edges = scope_connectivity(top, _table(top, _SRC, _SINK))
    assert edges == (
        NetEdge(src=("inst", "u_src"), dst=("inst", "u_sink"), nets=("b",)),
    )


def test_assign_direction_is_ignored() -> None:
    """The alias hop is symmetric; pin directions decide the arrow.

    Here the ``assign`` runs "backwards" relative to the dataflow —
    the edge must still point driver → sink.
    """
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "b")),
            _inst("u_sink", "sink", _conn("d", "a")),
        ),
        assigns=(_assign("b", "a"),),
    )
    edges = scope_connectivity(top, _table(top, _SRC, _SINK))
    assert [(e.src, e.dst) for e in edges] == [(("inst", "u_src"), ("inst", "u_sink"))]


def test_top_ports_are_endpoints() -> None:
    top = _module(
        "top",
        ports=(_port("din", "input"), _port("dout", "output")),
        instances=(
            _inst("u_sink", "sink", _conn("d", "din")),
            _inst("u_src", "src", _conn("q", "dout")),
        ),
    )
    edges = scope_connectivity(top, _table(top, _SRC, _SINK))
    assert set((e.src, e.dst) for e in edges) == {
        (("port", "din"), ("inst", "u_sink")),
        (("inst", "u_src"), ("port", "dout")),
    }


def test_interface_port_drives_when_group_has_no_driver() -> None:
    """A direction-less interface bundle feeding a consumer reads as
    an input to the block."""
    top = _module(
        "top",
        ports=(_port("bus", None, port_kind="interface", interface_type="apb_intf"),),
        instances=(_inst("u_sink", "sink", _conn("d", "bus.psel")),),
    )
    edges = scope_connectivity(top, _table(top, _SINK))
    assert edges == (
        NetEdge(src=("port", "bus"), dst=("inst", "u_sink"), nets=("bus",)),
    )


def test_interface_port_sinks_when_group_has_a_driver() -> None:
    top = _module(
        "top",
        ports=(_port("bus", None, port_kind="interface", interface_type="apb_intf"),),
        instances=(_inst("u_src", "src", _conn("q", "bus.prdata")),),
    )
    edges = scope_connectivity(top, _table(top, _SRC))
    assert edges == (
        NetEdge(src=("inst", "u_src"), dst=("port", "bus"), nets=("bus",)),
    )


def test_interface_port_is_bidirectional_when_group_has_both() -> None:
    """A real bus bundle carries traffic both ways; so does its edge pair."""
    duplex = _module("duplex", ports=(_port("q", "output"), _port("d", "input")))
    top = _module(
        "top",
        ports=(_port("bus", None, port_kind="interface", interface_type="apb_intf"),),
        instances=(
            _inst(
                "u_duplex", "duplex", _conn("q", "bus.prdata"), _conn("d", "bus.psel")
            ),
        ),
    )
    edges = scope_connectivity(top, _table(top, duplex))
    assert [(e.src, e.dst) for e in edges] == [
        (("inst", "u_duplex"), ("port", "bus")),
        (("port", "bus"), ("inst", "u_duplex")),
    ]


def test_blackbox_pin_counts_as_sink_when_a_driver_exists() -> None:
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "w")),
            _inst("u_bb", "missing_module", _conn("anything", "w")),
        ),
    )
    edges = scope_connectivity(top, _table(top, _SRC))
    assert edges == (NetEdge(src=("inst", "u_src"), dst=("inst", "u_bb"), nets=("w",)),)


def test_blackbox_only_group_emits_nothing() -> None:
    """With no known direction anywhere, inventing an arrow would be a lie."""
    top = _module(
        "top",
        instances=(
            _inst("u_bb1", "missing_a", _conn("p", "w")),
            _inst("u_bb2", "missing_b", _conn("p", "w")),
        ),
    )
    assert scope_connectivity(top, _table(top)) == ()


def test_positional_connections_are_skipped() -> None:
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn(None, "w")),
            _inst("u_sink", "sink", _conn(None, "w")),
        ),
    )
    assert scope_connectivity(top, _table(top, _SRC, _SINK)) == ()


def test_clock_and_reset_groups_are_dropped() -> None:
    clocked = _module(
        "clocked",
        ports=(
            _port("clk", "input"),
            _port("rst_n", "input"),
            _port("q", "output"),
        ),
    )
    driver = _module(
        "driver",
        ports=(
            _port("clk_out", "output"),
            _port("reset_out", "output"),
            _port("q", "output"),
        ),
    )
    top = _module(
        "top",
        instances=(
            _inst(
                "u_gen",
                "driver",
                _conn("clk_out", "sys_clk"),
                _conn("reset_out", "sys_rst_n"),
                _conn("q", "data"),
            ),
            _inst(
                "u_ff",
                "clocked",
                _conn("clk", "sys_clk"),
                _conn("rst_n", "sys_rst_n"),
                _conn("q", "unused"),
            ),
        ),
    )
    edges = scope_connectivity(top, _table(top, clocked, driver))
    # Only the clock/reset groups had sinks; ``data`` has no consumer.
    assert edges == ()


def test_group_survives_when_only_some_names_are_clock_shaped() -> None:
    """``assign en = go & clk_locked;`` must not delete the ``go`` group."""
    gate = _module("gate", ports=(_port("d", "input"),))
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "go")),
            _inst("u_gate", "gate", _conn("d", "en")),
        ),
        assigns=(_assign("en", "go & clk_locked"),),
    )
    edges = scope_connectivity(top, _table(top, _SRC, gate))
    assert [(e.src, e.dst) for e in edges] == [(("inst", "u_src"), ("inst", "u_gate"))]


def test_no_self_edges_for_a_feedback_net() -> None:
    loop = _module("loop", ports=(_port("q", "output"), _port("d", "input")))
    top = _module(
        "top",
        instances=(_inst("u_loop", "loop", _conn("q", "w"), _conn("d", "w")),),
    )
    assert scope_connectivity(top, _table(top, loop)) == ()


def test_edges_bundle_by_endpoint_pair_and_sort_nets() -> None:
    wide = _module("wide", ports=(_port("a", "output"), _port("b", "output")))
    dual = _module("dual", ports=(_port("x", "input"), _port("y", "input")))
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("a", "zeta"), _conn("b", "alpha")),
            _inst("u_dual", "dual", _conn("x", "zeta[3:0]"), _conn("y", "alpha")),
        ),
    )
    edges = scope_connectivity(top, _table(top, wide, dual))
    assert edges == (
        NetEdge(
            src=("inst", "u_wide"),
            dst=("inst", "u_dual"),
            nets=("alpha", "zeta"),
        ),
    )


def test_output_is_deterministic_across_runs() -> None:
    """Set iteration must never reach the emitted tuple."""
    top = _module(
        "top",
        ports=(_port("din", "input"), _port("dout", "output")),
        instances=(
            _inst("u_a", "src", _conn("q", "n1")),
            _inst("u_b", "sink", _conn("d", "n2")),
            _inst("u_c", "sink", _conn("d", "n1")),
            _inst("u_d", "src", _conn("q", "dout")),
            _inst("u_e", "sink", _conn("d", "din")),
        ),
        assigns=(_assign("n2", "n1"),),
    )
    table = _table(top, _SRC, _SINK)
    first = scope_connectivity(top, table)
    second = scope_connectivity(top, table)
    assert first == second
    assert list(first) == sorted(first, key=lambda e: (e.src, e.dst))


def test_module_without_instances_yields_nothing() -> None:
    top = _module("top", ports=(_port("din", "input"),))
    assert scope_connectivity(top, _table(top)) == ()
