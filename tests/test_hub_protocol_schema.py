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
2. ``cov_focus`` (rtl-buddy/rtl-buddy-view#133) and ``phys_focus``
   (rtl-buddy/rtl_buddy#558) are structurally siblings of
   ``graph_focus`` — one required coordinate string plus optional
   hints, closed payload, ``kind: "event"``.
3. The examples in ``docs/hub-protocol.md`` are what implementers copy,
   and nothing else checks them: the ``hello`` row shipped
   ``client: "viewer"`` for the whole life of the document, a value the
   schema has never accepted. Every envelope example in the doc is now
   validated against the schema, and every ``origin``/``client``
   literal anywhere in the prose is checked against the vocabulary.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "hub-protocol-v1.json"
DOC_PATH = Path(__file__).parent.parent / "docs" / "hub-protocol.md"

# The v1 origin vocabulary, in the order the schema lists it.
ORIGINS = ["view", "wave", "src", "cli", "notebook", "graph", "cov", "phys"]

# The peers whose prose the doc fence checks by name: the wire origin,
# its focus event, and the ``§4.x`` heading that has to introduce it.
# A new origin with a pane of its own belongs here — that is what keeps
# row 3 of the ``docs/hub-protocol.md`` §13.1 checklist guarded.
PROSE_PEERS = [
    ("cov", "cov_focus", "### 4.9 Coverage pane (`cov` client, browser)"),
    (
        "phys",
        "phys_focus",
        "### 4.10 Physical-metrics pane (`phys` client, browser)",
    ),
]

UUID = "9c3f8e5f-7d1b-4d3a-9a3b-1a2f5c8e7d3a"


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC_PATH.read_text()


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


def _strip_line_comments(text: str) -> str:
    """Drop ``//`` annotations from the doc's fenced JSON blocks.

    The worked examples annotate envelopes with ``// → wave`` and the
    like, which is not JSON. No string literal in those blocks contains
    ``//``, so a naive strip is exact here.
    """
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def _iter_json_objects(text: str):
    """Yield every top-level ``{...}`` object in ``text``, decoded."""
    decoder = json.JSONDecoder()
    idx = 0
    while (start := text.find("{", idx)) != -1:
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue
        idx = end
        yield obj


def _doc_envelopes(doc: str) -> list[dict]:
    """Every fenced JSON block in the doc that is a wire envelope.

    Non-envelope blocks (the ``hub.json`` discovery record) are skipped
    by requiring both ``kind`` and ``type``.
    """
    envelopes: list[dict] = []
    for block in re.findall(r"```json\n(.*?)```", doc, flags=re.DOTALL):
        for obj in _iter_json_objects(_strip_line_comments(block)):
            if isinstance(obj, dict) and "kind" in obj and "type" in obj:
                envelopes.append(obj)
    return envelopes


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


def test_phys_may_hello_and_appear_in_welcome(
    validator: jsonschema.protocols.Validator,
) -> None:
    validator.validate(
        _envelope(
            "hello",
            {"client": "phys", "version": "0.1.0", "capabilities": ["phys_focus"]},
            origin="phys",
            kind="request",
        )
    )
    validator.validate(
        _envelope(
            "welcome",
            {"server_version": "0.1.0", "registered_clients": ["view", "phys"]},
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


# --- phys_focus -------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"target": "instance:top.u_fifo.u_wr_ptr"},
        {"target": "top.u_fifo.u_wr_ptr"},  # unprefixed reads as an instance
        {"target": "module:fifo"},
        {"target": "module:fifo", "metric": "cells"},
        {"target": "module:fifo", "metric": "area"},
        {"target": "instance:top.u_fifo", "metric": "leakage"},
        {"target": "instance:top.u_fifo", "metric": "dynamic"},
        {"target": "instance:top.u_fifo", "metric": "total"},
    ],
)
def test_phys_focus_accepts_target_plus_optional_metric(
    validator: jsonschema.protocols.Validator, payload: dict
) -> None:
    validator.validate(_envelope("phys_focus", payload, origin="cli"))


@pytest.mark.parametrize(
    "payload",
    [
        {},  # target is required
        {"target": ""},  # and non-empty
        {"target": "module:fifo", "metric": "power"},  # closed enum
        {"target": "module:fifo", "metric": "switching"},  # summed into `dynamic`
        {"target": "module:fifo", "metric": "internal"},
        {"target": "module:fifo", "line": 42},  # closed payload — cov_focus's hint
        {"target": "module:fifo", "instance": "top.u_fifo"},
        {"node": "module:fifo"},  # graph_focus's payload, not this one
    ],
)
def test_phys_focus_rejects_malformed_payloads(
    validator: jsonschema.protocols.Validator, payload: dict
) -> None:
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(_envelope("phys_focus", payload, origin="cli"))


def test_phys_focus_is_an_event_not_a_request(
    validator: jsonschema.protocols.Validator,
) -> None:
    """Same asymmetry as graph_focus and cov_focus: broadcast state."""
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(
            _envelope(
                "phys_focus", {"target": "module:fifo"}, origin="cli", kind="request"
            )
        )


def test_phys_focus_may_originate_anywhere(
    validator: jsonschema.protocols.Validator,
) -> None:
    """`any → all`, so the pane itself is a legal producer too."""
    for origin in ORIGINS:
        validator.validate(
            _envelope("phys_focus", {"target": "module:fifo"}, origin=origin)
        )


def test_phys_focus_mirrors_cov_focus_structurally(schema: dict) -> None:
    """The three focus events stay siblings.

    ``phys_focus`` was specified as "``cov_focus`` for the physical
    model"; if one grows a required field or opens its payload, the
    divergence should be a deliberate edit here, not a silent drift.
    """
    branches = {
        branch["if"]["properties"]["type"]["const"]: branch["then"]
        for branch in schema["allOf"]
        if "const" in branch["if"]["properties"]["type"]
    }
    cov = branches["cov_focus"]["properties"]["payload"]
    phys = branches["phys_focus"]["properties"]["payload"]
    for shape in (cov, phys):
        assert shape["type"] == "object"
        assert shape["additionalProperties"] is False
        assert shape["required"] == ["target"]
        assert shape["description"]
    assert branches["phys_focus"]["properties"]["kind"] == {"const": "event"}
    # The metric hint is a closed enum in both, and `dynamic` is the
    # pane-resolved sum rather than a `/phy.json` key of its own.
    assert phys["properties"]["metric"]["enum"] == [
        "cells",
        "area",
        "leakage",
        "dynamic",
        "total",
    ]
    assert "internal + switching" in phys["properties"]["metric"]["description"]


# --- schema ↔ docs ----------------------------------------------------------


@pytest.mark.parametrize(("origin", "event", "heading"), PROSE_PEERS)
def test_docs_document_the_peer(
    doc: str, origin: str, event: str, heading: str
) -> None:
    """`docs/hub-protocol.md` is the prose form of the same promise."""
    assert f"| `{event}`" in doc, f"§3 event catalog row missing for {event}"
    assert heading in doc, f"§4.x peer section missing for `{origin}`"


def test_every_prose_origin_list_carries_the_whole_vocabulary(doc: str) -> None:
    """No glossary or checklist line lags the wire.

    This used to name `cov` — the origin it was written for — which
    made row 3 of the §13.1 checklist half-guarded: the next origin
    could land on the wire with the prose lists still enumerating the
    old vocabulary. It is now driven by `ORIGINS`, so adding an origin
    to the pin above is what fails the doc lists that forgot it.

    A line is "an origin list" if it names ``notebook`` and ``graph``
    in backticks — the two that only ever appear in an enumeration of
    the whole vocabulary, never on their own in a sentence.
    """
    lists = [
        line for line in doc.splitlines() if "`notebook`" in line and "`graph`" in line
    ]
    assert lists, "origin lists moved out of the document"
    for line in lists:
        for origin in ORIGINS:
            assert f"`{origin}`" in line, f"origin list missing {origin}: {line}"


def test_doc_examples_name_only_real_origins(doc: str) -> None:
    """No example invents a peer name the schema would reject.

    §3's `hello` row said `client: "viewer"` — readable, and rejected on
    the wire by the `client` enum. Sweep every `origin` / `client` /
    `registered_clients` literal in the document, in JSON blocks and in
    inline prose alike.
    """
    found = set(re.findall(r'"(?:origin|client)"\s*:\s*"([^"]*)"', doc))
    found |= set(re.findall(r"\bclient:\s*\"([^\"]*)\"", doc))
    for array in re.findall(r'"registered_clients"\s*:\s*\[([^\]]*)\]', doc):
        found |= set(re.findall(r'"([^"]*)"', array))
    assert found, "origin/client literals moved out of the document"
    assert found <= set(ORIGINS), (
        f"unknown peer names in examples: {sorted(found - set(ORIGINS))}"
    )


def test_doc_envelope_examples_validate(
    validator: jsonschema.protocols.Validator, doc: str
) -> None:
    """Every envelope an implementer can copy out of the doc is legal.

    §8 abbreviates `id` ("in practice they are full UUIDs"), so that one
    field is normalised before validating; everything else is checked as
    written.
    """
    envelopes = _doc_envelopes(doc)
    assert len(envelopes) >= 10, (
        f"only {len(envelopes)} envelopes parsed; extractor drifted"
    )
    for envelope in envelopes:
        validator.validate({**envelope, "id": UUID})


def test_doc_and_schema_agree_on_the_cov_focus_line_hint(
    schema: dict, doc: str
) -> None:
    """`line` is not file-only, in either document.

    The schema is the truth: `line` narrows a `file:` target *or* a
    `module:` target that resolves to a single file, and is ignored
    rather than rejected anywhere else. §3 used to say "file targets
    only", which reads as a constraint the schema does not impose.
    """
    branches = {
        branch["if"]["properties"]["type"]["const"]: branch["then"]
        for branch in schema["allOf"]
        if "const" in branch["if"]["properties"]["type"]
    }
    line = branches["cov_focus"]["properties"]["payload"]["properties"]["line"]
    assert "module:" in line["description"]
    assert "ignored" in line["description"]

    (row,) = [ln for ln in doc.splitlines() if ln.startswith("| `cov_focus`")]
    assert "`module:` target that resolves to a single file" in row
    assert "ignored, not an error" in row
