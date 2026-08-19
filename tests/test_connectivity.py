"""Unit tests for the scope-local connectivity analyzer.

Everything here builds a :class:`ModuleTable` by hand — the analyzer
is a pure function over the data model, so no Verible run is needed.
The Verible-backed end-to-end coverage lives in
``test_block_diagram.py``.
"""

from __future__ import annotations

from rtl_buddy_view.connectivity import (
    NetEdge,
    instance_params,
    port_width,
    port_width_expr,
    root_identifiers,
    scope_connectivity,
)
from rtl_buddy_view.extractor import (
    Assign,
    Instance,
    Module,
    ModuleTable,
    NetDecl,
    Parameter,
    ParameterOverride,
    Port,
    PortConnection,
)


# --- builders ---------------------------------------------------------------


def _port(
    name: str, direction: str | None, type_text: str | None = None, **kw: object
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


def _inst(
    name: str,
    module_name: str,
    *conns: PortConnection,
    params: tuple[tuple[str | None, str], ...] = (),
) -> Instance:
    return Instance(
        name=name,
        module_name=module_name,
        param_overrides=tuple(
            ParameterOverride(param_name=n, value_text=v, location=None)
            for n, v in params
        ),
        port_connections=conns,
        location=None,
    )


def _module(
    name: str,
    *,
    ports: tuple[Port, ...] = (),
    instances: tuple[Instance, ...] = (),
    assigns: tuple[Assign, ...] = (),
    net_decls: tuple[NetDecl, ...] = (),
    parameters: tuple[tuple[str, str], ...] = (),
) -> Module:
    return Module(
        name=name,
        ports=ports,
        parameters=tuple(
            Parameter(name=n, default_text=v, location=None) for n, v in parameters
        ),
        instances=instances,
        location=None,
        assigns=assigns,
        net_decls=net_decls,
    )


def _assign(lhs: str, rhs: str) -> Assign:
    return Assign(lhs_text=lhs, rhs_text=rhs, location=None)


def _decl(name: str, type_text: str | None) -> NetDecl:
    return NetDecl(name=name, type_text=type_text, location=None)


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
        NetEdge(
            src=("inst", "u_src"),
            dst=("inst", "u_sink"),
            nets=("w",),
            src_pins=("q",),
            dst_pins=("d",),
        ),
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
        NetEdge(
            src=("inst", "u_src"),
            dst=("inst", "u_sink"),
            nets=("q",),
            src_pins=("q",),
            dst_pins=("d",),
        ),
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
        NetEdge(
            src=("inst", "u_src"),
            dst=("inst", "u_sink"),
            nets=("b",),
            src_pins=("q",),
            dst_pins=("d",),
        ),
    )


def test_assign_flow_carries_a_driver_downstream() -> None:
    """``assign b = a;`` with the driver on ``a`` reaches ``b``'s sink."""
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "a")),
            _inst("u_sink", "sink", _conn("d", "b")),
        ),
        assigns=(_assign("b", "a"),),
    )
    edges = scope_connectivity(top, _table(top, _SRC, _SINK))
    assert edges == (
        NetEdge(
            src=("inst", "u_src"),
            dst=("inst", "u_sink"),
            nets=("b",),
            src_pins=("q",),
            dst_pins=("d",),
        ),
    )


def test_no_edge_against_the_assign_direction() -> None:
    """A pin driving an assign's *LHS* drives nothing downstream of it.

    ``assign b = a;`` already drives ``b``; a child output on ``b`` is
    a second driver, not a path back out to ``a``'s readers. The old
    undirected alias model emitted ``u_src -> u_sink`` here.
    """
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "b")),
            _inst("u_sink", "sink", _conn("d", "a")),
        ),
        assigns=(_assign("b", "a"),),
    )
    assert scope_connectivity(top, _table(top, _SRC, _SINK)) == ()


def test_assign_flow_composes_over_multiple_hops() -> None:
    """``assign c = b; assign b = a;`` is one transitive path a → c."""
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "a")),
            _inst("u_sink", "sink", _conn("d", "c")),
        ),
        assigns=(_assign("c", "b"), _assign("b", "a")),
    )
    edges = scope_connectivity(top, _table(top, _SRC, _SINK))
    assert edges == (
        NetEdge(
            src=("inst", "u_src"),
            dst=("inst", "u_sink"),
            nets=("c",),
            src_pins=("q",),
            dst_pins=("d",),
        ),
    )


