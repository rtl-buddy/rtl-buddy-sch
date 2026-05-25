"""Pure-Python VCD reader for the Phase 8 wave snapshot overlay.

Reads a Value Change Dump file, walks to a target time T, and returns
the last-known value of every variable at-or-before T. Designed for
the offline-snapshot use case — single sample point, no time-series
output, no continuous streaming. The wave overlay
(:mod:`rtl_buddy_view.overlays.wave`) is the only consumer.

Why not pyvcd / pylibfst:

* pyvcd is read-write but its reader path is uncommon; bringing it
  in just for one sample point would add a third-party dep and a
  packaging concern (rtl-buddy-view stays at two runtime deps today:
  typer + jsonschema).
* pylibfst requires a native libfst build, which we don't want to
  inflict on a default install. FST support is a Phase-8 follow-up;
  v1 ships VCD only.

The parser covers the subset of the VCD spec our fixtures + typical
simulator outputs (verilator, iverilog, modelsim) emit — header
sections, ``$timescale``, nested ``$scope module`` / ``$upscope``,
``$var <type> <width> <id> <name>``, ``$enddefinitions``, scalar
value changes (``0!``, ``1!``, ``x!``, ``z!``), and vector changes
(``b<bits> <id>``). Real-number changes (``r<float> <id>``) and
strings (``s<string> <id>``) parse but render as a passthrough
literal. Arrays / structs aren't modelled; producers serialise them
to per-bit signals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class VcdParseError(ValueError):
    """Raised on malformed input.

    The CLI catches this and surfaces ``overlay wave: <message>`` to
    the user — distinct from a generic ``ValueError`` so the wave
    overlay's loader can attach extra context without colliding with
    other overlay errors.
    """


#: Power-of-ten multipliers from each unit to fs. Anything outside this
#: set in a VCD ``$timescale`` is rejected — femtosecond resolution is
#: the canonical hub-side encoding so a sub-fs spec would lose precision
#: silently downstream.
_UNIT_TO_FS: dict[str, int] = {
    "fs": 1,
    "ps": 1_000,
    "ns": 1_000_000,
    "us": 1_000_000_000,
    "ms": 1_000_000_000_000,
    "s": 1_000_000_000_000_000,
}


# Time spec for CLI / overlay use. ``end`` is the sentinel for
# "sample at the last recorded time in the file".
_TIMESPEC_RE = re.compile(
    r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>fs|ps|ns|us|ms|s)\s*$",
    re.IGNORECASE,
)


def parse_time_spec(spec: str) -> int | None:
    """Convert ``"12500ns"`` / ``"100us"`` / ``"end"`` to fs.

    Returns the time in femtoseconds, or ``None`` for the ``end``
    sentinel (the reader expands that to the last recorded time at
    sample time, when it actually knows the file's tail).

    Raises :class:`VcdParseError` on garbage input.
    """
    if spec.strip().lower() == "end":
        return None
    m = _TIMESPEC_RE.match(spec)
    if m is None:
        raise VcdParseError(
            f"unrecognised time spec {spec!r}; expected NUMBER+UNIT "
            f"(e.g. 12500ns, 1.5us) or the literal 'end'."
        )
    num = float(m.group("num"))
    unit = m.group("unit").lower()
    return int(round(num * _UNIT_TO_FS[unit]))


@dataclass(frozen=True)
class VcdSnapshot:
    """Result of sampling a VCD at a specific time.

    * ``t_fs`` — the time the sample was taken at, in fs. May not equal
      the user's requested time exactly: the parser walks change records
      and stops when the next ``#T`` would exceed the target, so the
      returned ``t_fs`` is the highest in-range change point. (When the
      user asked for ``end``, this is the last record in the file.)
    * ``signals`` — ``hier_path → SV-literal value``. Values use the
      surfer / handle_bits convention (binary literal characters,
      including ``x`` / ``z``) so the renderer can format consistently
      with the live ``wave_values_changed`` path.
    * ``timescale_fs`` — the file's tick→fs multiplier. Useful for
      tests + error messages; not consumed by the overlay.
    * ``declared`` — every variable seen in a ``$var`` declaration,
      including ones with no transitions before ``t_fs``. The overlay
      paints ``??`` (or skips) for these.
    """

    t_fs: int
    signals: dict[str, str]
    timescale_fs: int = 1
    declared: set[str] = field(default_factory=set)


def sample_vcd(path: Path, target_fs: int | None) -> VcdSnapshot:
    """Read ``path`` and return the values of every variable at
    ``target_fs`` (in femtoseconds). When ``target_fs`` is ``None``,
    sample at the file's last recorded time.

    Raises :class:`VcdParseError` on malformed input. Files with no
    ``$var`` declarations raise rather than returning an empty
    snapshot — silent emptiness almost always indicates the user
    pointed at the wrong file.
    """

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return _parse(fh.read(), target_fs)


def _parse(text: str, target_fs: int | None) -> VcdSnapshot:
    # Two-phase parser. Phase 1 walks $-keyword blocks for the header
    # (timescale, $scope, $var, …) until ``$enddefinitions $end``.
    # Phase 2 walks the body line-by-line for ``#T`` timestamps and
    # value-change records. Splitting on $end inside the header tokens
    # lets us collapse multi-line blocks ($comment, $version, …) into
    # a single bracketed unit.
    timescale_fs = 1
    # VCD's id-code is a (1:N) reference: a single id can be declared
    # in multiple $var lines when the same wire is exposed under
    # several hierarchy paths (e.g. tb.dut.clk + tb.dut.u_ff.clk both
    # bound to the same testbench-side wire). Aliasing is part of the
    # spec, so we track every path each id resolves to.
    id_to_paths: dict[str, list[str]] = {}
    id_to_width: dict[str, int] = {}
    declared: set[str] = set()
    scope_stack: list[str] = []

    # Split into tokens for the header pass, then locate the boundary.
    # The body starts right after the ``$end`` that closes
    # ``$enddefinitions``.
    end_def_idx = text.find("$enddefinitions")
    if end_def_idx < 0:
        raise VcdParseError("no $enddefinitions block found; not a VCD file?")
    # Step past ``$enddefinitions`` itself before scanning for the
    # closing ``$end`` — ``$enddefinitions`` is a prefix of ``$end``
    # so a naive ``find('$end', end_def_idx)`` lands on the keyword
    # itself and the body / header split collapses.
    after_end_def = text.find("$end", end_def_idx + len("$enddefinitions"))
    if after_end_def < 0:
        raise VcdParseError("$enddefinitions present but not closed by $end.")
    header_text = text[: after_end_def + len("$end")]
    body_text = text[after_end_def + len("$end") :]

    header_tokens = header_text.split()
    i = 0
    n = len(header_tokens)
    while i < n:
        tok = header_tokens[i]
        if tok == "$timescale":
            block, j = _block_to_end(header_tokens, i + 1)
            if not block:
                raise VcdParseError("empty $timescale block.")
            num, unit = _parse_timescale_block(block)
            timescale_fs = num * _UNIT_TO_FS[unit]
            i = j
        elif tok == "$scope":
            block, j = _block_to_end(header_tokens, i + 1)
            # ``$scope module <name> $end`` — the last word is the name.
            if not block:
                raise VcdParseError("$scope block missing name.")
            scope_stack.append(block[-1])
            i = j
        elif tok == "$upscope":
            _, j = _block_to_end(header_tokens, i + 1)
            if scope_stack:
                scope_stack.pop()
            i = j
        elif tok == "$var":
            block, j = _block_to_end(header_tokens, i + 1)
            # ``$var <type> <width> <id> <name> [<slice>] $end``
            if len(block) < 4:
                raise VcdParseError(f"$var with too few fields: {block!r}")
            try:
                width = int(block[1])
            except ValueError as exc:
                raise VcdParseError(f"non-integer width in $var: {block!r}") from exc
            vcd_id = block[2]
            name = block[3]
            hier_path = ".".join(scope_stack + [name])
            id_to_paths.setdefault(vcd_id, []).append(hier_path)
            # Width should be consistent across aliased paths; the
            # spec doesn't allow conflicting widths on the same id.
            # We trust the first declaration.
            id_to_width.setdefault(vcd_id, width)
            declared.add(hier_path)
            i = j
        elif tok == "$enddefinitions":
            _, j = _block_to_end(header_tokens, i + 1)
            i = j
            # End of header; body comes from ``body_text``.
            break
        elif tok.startswith("$"):
            # $comment / $date / $version / inline $dumpvars in header,
            # anything else we don't model — drain to matching $end.
            _, j = _block_to_end(header_tokens, i + 1)
            i = j
        else:
            # Bare token in the header (whitespace, stray identifier
            # from a malformed file). Skip.
            i += 1

    if not declared:
        raise VcdParseError(
            "no $var declarations found before $enddefinitions; is this "
            "really a VCD file?"
        )

    # ----- body pass --------------------------------------------------
    #
    # We track the current value per id and the latest fully-applied
    # timestamp. A line beginning with ``#`` is a timestamp marker; a
    # change record applies on top of the prior timestamp. We stop at
    # the first ``#T`` whose value exceeds the target.
    current: dict[str, str] = {}
    last_emitted_fs = 0

    for raw in body_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("$"):
            # $dumpvars / $dumpall / $dumpon / $dumpoff blocks wrap a
            # batch of value changes. We process the inner records as
            # usual on subsequent iterations; the $-keywords + their
            # trailing $end are no-ops at this layer.
            continue
        if line[0] == "#":
            try:
                tick = int(line[1:])
            except ValueError as exc:
                raise VcdParseError(f"non-integer timestamp on line {line!r}") from exc
            new_t_fs = tick * timescale_fs
            if target_fs is not None and new_t_fs > target_fs:
                break
            last_emitted_fs = new_t_fs
            continue
        _apply_change(line, id_to_paths, id_to_width, current)

    if target_fs is None:
        t_fs = last_emitted_fs
    else:
        t_fs = min(target_fs, last_emitted_fs)

    # Expand id-keyed `current` to all aliased hierarchy paths.
    signals: dict[str, str] = {}
    for ident, value in current.items():
        for path in id_to_paths.get(ident, ()):
            signals[path] = value

    return VcdSnapshot(
        t_fs=t_fs,
        signals=signals,
        timescale_fs=timescale_fs,
        declared=declared,
    )


def _apply_change(
    line: str,
    id_to_paths: dict[str, list[str]],
    id_to_width: dict[str, int],
    current: dict[str, str],
) -> None:
    ch0 = line[0]
    if ch0 in "01xzXZuU-lLhHwW":
        value = ch0.lower()
        ident = line[1:]
        if ident in id_to_paths:
            current[ident] = value
        return
    if ch0 in "bB":
        sp = line.find(" ")
        if sp < 0:
            return
        bits = line[1:sp]
        ident = line[sp + 1 :]
        if ident in id_to_paths:
            w = id_to_width[ident]
            # VCD permits truncated leading zeros; pad to declared
            # width so the consumer can render fixed-width hex without
            # re-deriving the bit count.
            if len(bits) < w:
                pad = bits[0] if (bits and bits[0] in "xzXZ") else "0"
                bits = bits.rjust(w, pad)
            elif len(bits) > w:
                bits = bits[-w:]
            current[ident] = bits.lower()
        return
    if ch0 in "rR":
        sp = line.find(" ")
        if sp < 0:
            return
        real_lit = line[1:sp]
        ident = line[sp + 1 :]
        if ident in id_to_paths:
            current[ident] = f"r{real_lit}"
        return
    if ch0 in "sS":
        sp = line.find(" ")
        if sp < 0:
            return
        str_lit = line[1:sp]
        ident = line[sp + 1 :]
        if ident in id_to_paths:
            current[ident] = f"s{str_lit}"


def _block_to_end(tokens: list[str], start: int) -> tuple[list[str], int]:
    """Read tokens from ``start`` until ``$end``; return ``(words, idx_after)``.

    ``idx_after`` points past the matched ``$end`` so the caller can
    advance ``i = idx_after`` and continue.
    """
    out: list[str] = []
    j = start
    while j < len(tokens):
        if tokens[j] == "$end":
            return out, j + 1
        out.append(tokens[j])
        j += 1
    raise VcdParseError("reached EOF inside a $-block (missing $end).")


def _parse_timescale_block(words: list[str]) -> tuple[int, str]:
    """Pull ``(num, unit)`` from a ``$timescale`` block's words.

    Tolerates the unit being a separate token (``1 ns``) or fused
    (``1ns``).
    """
    blob = "".join(words)
    m = _TIMESPEC_RE.match(blob)
    if m is None:
        raise VcdParseError(f"unrecognised $timescale: {blob!r}")
    return int(round(float(m.group("num")))), m.group("unit").lower()
