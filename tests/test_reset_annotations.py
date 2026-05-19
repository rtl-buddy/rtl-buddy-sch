"""Tests for the reset-domain map loader (Phase 3 — #3).

Exercises both the happy path (a populated v1.0 payload) and the
guard rails the schema-version check is meant to provide: missing
keys, wrong types, unsupported major version, malformed JSON, and
the no-reset empty shape that producers emit when given a design
with no reset-bearing flops.

The producer is rtl-buddy-cdc; the schema is documented in
[rtl-buddy/rtl-buddy-cdc#108](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/108)
and at ``wiki/raw/articles/rtl-buddy-cdc-reset-domain-map-schema.md``
in that repo.

The ``bad_marked_reset_polarity.json`` fixture is a byte-for-byte
copy of the producer's CI golden fixture so this loader is exercised
against a payload the producer also tests against.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rtl_buddy_view import reset_annotations as rann

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "reset_domain_maps"


# --- happy paths -------------------------------------------------------------


def test_loads_populated_v1_payload() -> None:
    m = rann.load_reset_domain_map(FIXTURE_DIR / "two_clock_two_reset_with_rdc.json")
    assert m.schema_version == "1.0"
    assert m.design_top == "top"
    assert m.generator_name == "rtl-buddy-cdc"
    assert [s.name for s in m.reset_sources] == ["rst_n", "top.u_rstgen.rst_sync"]
    assert m.reset_sources[0].polarity == "low"
    assert m.reset_sources[1].via_synchronizer is True
    assert m.reset_synchronizers[0].instance_path == "top.u_rstgen.u_sync"
    assert m.reset_synchronizers[0].dest_clock == "clk_b"
    assert m.reset_synchronizers[0].async_in == "rst_n"
    assert m.reset_synchronizers[0].async_in_kind == "port"
    assert len(m.flop_resets) == 2
    assert {f.clock for f in m.flop_resets} == {"clk_a", "clk_b"}
    assert m.flop_resets[0].location is not None
    assert m.flop_resets[0].location.start_line == 42
    assert len(m.reset_crossings) == 1
    assert m.reset_crossings[0].kind == "async-deassert"
    assert m.reset_crossings[0].instance_path == "top.u_fifo.u_rd_ptr"
    assert m.is_empty is False


def test_loads_producer_golden_fixture() -> None:
    """``bad_marked_reset_polarity.json`` is the producer's own CI golden.

    Loading it cleanly here is the cross-repo handshake — if the
    producer ever drifts the schema, this test fails fast with the
    drift's exact field/type in the message.
    """
    m = rann.load_reset_domain_map(FIXTURE_DIR / "bad_marked_reset_polarity.json")
    assert m.schema_version == "1.0"
    assert m.design_top == "bad_marked_reset_polarity"
    # The declared_polarity / inferred polarity disagreement is the
    # whole point of this fixture — make sure both made it through.
    src = m.reset_sources[0]
    assert src.declared_polarity == "low"
    assert src.polarity == "low"
    flop = m.flop_resets[0]
    assert flop.polarity == "high"
    # ...and the producer flagged it as a polarity-mismatch crossing.
    assert m.reset_crossings[0].kind == "polarity-mismatch"
    assert m.reset_crossings[0].polarity == "high"


def test_loads_no_reset_payload() -> None:
    m = rann.load_reset_domain_map(FIXTURE_DIR / "empty_no_resets.json")
    assert m.schema_version == "1.0"
    assert m.reset_sources == ()
    assert m.flop_resets == ()
    assert m.reset_crossings == ()
    assert m.is_empty is True  # consumers gate on this


# --- query helpers -----------------------------------------------------------


def test_crossings_into_exact_match() -> None:
    m = rann.load_reset_domain_map(FIXTURE_DIR / "two_clock_two_reset_with_rdc.json")
    hits = m.crossings_into("top.u_fifo.u_rd_ptr")
    assert len(hits) == 1
    assert hits[0].kind == "async-deassert"
    # Misses on a non-crossing flop don't surface the path.
    assert m.crossings_into("top.u_core.u_reg") == ()


def test_synchronizer_paths_set() -> None:
    m = rann.load_reset_domain_map(FIXTURE_DIR / "two_clock_two_reset_with_rdc.json")
    assert m.synchronizer_paths() == frozenset({"top.u_rstgen.u_sync"})


def test_synchronizer_paths_empty_when_none() -> None:
    m = rann.load_reset_domain_map(FIXTURE_DIR / "bad_marked_reset_polarity.json")
    assert m.synchronizer_paths() == frozenset()


def test_flop_reset_lookup() -> None:
    m = rann.load_reset_domain_map(FIXTURE_DIR / "two_clock_two_reset_with_rdc.json")
    f = m.flop_reset("top.u_core.u_reg")
    assert f is not None
    assert f.clock == "clk_a"
    assert f.reset == "rst_n"
    assert m.flop_reset("top.does.not.exist") is None


# --- schema-version policy ---------------------------------------------------


def test_rejects_unsupported_major(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_resets.json").read_text())
    payload["schema_version"] = "2.0"
    p = tmp_path / "future.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(rann.ResetAnnotationsError, match="not supported"):
        rann.load_reset_domain_map(p)


def test_accepts_minor_version_bump(tmp_path: Path) -> None:
    """1.x is forward-compatible — minor bumps must keep loading."""
    payload = json.loads((FIXTURE_DIR / "empty_no_resets.json").read_text())
    payload["schema_version"] = "1.5"
    p = tmp_path / "future.json"
    p.write_text(json.dumps(payload))
    m = rann.load_reset_domain_map(p)
    assert m.schema_version == "1.5"


def test_rejects_missing_schema_version(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_resets.json").read_text())
    del payload["schema_version"]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(rann.ResetAnnotationsError, match="schema_version"):
        rann.load_reset_domain_map(p)


def test_rejects_nonstring_schema_version(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_resets.json").read_text())
    payload["schema_version"] = 1.0  # the number, not "1.0"
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(rann.ResetAnnotationsError, match="schema_version"):
        rann.load_reset_domain_map(p)


def test_rejects_nonnumeric_major(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_resets.json").read_text())
    payload["schema_version"] = "alpha.0"
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(rann.ResetAnnotationsError, match="numeric major"):
        rann.load_reset_domain_map(p)


# --- structural validation ---------------------------------------------------


def test_rejects_missing_design_block(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_resets.json").read_text())
    del payload["design"]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(rann.ResetAnnotationsError, match="design"):
        rann.load_reset_domain_map(p)


def test_rejects_missing_generator_block(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_resets.json").read_text())
    del payload["generator"]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(rann.ResetAnnotationsError, match="generator"):
        rann.load_reset_domain_map(p)


def test_rejects_flop_reset_with_wrong_clock_type(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_resets.json").read_text())
    payload["flop_resets"] = [
        {
            "instance_path": "top.x",
            "clock": 42,  # wrong type
            "reset": "rst_n",
            "reset_kind": "port",
            "polarity": "low",
            "type": "async",
        }
    ]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(rann.ResetAnnotationsError, match="clock"):
        rann.load_reset_domain_map(p)


def test_rejects_reset_source_with_wrong_clock_type(tmp_path: Path) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_resets.json").read_text())
    payload["reset_sources"] = [
        {
            "name": "rst_n",
            "source": "port",
            "polarity": "low",
            "type": "async",
            "clock": 42,  # wrong type
            "via_synchronizer": False,
        }
    ]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(rann.ResetAnnotationsError, match="clock"):
        rann.load_reset_domain_map(p)


def test_rejects_reset_source_with_wrong_declared_polarity_type(
    tmp_path: Path,
) -> None:
    payload = json.loads((FIXTURE_DIR / "empty_no_resets.json").read_text())
    payload["reset_sources"] = [
        {
            "name": "rst_n",
            "source": "port",
            "polarity": "low",
            "type": "async",
            "clock": None,
            "via_synchronizer": False,
            "declared_polarity": 0,  # wrong type
        }
    ]
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(rann.ResetAnnotationsError, match="declared_polarity"):
        rann.load_reset_domain_map(p)


def test_rejects_top_level_non_object(tmp_path: Path) -> None:
    p = tmp_path / "broken.json"
    p.write_text("[]")
    with pytest.raises(rann.ResetAnnotationsError, match="object"):
        rann.load_reset_domain_map(p)


# --- I/O errors --------------------------------------------------------------


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(rann.ResetAnnotationsError, match="could not read"):
        rann.load_reset_domain_map(tmp_path / "does_not_exist.json")


def test_rejects_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "garbage.json"
    p.write_text("not valid json {")
    with pytest.raises(rann.ResetAnnotationsError, match="invalid JSON"):
        rann.load_reset_domain_map(p)


# --- error class isolation ---------------------------------------------------


def test_reset_annotations_error_is_distinct_from_clock_error() -> None:
    """A consumer wiring both flags needs to tell errors apart.

    The reset loader raises its own ``ResetAnnotationsError`` so a
    ``try: load_reset_domain_map(...) except ResetAnnotationsError``
    block doesn't accidentally swallow a clock-map error and vice
    versa.
    """
    from rtl_buddy_view import annotations as cann

    assert rann.ResetAnnotationsError is not cann.AnnotationsError
    assert not issubclass(rann.ResetAnnotationsError, cann.AnnotationsError)
    assert not issubclass(cann.AnnotationsError, rann.ResetAnnotationsError)
