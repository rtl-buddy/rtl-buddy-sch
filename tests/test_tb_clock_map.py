"""Tests for the TB-context clock/reset map loader + merge.

Phase 6e (rtl-buddy-view #99). The loader's failure modes are
mirror-images of :func:`annotations.load_domain_map` — same
:class:`AnnotationsError` discipline. The merge function's contract
is the rule pinned by the issue: DUT-side wins inside every DUT
instance, TB-side fills outside, boundary aliases warn but don't
error.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from rtl_buddy_view.annotations import (
    AnnotationsError,
    Clock,
    DomainMap,
    FlopDomain,
)
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.reset_annotations import (
    FlopReset,
    ResetDomainMap,
)
from rtl_buddy_view.tb_clock_map import (
    TbClock,
    TbClockMap,
    TbReset,
    load_tb_clock_map,
    merge_into_domain_map,
    merge_into_reset_map,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tb_over_dut" / "tb_clock_map.json"


# --- loader ---------------------------------------------------------------


def test_loader_round_trips_minimal_payload(tmp_path: Path) -> None:
    """Smoke: every documented field round-trips through the
    loader."""
    m = load_tb_clock_map(FIXTURE_PATH)
    assert m.schema_version == "1.0"
    assert [c.name for c in m.clocks] == ["main_clk"]
    main = m.clocks[0]
    assert main.drives == ("tb_top.u_clkgen", "tb_top.u_driver")
    assert main.period_ns == 10.0
    assert [r.name for r in m.resets] == ["por_rst_n"]
    assert m.resets[0].active_low is True


def test_loader_rejects_wrong_filetype(tmp_path: Path) -> None:
    """A domain_map.json fed in by mistake produces a clear
    diagnostic — the filetype header is the discriminator."""
    p = tmp_path / "wrong.json"
    p.write_text(
        json.dumps({"rtl-buddy-filetype": "domain_map", "schema_version": "1.0"})
    )
    with pytest.raises(AnnotationsError, match="rtl-buddy-filetype"):
        load_tb_clock_map(p)


def test_loader_rejects_unsupported_schema_major(tmp_path: Path) -> None:
    p = tmp_path / "future.json"
    p.write_text(
        json.dumps(
            {
                "rtl-buddy-filetype": "tb_clock_map",
                "schema_version": "2.0",
                "clocks": [],
            }
        )
    )
    with pytest.raises(AnnotationsError, match="schema major 2"):
        load_tb_clock_map(p)


def test_loader_rejects_empty_drives(tmp_path: Path) -> None:
    """A clock must drive at least one path — empty drives is a
    misconfiguration, not a no-op."""
    p = tmp_path / "empty.json"
    p.write_text(
        json.dumps(
            {
                "rtl-buddy-filetype": "tb_clock_map",
                "schema_version": "1.0",
                "clocks": [{"name": "clk_a", "drives": []}],
            }
        )
    )
    with pytest.raises(AnnotationsError, match="non-empty list"):
        load_tb_clock_map(p)


def test_loader_rejects_non_string_drive(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(
        json.dumps(
            {
                "rtl-buddy-filetype": "tb_clock_map",
                "schema_version": "1.0",
                "clocks": [{"name": "clk_a", "drives": ["tb.u_a", 42]}],
            }
        )
    )
    with pytest.raises(AnnotationsError, match="must be a non-empty string"):
        load_tb_clock_map(p)


def test_loader_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    with pytest.raises(AnnotationsError, match="invalid JSON"):
        load_tb_clock_map(p)


def test_loader_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AnnotationsError, match="could not read"):
        load_tb_clock_map(tmp_path / "does-not-exist.json")


# --- merge: clock side ---------------------------------------------------


def _hier(
    instance_path: str, module: str, children: tuple[HierNode, ...] = ()
) -> HierNode:
    return HierNode(
        instance_path=instance_path,
        module_name=module,
        instance=None,
        module=None,
        is_blackbox=False,
        children=children,
    )


def _tb_root() -> HierNode:
    """Reproduces the tb_over_dut fixture's instance tree:

    ::

        tb_top
        ├── u_clkgen : clkgen
        ├── u_driver : driver
        └── u_dut    : dut
            ├── u_a  : leaf
            └── u_b  : leaf
    """
    leaf_a = _hier("tb_top.u_dut.u_a", "leaf")
    leaf_b = _hier("tb_top.u_dut.u_b", "leaf")
    dut = _hier("tb_top.u_dut", "dut", children=(leaf_a, leaf_b))
    clkgen = _hier("tb_top.u_clkgen", "clkgen")
    driver = _hier("tb_top.u_driver", "driver")
    return _hier("tb_top", "tb_top", children=(clkgen, driver, dut))


def test_merge_adds_tb_drives_outside_dut_subtree() -> None:
    """TB-side drives for paths outside the DUT subtree become
    synthetic FlopDomain entries; the renderer's predominant_clock
    walk then tints those nodes."""
    tb = TbClockMap(
        schema_version="1.0",
        clocks=(
            TbClock(name="main_clk", drives=("tb_top.u_clkgen", "tb_top.u_driver")),
        ),
    )
    merged = merge_into_domain_map(None, tb, root=_tb_root(), dut_top_module="dut")
    paths = {f.instance_path for f in merged.flop_domains}
    assert paths == {"tb_top.u_clkgen", "tb_top.u_driver"}
    assert all(f.clock == "main_clk" for f in merged.flop_domains)
    # The synthesised Clock entry is present so the SPA legend lists it.
    assert any(c.name == "main_clk" for c in merged.clocks)


def test_merge_skips_drives_inside_dut_with_warning() -> None:
    """A TB-side drive aliased to a path inside the DUT subtree is
    skipped — DUT-side wins by contract — and a warning is emitted.
    """
    tb = TbClockMap(
        schema_version="1.0",
        clocks=(
            TbClock(
                name="main_clk",
                drives=("tb_top.u_dut", "tb_top.u_dut.u_a", "tb_top.u_clkgen"),
            ),
        ),
    )
    warn = io.StringIO()
    merged = merge_into_domain_map(
        None,
        tb,
        root=_tb_root(),
        dut_top_module="dut",
        warn_stream=warn,
    )
    paths = {f.instance_path for f in merged.flop_domains}
    # tb_top.u_dut and tb_top.u_dut.u_a are inside the DUT → dropped.
    # tb_top.u_clkgen sits outside → kept.
    assert paths == {"tb_top.u_clkgen"}
    log = warn.getvalue()
    assert "tb_top.u_dut" in log
    assert "DUT-side domain map wins" in log


def test_merge_preserves_dut_side_entries() -> None:
    """When a DUT-side DomainMap is supplied, its existing flops +
    clocks + crossings survive the merge intact."""
    dut = DomainMap(
        schema_version="1.0",
        generator_name="rtl-buddy-cdc",
        generator_version="0.1",
        design_top="dut",
        design_frontend="slang",
        clocks=(Clock(name="dut_clk", period=2.0, source="create_clock", ports=()),),
        flop_domains=(
            FlopDomain(
                instance_path="tb_top.u_dut.u_a", clock="dut_clk", location=None
            ),
        ),
    )
    tb = TbClockMap(
        schema_version="1.0",
        clocks=(TbClock(name="main_clk", drives=("tb_top.u_clkgen",)),),
    )
    merged = merge_into_domain_map(dut, tb, root=_tb_root(), dut_top_module="dut")
    clock_names = {c.name for c in merged.clocks}
    assert clock_names == {"dut_clk", "main_clk"}
    # Both DUT- and TB-side flops present.
    by_path = {f.instance_path: f.clock for f in merged.flop_domains}
    assert by_path == {
        "tb_top.u_dut.u_a": "dut_clk",
        "tb_top.u_clkgen": "main_clk",
    }


def test_merge_without_dut_top_module_treats_everything_as_outside() -> None:
    """When --tb-top is set without --top, no DUT subtree exists;
    every TB-side drive is kept."""
    tb = TbClockMap(
        schema_version="1.0",
        clocks=(
            TbClock(name="main_clk", drives=("tb_top.u_dut.u_a", "tb_top.u_clkgen")),
        ),
    )
    merged = merge_into_domain_map(None, tb, root=_tb_root(), dut_top_module=None)
    assert {f.instance_path for f in merged.flop_domains} == {
        "tb_top.u_dut.u_a",
        "tb_top.u_clkgen",
    }


# --- merge: reset side ---------------------------------------------------


def test_reset_merge_adds_drives_outside_dut() -> None:
    tb = TbClockMap(
        schema_version="1.0",
        resets=(
            TbReset(name="por_rst_n", drives=("tb_top.u_driver",), active_low=True),
        ),
    )
    merged = merge_into_reset_map(None, tb, root=_tb_root(), dut_top_module="dut")
    assert len(merged.flop_resets) == 1
    fr = merged.flop_resets[0]
    assert fr.instance_path == "tb_top.u_driver"
    assert fr.reset == "por_rst_n"
    assert fr.polarity == "low"  # active_low=True ⇒ low polarity
    assert fr.type == "async"


def test_reset_merge_skips_drives_inside_dut_with_warning() -> None:
    tb = TbClockMap(
        schema_version="1.0",
        resets=(
            TbReset(
                name="por_rst_n",
                drives=("tb_top.u_dut.u_a", "tb_top.u_driver"),
            ),
        ),
    )
    warn = io.StringIO()
    merged = merge_into_reset_map(
        None,
        tb,
        root=_tb_root(),
        dut_top_module="dut",
        warn_stream=warn,
    )
    paths = {f.instance_path for f in merged.flop_resets}
    assert paths == {"tb_top.u_driver"}
    assert "DUT-side reset map wins" in warn.getvalue()


def test_reset_merge_preserves_dut_side_resets() -> None:
    dut = ResetDomainMap(
        schema_version="1.0",
        generator_name="rtl-buddy-cdc",
        generator_version="0.1",
        design_top="dut",
        design_frontend="slang",
        flop_resets=(
            FlopReset(
                instance_path="tb_top.u_dut.u_a",
                clock="dut_clk",
                reset="rst_n",
                reset_kind="port",
                polarity="low",
                type="async",
                location=None,
            ),
        ),
    )
    tb = TbClockMap(
        schema_version="1.0",
        resets=(TbReset(name="por_rst_n", drives=("tb_top.u_driver",)),),
    )
    merged = merge_into_reset_map(dut, tb, root=_tb_root(), dut_top_module="dut")
    by_path = {f.instance_path: f.reset for f in merged.flop_resets}
    assert by_path == {
        "tb_top.u_dut.u_a": "rst_n",  # DUT-side preserved
        "tb_top.u_driver": "por_rst_n",  # TB-side added
    }
