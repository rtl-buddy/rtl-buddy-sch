"""Clock → colour assignment (rtl-buddy-sch#196, issue comment).

``_palette_color`` used to hash every clock name into seven swatches,
so distinct domains of one figure collided. These tests pin the new
rule — chosen colours first, then a distinct swatch per clock in
sorted order, the hash only as an overflow fallback — and that every
place one dot figure colours a clock (fills, cluster outlines, the
legend, mermaid styles) reads the same assignment. Verible-free.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from rtl_buddy_view.annotations import Clock, DomainMap, FlopDomain
from rtl_buddy_view.clock_colors import (
    CLOCK_PALETTE,
    SWATCH_NAMES,
    ClockColorMap,
    ClockColorsError,
    assign_clock_colors,
    chosen_clock_colors,
    domain_map_clocks,
    hash_color,
    load_clock_colors,
    parse_clock_colors,
    parse_color,
)
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.hints import HintMap, NetHints
from rtl_buddy_view.overlays.clock_colors import ClockColorsOverlay
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.render import mermaid as mermaid_render

#: The five clocks of the issue comment's design. Under the old hash
#: ``s_clk`` / ``f_clk`` shared orange and three shared purple.
COMMENT_CLOCKS = ("s_clk", "f_clk", "t_a_clk", "t_a1_clk", "clk")


def _map(*clocks: str, flops: dict[str, str] | None = None) -> DomainMap:
    return DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=tuple(
            Clock(name=c, period=None, source="create_clock", ports=(c,))
            for c in clocks
        ),
        flop_domains=tuple(
            FlopDomain(instance_path=path, clock=clk, location=None)
            for path, clk in (flops or {}).items()
        ),
    )


def _leaf(path: str) -> HierNode:
    return HierNode(
        instance_path=path,
        module_name=path.rsplit(".", 1)[-1],
        instance=None,
        module=None,
        is_blackbox=False,
    )


def _top(*children: str) -> HierNode:
    return HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=None,
        is_blackbox=False,
        children=tuple(_leaf(f"top.{c}") for c in children),
    )


# --- the bug -----------------------------------------------------------------


def test_the_old_hash_collides_on_the_comment_design() -> None:
    """The reported collision, so the fix is measured against it."""
    assert hash_color("s_clk") == hash_color("f_clk") == "#fed7aa"
    assert hash_color("t_a_clk") == hash_color("t_a1_clk") == hash_color("clk")


def test_one_figure_never_repeats_a_swatch_while_swatches_last() -> None:
    colors = assign_clock_colors(COMMENT_CLOCKS)
    assert len(set(colors.values())) == len(COMMENT_CLOCKS)


def test_swatches_are_dealt_in_sorted_name_order() -> None:
    """The same rule as viewer/src/palette.js::buildClockPalette."""
    colors = assign_clock_colors(COMMENT_CLOCKS)
    for index, name in enumerate(sorted(COMMENT_CLOCKS)):
        assert colors[name] == CLOCK_PALETTE[index]


def test_more_clocks_than_swatches_falls_back_to_the_hash() -> None:
    names = [f"c{i}" for i in range(len(CLOCK_PALETTE) + 1)]
    colors = assign_clock_colors(names)
    assert colors == {n: hash_color(n) for n in names}


def test_a_chosen_colour_wins_and_stays_reserved() -> None:
    colors = assign_clock_colors(("a_clk", "b_clk"), {"b_clk": CLOCK_PALETTE[0]})
    assert colors["b_clk"] == CLOCK_PALETTE[0]
    # a_clk would have been dealt blue; blue is taken, so it gets green.
    assert colors["a_clk"] == CLOCK_PALETTE[1]


def test_a_chosen_colour_for_an_absent_clock_is_still_reserved() -> None:
    """One colour means one clock across a document set's figures."""
    colors = assign_clock_colors(("a_clk",), {"s_clk": CLOCK_PALETTE[0]})
    assert colors["a_clk"] == CLOCK_PALETTE[1]


def test_domain_map_clocks_unions_every_place_a_clock_appears() -> None:
    dm = _map("a_clk", flops={"top.u_x": "gen_clk"})
    assert domain_map_clocks(dm) == ("a_clk", "gen_clk")


# --- colour spellings ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("blue", CLOCK_PALETTE[0]),
        ("Orange", CLOCK_PALETTE[5]),
        ("#ABCDEF", "#abcdef"),
        (" #dbeafe ", "#dbeafe"),
        ("lightblue", None),
        ("#abc", None),
        ("", None),
    ],
)
def test_parse_color(text: str, expected: str | None) -> None:
    assert parse_color(text) == expected


def test_every_swatch_has_a_name() -> None:
    assert tuple(SWATCH_NAMES.values()) == CLOCK_PALETTE


# --- the clock-colors overlay document ------------------------------------------


def test_parse_clock_colors_normalises() -> None:
    cmap = parse_clock_colors(
        {"schema_version": "1.0", "clocks": {"s_clk": "blue", "f_clk": "#FDE68A"}},
        source="m.json",
    )
    assert cmap.colors == {"f_clk": "#fde68a", "s_clk": CLOCK_PALETTE[0]}


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ([], "top-level must be a JSON object"),
        ({}, "schema_version missing"),
        ({"schema_version": "2.0"}, "not supported"),
        ({"schema_version": "x"}, "not supported"),
        ({"schema_version": "1.0", "clocks": []}, "'clocks' must be a JSON object"),
        ({"schema_version": "1.0", "clocks": {"a": 3}}, "must be a string"),
        ({"schema_version": "1.0", "clocks": {"a": "teal"}}, "not a colour"),
    ],
)
def test_parse_clock_colors_rejects(payload: object, match: str) -> None:
    with pytest.raises(ClockColorsError, match=match):
        parse_clock_colors(payload, source="m.json")