def test_mixing_assign_does_not_alias_two_drivers() -> None:
    """The demo-design regression: ``assign wr_en = hwif.push & ~full;``.

    One assign mixes a CSR data source with a FIFO status net. Under
    the old undirected grouping both drivers landed in one group, so
    ``u_afifo`` inherited every sink ``u_csr`` feeds — a wrong edge
    the rendered block diagram showed as ``u_afifo -> u_cmd``. Under
    directed flow ``fifo_wr_full`` only runs *into* ``fifo_wr_en``.
    """
    csr = _module("csr", ports=(_port("hwif_out", "output"),))
    afifo = _module(
        "afifo",
        ports=(
            _port("wr_full", "output"),
            _port("wr_en", "input"),
            _port("wr_data", "input"),
        ),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_csr", "csr", _conn("hwif_out", "hwif_out")),
            _inst(
                "u_afifo",
                "afifo",
                _conn("wr_full", "fifo_wr_full"),
                _conn("wr_en", "fifo_wr_en"),
                _conn("wr_data", "hwif_out.fifo_data.value"),
            ),
            _inst("u_cmd", "sink", _conn("d", "hwif_out.cmd.value")),
        ),
        assigns=(
            _assign("fifo_wr_en", "hwif_out.fifo_push.PUSH.value & ~fifo_wr_full"),
        ),
    )
    edges = scope_connectivity(top, _table(top, csr, afifo, _SINK))
    # u_csr keeps both of its real edges; u_afifo acquires none of
    # them, and its own status net feeding back in is not a self-edge.
    assert edges == (
        NetEdge(
            src=("inst", "u_csr"),
            dst=("inst", "u_afifo"),
            nets=("fifo_wr_en", "hwif_out"),
            src_pins=("hwif_out",),
            dst_pins=("wr_data", "wr_en"),
        ),
        NetEdge(
            src=("inst", "u_csr"),
            dst=("inst", "u_cmd"),
            nets=("hwif_out",),
            src_pins=("hwif_out",),
            dst_pins=("d",),
        ),
    )


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
        NetEdge(
            src=("port", "bus"),
            dst=("inst", "u_sink"),
            nets=("bus",),
            src_pins=("bus",),
            dst_pins=("d",),
        ),
    )


def test_interface_port_sinks_when_group_has_a_driver() -> None:
    top = _module(
        "top",
        ports=(_port("bus", None, port_kind="interface", interface_type="apb_intf"),),
        instances=(_inst("u_src", "src", _conn("q", "bus.prdata")),),
    )
    edges = scope_connectivity(top, _table(top, _SRC))
    assert edges == (
        NetEdge(
            src=("inst", "u_src"),
            dst=("port", "bus"),
            nets=("bus",),
            src_pins=("q",),
            dst_pins=("bus",),
        ),
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
    assert edges == (
        NetEdge(
            src=("inst", "u_src"),
            dst=("inst", "u_bb"),
            nets=("w",),
            src_pins=("q",),
            # The blackbox pin has no known direction, but its formal
            # name is right there at the binding site.
            dst_pins=("anything",),
        ),
    )


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


def test_tokenless_clock_and_reset_names_are_dropped() -> None:
    """``cclk`` / ``wclk`` / ``crst_n`` / ``aresetn`` carry no underscore
    token yet are clocks/resets; the block-only widened patterns must
    drop them so one clock tree isn't shown while another is hidden."""
    sink = _module(
        "sink",
        ports=(
            _port("clk", "input"),
            _port("rclk", "input"),
            _port("rst_n", "input"),
            _port("arst", "input"),
        ),
    )
    driver = _module(
        "driver",
        ports=(
            _port("c_out", "output"),
            _port("w_out", "output"),
            _port("r_out", "output"),
            _port("a_out", "output"),
        ),
    )
    top = _module(
        "top",
        instances=(
            _inst(
                "u_gen",
                "driver",
                _conn("c_out", "cclk"),
                _conn("w_out", "wclk"),
                _conn("r_out", "crst_n"),
                _conn("a_out", "aresetn"),
            ),
            _inst(
                "u_ff",
                "sink",
                _conn("clk", "cclk"),
                _conn("rclk", "wclk"),
                _conn("rst_n", "crst_n"),
                _conn("arst", "aresetn"),
            ),
        ),
    )
    edges = scope_connectivity(top, _table(top, sink, driver))
    assert edges == ()


def test_data_names_merely_ending_in_rst_survive() -> None:
    """``burst`` / ``first`` end in "rst" but lack the polarity tail the
    widened reset pattern requires — they are data, keep their edges."""
    sink = _module("sink", ports=(_port("len", "input"), _port("sel", "input")))
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "burst")),
            _inst("u_sink", "sink", _conn("len", "burst"), _conn("sel", "first")),
        ),
    )
    edges = scope_connectivity(top, _table(top, _SRC, sink))
    assert [(e.src, e.dst) for e in edges] == [(("inst", "u_src"), ("inst", "u_sink"))]


def test_domain_suffixed_data_names_survive() -> None:
    """A multi-token net whose last token ends in "clk" is
    domain-suffixed *data* (``src_sel_cclk``, ``result_payload_cclk``),
    not a clock — the widened pattern must not delete its edge."""
    sink = _module("sink", ports=(_port("sel", "input"), _port("d", "input")))
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "src_sel_cclk")),
            _inst(
                "u_sink",
                "sink",
                _conn("sel", "src_sel_cclk"),
                _conn("d", "result_payload_cclk"),
            ),
        ),
    )
    edges = scope_connectivity(top, _table(top, _SRC, sink))
    assert [(e.src, e.dst) for e in edges] == [(("inst", "u_src"), ("inst", "u_sink"))]


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
            src_pins=("a", "b"),
            dst_pins=("x", "y"),
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


