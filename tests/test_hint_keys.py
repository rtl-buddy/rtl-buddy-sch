"""Hint keys for generate-scoped targets (rtl-buddy-sch#217).

Verible-free: the key grammar, the wildcard rule, binding against a
hand-built :class:`DesignNames`, the pragma template and the
connectivity lookup are all pure functions. The end-to-end proof over
the issue's repro lives in ``test_cli_hint_generate.py``.
"""

from __future__ import annotations

import pytest

from rtl_buddy_view.connectivity import _hint_for
from rtl_buddy_view.extractor import Instance, Module, ModuleTable, SourceLocation
from rtl_buddy_view.hints import (
    DesignNames,
    HintMap,
    HintsError,
    InstanceHints,
    NetHints,
    bind_hint_keys,
    generate_template,
    key_pattern,
    merge_hint_maps,
    parse_hint_key,
    parse_hint_sidecar,
    resolve_hints,
    scan_pragmas,
)

FILE = "/design/top.sv"


def _sidecar(**payload: object) -> dict[str, object]:
    return {"schema_version": "1.3", **payload}


def _names(
    instances: dict[str, set[str]] | None = None,
    nets: dict[str, set[str]] | None = None,
) -> DesignNames:
    return DesignNames(
        instances={k: frozenset(v) for k, v in (instances or {}).items()},
        nets={k: frozenset(v) for k, v in (nets or {}).items()},
    )


TOP8 = _names(
    instances={
        "top8": {
            "g[0].sub",
            "g[0].i_mrg",
            "g[0].i_lpb",
            "g[1].sub",
            "g[1].i_mrg",
            "g[1].i_lpb",
        }
    },
    nets={"top8": {"q", "g[0].sub", "g[1].sub", "g[0].w", "g[1].w"}},
)


# --- grammar ----------------------------------------------------------------


@pytest.mark.parametrize(
    "key, expected",
    [
        ("top.u_a", ("top", "u_a")),
        ("top8.g[0].i_lpb", ("top8", "g[0].i_lpb")),
        ("top8.g[*].i_lpb", ("top8", "g[*].i_lpb")),
        ("top.g.u_x", ("top", "g.u_x")),
        ("top.g[0].h[1].u_x", ("top", "g[0].h[1].u_x")),
        ("top.dv[SR_A]", ("top", "dv[SR_A]")),
        ("top.q[0][1]", ("top", "q[0][1]")),
        ("top.u_*", ("top", "u_*")),
    ],
)
def test_key_splits_on_the_first_dot(key: str, expected: tuple[str, str]) -> None:
    assert parse_hint_key(key) == expected