def test_load_clock_colors_reads_and_reports(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"schema_version": "1.0", "clocks": {"a": "pink"}}))
    assert load_clock_colors(good).colors == {"a": CLOCK_PALETTE[3]}
    assert ClockColorsOverlay().load(good).colors == {"a": CLOCK_PALETTE[3]}
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    with pytest.raises(ClockColorsError, match="invalid JSON"):
        load_clock_colors(bad)
    with pytest.raises(ClockColorsError, match="could not read"):
        load_clock_colors(tmp_path / "missing.json")


def test_overlay_plugin_is_inert_at_join_and_contribute() -> None:
    overlay = ClockColorsOverlay()
    assert overlay.name == "clock-colors"
    assert overlay.join(_top(), ClockColorMap()) is None
    assert overlay.contribute(None) is None


# --- pragma colours ------------------------------------------------------------


def test_a_port_colour_hint_colours_its_clock_and_the_overlay_wins() -> None:
    dm = DomainMap(
        schema_version="1.0",
        generator_name="t",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(
            Clock(name="sys", period=None, source="create_clock", ports=("clk_in",)),
            Clock(name="aux_clk", period=None, source="create_clock", ports=()),
        ),
    )
    hints = HintMap(
        nets={
            ("top", "clk_in"): NetHints(classification="clock", color="#111111"),
            ("top", "aux_clk"): NetHints(color="#222222"),
            ("top", "not_a_clock"): NetHints(color="#333333"),
            ("other", "aux_clk"): NetHints(color="#444444"),
        }
    )
    chosen = chosen_clock_colors(dm, hints=hints, modules=("top", "other"))
    # ``sys`` through its port list, ``aux_clk`` by name, and the
    # first module named beats a later one.
    assert chosen == {"sys": "#111111", "aux_clk": "#222222"}
    overlay = ClockColorMap(colors={"sys": "#555555"})
    assert chosen_clock_colors(dm, hints=hints, modules=("top",), overlay=overlay)[
        "sys"
    ] == ("#555555")
    assert chosen_clock_colors(None, hints=hints, modules=("top",)) == {}


# --- every renderer reads one assignment ------------------------------------------


def test_dot_fills_outlines_and_legend_agree() -> None:
    dm = _map(*COMMENT_CLOCKS, flops={"top.u_s": "s_clk", "top.u_f": "f_clk"})
    out = io.StringIO()
    dot_render.render(_top("u_s", "u_f"), out, domain_map=dm, with_legend=True)
    text = out.getvalue()
    colors = assign_clock_colors(COMMENT_CLOCKS)
    for clock in COMMENT_CLOCKS:
        assert f'"_legend_{clock}" [label="{clock}", shape=box, ' in text
        assert re.search(rf'"_legend_{clock}" .*fillcolor="{colors[clock]}"', text), (
            clock
        )
    assert re.search(rf'"top\.u_s" .*fillcolor="{colors["s_clk"]}"', text)
    assert re.search(rf'"top\.u_f" .*fillcolor="{colors["f_clk"]}"', text)
    assert colors["s_clk"] != colors["f_clk"]


def test_chosen_colours_reach_dot_and_mermaid_alike() -> None:
    dm = _map("s_clk", "f_clk", flops={"top.u_s": "s_clk", "top.u_f": "f_clk"})
    chosen = {"s_clk": "#123456"}
    dot_out = io.StringIO()
    dot_render.render(_top("u_s", "u_f"), dot_out, domain_map=dm, clock_colors=chosen)
    mmd_out = io.StringIO()
    mermaid_render.render(
        _top("u_s", "u_f"), mmd_out, domain_map=dm, clock_colors=chosen
    )
    assert 'fillcolor="#123456"' in dot_out.getvalue()
    assert "style top_u_s fill:#123456" in mmd_out.getvalue()
    # f_clk: blue is the first free swatch.
    assert f'fillcolor="{CLOCK_PALETTE[0]}"' in dot_out.getvalue()
    assert f"style top_u_f fill:{CLOCK_PALETTE[0]}" in mmd_out.getvalue()


def test_the_assignment_does_not_leak_out_of_a_render() -> None:
    dm = _map("zz_clk", flops={"top.u_z": "zz_clk"})
    dot_render.render(_top("u_z"), io.StringIO(), domain_map=dm)
    assert dot_render._palette_color("zz_clk") == hash_color("zz_clk")


def test_the_viewer_tokens_carry_the_same_swatches_in_order() -> None:
    """The SPA (palette.js) deals ``--clk-1``…``--clk-7`` by sorted index,
    exactly as :func:`assign_clock_colors` deals ``CLOCK_PALETTE``; the
    light-theme token values must be the same swatches, or a figure and
    the hub canvas would disagree on a clock's colour."""
    tokens = (Path(__file__).parents[1] / "viewer" / "src" / "tokens.css").read_text()
    light = dict(re.findall(r"(--clk-\d):\s*(#[0-9a-fA-F]{6})", tokens)[:7])
    assert tuple(light[f"--clk-{i}"].lower() for i in range(1, 8)) == CLOCK_PALETTE
