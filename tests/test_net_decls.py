"""Verible-gated coverage for module-body net/variable declaration capture.

``Module.net_decls`` is what lets the connectivity analyzer width a
net that no *pin* drives — an ``assign``-driven bundle. The shapes it
has to survive are all CST-level, so this file pins them against a
real Verible run rather than a hand-built ``ModuleTable``: the
variable form (``kDataDeclaration``) shares its node type with module
instantiation, and the net form (``kNetDeclaration``) hangs its packed
range off a *second* type node. Both were confirmed with
``verible-verilog-syntax --printtree``.

The declaration source lives inline (written to ``tmp_path``) rather
than under ``fixtures/``: it is a grammar catalogue, not a design, and
nothing else has any use for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.extractor import Module
from rtl_buddy_view.frontend import verible

pytestmark = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

SHAPES_SV = """\
module nd_shapes #(
  parameter int W = 4
) (
  input  logic       clk,
  output logic [3:0] o
);
  logic [18:0] payload;
  wire plain;
  logic multi_a, multi_b;
  wire [W-1:0] parametrised;
  logic [7:0] initialised = 8'h5;
  wire [2:0] net_init = payload[2:0];
  reg [1:0] legacy;
  wire signed [7:0] signed_net;
  bit [3:0] flags;
  int counter;
  logic   [1:0]   padded;

  nd_leaf u_leaf (.din(payload));
endmodule

module nd_leaf (input logic [18:0] din);
endmodule
"""

SCOPES_SV = """\
module nd_scopes;
  logic [3:0] body_net;

  function automatic int f(input int x);
    logic [7:0] fn_local;
    return x;
  endfunction

  always_comb begin
    automatic logic [5:0] proc_local = 0;
  end

  for (genvar i = 0; i < 2; i++) begin : g_loop
    wire [2:0] loop_net;
  end
endmodule

module nd_non_ansi(a, b);
  input [3:0] a;
  output b;
  wire [3:0] a;
  logic [1:0] local_net;
endmodule
"""


def _parse(tmp_path: Path, name: str, body: str) -> dict[str, Module]:
    src = tmp_path / name
    src.write_text(body, encoding="utf-8")
    return verible.parse([src]).modules_by_name


@pytest.fixture(scope="module")
def shapes(tmp_path_factory: pytest.TempPathFactory) -> Module:
    modules = _parse(tmp_path_factory.mktemp("shapes"), "nd_shapes.sv", SHAPES_SV)
    return modules["nd_shapes"]


@pytest.fixture(scope="module")
def scopes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Module]:
    return _parse(tmp_path_factory.mktemp("scopes"), "nd_scopes.sv", SCOPES_SV)


# --- shapes -----------------------------------------------------------------


def test_every_declarator_shape_is_captured_with_its_type_verbatim(
    shapes: Module,
) -> None:
    """One record per declarator, ``type_text`` exactly as written.

    Verbatim matters: :func:`rtl_buddy_view.connectivity.port_width`
    is the only thing that parses it, and it reports a parameterised
    bound as *unknown* rather than guessing — which it can only do if
    the frontend hands the bound over untouched. ``padded`` pins how
    literal that is: alignment whitespace inside the declaration
    survives, exactly as it does for :attr:`Port.type_text`.
    """
    assert [(d.name, d.type_text) for d in shapes.net_decls] == [
        ("payload", "logic [18:0]"),
        ("plain", "wire"),
        ("multi_a", "logic"),
        ("multi_b", "logic"),
        ("parametrised", "wire [W-1:0]"),
        ("initialised", "logic [7:0]"),
        ("net_init", "wire [2:0]"),
        ("legacy", "reg [1:0]"),
        ("signed_net", "wire signed [7:0]"),
        ("flags", "bit [3:0]"),
        ("counter", "int"),
        ("padded", "logic   [1:0]"),
    ]


def test_a_multi_declarator_statement_shares_one_type(shapes: Module) -> None:
    """``logic multi_a, multi_b;`` is two nets, not one."""
    multi = [d for d in shapes.net_decls if d.name.startswith("multi_")]
    assert len(multi) == 2
    assert {d.type_text for d in multi} == {"logic"}


def test_every_declaration_carries_a_source_location(shapes: Module) -> None:
    """The declarator's own identifier, not the whole statement."""
    payload = next(d for d in shapes.net_decls if d.name == "payload")
    multi_b = next(d for d in shapes.net_decls if d.name == "multi_b")
    assert payload.location is not None and payload.location.start_line == 7
    assert multi_b.location is not None and multi_b.location.start_line == 9


