"""Resolve cdc-emitted synth-internal flop paths to source instances.

``rtl-buddy-cdc --emit-domain-map`` reports per-flop domains and
crossings against **netlist** flop names, e.g. (slang frontend):

    "dst_flop": "ip_dtnpu_credit_cdc.u_sync.$slang$sdff$3"

These names never match a source-instance path in rtl-buddy-view's
hierarchy (``ip_dtnpu_credit_cdc.u_sync``), so ``crossings_into`` —
which uses exact match — silently returns nothing for any real
cdc-emitted map. Without the rewrite below, the ``⚠CDC`` markers
never fire on real-world overlays.

Resolution uses two signals:

* **Path-prefix walk** — the longest prefix of the flop path that
  resolves to a HierNode in the elaborated hierarchy. Handles the
  common case where the synth tool preserves source hierarchy in
  its flop names.
* **Source location** — the matching ``FlopDomain`` entry carries a
  ``location`` (file + line range) for the inferred flop. The
  chosen instance's :class:`Module` source range must contain that
  location; otherwise the path-walk result is rejected and we fall
  back to scanning the tree for any instance whose module range
  contains the location. Disambiguates flattened-synthesis cases
  where the netlist path lies about hierarchy.

Flops that resolve to nothing are dropped from the crossings list —
they were silently invisible to renderers before this rewrite too,
so this is no regression.
"""

from __future__ import annotations

from dataclasses import replace

from rtl_buddy_view.annotations import Crossing, DomainMap
from rtl_buddy_view.extractor import SourceLocation
from rtl_buddy_view.graph import HierNode


def resolve_for_hierarchy(domain_map: DomainMap, root: HierNode) -> DomainMap:
    """Return a new ``DomainMap`` with crossings retargeted at source instances.

    Idempotent: if every ``dst_flop`` / ``src_flop`` already matches a
    HierNode exactly, the returned map is equivalent to the input.
    """
    by_path = _build_path_index(root)
    locations = {f.instance_path: f.location for f in domain_map.flop_domains}

    rewritten: list[Crossing] = []
    for c in domain_map.crossings:
        dst = _resolve_one(c.dst_flop, locations.get(c.dst_flop), by_path)
        if dst is None:
            # Could not attribute to a source instance — drop. The
            # renderer would have ignored it anyway under exact match.
            continue
        src = c.src_flop
        if src is not None:
            src_resolved = _resolve_one(src, locations.get(src), by_path)
            # Keep the original ``src_flop`` text if resolution fails —
            # the renderer doesn't key edges on it (it keys on
            # ``dst_flop``), and the human-readable text still informs.
            src = src_resolved if src_resolved is not None else src
        rewritten.append(replace(c, dst_flop=dst, src_flop=src))

    return replace(domain_map, crossings=tuple(rewritten))


def _build_path_index(root: HierNode) -> dict[str, HierNode]:
    """Walk the hierarchy once, index by ``instance_path``."""
    out: dict[str, HierNode] = {}

    def _walk(node: HierNode) -> None:
        out[node.instance_path] = node
        for child in node.children:
            _walk(child)

    _walk(root)
    return out


def _resolve_one(
    flop_path: str,
    flop_location: SourceLocation | None,
    by_path: dict[str, HierNode],
) -> str | None:
    """Return the deepest source-instance path attributable to ``flop_path``.

    Returns ``None`` if neither path-walk nor location-scan finds a
    match — the crossing is unattributable to a visible source
    instance (e.g. an inlined module that the elaborator dropped).
    """
    if flop_path in by_path:
        # Exact match — already a source-instance path. Common when
        # the cdc-emitted map points at module-level instances (as in
        # rtl-buddy-view's own hand-authored fixtures).
        return flop_path

    # Path-walk: try successively shorter prefixes. Most synth tools
    # encode the source hierarchy as a dotted prefix of the netlist
    # name (slang's ``$slang$sdff$N`` is one such leaf).
    candidate = _longest_known_prefix(flop_path, by_path)
    if candidate is not None and _location_consistent(
        by_path[candidate], flop_location
    ):
        return candidate

    # Fall back: scan the tree for any instance whose module range
    # contains the flop location. Used when the synth path lies (e.g.
    # because the synthesizer flattened an intervening module).
    if flop_location is not None:
        match = _deepest_containing_instance(by_path, flop_location)
        if match is not None:
            return match

    # Last resort: return the path-walk candidate even without a
    # location confirmation. Path-walk alone is what fix-cheap would
    # have done; preserving it here means we strictly improve on the
    # exact-match status quo even when the map omits locations.
    return candidate


def _longest_known_prefix(flop_path: str, by_path: dict[str, HierNode]) -> str | None:
    """Walk dotted segments right-to-left, return the longest known prefix."""
    segments = flop_path.split(".")
    for cut in range(len(segments) - 1, 0, -1):
        candidate = ".".join(segments[:cut])
        if candidate in by_path:
            return candidate
    return None


def _location_consistent(node: HierNode, flop_location: SourceLocation | None) -> bool:
    """``True`` when ``node``'s module source range contains the flop location.

    Treated as vacuously true when either side lacks line info — only
    full file+start_line+end_line on both sides is treated as a real
    cross-check signal.
    """
    if flop_location is None or node.module is None:
        return True
    mod_loc = node.module.location
    if mod_loc is None:
        return True
    if not _full_range(mod_loc) or flop_location.start_line is None:
        return True
    if mod_loc.file != flop_location.file:
        # Different files: definitely the wrong module. Path-walk
        # gave us something whose source lives elsewhere — usually a
        # flattened-synthesis case.
        return False
    return mod_loc.start_line <= flop_location.start_line <= mod_loc.end_line


def _full_range(loc: SourceLocation) -> bool:
    return loc.start_line is not None and loc.end_line is not None


def _deepest_containing_instance(
    by_path: dict[str, HierNode], flop_location: SourceLocation
) -> str | None:
    """Return the path of the deepest HierNode whose module range contains
    ``flop_location``. Depth tie-breaks by alphabetical path for stability.

    "Contains" requires same file and ``start_line ∈ [module.start_line,
    module.end_line]``. ``end_column`` precision isn't worth the cost —
    a flop is small and lives strictly inside its enclosing module body.
    """
    if flop_location.start_line is None:
        return None
    matches: list[tuple[int, str]] = []  # (depth, path)
    for path, node in by_path.items():
        if node.module is None or node.module.location is None:
            continue
        mod_loc = node.module.location
        if (
            mod_loc.file != flop_location.file
            or not _full_range(mod_loc)
            or not (mod_loc.start_line <= flop_location.start_line <= mod_loc.end_line)
        ):
            continue
        matches.append((path.count("."), path))
    if not matches:
        return None
    matches.sort(key=lambda dp: (-dp[0], dp[1]))
    return matches[0][1]
