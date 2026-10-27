"""Generate-scope names agree with slang's elaboration (rtl-buddy-sch#209).

The ``slang`` frontend is still a stub (``frontend/slang.py`` raises
``NotImplementedError``), so there is no second rtl-buddy tree to diff
against. What can drift is the *naming*: the Verible frontend unrolls
generate blocks itself and has to name the result exactly as slang and
simulators do (``g[0].u_s``, ``genblk2.u_m1``, ``c2.u_c``). This
elaborates the same fixtures with pyslang directly and checks the two
agree:

* every node the Verible path decided (not conditional, no ``[*]``
  index) is an instance slang elaborated, under the same path;
* every instance slang elaborated is either one of those, or is
  covered by a symbolic (``g[*]``) or conditional node.

Interface instances are left out: slang names each element of an
interface array (``bq[0]``), the Verible hierarchy keeps the array as
one ``bq`` node.

Needs the optional ``[slang]`` extra and Verible, so it skips in the
default CI environment; run it with
``uv run --with 'pyslang>=10,<11' pytest tests/test_generate_slang_parity.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy, is_interface_instance
from rtl_buddy_view.query import walk

pyslang = pytest.importorskip("pyslang")

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "generate_blocks"

pytestmark = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


def _slang_paths(files: list[Path], top: str) -> set[str]:
    compilation = pyslang.Compilation()
    for path in files:
        compilation.addSyntaxTree(pyslang.SyntaxTree.fromFile(str(path)))
    errors = [d for d in compilation.getAllDiagnostics() if d.isError()]
    assert not errors, errors
    paths: set[str] = set()
    for inst in compilation.getRoot().topInstances:
        if inst.name != top:
            continue

        def visit(sym: object) -> None:
            if getattr(sym, "kind", None) != pyslang.SymbolKind.Instance:
                return
            definition = sym.definition  # type: ignore[attr-defined]
            if definition.definitionKind == pyslang.DefinitionKind.Interface:
                return
            paths.add(sym.hierarchicalPath)  # type: ignore[attr-defined]

        inst.visit(visit)
    return paths


@pytest.mark.parametrize("top", ["top7", "gen_if_top", "gen_nested"])
def test_verible_unrolling_names_match_slang(top: str) -> None:
    files = parse_filelist(FIXTURE_DIR / "files.f")
    table = parse_to_modules(files, frontend=Frontend.verible)
    nodes = [
        n
        for n in walk(build_hierarchy(table, top))
        if not is_interface_instance(n, table)
    ]
    decided = {
        n.instance_path
        for n in nodes
        if "[*]" not in n.instance_path and not n.is_conditional
    }
    undecided = [
        re.compile(re.escape(n.instance_path).replace(r"\[\*\]", r"\[\d+\]") + "$")
        for n in nodes
        if n.instance_path not in decided
    ]
    slang = _slang_paths(files, top)

    assert decided <= slang
    leftover = sorted(
        p for p in slang - decided if not any(r.match(p) for r in undecided)
    )
    assert leftover == []
