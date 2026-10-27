"""Recognised-safe CDC crossings are not drawn as hazards (rtl-buddy-sch#211).

rtl-buddy-cdc's domain map schema 1.2 (rtl-buddy-cdc#307) gives every
crossing a ``synchronizer`` scheme (or ``null``) and its post-waiver
``findings`` (``[]`` clean, ``null`` when the rules never ran). A
crossing is recognised-safe iff it has a scheme AND ``findings == []``;
everything else — and every 1.1 crossing — keeps the ``⚠CDC`` hazard
rendering.

The ``cdc_sync_design`` fixture has one destination per verdict (see
its ``cdc_top.sv``). ``goldens_v1_1/`` was rendered by the code
*before* this change from ``domain_map_v1_1.json`` (the same crossings
without the 1.2 keys), so comparing against it proves a 1.1 map still
renders byte-identically. Never regenerate those goldens from newer
code to make a failure go away: a diff there is a 1.1 regression.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.annotations import (
    AnnotationsError,
    Clock,
    Crossing,
    DomainMap,
    load_domain_map,
)
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import HierNode, build_hierarchy
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.render import json_render
from rtl_buddy_view.render import mermaid as mermaid_render
from rtl_buddy_view.render import tree as tree_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cdc_sync_design"
GOLDEN_V11 = FIXTURE_DIR / "goldens_v1_1"

_needs_verible = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

TEAL = "#0d9488"
RED = "#dc2626"


# --- annotations: parsing ----------------------------------------------------


def _write_map(tmp_path: Path, crossing_extra: dict, version: str = "1.2") -> Path:
    payload = {
        "schema_version": version,
        "generator": {"name": "rtl-buddy-cdc", "version": "0"},
        "design": {"top": "top", "frontend": "slang"},
        "clocks": [{"name": "clk_a", "source": "create_clock", "ports": []}],
        "crossings": [
            {
                "src_clock": "clk_a",
                "dst_clock": "clk_b",
                "dst_flop": "top.u_x",
                "async_per_sdc": True,
                **crossing_extra,
            }
        ],
    }
    p = tmp_path / "map.json"
    p.write_text(json.dumps(payload))
    return p


def test_v1_1_crossing_has_no_scheme_and_unknown_findings(tmp_path: Path) -> None:
    c = load_domain_map(_write_map(tmp_path, {}, version="1.1")).crossings[0]
    assert c.synchronizer is None
    assert c.findings is None
    assert c.is_synchronized is False


def test_v1_2_clean_scheme_is_synchronized(tmp_path: Path) -> None:
    c = load_domain_map(
        _write_map(tmp_path, {"synchronizer": "2ff", "findings": []})
    ).crossings[0]
    assert c.synchronizer == "2ff"
    assert c.findings == ()
    assert c.is_synchronized is True
    assert c.scheme_label == "2FF"


def test_v1_2_findings_null_is_not_empty(tmp_path: Path) -> None:
    """``null`` means the rules never ran — not the same as ``[]``."""
    c = load_domain_map(
        _write_map(tmp_path, {"synchronizer": "2ff", "findings": None})
    ).crossings[0]
    assert c.findings is None
    assert c.is_synchronized is False


def test_v1_2_flagged_scheme_is_a_hazard(tmp_path: Path) -> None:
    c = load_domain_map(
        _write_map(tmp_path, {"synchronizer": "2ff", "findings": ["CDC-003"]})
    ).crossings[0]
    assert c.findings == ("CDC-003",)
    assert c.is_synchronized is False


def test_v1_2_null_scheme_clean_is_a_hazard(tmp_path: Path) -> None:
    """A waived crossing with no scheme: clean findings, still no synchronizer."""
    c = load_domain_map(
        _write_map(tmp_path, {"synchronizer": None, "findings": []})
    ).crossings[0]
    assert c.synchronizer is None
    assert c.is_synchronized is False


def test_unknown_scheme_is_still_a_scheme(tmp_path: Path) -> None:
    c = load_domain_map(
        _write_map(tmp_path, {"synchronizer": "fifo_ptr", "findings": []})
    ).crossings[0]
    assert c.is_synchronized is True
    assert c.scheme_label == "FIFO_PTR"


@pytest.mark.parametrize(
    "extra",
    [
        {"synchronizer": 2},
        {"findings": "CDC-001"},
        {"findings": [1]},
    ],
)
def test_malformed_1_2_keys_raise(tmp_path: Path, extra: dict) -> None:
    with pytest.raises(AnnotationsError):
        load_domain_map(_write_map(tmp_path, extra))


def test_hazards_and_synchronized_partition_crossings_into() -> None:
    m = load_domain_map(FIXTURE_DIR / "domain_map_v1_2.json")
    for inst in ("u_sync", "u_flagged", "u_raw", "u_unchecked", "u_mix", "u_dual"):
        path = f"cdc_top.{inst}"
        everything = m.crossings_into(path)
        assert set(m.hazards_into(path)) | set(m.synchronized_into(path)) == set(
            everything
        )
        assert not set(m.hazards_into(path)) & set(m.synchronized_into(path))
    assert m.hazards_into("cdc_top.u_sync") == ()
    assert m.synchronized_into("cdc_top.u_flagged") == ()
    assert m.synchronized_into("cdc_top.u_raw") == ()
    assert m.synchronized_into("cdc_top.u_unchecked") == ()


# --- fixture-driven renders --------------------------------------------------


def _design():
    table = parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )
    return table, build_hierarchy(table, "cdc_top")


def _renders(map_name: str) -> dict[str, str]:
    table, root = _design()
    dm = load_domain_map(FIXTURE_DIR / map_name)
    variants = {
        "hierarchy.dot": lambda o: dot_render.render(
            root, o, domain_map=dm, with_legend=True
        ),
        "cluster.dot": lambda o: dot_render.render(
            root, o, domain_map=dm, as_cluster_tree=True
        ),
        "block.dot": lambda o: dot_render.render(
            root, o, domain_map=dm, block_diagram=True, module_table=table
        ),
        "hierarchy.mmd": lambda o: mermaid_render.render(root, o, domain_map=dm),
        "tree.txt": lambda o: tree_render.render(root, o, domain_map=dm, links=False),
    }
    out: dict[str, str] = {}
    for name, fn in variants.items():
        buf = io.StringIO()
        fn(buf)
        out[name] = buf.getvalue()
    return out


def _line(text: str, *needles: str) -> str:
    # ``style=invis`` lines are the grid packer's layout spacers.
    hits = [
        ln
        for ln in text.splitlines()
        if all(n in ln for n in needles) and "style=invis" not in ln
    ]
    assert len(hits) == 1, (needles, hits)
    return hits[0]


@_needs_verible
@pytest.mark.parametrize(
    "name",
    ["hierarchy.dot", "cluster.dot", "block.dot", "hierarchy.mmd", "tree.txt"],
)
def test_v1_1_map_renders_byte_identically(name: str) -> None:
    assert _renders("domain_map_v1_1.json")[name] == (GOLDEN_V11 / name).read_text()


@_needs_verible
@pytest.mark.parametrize("name", ["hierarchy.dot", "cluster.dot", "block.dot"])
def test_dot_top_arrows_follow_the_verdict(name: str) -> None:
    text = _renders("domain_map_v1_2.json")[name]
    safe = _line(text, '"_in_clk_a" -> "cdc_top.u_sync"')
    assert 'label="2FF clk_a→clk_b"' in safe
    assert TEAL in safe and RED not in safe and "dashed" not in safe
    for inst in ("u_flagged", "u_raw", "u_unchecked"):
        arrow = _line(text, f'"_in_clk_a" -> "cdc_top.{inst}"')
        assert 'label="⚠CDC: clk_a→clk_b"' in arrow
        assert RED in arrow and 'style="dashed"' in arrow
    # Mixed destination: the hazard pair stays red, the safe pair is teal.
    assert RED in _line(text, '"_in_clk_a" -> "cdc_top.u_mix"')
    mix_safe = _line(text, '"_in_clk_c" -> "cdc_top.u_mix"')
    assert 'label="2FF clk_c→clk_b"' in mix_safe and TEAL in mix_safe
    # Two schemes on one pair share one arrow.
    dual = _line(text, '"_in_clk_a" -> "cdc_top.u_dual"')
    assert 'label="2FF+HANDSHAKE clk_a→clk_b"' in dual


@_needs_verible
@pytest.mark.parametrize("name", ["cluster.dot", "block.dot"])
def test_dot_destination_nodes_follow_the_verdict(name: str) -> None:
    text = _renders("domain_map_v1_2.json")[name]
    sync = _line(text, '  "cdc_top.u_sync" [')
    assert "2FF clk_a→clk_b" in sync and "⚠" not in sync
    assert f'color="{TEAL}"' in sync and RED not in sync
    # The clock-domain fill is untouched: still the src:dst stripe.
    assert 'style="rounded,striped"' in sync
    for inst in ("u_flagged", "u_raw", "u_unchecked"):
        node = _line(text, f'  "cdc_top.{inst}" [')
        assert "⚠CDC: clk_a→clk_b" in node and f'color="{RED}"' in node
    # Two-pair grids: all-safe is teal, any hazard keeps the red border.
    assert f'color="{TEAL}"' in _line(text, '  "cdc_top.u_dual" [')
    assert f'color="{RED}"' in _line(text, '  "cdc_top.u_mix" [')


@_needs_verible
def test_dot_hierarchy_edges_name_the_scheme() -> None:
    text = _renders("domain_map_v1_2.json")["hierarchy.dot"]
    ab = _line(text, '"cdc_top.u_bridge" -> "cdc_top.u_bridge.u_ab"')
    assert 'label="2FF clk_a→clk_b\\n' in ab
    assert f'color="{TEAL}"' in ab and "dashed" not in ab and "⚠" not in ab
    ba = _line(text, '"cdc_top.u_bridge" -> "cdc_top.u_bridge.u_ba"')
    assert 'label="GRAY clk_b→clk_a\\n' in ba


@_needs_verible
def test_mermaid_follows_the_verdict() -> None:
    text = _renders("domain_map_v1_2.json")["hierarchy.mmd"]
    sync = _line(text, "| cdc_top_u_sync")
    assert "-->" in sync and "-.->" not in sync
    assert '"2FF clk_a→clk_b<br/>' in sync
    assert _line(text, "style cdc_top_u_sync ").endswith(f"stroke:{TEAL}")
    for inst in ("u_flagged", "u_raw", "u_unchecked"):
        edge = _line(text, f"| cdc_top_{inst}")
        assert "-.->" in edge and "⚠CDC: clk_a→clk_b" in edge
        assert _line(text, f"style cdc_top_{inst} ").endswith(f"stroke:{RED}")
    # Mixed: only the hazard after the ⚠, the safe pair on its own line.
    mix = _line(text, "| cdc_top_u_mix")
    assert '"⚠CDC: clk_a→clk_b<br/>2FF clk_c→clk_b<br/>' in mix
    assert _line(text, "style cdc_top_u_mix ").endswith(f"stroke:{RED}")


@_needs_verible
def test_tree_follows_the_verdict() -> None:
    lines = _renders("domain_map_v1_2.json")["tree.txt"].splitlines()

    def row(inst: str) -> str:
        return next(ln for ln in lines if f" {inst} : " in ln)

    assert row("u_sync").endswith("[clk_b]  ✓CDC[2FF clk_a→clk_b]")
    for inst in ("u_flagged", "u_raw", "u_unchecked"):
        assert row(inst).endswith("⚠CDC[clk_a→clk_b]")
    assert row("u_mix").endswith("⚠CDC[clk_a→clk_b]  ✓CDC[2FF clk_c→clk_b]")
    assert "⚠" not in row("u_dual")
    assert row("u_ba").endswith("✓CDC[GRAY clk_b→clk_a]")


@_needs_verible
def test_view_json_edge_overlay_carries_the_verdict() -> None:
    _, root = _design()
    dm = load_domain_map(FIXTURE_DIR / "domain_map_v1_2.json")
    buf = io.StringIO()
    json_render.render(root, buf, domain_map=dm)
    edges = {
        e["to"]: e["overlays"].get("clock") for e in json.loads(buf.getvalue())["edges"]
    }
    assert edges["cdc_top.u_sync"] == {
        "crossing": True,
        "hazard": False,
        "pairs": [
            {
                "src_clock": "clk_a",
                "dst_clock": "clk_b",
                "flops": 1,
                "hazard": False,
                "synchronizers": ["2ff"],
            }
        ],
    }
    for inst in ("u_flagged", "u_raw", "u_unchecked"):
        block = edges[f"cdc_top.{inst}"]
        assert block["hazard"] is True
        assert block["pairs"][0]["hazard"] is True
        assert block["pairs"][0]["synchronizers"] == []
    mix = edges["cdc_top.u_mix"]
    assert mix["hazard"] is True
    assert [
        (p["src_clock"], p["hazard"], p["synchronizers"]) for p in mix["pairs"]
    ] == [
        ("clk_a", True, []),
        ("clk_c", False, ["2ff"]),
    ]
    dual = edges["cdc_top.u_dual"]
    assert dual["hazard"] is False
    assert dual["pairs"][0]["synchronizers"] == ["2ff", "handshake"]


# --- synthetic: unknown scheme reaches the figure ----------------------------


def test_unknown_scheme_renders_upper_cased() -> None:
    dst = HierNode(
        instance_path="top.u_x",
        module_name="ff",
        instance=None,
        module=None,
        is_blackbox=False,
        children=(),
    )
    root = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=None,
        is_blackbox=False,
        children=(dst,),
    )
    dm = DomainMap(
        schema_version="1.2",
        generator_name="t",
        generator_version="0",
        design_top="top",
        design_frontend="slang",
        clocks=(Clock(name="clk_a", period=None, source="create_clock", ports=()),),
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_x",
                min_hops=0,
                width=1,
                async_per_sdc=True,
                synchronizer="fifo_ptr",
                findings=(),
            ),
        ),
    )
    buf = io.StringIO()
    mermaid_render.render(root, buf, domain_map=dm)
    assert "FIFO_PTR clk_a→clk_b" in buf.getvalue()
    assert "⚠" not in buf.getvalue()
