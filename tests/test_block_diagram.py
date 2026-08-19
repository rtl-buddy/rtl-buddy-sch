"""End-to-end coverage for ``--block-diagram``.

Parses the ``block_diagram_demo`` fixture with Verible, so it skips
when the binary is absent — same gate the other fixture-driven tests
use.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.connectivity import scope_connectivity
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import dot as dot_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "block_diagram_demo"


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


# --- extractor: continuous assigns ------------------------------------------


def test_frontend_captures_continuous_assigns() -> None:
    table = _table()
    top = table.modules_by_name["blk_top"]
    assert [(a.lhs_text, a.rhs_text) for a in top.assigns] == [
        ("prod_payload_q", "prod_payload")
    ]
    assert top.assigns[0].location is not None
    assert top.assigns[0].location.start_line is not None


def test_frontend_captures_multiple_assigns_in_one_module() -> None:
    table = _table()
    prod = table.modules_by_name["blk_producer"]
    assert [a.lhs_text for a in prod.assigns] == ["payload", "valid"]


def test_modules_without_assigns_default_to_empty() -> None:
    table = _table()
    assert table.modules_by_name["blk_leaf"].assigns == ()


# --- extractor: net declarations --------------------------------------------


def test_frontend_captures_module_body_net_declarations() -> None:
    table = _table()
    top = table.modules_by_name["blk_top"]
    assert [(d.name, d.type_text) for d in top.net_decls] == [
        ("prod_valid", "logic"),
        ("prod_payload", "logic [7:0]"),
        ("prod_payload_q", "logic [7:0]"),
    ]
    assert table.modules_by_name["blk_producer"].net_decls[0].name == "stage"


def test_modules_without_declarations_default_to_empty() -> None:
    table = _table()
    assert table.modules_by_name["blk_leaf"].net_decls == ()


# --- analyzer ---------------------------------------------------------------


def test_scope_connectivity_traces_the_fixture_dataflow() -> None:
    """Every hop the fixture was built to exercise, in one assertion.

    ``cmd_in`` → ``u_prod`` is a direct port match; ``u_prod`` →
    ``u_cons`` needs the ``prod_payload_q`` assign alias *and* the
    ``[7:0]`` part-select on the actual; ``u_cons`` → ``result_out``
    closes on a top output port. ``clk`` / ``rst_n`` are wired by
    implicit shorthand and must be filtered as clock/reset trees, not
    silently missed.
    """
    table = _table()
    edges = scope_connectivity(table.modules_by_name["blk_top"], table)
    assert [(e.src, e.dst, e.nets) for e in edges] == [
        (("inst", "u_cons"), ("port", "result_out"), ("result_out",)),
        (
            ("inst", "u_prod"),
            ("inst", "u_cons"),
            ("prod_payload_q", "prod_valid"),
        ),
        (("port", "cmd_in"), ("inst", "u_prod"), ("cmd_in",)),
    ]


def test_every_bundle_in_the_fixture_has_a_width() -> None:
    """The nets the fixture routes over assigns are all measurable now.

    ``prod_payload_q`` is produced by ``assign prod_payload_q =
    prod_payload;`` and so has no driving *pin* — before the scope's
    own ``logic [7:0]`` declaration was consulted, the whole
    ``u_prod`` → ``u_cons`` bundle reported ``bits = None`` and the
    canvas drew no bus slash on it.
    """
    table = _table()
    edges = scope_connectivity(table.modules_by_name["blk_top"], table)
    assert [(e.nets, e.bits) for e in edges] == [
        (("result_out",), 8),
        (("prod_payload_q", "prod_valid"), 9),
        (("cmd_in",), 16),
    ]


def test_an_assign_driven_output_port_gets_its_own_declared_width() -> None:
    """``assign payload = stage;`` drives ``blk_producer``'s own port.

    Nothing pins ``payload`` from the inside, so the width has to come
    from the scope's port declaration — the last resolution tier.
    """
    table = _table()
    edges = scope_connectivity(table.modules_by_name["blk_producer"], table)
    by_net = {e.nets: e.bits for e in edges}
    assert by_net[("payload",)] == 8
    assert by_net[("valid",)] == 1


def test_scope_connectivity_inside_a_nested_scope() -> None:
    """``blk_producer``'s only child is fed by its own ports, so the
    nested scope's edges all touch ports — none of which the renderer
    draws below the top frame."""
    table = _table()
    edges = scope_connectivity(table.modules_by_name["blk_producer"], table)
    assert all("port" in (e.src[0], e.dst[0]) for e in edges)


# --- renderer ---------------------------------------------------------------


def _render_block() -> str:
    table = _table()
    root = build_hierarchy(table, "blk_top")
    out = io.StringIO()
    dot_render.render(root, out, block_diagram=True, module_table=table)
    return out.getvalue()


def test_block_render_emits_containment_and_dataflow() -> None:
    text = _render_block()
    assert "subgraph cluster_blk_top_u_prod {" in text
    assert '"_in_cmd_in" -> "blk_top.u_prod"' in text
    assert '"blk_top.u_prod" -> "blk_top.u_cons"' in text
    assert '"blk_top.u_cons" -> "_out_result_out"' in text
    assert 'xlabel="prod_payload_q\\lprod_valid\\l"' in text


def test_block_render_drops_the_clock_and_reset_spiderweb() -> None:
    text = _render_block()
    assert '"_in_clk" ->' not in text
    assert '"_in_rst_n" ->' not in text


def test_block_render_names_only_clusters_it_emitted() -> None:
    """Every ``lhead`` / ``ltail`` must resolve — an unknown cluster is
    a Graphviz warning on every single render."""
    text = _render_block()
    declared = {
        line.strip().split()[1]
        for line in text.splitlines()
        if line.strip().startswith("subgraph ")
    }
    for attr in ("lhead", "ltail"):
        for chunk in text.split(f'{attr}="')[1:]:
            assert chunk.split('"')[0] in declared


def test_default_render_names_only_clusters_it_emitted() -> None:
    """Same guard for the default box layout — the regression this
    change set fixes."""
    table = _table()
    root = build_hierarchy(table, "blk_top")
    out = io.StringIO()
    dot_render.render(root, out, module_table=table)
    text = out.getvalue()
    assert "lhead=" not in text
    assert "ltail=" not in text


# --- CLI --------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rtl_buddy_view", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_block_diagram_flag_renders_dataflow() -> None:
    result = _run_cli(
        "--top",
        "blk_top",
        "--filelist",
        str(FIXTURE_DIR / "files.f"),
        "--format",
        "dot",
        "--block-diagram",
    )
    assert result.returncode == 0, result.stderr
    assert '"blk_top.u_prod" -> "blk_top.u_cons"' in result.stdout
    assert "subgraph cluster_blk_top_u_prod {" in result.stdout


def test_cli_block_diagram_warns_and_is_ignored_for_other_formats() -> None:
    result = _run_cli(
        "--top",
        "blk_top",
        "--filelist",
        str(FIXTURE_DIR / "files.f"),
        "--format",
        "tree",
        "--block-diagram",
    )
    assert result.returncode == 0, result.stderr
    assert "--block-diagram" in result.stderr
    assert "blk_top" in result.stdout


def test_cli_dot_without_the_flag_is_unchanged() -> None:
    plain = _run_cli(
        "--top",
        "blk_top",
        "--filelist",
        str(FIXTURE_DIR / "files.f"),
        "--format",
        "dot",
    )
    assert plain.returncode == 0, plain.stderr
    assert "subgraph cluster_blk_top_u_prod {" not in plain.stdout
    assert '"blk_top.u_prod" -> "blk_top.u_prod.u_stage"' in plain.stdout