# --- port_width -------------------------------------------------------------


def test_port_width_reads_an_integer_packed_range() -> None:
    assert port_width("logic [18:0]") == 19
    assert port_width("wire [7:0]") == 8


def test_port_width_is_direction_agnostic_about_the_bounds() -> None:
    """``[0:7]`` is a little-endian vector, still 8 bits wide."""
    assert port_width("logic [0:7]") == 8


def test_port_width_without_a_range_is_one_bit() -> None:
    assert port_width("logic") == 1
    assert port_width("wire") == 1


def test_port_width_of_a_parameterised_bound_is_unknown() -> None:
    """A wrong bus width is worse than an unlabelled wire."""
    assert port_width("logic [PTR_W-1:0]") is None
    assert port_width("logic [$clog2(DEPTH)-1:0]") is None


def test_port_width_of_a_missing_or_empty_type_is_unknown() -> None:
    assert port_width(None) is None
    assert port_width("   ") is None


def test_port_width_folds_a_parameterised_bound_when_params_pin_it() -> None:
    assert port_width("logic [WIDTH-1:0]", {"WIDTH": "19"}) == 19
    assert port_width("logic [DATA_W*2-1:0]", {"DATA_W": "4"}) == 8
    # A parameter that resolves to another symbol is still no number.
    assert port_width("logic [WIDTH-1:0]", {"WIDTH": "PTR_W"}) is None
    assert port_width("logic [$clog2(D)-1:0]", {"D": "8"}) is None


def test_literal_ranges_are_never_re_read_through_params() -> None:
    """The substitution tier is a fallback, so nothing resolved moves."""
    assert port_width("logic [18:0]", {"WIDTH": "3"}) == 19
    assert port_width("logic", {"WIDTH": "3"}) == 1


def test_port_width_expr_names_the_declaration_regardless_of_the_number() -> None:
    """The two answers are independent, and both may be present."""
    assert port_width_expr("logic [PTR_W-1:0]") == "PTR_W"
    assert port_width_expr("logic [WIDTH-1:0]") == "WIDTH"
    # …even where the binding site folds a real number out of it.
    assert port_width("logic [WIDTH-1:0]", {"WIDTH": "19"}) == 19
    assert port_width_expr("logic [WIDTH-1:0]") == "WIDTH"
    # A range written in integers is not algebra.
    assert port_width_expr("logic [18:0]") is None
    assert port_width_expr("logic") is None
    assert port_width_expr(None) is None


# --- formal pins ------------------------------------------------------------


def test_implicit_connection_reports_the_formal_not_the_net() -> None:
    """``.q`` binds net ``q``; the *pin* is still ``q`` on both ends."""
    sink = _module("sink", ports=(_port("d", "input"),))
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q")),
            _inst("u_sink", "sink", _conn("d", "q")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, _SRC, sink))
    assert (edge.src_pins, edge.dst_pins) == (("q",), ("d",))


def test_interface_endpoint_pins_are_the_port_name() -> None:
    top = _module(
        "top",
        ports=(_port("bus", None, port_kind="interface", interface_type="apb_intf"),),
        instances=(_inst("u_sink", "sink", _conn("d", "bus.psel")),),
    )
    (edge,) = scope_connectivity(top, _table(top, _SINK))
    assert edge.src_pins == ("bus",)


def test_blackbox_endpoint_keeps_its_formal_pin_name() -> None:
    """Direction is unknown on a blackbox pin; the formal never is."""
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "w")),
            _inst("u_bb", "missing_module", _conn("data_i", "w")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, _SRC))
    assert edge.dst_pins == ("data_i",)


def test_pins_are_deduped_and_sorted_across_a_bundle() -> None:
    wide = _module(
        "wide", ports=(_port("zeta_o", "output"), _port("alpha_o", "output"))
    )
    dual = _module("dual", ports=(_port("y_i", "input"), _port("x_i", "input")))
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("zeta_o", "n1"), _conn("alpha_o", "n2")),
            _inst("u_dual", "dual", _conn("y_i", "n1"), _conn("x_i", "n2")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, dual))
    assert edge.src_pins == ("alpha_o", "zeta_o")
    assert edge.dst_pins == ("x_i", "y_i")


def test_a_driver_pin_off_this_bundle_stays_off_the_edge() -> None:
    """``u_src`` drives two nets; only one reaches ``u_sink``.

    The unrelated output pin must not be listed on the edge just
    because the same endpoint pair is connected elsewhere.
    """
    wide = _module("wide", ports=(_port("used", "output"), _port("spare", "output")))
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("used", "w"), _conn("spare", "unread")),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, _SINK))
    assert edge.src_pins == ("used",)


# --- bit widths -------------------------------------------------------------