@pytest.mark.parametrize(
    "key, reason",
    [
        ("top", "no '.' between the module and the name"),
        (".u_a", "empty module name"),
        ("top.", "nothing after the module name"),
        ("t*p.u_a", "may not contain"),
        ("top[0].u_a", "may not contain"),
        ("top.g[0.u_a", r"unbalanced '\[' at offset 1"),
        ("top.g0].u_a", r"unbalanced '\]' at offset 2"),
        ("top.g[[0]].u_a", r"nested '\['"),
        ("top.g[].u_a", r"empty index '\[\]'"),
        ("top.[0].u_a", r"'\[' at offset 0 must follow a name"),
        ("top.g[0]x.u_a", r"unexpected 'x' after '\]'"),
        ("top.g..u_a", "stray '.'"),
        ("top.g.", "trailing '.'"),
    ],
)
def test_malformed_keys_say_what_is_wrong(key: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        parse_hint_key(key)


def test_sidecar_reports_a_malformed_generate_key_with_its_location() -> None:
    with pytest.raises(HintsError) as e:
        parse_hint_sidecar(
            _sidecar(instances={"top8.g[0.i_lpb": {"hide": True}}), source="h8.json"
        )
    message = str(e.value)
    assert message.startswith("h8.json: instances.top8.g[0.i_lpb: ")
    assert "unbalanced '['" in message
    assert "'g[0.i_lpb'" in message


def test_sidecar_accepts_the_issue_key() -> None:
    hints = parse_hint_sidecar(
        _sidecar(instances={"top8.g[0].i_lpb": {"hide": True}}), source="h8.json"
    )
    assert hints.for_instance("top8", "g[0].i_lpb") == InstanceHints(hide=True)
    assert hints.source == "h8.json"


# --- wildcard + template ----------------------------------------------------


@pytest.mark.parametrize(
    "pattern, name, hit",
    [
        ("g[*].u_x", "g[0].u_x", True),
        ("g[*].u_x", "g[12].u_x", True),
        ("g[*].u_x", "g[*].u_x", True),  # the symbolic iteration itself
        ("g[*].u_x", "g.u_x", False),
        ("g[*].u_x", "g[0].h[1].u_x", False),  # never crosses a dot
        ("g[*].*", "g[0].u_y", True),
        ("u_*", "u_fifo", True),
        ("u_*", "g[0].u_fifo", False),
        ("*.u_x", "g[0].u_x", True),  # brackets are part of the segment
        ("*.u_x", "g.u_x", True),
        ("g*.u_x", "g[3].u_x", True),
        ("g[*][*].u", "g[0][1].u", True),
    ],
)
def test_wildcard_matches_within_one_segment(
    pattern: str, name: str, hit: bool
) -> None:
    compiled = key_pattern(pattern)
    assert compiled is not None
    assert bool(compiled.fullmatch(name)) is hit


def test_a_key_without_a_star_is_exact() -> None:
    assert key_pattern("g[0].u_x") is None


@pytest.mark.parametrize(
    "name, template",
    [
        ("u_x", "u_x"),
        ("g[0].u_x", "g[*].u_x"),
        ("g[0].h[3].u_x", "g[*].h[*].u_x"),
        ("g.u_x", "g.u_x"),
        ("g[*].u_x", "g[*].u_x"),
        ("g[0].sub", "g[*].sub"),
    ],
)
def test_generate_template(name: str, template: str) -> None:
    assert generate_template(name) == template


# --- binding ----------------------------------------------------------------


def test_exact_generate_key_hides_one_iteration() -> None:
    bound = bind_hint_keys(
        HintMap(instances={("top8", "g[0].i_lpb"): InstanceHints(hide=True)}), TOP8
    )
    assert dict(bound.instances) == {("top8", "g[0].i_lpb"): InstanceHints(hide=True)}
    assert bound.warnings == ()


def test_star_index_reaches_every_iteration() -> None:
    bound = bind_hint_keys(
        HintMap(instances={("top8", "g[*].i_lpb"): InstanceHints(hide=True)}), TOP8
    )
    assert sorted(bound.instances) == [("top8", "g[0].i_lpb"), ("top8", "g[1].i_lpb")]
    assert bound.warnings == ()


def test_glob_reaches_every_matching_name() -> None:
    bound = bind_hint_keys(
        HintMap(instances={("top8", "*.i_*"): InstanceHints(collapse=True)}), TOP8
    )
    assert sorted(name for _, name in bound.instances) == [
        "g[0].i_lpb",
        "g[0].i_mrg",
        "g[1].i_lpb",
        "g[1].i_mrg",
    ]


def test_non_generate_key_is_unchanged() -> None:
    names = _names(instances={"top": {"u_a", "u_b"}}, nets={"top": {"clk"}})
    hints = HintMap(
        instances={("top", "u_a"): InstanceHints(hide=True, label="A")},
        nets={("top", "clk"): NetHints(classification="clock")},
    )
    bound = bind_hint_keys(hints, names)
    assert bound == hints
    assert bound.warnings == ()


def test_exact_key_beats_wildcard_and_narrow_beats_wide() -> None:
    hints = HintMap(
        instances={
            ("top8", "*.*"): InstanceHints(label="any", rank=1),
            ("top8", "g[*].i_lpb"): InstanceHints(label="loopback"),
            ("top8", "g[1].i_lpb"): InstanceHints(label="second"),
        }
    )
    bound = bind_hint_keys(hints, TOP8)
    assert bound.for_instance("top8", "g[0].i_lpb") == InstanceHints(
        label="loopback", rank=1
    )
    assert bound.for_instance("top8", "g[1].i_lpb") == InstanceHints(
        label="second", rank=1
    )
    assert bound.for_instance("top8", "g[0].i_mrg") == InstanceHints(
        label="any", rank=1
    )


def test_keys_that_name_nothing_warn_with_the_known_names() -> None:
    hints = HintMap(
        instances={
            ("top8", "g[0].i_lbp"): InstanceHints(hide=True),
            ("top8", "g[*].u_none"): InstanceHints(hide=True),
            ("nope", "g[*].x"): InstanceHints(hide=True),
        },
        source="h8.json",
    )
    bound = bind_hint_keys(hints, TOP8)
    assert bound.warnings == (
        "h8.json: instances.nope.g[*].x: module 'nope' is not in the design; "
        "the key names nothing",
        "h8.json: instances.top8.g[*].u_none: names no instance of module 'top8' "
        "(its instances: g[0].i_lpb, g[0].i_mrg, g[0].sub, g[1].i_lpb, "
        "g[1].i_mrg, g[1].sub)",
        "h8.json: instances.top8.g[0].i_lbp: names no instance of module 'top8' "
        "(its instances: g[0].i_lpb, g[0].i_mrg, g[0].sub, g[1].i_lpb, "
        "g[1].i_mrg, g[1].sub)",
    )
    # An exact key is kept even when dead: binding never changes what
    # an existing sidecar does, it only says so.
    assert ("top8", "g[0].i_lbp") in bound.instances
    assert ("top8", "g[*].u_none") not in bound.instances


def test_long_name_lists_are_truncated() -> None:
    names = _names(instances={"top": {f"u_{i:02d}" for i in range(12)}})
    bound = bind_hint_keys(
        HintMap(instances={("top", "u_x"): InstanceHints(hide=True)}), names
    )
    assert bound.warnings[0].endswith("u_07, …)")


def test_net_keys_take_the_same_generate_forms() -> None:
    hints = HintMap(
        nets={
            ("top8", "g[0].w"): NetHints(classification="clock"),
            ("top8", "g[*].sub"): NetHints(bundle="bus"),
            ("top8", "g[*].sub[1]"): NetHints(emphasis="main"),
            # Exact member / element paths extend a known net.
            ("top8", "g[1].sub[0].v"): NetHints(emphasis="side"),
        }
    )
    bound = bind_hint_keys(hints, TOP8)
    assert bound.warnings == ()
    assert dict(bound.nets) == {
        ("top8", "g[0].sub"): NetHints(bundle="bus"),
        ("top8", "g[0].sub[1]"): NetHints(emphasis="main"),
        ("top8", "g[0].w"): NetHints(classification="clock"),
        ("top8", "g[1].sub"): NetHints(bundle="bus"),
        ("top8", "g[1].sub[0].v"): NetHints(emphasis="side"),
        ("top8", "g[1].sub[1]"): NetHints(emphasis="main"),
    }


def test_net_key_that_names_nothing_warns() -> None:
    bound = bind_hint_keys(
        HintMap(nets={("top8", "g[*].nope"): NetHints(emphasis="main")}), TOP8
    )
    assert len(bound.warnings) == 1
    assert "nets.top8.g[*].nope: names no net of module 'top8'" in bound.warnings[0]
    assert not bound.nets


def test_sidecar_wildcard_beats_pragma_once_each_is_bound() -> None:
    pragma = bind_hint_keys(
        HintMap(instances={("top8", "g[0].i_lpb"): InstanceHints(label="pragma")}),
        TOP8,
    )
    sidecar = bind_hint_keys(
        HintMap(instances={("top8", "g[*].i_lpb"): InstanceHints(label="sidecar")}),
        TOP8,
    )
    merged = merge_hint_maps(pragma, sidecar)
    assert merged.for_instance("top8", "g[0].i_lpb") == InstanceHints(label="sidecar")


def test_empty_map_binds_to_itself() -> None:
    empty = HintMap()
    assert bind_hint_keys(empty, TOP8) is empty


# --- in-source pragmas inside a generate loop -------------------------------


def test_pragma_on_a_loop_body_instance_speaks_for_every_iteration() -> None:
    """The table carries one Instance per iteration at the same line; a
    pragma used to bind only the first (``g[0]``)."""

    def inst(name: str) -> Instance:
        return Instance(
            name=name,
            module_name="leaf",
            param_overrides=(),
            port_connections=(),
            location=SourceLocation(file=FILE, start_line=3),
        )

    table = ModuleTable(
        modules_by_name={
            "top": Module(
                name="top",
                ports=(),
                parameters=(),
                instances=(inst("g[0].u_x"), inst("g[1].u_x")),
                location=SourceLocation(file=FILE, start_line=1),
            )
        }
    )
    source = "module top;\n  for (genvar i = 0; i < 2; i++) begin : g\n    leaf u_x ();  // rbsch: hide\n"
    resolved = resolve_hints(scan_pragmas({FILE: source}), table)
    assert dict(resolved.instances) == {("top", "g[*].u_x"): InstanceHints(hide=True)}
    bound = bind_hint_keys(
        resolved, _names(instances={"top": {"g[0].u_x", "g[1].u_x", "g[2].u_x"}})
    )
    assert sorted(name for _, name in bound.instances) == [
        "g[0].u_x",
        "g[1].u_x",
        "g[2].u_x",
    ]


# --- connectivity lookup ----------------------------------------------------


def test_scoped_member_inherits_its_generate_interface_hint() -> None:
    hints = {"g[0].sub": NetHints(bundle="bus0")}
    assert _hint_for("g[0].sub[1].v", hints) == NetHints(bundle="bus0")
    assert _hint_for("g[0].sub[1]", hints) == NetHints(bundle="bus0")
    assert _hint_for("g[1].sub[1].v", hints) is None


def test_most_specific_scoped_prefix_wins() -> None:
    hints = {
        "g[0].sub": NetHints(bundle="whole"),
        "g[0].sub[1]": NetHints(bundle="element"),
    }
    assert _hint_for("g[0].sub[1].v", hints) == NetHints(bundle="element")
    assert _hint_for("g[0].sub[0].v", hints) == NetHints(bundle="whole")


def test_unscoped_keys_still_resolve_to_their_root() -> None:
    """Pre-#217 behaviour: no dotted prefix, so only exact and root."""
    hints = {"q": NetHints(bundle="q"), "q[1]": NetHints(bundle="elem")}
    assert _hint_for("q[1].c_sta", hints) == NetHints(bundle="q")
    assert _hint_for("q[1]", hints) == NetHints(bundle="elem")
    assert _hint_for("dv[SR_A]", {"dv": NetHints(emphasis="main")}) == NetHints(
        emphasis="main"
    )


def test_key_on_a_module_without_instances_says_so() -> None:
    bound = bind_hint_keys(
        HintMap(instances={("leaf", "u_x"): InstanceHints(hide=True)}),
        _names(instances={"leaf": set()}),
    )
    assert bound.warnings == (
        "instances.leaf.u_x: module 'leaf' has no instances; the key names nothing",
    )
