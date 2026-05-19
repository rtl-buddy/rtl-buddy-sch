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


# --- rtl-buddy-cdc#136: source_instance_path fields -------------------------


def _v136_payload() -> dict:
    """Shape of a real ``--emit-domain-map`` payload from cdc≥#136.

    Mirrors the credit_cdc style: synth-internal ``instance_path`` /
    ``dst_flop`` strings sit alongside the new ``source_instance_path``
    fields that point at the deepest enclosing source-level instance.
    """
    return {
        "schema_version": "1.0",
        "generator": {"name": "rtl-buddy-cdc", "version": "0.2.0"},
        "design": {"top": "top", "frontend": "slang"},
        "clocks": [
            {
                "name": "src_clk",
                "period": 5.0,
                "source": "create_clock",
                "ports": ["src_clk"],
            },
            {
                "name": "dst_clk",
                "period": 5.0,
                "source": "create_clock",
                "ports": ["dst_clk"],
            },
        ],
        "clock_groups": [
            {"kind": "asynchronous", "members": [["src_clk"], ["dst_clk"]]}
        ],
        "flop_domains": [
            {
                "instance_path": "top.u_sync.$slang$sdff$3",
                "source_instance_path": "top.u_sync",
                "clock": "dst_clk",
                "location": {
                    "file": "ip_cdc_sync.sv",
                    "start_line": 18,
                    "end_line": 27,
                },
            }
        ],
        "crossings": [
            {
                "src_clock": "src_clk",
                "dst_clock": "dst_clk",
                "dst_flop": "top.u_sync.$slang$sdff$3",
                "dst_source_instance_path": "top.u_sync",
                "src_flop": "top.$slang$sdff$2",
                "src_source_instance_path": "top",
                "min_hops": 0,
                "width": 1,
                "async_per_sdc": True,
            }
        ],
    }


def test_parses_source_instance_path_on_flop_domain(tmp_path: Path) -> None:
    p = tmp_path / "v136.json"
    p.write_text(json.dumps(_v136_payload()))
    m = ann.load_domain_map(p)
    assert m.flop_domains[0].source_instance_path == "top.u_sync"
    # Original (netlist) ``instance_path`` is preserved verbatim.
    assert m.flop_domains[0].instance_path == "top.u_sync.$slang$sdff$3"


def test_parses_source_instance_paths_on_crossing(tmp_path: Path) -> None:
    p = tmp_path / "v136.json"
    p.write_text(json.dumps(_v136_payload()))
    m = ann.load_domain_map(p)
    c = m.crossings[0]
    assert c.dst_source_instance_path == "top.u_sync"
    assert c.src_source_instance_path == "top"
    # Original synth-flop names still readable for debugging.
    assert c.dst_flop == "top.u_sync.$slang$sdff$3"
    assert c.src_flop == "top.$slang$sdff$2"


def test_crossings_into_uses_source_instance_path_when_set(
    tmp_path: Path,
) -> None:
    """Renderers ask ``crossings_into('top.u_sync')`` — that must match
    a crossing whose ``dst_flop`` is a synth-internal name as long as
    its ``dst_source_instance_path`` resolves the right way."""
    p = tmp_path / "v136.json"
    p.write_text(json.dumps(_v136_payload()))
    m = ann.load_domain_map(p)
    hits = m.crossings_into("top.u_sync")
    assert len(hits) == 1
    assert hits[0].src_clock == "src_clk"


def test_crossings_into_falls_back_to_dst_flop_for_old_producers(
    tmp_path: Path,
) -> None:
    """Maps without the new field still resolve via exact ``dst_flop`` match."""
    payload = _v136_payload()
    del payload["crossings"][0]["dst_source_instance_path"]
    del payload["crossings"][0]["src_source_instance_path"]
    p = tmp_path / "v136_no_sip.json"
    p.write_text(json.dumps(payload))
    m = ann.load_domain_map(p)
    # Exact-match lookup against the synth name still works — that's
    # the legacy behavior, preserved.
    assert len(m.crossings_into("top.u_sync.$slang$sdff$3")) == 1
    # But asking for the source-instance path returns nothing —
    # exactly the gap that motivated cdc#136.
    assert m.crossings_into("top.u_sync") == ()


def test_predominant_clock_uses_source_instance_path(tmp_path: Path) -> None:
    """``predominant_clock('top.u_sync')`` must aggregate flops whose
    ``source_instance_path`` is under ``top.u_sync``, even when their
    ``instance_path`` is a synth-internal name that doesn't share the
    source-path prefix."""
    p = tmp_path / "v136.json"
    p.write_text(json.dumps(_v136_payload()))
    m = ann.load_domain_map(p)
    assert m.predominant_clock("top.u_sync") == "dst_clk"


def test_rejects_non_string_source_instance_path(tmp_path: Path) -> None:
    payload = _v136_payload()
    payload["flop_domains"][0]["source_instance_path"] = 42
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ann.AnnotationsError, match="source_instance_path"):
        ann.load_domain_map(p)