def test_bits_sum_the_bundle_from_the_driving_pins() -> None:
    wide = _module(
        "wide",
        ports=(
            _port("payload", "output", "logic [18:0]"),
            _port("valid", "output", "logic"),
        ),
    )
    dual = _module(
        "dual",
        ports=(
            _port("data", "input", "logic [18:0]"),
            _port("vld", "input", "logic"),
        ),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("payload", "pay"), _conn("valid", "vld")),
            _inst("u_dual", "dual", _conn("data", "pay"), _conn("vld", "vld")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, dual))
    assert edge.bits == 20


def test_bits_are_unknown_when_any_net_in_the_bundle_is() -> None:
    """A partial sum would read as a real number to a bus-slash renderer."""
    wide = _module(
        "wide",
        ports=(
            _port("payload", "output", "logic [WIDTH-1:0]"),
            _port("valid", "output", "logic"),
        ),
    )
    dual = _module(
        "dual",
        ports=(_port("data", "input", None), _port("vld", "input", "logic")),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("payload", "pay"), _conn("valid", "vld")),
            _inst("u_dual", "dual", _conn("data", "pay"), _conn("vld", "vld")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, dual))
    assert edge.bits is None


def test_bits_of_a_net_entering_from_a_scope_port() -> None:
    """The scope's own ``input`` port is the driver of the net it names."""
    sink = _module("sink", ports=(_port("d", "input", "logic [7:0]"),))
    top = _module(
        "top",
        ports=(_port("din", "input", "logic [7:0]"),),
        instances=(_inst("u_sink", "sink", _conn("d", "din")),),
    )
    (edge,) = scope_connectivity(top, _table(top, sink))
    assert edge.bits == 8


def test_bits_ignore_the_sink_side_width() -> None:
    """Only the driver declares the net; a sink pin can be narrower."""
    wide = _module("wide", ports=(_port("q", "output", "logic [15:0]"),))
    sink = _module("sink", ports=(_port("d", "input", "logic [3:0]"),))
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("q", "w")),
            _inst("u_sink", "sink", _conn("d", "w[3:0]")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, sink))
    assert edge.bits == 16


def test_bits_are_unknown_for_a_concatenated_driver_binding() -> None:
    """``.q({a, b})`` gives the port's width to no single net."""
    wide = _module("wide", ports=(_port("q", "output", "logic [7:0]"),))
    dual = _module(
        "dual",
        ports=(_port("x", "input", "logic [3:0]"), _port("y", "input", "logic [3:0]")),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("q", "{hi, lo}")),
            _inst("u_dual", "dual", _conn("x", "hi"), _conn("y", "lo")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, dual))
    assert edge.bits is None


def test_bits_are_unknown_when_two_drivers_disagree() -> None:
    a = _module("a", ports=(_port("q", "output", "logic [7:0]"),))
    b = _module("b", ports=(_port("q", "output", "logic [3:0]"),))
    top = _module(
        "top",
        instances=(
            _inst("u_a", "a", _conn("q", "w")),
            _inst("u_b", "b", _conn("q", "w")),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
    )
    edges = scope_connectivity(top, _table(top, a, b, _SINK))
    assert [e.bits for e in edges] == [None, None]


# --- bit widths: net declarations -------------------------------------------


def _assign_driven_top(*, net_decls: tuple[NetDecl, ...]) -> Module:
    """The demo's shape: a CSR block feeding a handshake over an assign.

    ``u_csr``'s only output is an untyped struct bundle, so the
    payload nets it feeds have **no driving pin** to take a width
    from. Their declarations in the scope body are the only source
    there is.
    """
    return _module(
        "top",
        instances=(
            _inst("u_csr", "csr", _conn("hwif_out", "hwif_out")),
            _inst(
                "u_hs",
                "hs",
                _conn("src_data", "cmd_payload"),
                _conn("src_valid", "cmd_valid"),
            ),
        ),
        assigns=(
            _assign("cmd_payload", "hwif_out.op.value"),
            _assign("cmd_valid", "hwif_out.ctrl.GO.value"),
        ),
        net_decls=net_decls,
    )


_CSR = _module("csr", ports=(_port("hwif_out", "output", None),))
_HS = _module(
    "hs",
    ports=(
        _port("src_data", "input", "logic [18:0]"),
        _port("src_valid", "input", "logic"),
    ),
)


def test_bits_of_an_assign_driven_bundle_come_from_the_declarations() -> None:
    """19 + 1, from ``logic [18:0]`` / ``logic`` in the scope body."""
    top = _assign_driven_top(
        net_decls=(
            _decl("cmd_payload", "logic [18:0]"),
            _decl("cmd_valid", "logic"),
        )
    )
    (edge,) = scope_connectivity(top, _table(top, _CSR, _HS))
    assert (edge.nets, edge.bits) == (("cmd_payload", "cmd_valid"), 20)


def test_an_assign_driven_bundle_without_declarations_stays_unknown() -> None:
    """The before picture — no driving pin, no declaration, no width."""
    top = _assign_driven_top(net_decls=())
    (edge,) = scope_connectivity(top, _table(top, _CSR, _HS))
    assert edge.bits is None


def test_a_driving_pin_beats_a_net_declaration() -> None:
    """The driver is the authority; the declaration is the fallback."""
    wide = _module("wide", ports=(_port("q", "output", "logic [15:0]"),))
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("q", "w")),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
        net_decls=(_decl("w", "logic [3:0]"),),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, _SINK))
    assert edge.bits == 16


