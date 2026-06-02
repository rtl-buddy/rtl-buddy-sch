"""Loader for the rtl-buddy-axi-profiler axi-perf.json (Phase 11 overlay input).

Consumes the v1 JSON artifact produced by
[rtl-buddy/rtl-buddy-axi-profiler#1](https://github.com/rtl-buddy/rtl-buddy-axi-profiler/issues/1).
Schema is owned by the profiler; this module is the consumer side
and validates the version field on load so an incompatible producer
fails loudly instead of silently misrendering.

What this module does:

- Load + structurally validate the v1.x schema.
- Build an in-memory ``AxiPerfMap`` with two lookup indices — by
  ``(master_path, slave_path)`` edge and by interface-instance path
  (the #114 tb-top join) — with hierarchical child support.

Renderers consume the map directly: ``json_render`` emits per-node /
per-edge ``overlays.axi_perf`` plus a top-level ``axi_perf`` block,
and the viewer's AXI tab renders from it. (The original #60 plan for
per-edge styling + tree-mode ASCII annotations was superseded by the
dedicated AXI tab in #69 — see ``docs/axi-perf-overlay.md``.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: Highest schema major.minor we know how to consume. v1.x is
#: additive-only post-lock per the producer's contract; v2.x is a
#: breaking-change escape hatch that bumps the supported major here.
SUPPORTED_SCHEMA_MAJOR = 1


class AxiPerfAnnotationsError(ValueError):
    """Raised when an axi-perf.json payload can't be loaded.

    Distinct from generic JSON / IO errors so callers can surface a
    targeted "AXI perf map is malformed" message rather than a
    confusing stack trace.
    """


@dataclass(frozen=True)
class ChannelStats:
    util_pct: float
    bp_pct: float
    peak_occ: int
    # Exactly one of txns / beats is populated per AXI channel role
    # (AR/AW/B carry txns; R/W carry beats). The other is None.
    txns: int | None = None
    beats: int | None = None


@dataclass(frozen=True)
class LatencyStats:
    p50: int
    p95: int
    p99: int
    max: int
    hist_log2: tuple[int, ...]  # always length 16 per the v1 schema


@dataclass(frozen=True)
class Throughput:
    read_bps: float
    write_bps: float


@dataclass(frozen=True)
class Outstanding:
    read_peak: int
    read_avg: float
    write_peak: int
    write_avg: float


@dataclass(frozen=True)
class Errors:
    slverr: int
    decerr: int


@dataclass(frozen=True)
class Bundle:
    name: str
    master_path: str
    slave_path: str
    protocol: str
    data_width: int
    id_width: int
    default_view: str
    channels: dict[str, ChannelStats]
    throughput: Throughput
    outstanding: Outstanding
    ar_to_r_first: LatencyStats
    aw_to_b: LatencyStats
    errors: Errors
    children: tuple["Bundle", ...] = ()
    # Interface-connection identity (v1.x additive — populated by the
    # producer's verible-interface detector). When set, the bundle is a
    # true SV interface instance and the view can attach it to the
    # matching interface-port pin (the "tb-top mechanism") instead of
    # the brittle (master_path, slave_path) edge match. All optional so
    # legacy regex-detected bundles (which omit them) keep working.
    interface_instance: str | None = None
    master_port: str | None = None
    slave_port: str | None = None
    master_modport: str | None = None
    slave_modport: str | None = None


@dataclass(frozen=True)
class Arbitration:
    fairness_jain: float
    starved_masters: tuple[str, ...]


@dataclass(frozen=True)
class Interconnect:
    node_path: str
    total_read_bps: float
    total_write_bps: float
    hottest_master: str
    hottest_slave: str
    arbitration: Arbitration


@dataclass(frozen=True)
class AxiPerfMap:
    """Parsed axi-perf.json. Renderers consume via the lookup methods."""

    schema_version: str
    tool: str
    tool_version: str
    produced_at: str
    design_top: str
    duration_cycles: int
    clock_period_ns: float
    bundles: tuple[Bundle, ...] = ()
    interconnects: tuple[Interconnect, ...] = ()
    # Flat lookup populated at load time so renderers don't repeatedly
    # walk the tree. Keys: (master_path, slave_path).
    _bundles_by_edge: dict[tuple[str, str], Bundle] = field(
        default_factory=dict, compare=False, repr=False
    )
    # Parallel lookup keyed by the dotted interface-instance path
    # (e.g. ``tb_axi_fifo_simple.slv``). Populated only for bundles that
    # carry ``interface_instance``; lets the renderer attach a bundle to
    # the interface-port pin on its endpoint without re-deriving paths.
    _bundles_by_interface_instance: dict[str, Bundle] = field(
        default_factory=dict, compare=False, repr=False
    )

    @property
    def is_empty(self) -> bool:
        """True when no bundles were emitted (typically because the
        producer's discover stage matched nothing)."""
        return not self.bundles

    def bundle_at_edge(self, master_path: str, slave_path: str) -> Bundle | None:
        """Return the bundle whose master/slave paths match, or None."""
        return self._bundles_by_edge.get((master_path, slave_path))

    def bundle_at_interface_instance(self, interface_instance: str) -> Bundle | None:
        """Return the bundle bound to the given interface-instance path,
        or None. Only verible-interface bundles populate this index."""
        return self._bundles_by_interface_instance.get(interface_instance)

    def interconnect_at(self, node_path: str) -> Interconnect | None:
        for ic in self.interconnects:
            if ic.node_path == node_path:
                return ic
        return None

    def iter_bundles(self):
        """Yield every bundle, flattening one level of ``children``.

        Renderers that attach a bundle to the node named by its
        ``master_path`` / ``slave_path`` (rather than to an interface
        pin) iterate this so child sub-bundles of an interconnect are
        reachable too."""
        for bundle in self.bundles:
            yield bundle
            for child in bundle.children:
                yield child


def load_axi_perf_map(path: Path) -> AxiPerfMap:
    """Load and validate an axi-perf.json artifact.

    Raises :class:`AxiPerfAnnotationsError` on missing/malformed
    payloads. File I/O and JSON parse errors are wrapped in the same
    exception so a single ``except`` catches every "axi-perf map
    didn't load" failure mode.
    """
    try:
        text = path.read_text()
    except OSError as e:
        raise AxiPerfAnnotationsError(f"could not read {path}: {e}") from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise AxiPerfAnnotationsError(f"{path}: invalid JSON ({e.msg})") from None
    if not isinstance(payload, dict):
        raise AxiPerfAnnotationsError(f"{path}: top-level must be a JSON object")
    return _parse_payload(payload, source_path=path)


def _parse_payload(payload: dict, *, source_path: Path) -> AxiPerfMap:
    version = payload.get("schema_version")
    if not isinstance(version, str):
        raise AxiPerfAnnotationsError(
            f"{source_path}: schema_version missing or not a string"
        )
    major = _major(version)
    if major != SUPPORTED_SCHEMA_MAJOR:
        raise AxiPerfAnnotationsError(
            f"{source_path}: schema_version {version!r} is not supported "
            f"(consumer expects {SUPPORTED_SCHEMA_MAJOR}.x); upgrade "
            f"rtl-buddy-view or downgrade the producing rtl-buddy-axi-profiler."
        )

    bundles = tuple(_parse_bundle(b, source_path) for b in payload.get("bundles", []))
    interconnects = tuple(
        _parse_interconnect(ic, source_path) for ic in payload.get("interconnects", [])
    )

    edge_index: dict[tuple[str, str], Bundle] = {}
    iface_index: dict[str, Bundle] = {}
    for bundle in bundles:
        _index_bundle(bundle, edge_index, iface_index)

    return AxiPerfMap(
        schema_version=version,
        tool=_require_str(payload, "tool", source_path),
        tool_version=_require_str(payload, "tool_version", source_path),
        produced_at=_require_str(payload, "produced_at", source_path),
        design_top=_require_str(payload, "design_top", source_path),
        duration_cycles=_require_int(payload, "duration_cycles", source_path),
        clock_period_ns=_require_float(payload, "clock_period_ns", source_path),
        bundles=bundles,
        interconnects=interconnects,
        _bundles_by_edge=edge_index,
        _bundles_by_interface_instance=iface_index,
    )


def _parse_bundle(raw: dict, source_path: Path) -> Bundle:
    if not isinstance(raw, dict):
        raise AxiPerfAnnotationsError(f"{source_path}: bundle entry must be an object")
    channels = {
        ch: _parse_channel(raw["channels"][ch], ch, source_path)
        for ch in ("ar", "aw", "r", "w", "b")
        if ch in raw.get("channels", {})
    }
    throughput = _parse_throughput(_require_dict(raw, "throughput", source_path))
    outstanding = _parse_outstanding(_require_dict(raw, "outstanding", source_path))
    latencies = _require_dict(raw, "latency_cycles", source_path)
    errors_raw = _require_dict(raw, "errors", source_path)
    children = tuple(_parse_bundle(c, source_path) for c in raw.get("children", []))
    return Bundle(
        name=_require_str(raw, "name", source_path),
        master_path=_require_str(raw, "master_path", source_path),
        slave_path=_require_str(raw, "slave_path", source_path),
        protocol=_require_str(raw, "protocol", source_path),
        data_width=_require_int(raw, "data_width", source_path),
        id_width=_require_int(raw, "id_width", source_path),
        default_view=str(raw.get("default_view", "parent")),
        channels=channels,
        throughput=throughput,
        outstanding=outstanding,
        ar_to_r_first=_parse_latency(
            _require_dict(latencies, "ar_to_r_first", source_path)
        ),
        aw_to_b=_parse_latency(_require_dict(latencies, "aw_to_b", source_path)),
        errors=Errors(
            slverr=_require_int(errors_raw, "slverr", source_path),
            decerr=_require_int(errors_raw, "decerr", source_path),
        ),
        children=children,
        interface_instance=_opt_str(raw, "interface_instance"),
        master_port=_opt_str(raw, "master_port"),
        slave_port=_opt_str(raw, "slave_port"),
        master_modport=_opt_str(raw, "master_modport"),
        slave_modport=_opt_str(raw, "slave_modport"),
    )


def _parse_channel(raw: dict, role: str, source_path: Path) -> ChannelStats:
    if not isinstance(raw, dict):
        raise AxiPerfAnnotationsError(
            f"{source_path}: channel {role!r} must be an object"
        )
    return ChannelStats(
        util_pct=_require_float(raw, "util_pct", source_path),
        bp_pct=_require_float(raw, "bp_pct", source_path),
        peak_occ=_require_int(raw, "peak_occ", source_path),
        txns=raw.get("txns") if isinstance(raw.get("txns"), int) else None,
        beats=raw.get("beats") if isinstance(raw.get("beats"), int) else None,
    )


def _parse_throughput(raw: dict) -> Throughput:
    return Throughput(
        read_bps=float(raw["read_bps"]),
        write_bps=float(raw["write_bps"]),
    )


def _parse_outstanding(raw: dict) -> Outstanding:
    return Outstanding(
        read_peak=int(raw["read_peak"]),
        read_avg=float(raw["read_avg"]),
        write_peak=int(raw["write_peak"]),
        write_avg=float(raw["write_avg"]),
    )


def _parse_latency(raw: dict) -> LatencyStats:
    hist = raw.get("hist_log2")
    if not isinstance(hist, list) or len(hist) != 16:
        raise AxiPerfAnnotationsError("latency.hist_log2 must be a length-16 array")
    return LatencyStats(
        p50=int(raw["p50"]),
        p95=int(raw["p95"]),
        p99=int(raw["p99"]),
        max=int(raw["max"]),
        hist_log2=tuple(int(x) for x in hist),
    )


def _parse_interconnect(raw: dict, source_path: Path) -> Interconnect:
    arb_raw = _require_dict(raw, "arbitration", source_path)
    return Interconnect(
        node_path=_require_str(raw, "node_path", source_path),
        total_read_bps=_require_float(raw, "total_read_bps", source_path),
        total_write_bps=_require_float(raw, "total_write_bps", source_path),
        hottest_master=_require_str(raw, "hottest_master", source_path),
        hottest_slave=_require_str(raw, "hottest_slave", source_path),
        arbitration=Arbitration(
            fairness_jain=_require_float(arb_raw, "fairness_jain", source_path),
            starved_masters=tuple(arb_raw.get("starved_masters", [])),
        ),
    )


def _index_bundle(
    bundle: Bundle,
    index: dict[tuple[str, str], Bundle],
    iface_index: dict[str, Bundle],
) -> None:
    key = (bundle.master_path, bundle.slave_path)
    if key in index:
        # Duplicate (master, slave) at the same hierarchy level — keep
        # the first. Producer should disambiguate via bundle.name; we
        # surface the duplication via the index ignoring the second so
        # the renderer doesn't double-paint an edge.
        pass
    else:
        index[key] = bundle
    # First-wins on the interface-instance key too, mirroring the edge
    # index — two bundles claiming the same interface instance is a
    # producer bug; the renderer paints the first deterministically.
    if bundle.interface_instance and bundle.interface_instance not in iface_index:
        iface_index[bundle.interface_instance] = bundle
    for child in bundle.children:
        _index_bundle(child, index, iface_index)


def _opt_str(payload: dict, key: str) -> str | None:
    """Read an optional string field; None when absent or not a string."""
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _require_str(payload: dict, key: str, source_path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise AxiPerfAnnotationsError(
            f"{source_path}: missing or non-string field {key!r}"
        )
    return value


def _require_int(payload: dict, key: str, source_path: Path) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise AxiPerfAnnotationsError(
            f"{source_path}: missing or non-int field {key!r}"
        )
    return value


def _require_float(payload: dict, key: str, source_path: Path) -> float:
    value = payload.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AxiPerfAnnotationsError(
            f"{source_path}: missing or non-numeric field {key!r}"
        )
    return float(value)


def _require_dict(payload: dict, key: str, source_path: Path) -> dict:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise AxiPerfAnnotationsError(
            f"{source_path}: missing or non-object field {key!r}"
        )
    return value


def _major(version: str) -> int:
    try:
        return int(version.split(".", 1)[0])
    except (ValueError, IndexError):
        raise AxiPerfAnnotationsError(
            f"schema_version {version!r} is not in MAJOR.MINOR form"
        ) from None
