"""Every node an edge names is a node the dot output declares.

Graphviz silently invents a default box for an undeclared node id on
an edge — which is how a modport-less root interface port once turned
into a stray ``_in_gen`` box inside the frame (rtl-buddy-sch#195). This
renders every fixture design from every module that can be a top, in
each dot layout mode, and checks that each edge endpoint is declared
and that each ``lhead`` / ``ltail`` names a cluster that exists.

Verible-backed, so it skips when the binary is absent.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.hints import resolve_hints, scan_pragmas
from rtl_buddy_view.render import dot as dot_render

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

_NODE_RE = re.compile(r'^\s*"([^"]+)"\s*\[', re.M)
_EDGE_RE = re.compile(r'^\s*"([^"]+)"\s*->\s*"([^"]+)"', re.M)
_CLUSTER_RE = re.compile(r"subgraph\s+(cluster_[^\s{]+)")
_CLUSTER_REF_RE = re.compile(r'\bl(?:head|tail)="([^"]+)"')

_MODES = {
    "block": {"block_diagram": True},
    "cluster": {"as_cluster_tree": True},
    "tree": {},
}


def _filelists() -> list[Path]:
    return sorted(FIXTURES.glob("*/files.f"))


def _undeclared(text: str) -> list[str]:
    declared = set(_NODE_RE.findall(text))
    clusters = set(_CLUSTER_RE.findall(text))
    problems = [
        f"edge {src} -> {dst}: {end} undeclared"
        for src, dst in _EDGE_RE.findall(text)
        for end in (src, dst)
        if end not in declared
    ]
    problems += [
        f"cluster ref {ref} undeclared"
        for ref in _CLUSTER_REF_RE.findall(text)
        if ref not in clusters
    ]
    return problems


def test_the_sweep_covers_the_interface_fixtures() -> None:
    names = {f.parent.name for f in _filelists()}
    assert {"iface_member_select", "iface_modport_edges", "iface_root_ports"} <= names


@pytest.mark.parametrize("filelist", _filelists(), ids=lambda p: p.parent.name)
def test_every_edge_endpoint_is_declared(filelist: Path) -> None:
    files = parse_filelist(filelist)
    table = parse_to_modules(files, frontend=Frontend.verible)
    hints = resolve_hints(scan_pragmas({str(p): p.read_text() for p in files}), table)
    problems: list[str] = []
    rendered = 0
    for top in sorted(table.modules_by_name):
        node = build_hierarchy(table, top)
        for mode, kwargs in _MODES.items():
            for hint_map in (None, hints):
                out = io.StringIO()
                dot_render.render(
                    node, out, module_table=table, hints=hint_map, **kwargs
                )
                rendered += 1
                problems += [
                    f"{top} [{mode}, hints={hint_map is not None}]: {p}"
                    for p in _undeclared(out.getvalue())
                ]
    assert rendered
    assert not problems, "\n".join(problems)


def test_the_check_catches_an_undeclared_endpoint() -> None:
    """The #195 symptom, by hand: the check must not pass vacuously."""
    text = '  "top.u_k" [label="u_k"];\n  "_in_gen" -> "top.u_k" [xlabel="gen"];\n'
    assert _undeclared(text) == ["edge _in_gen -> top.u_k: _in_gen undeclared"]
