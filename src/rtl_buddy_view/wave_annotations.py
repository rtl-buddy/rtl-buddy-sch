"""Phase 8 wave-snapshot overlay loader.

Reads a VCD waveform file and a sample time, joins per-port values
onto the hierarchy graph by hierarchy-suffix matching, and exposes
the result as a :class:`WaveMap` for the JSON renderer. Mirrors the
shape of :class:`rtl_buddy_view.annotations.DomainMap` / the reset
counterpart — every overlay's loader returns a frozen dataclass the
renderer queries by node path.

Spec input format on the CLI:

  --overlay wave=foo.vcd:12500ns
  --overlay wave=foo.vcd:end           # sample at last record
  --overlay wave=foo.vcd               # short-form, defaults to end

The ``<path>:<time>`` form splits on the LAST ``:`` in the string,
which makes filenames containing ``:`` work as long as the trailing
component is a valid time spec. Filenames whose last component
*looks* like a time spec but isn't can use the ``end`` sentinel or
:option:`--overlay wave=path` (no time, defaults to end).

Join convention: each VCD signal carries a full hierarchy path
(``tb.dut.u_ff.clk``). For each node + port we ask "is there a VCD
signal whose path ends with ``<node.id>.<port.name>``?". When
multiple match (rare — happens with one-letter port names in
multi-instance designs) we pick the shortest path, on the principle
that the user's design top is closer to the leaf than the testbench
wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rtl_buddy_view._vcd_reader import (
    VcdParseError,
    VcdSnapshot,
    parse_time_spec,
    sample_vcd,
)


class WaveAnnotationsError(ValueError):
    """Raised on bad CLI spec / unreadable file / malformed VCD.

    Distinct from :class:`VcdParseError` so the CLI can attach the
    overlay-name prefix in its uniform error path; the message body
    is preserved verbatim.
    """


#: Current consumer-side schema version. Bumping the major is a
#: breaking change for downstream tools that read ``overlay_meta.wave``.
SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class WavePortSample:
    """One per-port sample emitted onto ``node.overlays.wave.ports[]``."""

    name: str
    """Port name within the node — matches ``node.ports[].name``."""

    value: str
    """SV-literal value string. Binary by default for bit vectors
    (``"1010"``), single character for scalars (``"0"``, ``"x"``).
    Special prefixes ``r<float>`` and ``s<string>`` pass through for
    real / string variables."""


@dataclass(frozen=True)
class WaveMap:
    """Parsed Phase-8 wave snapshot keyed by view-side node path.

    The renderer queries :meth:`ports_for` per node and emits each
    port's value into ``node.overlays.wave.ports[]``. Empty maps
    (no signals matched any node) leave ``overlays.wave`` absent
    from the rendered output — same graceful-degradation contract
    as the clock / reset overlays.
    """

    schema_version: str
    t_fs: int
    source_file: str
    timescale_fs: int
    # Index built at load time: hier_path → value. Renderer joins
    # per-(node, port) via suffix matching.
    signals: dict[str, str] = field(default_factory=dict)
    # Set of every declared variable in the VCD, regardless of whether
    # it transitioned before the sample point. Used so the renderer
    # can distinguish "declared but never wrote" (legitimate ``x``)
    # from "not in the file at all".
    declared: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_empty(self) -> bool:
        return not self.signals

    def find_for_port(self, node_id: str, port_name: str) -> str | None:
        """Find the VCD value for ``<node_id>.<port_name>``.

        The view's ``node.id`` is design-relative (e.g.
        ``counter.u_ff``) but a real-world VCD wraps the design under
        a testbench (``tb.dut.u_ff.q``). To bridge the two we walk
        the node path from leaf toward the root, trying successively
        shorter suffixes:

        * ``counter.u_ff.q`` → ``u_ff.q`` → ``q``

        At each step we ask "does any VCD signal's hier path end with
        ``.<suffix>`` (or equal it)?". The first non-empty match
        wins; ties at one step break by shortest path (closest to
        leaf), which favours the design subtree over an aliased
        testbench wire.
        """
        segments = node_id.split(".") if node_id else []
        # Walk from the deepest-rooted suffix to the bare port name.
        # The "no segments" case (top-level lookup or empty node_id)
        # falls through to the bare-port-only attempt.
        for start in range(len(segments) + 1):
            tail = segments[start:]
            suffix_path = ".".join(tail + [port_name]) if tail else port_name
            match = self._best_match(suffix_path)
            if match is not None:
                return self.signals[match]
        return None

    def _best_match(self, suffix_path: str) -> str | None:
        """Return the shortest VCD path that equals or ends with
        ``.<suffix_path>``. ``None`` when nothing matches."""
        dot_suffix = f".{suffix_path}"
        best: str | None = None
        for path in self.signals.keys():
            if path == suffix_path or path.endswith(dot_suffix):
                if best is None or len(path) < len(best):
                    best = path
        return best


def parse_wave_overlay_spec(raw_spec: str) -> tuple[Path, str]:
    """Split the ``--overlay wave=`` argument into ``(path, time_spec)``.

    Accepts either ``<path>`` (defaults to ``end``) or
    ``<path>:<time_spec>``. The split is on the LAST ``:`` so
    filenames containing colons work as long as they don't end in
    something that looks like a valid time spec.
    """
    s = raw_spec.strip()
    if not s:
        raise WaveAnnotationsError("empty --overlay wave= value.")
    # Last colon — but ONLY if the trailing component looks like a
    # time spec or the literal ``end``. Otherwise treat the whole
    # string as the path (the user probably named their file with a
    # colon).
    last_colon = s.rfind(":")
    if last_colon > 0:
        trailing = s[last_colon + 1 :].strip()
        try:
            parse_time_spec(trailing)
            return Path(s[:last_colon]), trailing
        except VcdParseError:
            # Trailing isn't a time spec → whole thing is the path,
            # default to end.
            return Path(s), "end"
    return Path(s), "end"


def load_wave_map(spec: str) -> WaveMap:
    """Top-level loader the overlay class delegates to.

    ``spec`` is the raw ``--overlay wave=<spec>`` value the CLI hands
    in. Splits into ``(path, time_spec)``, parses both, samples the
    VCD, and packages the result.

    Raises :class:`WaveAnnotationsError` on any failure — the CLI
    catches it via the uniform overlay-loader error path.
    """
    path, time_spec = parse_wave_overlay_spec(spec)
    try:
        target_fs = parse_time_spec(time_spec)
    except VcdParseError as exc:
        raise WaveAnnotationsError(str(exc)) from exc
    if not path.exists():
        raise WaveAnnotationsError(f"waveform file not found: {path}")
    try:
        snap = sample_vcd(path, target_fs)
    except VcdParseError as exc:
        raise WaveAnnotationsError(str(exc)) from exc
    return _snapshot_to_map(snap, path)


def _snapshot_to_map(snap: VcdSnapshot, path: Path) -> WaveMap:
    return WaveMap(
        schema_version=SCHEMA_VERSION,
        t_fs=snap.t_fs,
        source_file=str(path),
        timescale_fs=snap.timescale_fs,
        signals=dict(snap.signals),
        declared=frozenset(snap.declared),
    )
