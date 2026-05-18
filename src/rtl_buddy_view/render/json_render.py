"""JSON renderer.

Emits a deterministic, schema-versioned JSON representation of the
hierarchy. This is the format ``rtl_buddy`` will consume via
``rb hier`` — the keys pinned in :data:`JSON_CONTRACT` are the
downstream-stable surface (renaming or retyping any of them is a
breaking change; AGENTS.md "Cross-repo coupling" enumerates the
contract).

Everything that can vary across runs (dict ordering, instance
order at the same level, instance paths) is sorted before
emission so two runs over the same input produce identical bytes —
keeps golden tests and rb-hier-side diffs noise-free.

When a :class:`rtl_buddy_view.annotations.DomainMap` is supplied,
each node gains a ``clock`` field and a ``crossings_in`` array
mirroring the async-crossing information. With no map (or an empty
one) those fields are ``null`` / ``[]`` respectively — same
graceful-degradation contract as the other renderers.
"""

from __future__ import annotations

import json
from typing import IO

from rtl_buddy_view.annotations import DomainMap
from rtl_buddy_view.graph import HierNode

SCHEMA_VERSION = "1.0"

#: Dotted JSON paths that downstream consumers (rtl_buddy) parse
#: out of this output. Renaming or retyping any of these is a
#: downstream-breaking change. A contract test pins the keys to
#: catch accidental drift. Anything *else* in the payload can
#: evolve freely.
JSON_CONTRACT: dict[str, type] = {
    "schema_version": str,
    "tool.name": str,
    "tool.version": str,
    "design.top": str,
    "nodes": list,
    "edges": list,
}


def render(
    node: HierNode,
    out: IO[str],
    *,
    domain_map: DomainMap | None = None,
) -> None:
    """Render ``node`` and its subtree as JSON to ``out``."""
    payload = _build_payload(node, domain_map)
    json.dump(payload, out, indent=2, sort_keys=False)
    out.write("\n")


def _build_payload(node: HierNode, domain_map: DomainMap | None) -> dict:
    nodes = sorted(
        (_node_dict(n, domain_map) for n in _walk(node)),
        key=lambda d: d["instance_path"],
    )
    edges = sorted(
        _walk_edges(node),
        key=lambda d: (d["parent"], d["child"]),
    )
    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "rtl-buddy-view", "version": _version()},
        "design": {"top": node.module_name},
        "nodes": nodes,
        "edges": edges,
    }
    return payload


def _version() -> str:
    """Project version, late-imported to keep this module light.

    Pulled at runtime rather than baked at import time so a version
    bump in pyproject.toml flows through without code changes.
    """
    try:
        from importlib.metadata import version as _v

        return _v("rtl-buddy-view")
    except Exception:  # pragma: no cover - importlib is in stdlib
        return "0.0.0"


# --- nodes -------------------------------------------------------------------


def _node_dict(node: HierNode, domain_map: DomainMap | None) -> dict:
    out: dict = {
        "instance_path": node.instance_path,
        "module_name": node.module_name,
        "instance_name": node.instance.name if node.instance is not None else None,
        "is_blackbox": node.is_blackbox,
        "param_overrides": [_param_override_dict(p) for p in _safe_overrides(node)],
        "port_connections": [_port_conn_dict(p) for p in _safe_port_conns(node)],
        "location": _location_dict(node),
    }
    if domain_map is not None and not domain_map.is_empty:
        out["clock"] = domain_map.predominant_clock(node.instance_path)
        out["crossings_in"] = [
            _crossing_dict(c) for c in domain_map.crossings_into(node.instance_path)
        ]
    else:
        out["clock"] = None
        out["crossings_in"] = []
    return out


def _safe_overrides(node: HierNode):
    if node.instance is None:
        return ()
    return node.instance.param_overrides


def _safe_port_conns(node: HierNode):
    if node.instance is None:
        return ()
    return node.instance.port_connections


def _param_override_dict(ov) -> dict:
    return {
        "param_name": ov.param_name,
        "value_text": ov.value_text,
    }


def _port_conn_dict(pc) -> dict:
    return {
        "port_name": pc.port_name,
        "net_expr_text": pc.net_expr_text,
    }


def _crossing_dict(c) -> dict:
    out: dict = {
        "src_clock": c.src_clock,
        "dst_clock": c.dst_clock,
        "min_hops": c.min_hops,
        "width": c.width,
        "async_per_sdc": c.async_per_sdc,
    }
    if c.src_flop is not None:
        out["src_flop"] = c.src_flop
    if c.src_port is not None:
        out["src_port"] = c.src_port
    return out


def _location_dict(node: HierNode) -> dict | None:
    """Source anchor for the node's own decl, or its instantiation.

    Prefers the instance's location (i.e. the line where this child
    is instantiated in its parent) since that's the more actionable
    anchor — "where is this instance?" — but falls back to the
    module declaration when there's no instance (root node).
    """
    if node.instance is not None and node.instance.location is not None:
        loc = node.instance.location
    elif node.module is not None and node.module.location is not None:
        loc = node.module.location
    else:
        return None
    return {
        "file": loc.file,
        "start_line": loc.start_line,
        "start_column": loc.start_column,
        "end_line": loc.end_line,
        "end_column": loc.end_column,
    }


# --- traversal ---------------------------------------------------------------


def _walk(node: HierNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _walk_edges(node: HierNode):
    for child in node.children:
        yield {"parent": node.instance_path, "child": child.instance_path}
        yield from _walk_edges(child)