def test_an_instantiation_is_not_mistaken_for_a_declaration(
    shapes: Module,
) -> None:
    """``kDataDeclaration`` covers both; ``kGateInstance`` separates them."""
    assert "u_leaf" not in {d.name for d in shapes.net_decls}
    assert [i.name for i in shapes.instances] == ["u_leaf"]


def test_ports_are_not_re_captured_as_net_declarations(shapes: Module) -> None:
    """They are already ``Module.ports`` — one name, one record."""
    assert {"clk", "o"}.isdisjoint({d.name for d in shapes.net_decls})


def test_a_module_without_declarations_stays_empty(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    modules = _parse(tmp_path_factory.mktemp("bare"), "bare.sv", SHAPES_SV)
    assert modules["nd_leaf"].net_decls == ()


# --- scoping ----------------------------------------------------------------


def test_procedural_temporaries_are_not_declarations(
    scopes: dict[str, Module],
) -> None:
    """Function and ``always``-block locals are not module nets.

    They hang off ``kBlockItemStatementList``, which the walk never
    descends into — capturing them would let a function's ``tmp``
    shadow a real net of the same name.
    """
    names = {d.name for d in scopes["nd_scopes"].net_decls}
    assert "fn_local" not in names
    assert "proc_local" not in names


def test_generate_scoped_declarations_are_captured(
    scopes: dict[str, Module],
) -> None:
    """The analyzer already treats generate-scoped assigns as flat."""
    assert [(d.name, d.type_text) for d in scopes["nd_scopes"].net_decls] == [
        ("body_net", "logic [3:0]"),
        ("loop_net", "wire [2:0]"),
    ]


def test_a_non_ansi_port_redeclaration_still_lands(
    scopes: dict[str, Module],
) -> None:
    """Nothing is dropped when the header gave us no port to collide with.

    A non-ANSI header carries no types, so ``Module.ports`` is empty
    and the body's ``wire [3:0] a;`` is the only width there is.
    """
    non_ansi = scopes["nd_non_ansi"]
    assert non_ansi.ports == ()
    assert [(d.name, d.type_text) for d in non_ansi.net_decls] == [
        ("a", "wire [3:0]"),
        ("local_net", "logic [1:0]"),
    ]


# --- net declarations with initializers -------------------------------------


def test_a_net_declaration_initializer_is_also_a_continuous_assign(
    shapes: Module,
) -> None:
    """``wire w = expr;`` drives ``w`` continuously (LRM 1800 § 6.7.1).

    Verible gives it ``kNetDeclarationAssignment``, a different node
    from the ``kNetVariableAssignment`` under a standalone ``assign``
    statement — so it was invisible to the extractor until this pass
    was added, and the net looked undriven.
    """
    assert ("net_init", "payload[2:0]") in [
        (a.lhs_text, a.rhs_text) for a in shapes.assigns
    ]


def test_a_variable_initializer_is_not_a_continuous_assign(
    shapes: Module,
) -> None:
    """``logic w = expr;`` is a one-time static init, not a driver."""
    assert "initialised" not in {a.lhs_text for a in shapes.assigns}


def test_an_initialised_declaration_is_still_a_declaration(
    shapes: Module,
) -> None:
    """Both forms keep their width — the assign pass is additive."""
    by_name = {d.name: d.type_text for d in shapes.net_decls}
    assert by_name["net_init"] == "wire [2:0]"
    assert by_name["initialised"] == "logic [7:0]"
