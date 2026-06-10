# Writing an overlay for rtl-buddy-view

This guide is for anyone shipping a third-party overlay package
against the rtl-buddy-view plugin surface — coverage, area, power,
waveform, or anything else that wants to layer per-instance metadata
on top of the hierarchy view.

It is not a tour of the existing built-ins (clock + reset live in
[`src/rtl_buddy_view/overlays/`](../src/rtl_buddy_view/overlays/)
and the README documents how to *use* them); it is the contract you
implement so the analyzer can discover and dispatch your overlay
like a first-class one.

> Phase 4 (rtl-buddy-view#17) defines the plugin protocol +
> `--overlay name=path` CLI. Entry-point discovery (#46) lets
> external packages register without modifying this repo.

## 1. What an overlay is

An overlay is a small Python class with three responsibilities:

1. **Load** a JSON (or other) artefact from a path the user passed
   on the command line.
2. **Join** that data onto the hierarchy graph (optional — the
   built-ins are no-ops; the seam exists for future overlays that
   want to mutate nodes).
3. **Contribute** rendered output to one or more renderers
   (optional — Phase 4 renderers reach into the payload directly).

Concretely: it implements
[`rtl_buddy_view.overlays.Overlay`](../src/rtl_buddy_view/overlays/__init__.py),
a `runtime_checkable` `Protocol`. You don't inherit from it; the
registry calls `isinstance(plugin, Overlay)` to structurally validate
your class.

## 2. Minimal example

```python
# my_overlay_pkg/phys_overlay.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AreaMap:
    schema_version: str
    per_instance: dict[str, float]  # instance_path -> area (um^2)


class PhysOverlay:
    name = "phys"
    schema_version = "1.0"

    def load(self, path: Path) -> AreaMap:
        import json
        data = json.loads(path.read_text())
        return AreaMap(
            schema_version=data["schema_version"],
            per_instance=data["per_instance"],
        )

    def join(self, graph, annotation) -> None:
        # No-op for now; renderers read AreaMap directly.
        return None

    def contribute(self, ctx) -> None:
        # No-op for now; same reason as above.
        return None
```

Once registered (§3), users invoke it with:

```
rtl-buddy-view --top top --filelist files.f \
    --overlay phys=path/to/area.json
```

And see it in `--list-overlays`:

```
clock     1.0    (built-in)
coverage  1.0    (built-in)
phys      1.0    (my-overlay-pkg)
reset     1.0    (built-in)
```

## 3. Registering via entry points

Package the overlay and declare the entry-point group
`rtl_buddy_view.overlays`. With `pyproject.toml`:

```toml
[project]
name = "my-overlay-pkg"
version = "0.1.0"
dependencies = ["rtl-buddy-view>=0.1"]

[project.entry-points."rtl_buddy_view.overlays"]
phys = "my_overlay_pkg.phys_overlay:PhysOverlay"
```

The entry-point value resolves to a no-arg callable — almost always
the class itself, which Python instantiates with `()`. Returning an
instance from a factory function works too if you need configurable
construction.

After `pip install` (or `uv pip install -e .`), the next
`rtl-buddy-view --list-overlays` invocation surfaces the overlay
with `(my-overlay-pkg)` as the source tag.

## 4. The `Overlay` protocol in detail

```python
class Overlay(Protocol):
    name: str
    schema_version: str

    def load(self, path: Path): ...
    def join(self, graph: HierNode, annotation) -> None: ...
    def contribute(self, ctx) -> None: ...
```

### `name`

Lower-snake-case-or-shorter token. Must be **stable** — it is the
user-facing identifier (`--overlay name=path`), the key under
`overlays_present` and `node.overlays` in
[`view.json`](view-json-v1.md), and the column in
`--list-overlays`. Renaming it is a breaking change.

Pick something that describes the *domain*, not the *analysis*.
The built-ins are `clock` and `reset` (the domains the user
thinks in), not `cdc` and `rdc` (the crossing analyses); the
Phase-6 built-in is `coverage`, not `cov` or `lcov`.

Collisions with a built-in name are detected at registry build
time and resolved in favour of the built-in, with a stderr
warning naming your package. Don't use a built-in name
(`clock`, `clock-tb`, `reset`, `coverage`, `wave`, `axi-perf`).

### `schema_version`

A `MAJOR.MINOR` string describing the JSON artefact your `load`
consumes — not the overlay code. Bump the major when a previously
required field changes type or disappears; bump the minor when
you add optional fields. The analyzer treats this as opaque
metadata and surfaces it in `--list-overlays` for diagnostics.

### `load(path: Path)`

Parse the user-supplied artefact and return your payload — almost
always a frozen dataclass. The registry doesn't introspect the
returned object; it hands it back to `join` / `contribute` and to
any renderer that knows how to consume your overlay specifically.

Raise *your own* exception subclass on failure. The CLI catches
loader exceptions per overlay and reports them as
`overlay <name>: <message>` — so make the message a complete
sentence ("expected key 'per_instance' in coverage map, got ['foo']").

### `join(graph, annotation)`

A hook for overlays that need to decorate the hierarchy graph
before renderers run. The built-ins are no-ops because their
renderers reach directly into the payload (`predominant_clock` /
`flop_reset` lookups). If your overlay needs to add per-node
state that several renderers consume, this is where to do it —
otherwise return `None`.

### `contribute(ctx)`

A hook for overlays that need to emit rendered output (badges,
legend entries, edge styling) into a renderer's context. Phase 4
renderers don't use it for the built-ins; the seam is reserved
for overlays that participate in rendering more actively. The
`ctx` type is renderer-specific; consult the target renderer's
source.

## 5. Composition with built-ins

Multiple `--overlay` flags compose naturally — every renderer is
already structured around the *graceful-degradation* contract:
running without an overlay produces the same output as the Phase 1
baseline. So `--overlay clock=... --overlay coverage=...` works
without any code change on your side.

If your overlay's metadata is meant to *combine* with another (e.g.
coverage-tinted nodes that still want a clock-colored border), use
distinct visual axes — color vs. border, fill vs. pattern, suffix
vs. prefix. The clock/reset built-ins reserve fill color and reset
suffix respectively; pick something else.

## 5a. Built-in: `clock-tb` (TB-context clock + reset, issue #99 / phase 6e)

When the renderer is invoked in TB-rooted mode (`--tb-top <module>`),
the DUT-side clock domain map produced by rtl-buddy-cdc only covers
the DUT subtree — SDC has no concept of testbench scope. Phase 6d
surfaces this with a `(no clock map above DUT)` legend footnote;
phase 6e adds an opt-in upgrade: a hand-authored `tb_clock_map.json`
that names each TB-level clock + the instance paths it drives.

```bash
rtl-buddy-view --top my_dut --tb-top my_tb \
  --filelist tb_plus_dut.f \
  --overlay clock=dut_domain_map.json \
  --overlay clock-tb=tb_clock_map.json
```

File shape:

```json
{
  "rtl-buddy-filetype": "tb_clock_map",
  "schema_version": "1.0",
  "clocks": [
    {
      "name": "main_clk",
      "drives": [
        "tb_top.u_clkgen",
        "tb_top.u_driver",
        "tb_top.u_dut.clk"
      ],
      "period_ns": 10
    }
  ],
  "resets": [
    {
      "name": "por_rst_n",
      "drives": ["tb_top.u_rstgen", "tb_top.u_dut.rst_n"],
      "active_low": true
    }
  ]
}
```

Merge rules (pinned by [#99](https://github.com/rtl-buddy/rtl-buddy-view/issues/99)
under "TB-context overlay story"):

- **TB-side fills outside DUT.** Drives that don't sit inside any
  instance whose module matches `--top` become synthetic
  per-flop entries on the merged `DomainMap`; the renderer's existing
  predominant-clock walk then tints the TB scopes.
- **DUT-side wins inside DUT.** Drives that sit inside (or at) any
  DUT instance are skipped with a stderr warning — the DUT's
  rtl-buddy-cdc-derived map is the authority for in-DUT flops.
- **Both can populate the boundary clock.** Naming the same clock
  on both sides (e.g. `tb.dut.clk` in `tb_clock_map.json` AND in
  the DUT's `domain_map.json`) is the supported way to keep the
  color consistent across the boundary; mismatches surface as a
  warning, not a fatal error.

Resets follow the same merge contract via `resets[]` with
`active_low:`.

The `clock-tb` overlay is independent of `clock` — supply it on its
own (no DUT-side analysis available) and the TB-side entries still
tint TB scopes; the DUT subtree just stays neutral. Combine with
`--overlay clock=...` for full coverage.

## 6. Stability guarantees

`Overlay`, `Annotation`, `OverlayRegistry`, `default_registry`,
`OverlayError`, and the `ENTRY_POINT_GROUP` constant are the
public surface of `rtl_buddy_view.overlays`. They will not change
in incompatible ways within a major version of rtl-buddy-view.

Per-renderer integration (how `dot.py` / `json_render.py` reach
into your payload) is **not** stable — those modules iterate and
get reworked. If your overlay needs to participate in rendering,
do it via `contribute()` once a renderer exposes the right hook;
don't depend on private renderer internals.

## 7. Diagnostics

A broken overlay must never block the user from rendering. Three
failure modes are caught at registry build time and converted to
stderr warnings:

| Failure                                  | Message form (stderr)             |
| ---------------------------------------- | --------------------------------- |
| `ep.load()` raises (import-time error)   | `failed to import ...`            |
| factory raises on instantiation          | `failed to instantiate ...`       |
| factory returned a non-`Overlay`          | `... not an Overlay`              |
| name collides with a built-in or another | `... is shadowed ...; skipped`    |

Each warning names the entry-point name and the distribution
package, so users can pinpoint which `pip install` introduced the
problem and either fix it or `pip uninstall` the offending package.

## 8. Testing your overlay

`OverlayRegistry` is constructable in isolation — instantiate one,
register your class, and exercise the dispatch path without
running the full analyzer. The built-in test patterns in
[`tests/test_overlays_registry.py`](../tests/test_overlays_registry.py)
and
[`tests/test_overlay_entry_points.py`](../tests/test_overlay_entry_points.py)
are good models for your own tests:

```python
from io import StringIO
from rtl_buddy_view.overlays import OverlayRegistry

def test_my_overlay_loads_a_known_payload(tmp_path):
    registry = OverlayRegistry()
    registry.register(CoverageOverlay(), source="my-overlay-pkg")
    map_file = tmp_path / "cov.json"
    map_file.write_text('{"schema_version": "1.0", "per_instance": {}}')

    overlay = registry.get("coverage")
    payload = overlay.load(map_file)

    assert payload.schema_version == "1.0"
```

For an end-to-end check that entry-point discovery wires through,
monkeypatch `rtl_buddy_view.overlays.entry_points` with a fake list
of `EntryPoint`s — again, see `test_overlay_entry_points.py` for
the pattern.

## 9. Worked examples in tree

- [`overlays/clock.py`](../src/rtl_buddy_view/overlays/clock.py) —
  thin wrapper over the Phase 2 clock-domain-map loader.
- [`overlays/reset.py`](../src/rtl_buddy_view/overlays/reset.py) —
  thin wrapper over the Phase 3 reset-domain-map loader.

Both are minimal (load + two no-op hooks) and demonstrate the
"renderers read the payload directly" pattern. A future coverage
or physical overlay can follow the same shape until/unless it
needs the `join` / `contribute` hooks.

## 10. Related docs

- [`view-json-v1.md`](view-json-v1.md) — locked schema for the
  JSON output your overlay's data will land in (under
  `node.overlays.<name>` / `edge.overlays.<name>`).
- [`hub-protocol.md`](hub-protocol.md) — the hub event schema your
  overlay's selections will travel through if it ever needs to
  bridge schematic ↔ waveform ↔ source.
- README — user-facing `--overlay name=path` quick reference.
