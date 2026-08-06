"""Contract tests for ``schemas/hub-protocol-v1.json``.

This repo is the source of truth for the hub wire contract: rtl_buddy
vendors the schema file byte-for-byte (its ``tests/test_hub_protocol.py``
byte-compares the copy), so a shape that is wrong here is wrong in both
repos. These tests keep the schema a live constraint rather than
documentation, and pin the two invariants that are easy to break by
hand-editing a 1000-line JSON file:

1. The ``origin`` vocabulary is repeated in eight places (the envelope,
   the ``state_snapshot`` response's three cached-state blocks and its
   peer list, ``hello``'s envelope + ``client``, ``welcome``'s
   ``registered_clients``). Adding a peer means adding it to all of
   them; the enum-sweep test below fails when one is missed.
2. ``cov_focus`` (rtl-buddy/rtl-buddy-view#133) is structurally a
   sibling of ``graph_focus`` — one required coordinate string plus
   optional hints, closed payload, ``kind: "event"``.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "hub-protocol-v1.json"
DOC_PATH = Path(__file__).parent.parent / "docs" / "hub-protocol.md"

# The v1 origin vocabulary, in the order the schema lists it.
ORIGINS = ["view", "wave", "src", "cli", "notebook", "graph", "cov"]

UUID = "9c3f8e5f-7d1b-4d3a-9a3b-1a2f5c8e7d3a"


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def validator(schema: dict) -> jsonschema.protocols.Validator:
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def _envelope(msg_type: str, payload: object, **overrides: object) -> dict:
    env = {
        "v": 1,
        "id": UUID,
        "origin": "cli",
        "kind": "event",
        "type": msg_type,
        "payload": payload,
    }
    env.update(overrides)
    return env


def _iter_enums(node: object):
    """Yield every ``enum`` list in the schema, depth-first."""
    if isinstance(node, dict):
        if isinstance(node.get("enum"), list):
            yield node["enum"]
        for value in node.values():
            yield from _iter_enums(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_enums(value)


# --- the origin vocabulary --------------------------------------------------


def test_origin_enum_is_the_full_vocabulary(schema: dict) -> None:
    assert schema["properties"]["origin"]["enum"] == ORIGINS


def test_every_origin_enum_lists_every_origin(schema: dict) -> None:
    """No half-updated copy of the origin list survives.

    An enum is "an origin list" if it contains ``view`` and ``wave``;
    every such list must be the complete vocabulary, or a peer becomes
    unable to appear in (say) ``state_snapshot.peers`` while being a
    legal envelope ``origin``.
    """
    origin_enums = [e for e in _iter_enums(schema) if {"view", "wave"} <= set(e)]
    assert len(origin_enums) == 8, f"origin lists moved: found {len(origin_enums)}"
    for enum in origin_enums:
        assert enum == ORIGINS


@pytest.mark.parametrize("origin", ORIGINS)
def test_every_origin_is_a_legal_envelope_origin(
    validator: jsonschema.protocols.Validator, origin: str
) -> None:
    validator.validate(_envelope("bye", None, origin=origin))


def test_unknown_origin_is_rejected(
    validator: jsonschema.protocols.Validator,
) -> None:
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(_envelope("bye", None, origin="coverage"))


def test_cov_may_hello_and_appear_in_welcome(
    validator: jsonschema.protocols.Validator,
) -> None:
    validator.validate(
        _envelope(
            "hello",
            {"client": "cov", "version": "0.1.0", "capabilities": ["cov_focus"]},
            origin="cov",
            kind="request",
        )
    )
    validator.validate(
        _envelope(
            "welcome",
            {"server_version": "0.1.0", "registered_clients": ["view", "cov"]},
            kind="response",
        )
    )


# --- cov_focus --------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"target": "file:rtl/fifo.sv"},
        {"target": "/abs/path/rtl/fifo.sv"},
        {"target": "module:fifo", "metric": "toggle"},
        {"target": "test:verif/dma#smoke"},
        {"target": "file:rtl/fifo.sv", "metric": "branch", "line": 42, "item": "b3"},
        {"target": "file:rtl/fifo.sv", "metric": "expression"},
        {"target": "file:rtl/fifo.sv", "metric": "cover"},
        {"target": "file:rtl/fifo.sv", "metric": "line"},
    ],
)
def test_cov_focus_accepts_target_plus_optional_hints(
    validator: jsonschema.protocols.Validator, payload: dict
) -> None:
    validator.validate(_envelope("cov_focus", payload, origin="cli"))


@pytest.mark.parametrize(
    "payload",
    [
        {},  # target is required
        {"target": ""},  # and non-empty
        {"target": "file:rtl/fifo.sv", "metric": "functional"},  # closed enum
        {"target": "file:rtl/fifo.sv", "line": 0},  # 1-based
        {"target": "file:rtl/fifo.sv", "file": "rtl/fifo.sv"},  # closed payload
        {"target": "file:rtl/fifo.sv", "item": ""},
        {"node": "module:fifo"},  # graph_focus's payload, not this one
    ],
)
def test_cov_focus_rejects_malformed_payloads(
    validator: jsonschema.protocols.Validator, payload: dict
) -> None:
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(_envelope("cov_focus", payload, origin="cli"))


def test_cov_focus_is_an_event_not_a_request(
    validator: jsonschema.protocols.Validator,
) -> None:
    """Same asymmetry as graph_focus: broadcast state, never point-to-point."""
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(
            _envelope(
                "cov_focus", {"target": "module:fifo"}, origin="cli", kind="request"
            )
        )


def test_cov_focus_may_originate_anywhere(
    validator: jsonschema.protocols.Validator,
) -> None:
    """`any → all`, so the pane itself is a legal producer too."""
    for origin in ORIGINS:
        validator.validate(
            _envelope("cov_focus", {"target": "module:fifo"}, origin=origin)
        )


def test_cov_focus_mirrors_graph_focus_structurally(schema: dict) -> None:
    """The two focus events stay siblings.

    ``cov_focus`` was specified as "``graph_focus`` for coverage"; if
    one grows a required field or opens its payload, the divergence
    should be a deliberate edit here, not a silent drift.
    """
    branches = {
        branch["if"]["properties"]["type"]["const"]: branch["then"]
        for branch in schema["allOf"]
        if "const" in branch["if"]["properties"]["type"]
    }
    graph = branches["graph_focus"]["properties"]["payload"]
    cov = branches["cov_focus"]["properties"]["payload"]
    for shape in (graph, cov):
        assert shape["type"] == "object"
        assert shape["additionalProperties"] is False
        assert len(shape["required"]) == 1
        assert shape["description"]
    assert graph["required"] == ["node"]
    assert cov["required"] == ["target"]
    assert branches["cov_focus"]["properties"]["kind"] == {"const": "event"}


# --- schema ↔ docs ----------------------------------------------------------


def test_docs_document_the_cov_peer() -> None:
    """`docs/hub-protocol.md` is the prose form of the same promise."""
    doc = DOC_PATH.read_text()
    assert "| `cov_focus`" in doc, "§3 event catalog row missing"
    assert "### 4.9 Coverage pane (`cov` client, browser)" in doc
    # Every prose list of origins carries the whole vocabulary.
    for line in doc.splitlines():
        if "`notebook`" in line and "`graph`" in line:
            assert "`cov`" in line, f"origin list missing cov: {line}"