def test_a_declaration_rescues_a_parameterised_driving_pin() -> None:
    """``logic [W-1:0]`` on the pin is unknown; the net's own decl isn't.

    A tier that saw the name but couldn't pin a number to it abstains
    rather than poisoning the result.
    """
    wide = _module("wide", ports=(_port("q", "output", "logic [W-1:0]"),))
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("q", "w")),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
        net_decls=(_decl("w", "logic [7:0]"),),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, _SINK))
    assert edge.bits == 8


def test_two_declarations_of_one_name_at_different_widths_are_unknown() -> None:
    """Generate-scope shadowing degrades rather than picks a side."""
    top = _assign_driven_top(
        net_decls=(
            _decl("cmd_payload", "logic [18:0]"),
            _decl("cmd_payload", "logic [7:0]"),
            _decl("cmd_valid", "logic"),
        )
    )
    (edge,) = scope_connectivity(top, _table(top, _CSR, _HS))
    assert edge.bits is None


def test_a_parameterised_declaration_is_unknown() -> None:
    top = _assign_driven_top(
        net_decls=(
            _decl("cmd_payload", "logic [WIDTH-1:0]"),
            _decl("cmd_valid", "logic"),
        )
    )
    (edge,) = scope_connectivity(top, _table(top, _CSR, _HS))
    assert edge.bits is None


# --- bit widths: the scope's own ports ---------------------------------------


def test_bits_of_an_assign_driven_output_port() -> None:
    """No pin drives ``dout``; the scope's own port declares it."""
    top = _module(
        "top",
        ports=(_port("din", "input", "logic [7:0]"), _port("dout", "output", "logic")),
        instances=(_inst("u_sink", "sink", _conn("d", "din")),),
        assigns=(_assign("dout", "din"),),
    )
    edges = scope_connectivity(top, _table(top, _SINK))
    (to_port,) = [e for e in edges if e.dst == ("port", "dout")]
    assert to_port.bits == 1


def test_a_driving_pin_beats_the_scopes_own_port_declaration() -> None:
    """A wide pin bound straight to a narrower-declared port wins.

    Port declarations are the last tier precisely so they can never
    change a width an actual driver already gave.
    """
    wide = _module("wide", ports=(_port("q", "output", "logic [15:0]"),))
    top = _module(
        "top",
        ports=(_port("dout", "output", "logic [7:0]"),),
        instances=(_inst("u_wide", "wide", _conn("q", "dout")),),
    )
    (edge,) = scope_connectivity(top, _table(top, wide))
    assert edge.bits == 16


def test_an_interface_port_has_no_width_to_lend() -> None:
    """A bundle is not a vector — its range-less slice is not 1 bit."""
    top = _module(
        "top",
        ports=(_port("apb", None, "apb_intf", port_kind="interface"),),
        instances=(_inst("u_sink", "sink", _conn("d", "apb.pwdata")),),
    )
    (edge,) = scope_connectivity(top, _table(top, _SINK))
    assert edge.bits is None


# --- bit widths: parameter substitution (tier 1) -----------------------------


def test_a_literal_override_widths_a_parameterised_pin() -> None:
    """``[WIDTH-1:0]`` + ``#(.WIDTH(19))`` is 19 bits **and** ``WIDTH``.

    Both answers, neither suppressing the other: the number for a
    consumer that wants arithmetic, the name for the reader who wants
    to know which knob sets this bus.
    """
    wide = _module(
        "wide",
        ports=(_port("q", "output", "logic [WIDTH-1:0]"),),
        parameters=(("WIDTH", "8"),),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("q", "w"), params=(("WIDTH", "19"),)),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, _SINK))
    assert (edge.bits, edge.bits_expr) == (19, "WIDTH")


def test_an_unoverridden_pin_falls_back_to_the_childs_default() -> None:
    wide = _module(
        "wide",
        ports=(_port("q", "output", "logic [WIDTH-1:0]"),),
        parameters=(("WIDTH", "8"),),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("q", "w")),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, _SINK))
    assert (edge.bits, edge.bits_expr) == (8, "WIDTH")


def test_a_positional_override_resolves_against_the_declared_order() -> None:
    """An index into a declaration we *have* is not a guess."""
    wide = _module(
        "wide",
        ports=(_port("q", "output", "logic [WIDTH-1:0]"),),
        parameters=(("DEPTH", "4"), ("WIDTH", "8")),
    )
    top = _module(
        "top",
        instances=(
            _inst(
                "u_wide", "wide", _conn("q", "w"), params=((None, "16"), (None, "3"))
            ),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, _SINK))
    assert edge.bits == 3


