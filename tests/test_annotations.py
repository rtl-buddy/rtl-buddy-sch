"""Tests for the clock-domain map loader.

Exercises both the happy path (a populated v1.0 payload) and the
guard rails the schema-version check is meant to provide: missing
keys, wrong types, unsupported major version, malformed JSON, and
the no-SDC empty-clocks shape that producers emit when given no
SDC file.

The producer is rtl-buddy-cdc; the schema is documented in
[rtl-buddy/rtl-buddy-cdc#106](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/106).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rtl_buddy_view import annotations as ann

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "domain_maps"


# --- happy paths -------------------------------------------------------------


def test_loads_populated_v1_payload() -> None:
    m = ann.load_domain_map(FIXTURE_DIR / "two_domain_with_crossing.json")
    assert m.schema_version == "1.0"
    assert m.design_top == "top"
    assert m.generator_name == "rtl-buddy-cdc"
    assert [c.name for c in m.clocks] == ["clk_a", "clk_b"]
    assert m.clocks[0].period == 10.0
    assert m.generated_clocks[0].master == "clk_a"
    assert m.clock_groups[0].kind == "asynchronous"
    assert m.clock_groups[0].members == (("clk_a",), ("clk_b",))
    assert len(m.flop_domains) == 3
    untraceable = next(f for f in m.flop_domains if f.clock is None)
    assert untraceable.instance_path == "top.u_orphan"
    assert m.flop_domains[0].location is not None
    assert m.flop_domains[0].location.start_line == 42
    assert len(m.crossings) == 1
    assert m.crossings[0].async_per_sdc is True
    assert m.crossings[0].src_flop == "top.u_fifo.u_wr_ptr"
    assert m.is_empty is False


def test_loads_no_sdc_payload() -> None:
    m = ann.load_domain_map(FIXTURE_DIR / "empty_no_sdc.json")
    assert m.schema_version == "1.0"
    assert m.clocks == ()
    assert m.flop_domains == ()
    assert m.is_empty is True  # consumers gate on this


# --- schema-version policy ---------------------------------------------------


def test_rejects_unsupported_major(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_sdc.json").read_text())
    payload["schema_version"] = "2.0"
    p = tmp_path / "future.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ann.AnnotationsError, match="not supported"):
        ann.load_domain_map(p)


def test_accepts_minor_version_bump(tmp_path: Path) -> None:
    """1.x is forward-compatible — minor bumps must keep loading."""
    payload = json.loads((FIXTURE_DIR / "empty_no_sdc.json").read_text())
    payload["schema_version"] = "1.5"
    p = tmp_path / "future.json"
    p.write_text(json.dumps(payload))
    m = ann.load_domain_map(p)
    assert m.schema_version == "1.5"


def test_rejects_missing_schema_version(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_sdc.json").read_text())
    del payload["schema_version"]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ann.AnnotationsError, match="schema_version"):
        ann.load_domain_map(p)


def test_rejects_nonstring_schema_version(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_sdc.json").read_text())
    payload["schema_version"] = 1.0  # the number, not "1.0"
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ann.AnnotationsError, match="schema_version"):
        ann.load_domain_map(p)


def test_rejects_nonnumeric_major(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_sdc.json").read_text())
    payload["schema_version"] = "alpha.0"
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ann.AnnotationsError, match="numeric major"):
        ann.load_domain_map(p)


# --- structural validation ---------------------------------------------------


def test_rejects_missing_design_block(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_sdc.json").read_text())
    del payload["design"]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ann.AnnotationsError, match="design"):
        ann.load_domain_map(p)


def test_rejects_missing_generator_block(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_sdc.json").read_text())
    del payload["generator"]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ann.AnnotationsError, match="generator"):
        ann.load_domain_map(p)


def test_rejects_flop_domains_with_wrong_clock_type(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_sdc.json").read_text())
    payload["flop_domains"] = [{"instance_path": "top.x", "clock": 42}]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ann.AnnotationsError, match="clock"):
        ann.load_domain_map(p)


def test_rejects_top_level_non_object(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("[]")
    with pytest.raises(ann.AnnotationsError, match="object"):
        ann.load_domain_map(p)


# --- I/O errors --------------------------------------------------------------


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ann.AnnotationsError, match="could not read"):
        ann.load_domain_map(tmp_path / "does_not_exist.json")


def test_rejects_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "garbage.json"
    p.write_text("not valid json {")
    with pytest.raises(ann.AnnotationsError, match="invalid JSON"):
        ann.load_domain_map(p)
