"""Unit tests for ``graph.find_tb_top`` — recovering the testbench top
module when a ``--tb-top`` hint doesn't name a real module.

Pure-data: builds ``ModuleTable``s by hand, so these run without
Verible (unlike the e2e ``--tb-top`` coverage in test_tb_top.py)."""

from __future__ import annotations

from rtl_buddy_view.extractor import Instance, Module, ModuleTable
from rtl_buddy_view.graph import find_tb_top


def _inst(module_name: str) -> Instance:
    return Instance(
        name=f"u_{module_name}",
        module_name=module_name,
        param_overrides=(),
        port_connections=(),
        location=None,
    )


def _mod(name: str, *children: str) -> Module:
    return Module(
        name=name,
        ports=(),
        parameters=(),
        instances=tuple(_inst(c) for c in children),
        location=None,
    )


def _table(*mods: Module) -> ModuleTable:
    table = ModuleTable()
    for m in mods:
        table.modules_by_name[m.name] = m
    return table


def test_returns_root_wrapping_the_dut() -> None:
    # tb_top -> dut; tb_top is the only root and it contains the DUT.
    table = _table(_mod("tb_top", "dut"), _mod("dut"))
    assert find_tb_top(table, "dut") == "tb_top"


def test_finds_tb_through_nested_hierarchy() -> None:
    table = _table(_mod("tb", "mid"), _mod("mid", "dut"), _mod("dut"))
    assert find_tb_top(table, "dut") == "tb"


def test_falls_back_to_sole_root_when_dut_is_not_a_module() -> None:
    # The "DUT" is an SV interface (apb_intf), absent from the module
    # table — fall back to the single root module.
    table = _table(_mod("tb_top", "sub"), _mod("sub"))
    assert find_tb_top(table, "apb_intf") == "tb_top"


def test_none_when_dut_is_its_own_sole_root() -> None:
    # No wrapping testbench — dut is the only root. Caller keeps the
    # DUT-rooted behaviour rather than substituting the DUT for itself.
    table = _table(_mod("dut", "sub"), _mod("sub"))
    assert find_tb_top(table, "dut") is None


def test_none_when_multiple_roots_contain_the_dut() -> None:
    # Ambiguous: two roots both wrap the DUT.
    table = _table(_mod("tb_a", "dut"), _mod("tb_b", "dut"), _mod("dut"))
    assert find_tb_top(table, "dut") is None


def test_none_when_dut_absent_and_multiple_roots() -> None:
    # DUT isn't a module and there's no single root to fall back to.
    table = _table(_mod("tb_top", "x"), _mod("lib"), _mod("x"))
    assert find_tb_top(table, "apb_intf") is None


def test_ignores_uninstantiated_library_modules() -> None:
    # A pile of unused library roots must not drown out the one root
    # that actually contains the DUT.
    table = _table(
        _mod("tb_top", "dut"),
        _mod("dut", "leaf"),
        _mod("leaf"),
        _mod("unused_lib_a"),
        _mod("unused_lib_b", "unused_lib_c"),
        _mod("unused_lib_c"),
    )
    assert find_tb_top(table, "dut") == "tb_top"