def test_substitution_folds_arithmetic_but_never_guesses() -> None:
    """``$clog2`` has no fold, so the width stays honestly unknown."""
    wide = _module(
        "wide",
        ports=(_port("q", "output", "logic [$clog2(DEPTH)-1:0]"),),
        parameters=(("DEPTH", "8"),),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("q", "w")),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, _SINK))
    assert (edge.bits, edge.bits_expr) == (None, None)


def test_scope_params_width_the_scopes_own_declarations() -> None:
    """A net declared ``[DATA_W-1:0]`` is as wide as this instance built it.

    The default (8) is what the module says; ``params`` is what the
    binding site said, and only one of those numbers belongs on the
    wires of this box.
    """
    top = _module(
        "top",
        instances=(
            _inst("u_sink", "sink", _conn("d", "pay")),
            _inst("u_src", "src", _conn("q", "raw")),
        ),
        assigns=(_assign("pay", "raw"),),
        net_decls=(_decl("pay", "logic [DATA_W-1:0]"),),
        parameters=(("DATA_W", "8"),),
    )
    table = _table(top, _SRC, _SINK)
    (default_edge,) = scope_connectivity(top, table)
    assert default_edge.bits == 8
    (bound_edge,) = scope_connectivity(top, table, params={"DATA_W": "19"})
    assert bound_edge.bits == 19


# --- bit widths: the algebraic answer (tier 2) -------------------------------


def test_a_symbolic_override_leaves_only_the_expression() -> None:
    """``.WIDTH(PTR_W)`` with ``PTR_W`` computed: no number exists.

    The expression is the child's own ``WIDTH``, not the caller's
    ``PTR_W`` — it is read off the declaration, and the declaration is
    where the name means something.
    """
    sync = _module(
        "ip_cdc_sync",
        ports=(
            _port("d", "input", "logic [WIDTH-1:0]"),
            _port("q", "output", "logic [WIDTH-1:0]"),
        ),
        parameters=(("WIDTH", "1"),),
    )
    top = _module(
        "top",
        instances=(
            _inst(
                "u_sync", "ip_cdc_sync", _conn("q", "w"), params=(("WIDTH", "PTR_W"),)
            ),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, sync, _SINK))
    assert (edge.bits, edge.bits_expr) == (None, "WIDTH")


def test_a_scope_declaration_keeps_its_own_name() -> None:
    """``logic [PTR_W-1:0] gray;`` in the scope reads as ``PTR_W``."""
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "raw")),
            _inst("u_sink", "sink", _conn("d", "gray")),
        ),
        assigns=(_assign("gray", "raw"),),
        net_decls=(_decl("gray", "logic [PTR_W-1:0]"),),
    )
    (edge,) = scope_connectivity(top, _table(top, _SRC, _SINK))
    assert (edge.bits, edge.bits_expr) == (None, "PTR_W")


def test_bits_stay_numeric_only_beside_the_expression() -> None:
    """``bits`` semantics are unchanged: integers, or ``null``.

    A numerically *declared* bundle also has no expression to show —
    a number is not algebra.
    """
    wide = _module(
        "wide",
        ports=(
            _port("payload", "output", "logic [18:0]"),
            _port("valid", "output", "logic"),
        ),
    )
    dual = _module(
        "dual",
        ports=(
            _port("data", "input", "logic [18:0]"),
            _port("vld", "input", "logic"),
        ),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("payload", "pay"), _conn("valid", "vld")),
            _inst("u_dual", "dual", _conn("data", "pay"), _conn("vld", "vld")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, dual))
    assert edge.bits == 20
    assert edge.bits_expr is None


def test_bits_expr_sums_one_symbol_and_the_numeric_remainder() -> None:
    wide = _module(
        "wide",
        ports=(
            _port("payload", "output", "logic [PTR_W-1:0]"),
            _port("valid", "output", "logic"),
        ),
    )
    dual = _module(
        "dual",
        ports=(_port("data", "input", None), _port("vld", "input", None)),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("payload", "pay"), _conn("valid", "vld")),
            _inst("u_dual", "dual", _conn("data", "pay"), _conn("vld", "vld")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, dual))
    assert (edge.bits, edge.bits_expr) == (None, "PTR_W+1")


def test_bits_expr_abstains_on_two_symbolic_terms() -> None:
    """Collecting like terms needs an algebra we deliberately don't have."""
    wide = _module(
        "wide",
        ports=(
            _port("a", "output", "logic [PTR_W-1:0]"),
            _port("b", "output", "logic [DATA_W-1:0]"),
        ),
    )
    dual = _module(
        "dual",
        ports=(_port("x", "input", None), _port("y", "input", None)),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("a", "n1"), _conn("b", "n2")),
            _inst("u_dual", "dual", _conn("x", "n1"), _conn("y", "n2")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, dual))
    assert (edge.bits, edge.bits_expr) == (None, None)


