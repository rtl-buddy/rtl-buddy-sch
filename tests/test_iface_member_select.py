"""End-to-end coverage for interface member selects (rtl-buddy-sch#193).

A scalar port bound to an interface *member* (``.en(cfg.a_en)``,
``.sta(q[1].c_sta)``) is its own net. Before the fix every member of
one interface — and every element of an interface array — collapsed
onto the interface's name, so the block diagram drew all-to-all edges
labelled ``cfg`` / ``q``.

Parses ``tests/fixtures/iface_member_select`` with Verible, so it
skips when the binary is absent — the gate the other fixture-driven
tests use.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from rtl_buddy_view import elk_export
from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.hints import resolve_hints, scan_pragmas
from rtl_buddy_view.render import dot as dot_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "iface_member_select"

pytestmark = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

_EDGE_RE = re.compile(r'^\s*"([^"]+)" -> "([^"]+)" \[.*?xlabel="([^"]*)"', re.M)


def _table():
    return parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )


def _block_edges(top: str, *, pragmas: bool = False) -> list[tuple[str, str, str]]:
    table = _table()
    hints = None
    if pragmas:
        sources = {
            str(path): path.read_text()
            for path in parse_filelist(FIXTURE_DIR / "files.f")
        }
        hints = resolve_hints(scan_pragmas(sources), table)
    out = io.StringIO()
    dot_render.render(
        build_hierarchy(table, top),
        out,
        block_diagram=True,
        module_table=table,
        hints=hints,
    )
    return [
        (src, dst, label.replace(r"\l", " ").strip())
        for src, dst, label in _EDGE_RE.findall(out.getvalue())
    ]


def test_dot_block_diagram_draws_one_edge_per_member() -> None:
    assert sorted(_block_edges("top2")) == [
        ("top2.u_c", "top2.u_csr", "cfg.c_sta"),
        ("top2.u_csr", "top2.u_a", "cfg.a_en"),
        ("top2.u_csr", "top2.u_b", "cfg.b_en"),
        ("top2.u_p0", "top2.u_k0", "q[0].c_sta"),
        ("top2.u_p1", "top2.u_k1", "q[1].c_sta"),
    ]


def test_elk_edges_carry_the_member_paths() -> None:
    table = _table()
    payload = elk_export.export_elk(build_hierarchy(table, "top2"), table)
    edges = {e["id"]: (e["rb"]["nets"], e["rb"]["bits"]) for e in payload["edges"]}
    assert edges == {
        "top2.u_c:sta->top2.u_csr:c_sta": (["cfg.c_sta"], 1),
        "top2.u_csr:a_en->top2.u_a:en": (["cfg.a_en"], 1),
        "top2.u_csr:b_en->top2.u_b:en": (["cfg.b_en"], 1),
        "top2.u_p0:sta->top2.u_k0:en": (["q[0].c_sta"], 1),
        "top2.u_p1:sta->top2.u_k1:en": (["q[1].c_sta"], 1),
    }


def test_bundle_pragma_on_the_interface_instance_names_the_members() -> None:
    table = _table()
    sources = {
        str(path): path.read_text() for path in parse_filelist(FIXTURE_DIR / "files.f")
    }
    hints = resolve_hints(scan_pragmas(sources), table)
    assert not hints.warnings
    assert hints.nets_for_module("top3")["cfg"].bundle == "cfg_bus"
    assert _block_edges("top3", pragmas=True) == [
        ("top3.u_csr", "top3.u_peer", "cfg_bus"),
    ]


def test_without_the_pragma_the_member_edges_stay_apart() -> None:
    assert sorted(_block_edges("top3")) == [
        ("top3.u_csr", "top3.u_peer", "cfg.a_en cfg.b_en"),
        ("top3.u_peer", "top3.u_csr", "cfg.c_sta"),
    ]


def test_struct_field_selects_are_not_interface_members() -> None:
    assert sorted(_block_edges("top4")) == [
        ("top4.u_src", "top4.u_x", "s"),
        ("top4.u_src", "top4.u_y", "s"),
    ]