def test_bits_expr_abstains_when_any_net_is_wholly_unknown() -> None:
    """Same rule as ``bits``: a partial sum is not an answer."""
    wide = _module(
        "wide",
        ports=(
            _port("a", "output", "logic [PTR_W-1:0]"),
            _port("b", "output", None),
        ),
    )
    dual = _module(
        "dual",
        ports=(_port("x", "input", None), _port("y", "input", None)),
    )
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("a", "n1"), _conn("b", "n2")),
            _inst("u_dual", "dual", _conn("x", "n1"), _conn("y", "n2")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, dual))
    assert (edge.bits, edge.bits_expr) == (None, None)


def test_an_unclean_bound_prints_nothing_at_all() -> None:
    """``[$clog2(D)-1:0]`` is knowable to no tier — silence beats noise."""
    wide = _module("wide", ports=(_port("q", "output", "logic [$clog2(DEPTH)-1:0]"),))
    top = _module(
        "top",
        instances=(
            _inst("u_wide", "wide", _conn("q", "w")),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
    )
    (edge,) = scope_connectivity(top, _table(top, wide, _SINK))
    assert (edge.bits, edge.bits_expr) == (None, None)


# --- instance_params ---------------------------------------------------------


def test_instance_params_writes_overrides_over_the_defaults() -> None:
    child = _module(
        "child",
        parameters=(("WIDTH", "8"), ("DEPTH", "16")),
    )
    overrides = _inst("u", "child", params=(("WIDTH", "19"),)).param_overrides
    assert instance_params(child, overrides) == {"WIDTH": "19", "DEPTH": "16"}


def test_instance_params_of_an_unknown_child_keeps_only_named_overrides() -> None:
    """A positional override on a blackbox has no order to resolve against."""
    overrides = _inst(
        "u", "bb", params=((None, "19"), ("MODE", "fast"))
    ).param_overrides
    assert instance_params(None, overrides) == {"MODE": "fast"}


# --- rbsch net hints (epic #159 phase 2) ------------------------------------


def _hints(**by_name: object):
    from rtl_buddy_view.hints import NetHints

    out: dict[str, NetHints] = {}
    for name, value in by_name.items():
        assert isinstance(value, NetHints)
        out[name] = value
    return out


def _net_hint(
    classification: str | None = None,
    emphasis: str | None = None,
    bundle: str | None = None,
):
    from rtl_buddy_view.hints import NetHints

    return NetHints(
        classification=classification,  # type: ignore[arg-type]
        emphasis=emphasis,  # type: ignore[arg-type]
        bundle=bundle,
    )


def test_hinted_clock_net_is_dropped() -> None:
    """``rbsch: clock`` suppresses a clock the name regexes can't see."""
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "tick")),
            _inst("u_sink", "sink", _conn("d", "tick")),
        ),
    )
    table = _table(top, _SRC, _SINK)
    assert scope_connectivity(top, table) != ()
    assert (
        scope_connectivity(
            top, table, net_hints=_hints(tick=_net_hint(classification="clock"))
        )
        == ()
    )


def test_hinted_data_net_survives_the_clock_filter() -> None:
    """``rbsch: data`` rescues a data net whose name reads as a clock."""
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "cclk")),
            _inst("u_sink", "sink", _conn("d", "cclk")),
        ),
    )
    table = _table(top, _SRC, _SINK)
    assert scope_connectivity(top, table) == ()
    edges = scope_connectivity(
        top, table, net_hints=_hints(cclk=_net_hint(classification="data"))
    )
    assert [e.nets for e in edges] == [("cclk",)]


def test_emphasis_lands_on_the_edge_and_implies_data() -> None:
    """``main``/``side`` are statements about a *dataflow* edge, so a
    hinted net also survives the clock filter without a second word."""
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "cclk")),
            _inst("u_sink", "sink", _conn("d", "cclk")),
        ),
    )
    table = _table(top, _SRC, _SINK)
    edges = scope_connectivity(
        top, table, net_hints=_hints(cclk=_net_hint(emphasis="side"))
    )
    assert [e.emphasis for e in edges] == ["side"]


def test_main_beats_side_within_a_bundle() -> None:
    src2 = _module("src2", ports=(_port("q", "output"), _port("r", "output")))
    sink2 = _module("sink2", ports=(_port("d", "input"), _port("e", "input")))
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src2", _conn("q", "a"), _conn("r", "b")),
            _inst("u_sink", "sink2", _conn("d", "a"), _conn("e", "b")),
        ),
    )
    edges = scope_connectivity(
        top,
        _table(top, src2, sink2),
        net_hints=_hints(a=_net_hint(emphasis="main"), b=_net_hint(emphasis="side")),
    )
    assert [e.emphasis for e in edges] == ["main"]


def test_empty_net_hints_are_byte_identical_to_none() -> None:
    top = _module(
        "top",
        instances=(
            _inst("u_src", "src", _conn("q", "w")),
            _inst("u_sink", "sink", _conn("d", "w")),
        ),
    )
    table = _table(top, _SRC, _SINK)
    assert scope_connectivity(top, table) == scope_connectivity(
        top, table, net_hints={}
    )


# --- rbsch bundles (epic #159 phase 3) --------------------------------------


_HS_SRC = _module(
    "hs_src",
    ports=(
        _port("valid", "output", "logic"),
        _port("data", "output", "logic [15:0]"),
        _port("ready", "input", "logic"),
    ),
)
_HS_SINK = _module(
    "hs_sink",
    ports=(
        _port("valid", "input", "logic"),
        _port("data", "input", "logic [15:0]"),
        _port("ready", "output", "logic"),
    ),
)


def _handshake_top() -> Module:
    return _module(
        "top",
        instances=(
            _inst(
                "u_src",
                "hs_src",
                _conn("valid", "cmd_valid"),
                _conn("data", "cmd_data"),
                _conn("ready", "cmd_ready"),
            ),
            _inst(
                "u_sink",
                "hs_sink",
                _conn("valid", "cmd_valid"),
                _conn("data", "cmd_data"),
                _conn("ready", "cmd_ready"),
            ),
        ),
    )


_CMD_BUS = _hints(
    cmd_valid=_net_hint(bundle="cmd_bus"),
    cmd_data=_net_hint(bundle="cmd_bus"),
    cmd_ready=_net_hint(bundle="cmd_bus"),
)


def test_bundle_names_the_edge_when_every_net_agrees() -> None:
    top = _handshake_top()
    edges = scope_connectivity(top, _table(top, _HS_SRC, _HS_SINK), net_hints=_CMD_BUS)
    assert len(edges) == 1
    (edge,) = edges
    assert edge.bundle == "cmd_bus"


def test_bundle_return_path_folds_into_the_data_direction() -> None:
    top = _handshake_top()
    edges = scope_connectivity(top, _table(top, _HS_SRC, _HS_SINK), net_hints=_CMD_BUS)
    (edge,) = edges
    assert (edge.src, edge.dst) == (("inst", "u_src"), ("inst", "u_sink"))
    # The folded return path's nets and pins merge in, sides swapped.
    assert edge.nets == ("cmd_data", "cmd_ready", "cmd_valid")
    assert edge.src_pins == ("data", "ready", "valid")
    assert edge.dst_pins == ("data", "ready", "valid")
    # The slash keeps describing the kept direction's payload only.
    assert edge.bits == 17


def test_mixed_edge_keeps_its_net_list_identity() -> None:
    """A net outside the bundle on the same endpoint pair blocks the
    name (and therefore the fold) — all-or-nothing."""
    top = _module(
        "top",
        instances=(
            _inst(
                "u_src",
                "hs_src",
                _conn("valid", "cmd_valid"),
                _conn("data", "cmd_data"),
                _conn("ready", "cmd_ready"),
            ),
            _inst(
                "u_sink",
                "hs_sink",
                _conn("valid", "cmd_valid"),
                _conn("data", "cmd_data"),
                _conn("ready", "cmd_ready"),
            ),
        ),
    )
    partial = _hints(
        cmd_valid=_net_hint(bundle="cmd_bus"),
        cmd_ready=_net_hint(bundle="cmd_bus"),
        # cmd_data deliberately unhinted.
    )
    edges = scope_connectivity(top, _table(top, _HS_SRC, _HS_SINK), net_hints=partial)
    # The ready-only return edge is unanimously bundled; the forward
    # edge is mixed, so it keeps its net-list identity — and without
    # a named forward edge there is nothing to fold into.
    assert sorted(e.bundle or "" for e in edges) == ["", "cmd_bus"]
    assert len(edges) == 2


def test_symmetric_bundle_abstains_from_folding() -> None:
    """Equal payload both ways: guessing the data direction would
    point an arrow the wrong way exactly when the reader can't tell."""
    ping = _module(
        "ping", ports=(_port("tx", "output", "logic"), _port("rx", "input", "logic"))
    )
    pong = _module(
        "pong", ports=(_port("tx", "output", "logic"), _port("rx", "input", "logic"))
    )
    top = _module(
        "top",
        instances=(
            _inst("u_ping", "ping", _conn("tx", "p2q"), _conn("rx", "q2p")),
            _inst("u_pong", "pong", _conn("tx", "q2p"), _conn("rx", "p2q")),
        ),
    )
    both = _hints(p2q=_net_hint(bundle="link"), q2p=_net_hint(bundle="link"))
    edges = scope_connectivity(top, _table(top, ping, pong), net_hints=both)
    assert len(edges) == 2
    assert {e.bundle for e in edges} == {"link"}


def test_sidecar_empty_bundle_breaks_unanimity() -> None:
    top = _handshake_top()
    revoked = _hints(
        cmd_valid=_net_hint(bundle="cmd_bus"),
        cmd_data=_net_hint(bundle=""),
        cmd_ready=_net_hint(bundle="cmd_bus"),
    )
    edges = scope_connectivity(top, _table(top, _HS_SRC, _HS_SINK), net_hints=revoked)
    assert sorted(e.bundle or "" for e in edges) == ["", "cmd_bus"]
