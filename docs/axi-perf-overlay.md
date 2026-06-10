# AXI performance overlay (Phase 11)

The axi-perf overlay consumes the `axi-perf.json` artefact produced by
[rtl-buddy-axi-profiler](https://github.com/rtl-buddy/rtl-buddy-axi-profiler)
and surfaces per-bundle AXI4 / AXI4-Lite performance — throughput,
AR→R latency, per-channel utilization + backpressure, outstanding
depth, and interconnect arbitration fairness — alongside the
rtl-buddy-view hierarchy. The headline as-built fact: perf data lives
in a **dedicated AXI Performance tab** in the SPA, not as per-edge
styling on the hierarchy graph. Per-edge styling was the original
[#60](https://github.com/rtl-buddy/rtl-buddy-view/issues/60) plan; it
was removed in [#69](https://github.com/rtl-buddy/rtl-buddy-view/issues/69)
and the bundle-to-node join was reworked onto the interface-port
mechanism in [#114](https://github.com/rtl-buddy/rtl-buddy-view/issues/114).

This page is the operator's guide — what each leg of the pipeline does,
how to produce and load a map, what the tab shows, and how a bundle
finds its node in the hierarchy.

## Architecture

```
rtl-buddy-axi-profiler            rtl-buddy-view                          viewer (SPA)
   │                                  │                                       │
   │ axi-profiler run                 │                                       │
   │   → axi-perf.json (v1)           │                                       │
   │                                  │                                       │
   └────── axi-perf.json ────────────►│ load_axi_perf_map(path)               │
                                      │   → AxiPerfMap (frozen)               │
                                      │     · _bundles_by_edge                │
                                      │     · _bundles_by_interface_instance  │
                                      │          │                            │
                                      │   json_render.render(axi_perf_map=…,  │
                                      │                      axi_perf_source=…)│
                                      │     · nodes[].overlays['axi-perf']    │
                                      │         (interconnect + bundle_pins)  │
                                      │     · edges[].overlays['axi-perf']    │
                                      │         (legacy edge block)           │
                                      │     · top-level axi_perf source block │
                                      │          │                            │
                                      │          └──── view.json ────────────►│  AxiPerfView.vue
                                      │                                       │   (AXI Performance tab)
                                      │   dot.render(axi_perf_map=…)          │  overlays/axi_perf.js
                                      │     → del axi_perf_map (ignored)      │   (schematic heatmap)
```

Three things to note up front:

- The loader validates the schema version on load, so an incompatible
  producer fails loudly instead of silently misrendering
  (`axi_perf_annotations.py`).
- `dot.render` accepts the `axi_perf_map` keyword purely for CLI
  plumbing uniformity with `json_render` — it `del`s the map before
  writing any output. DOT edges are parent→child by hierarchy, while
  AXI bundles connect siblings DOT does not draw as edges, so **no DOT
  edge is ever styled by perf data**.
- Renderers don't go through `join()` / `contribute()`. They reach
  into the `AxiPerfMap` directly via its lookup methods
  (`bundle_at_edge`, `bundle_at_interface_instance`, `interconnect_at`,
  `iter_bundles`) at render time — the same direct-lookup pattern the
  clock and reset built-ins use.

## Design note: the #69 / #114 pivot

The original [#60](https://github.com/rtl-buddy/rtl-buddy-view/issues/60)
plan was per-edge styling on hierarchy edges: stroke-width and colour
keyed by `(master_path, slave_path)`, with backpressure painting an
edge red. That plan was abandoned; this section records why, so the
as-built shape is unambiguous:

- **[#69](https://github.com/rtl-buddy/rtl-buddy-view/issues/69) moved
  perf into a dedicated tab.** Per-edge styling was dropped as the
  primary surface. AXI perf now renders in `AxiPerfView.vue`; the only
  thing painted on the schematic is an append-only at-a-glance heatmap
  (`overlays/axi_perf.js`) — pin outlines + a throughput badge in
  block-flow view, and a single aggregate node badge in hierarchy
  view, both coloured by peak backpressure.
- **[#114](https://github.com/rtl-buddy/rtl-buddy-view/issues/114)
  unified the join onto the interface-port / tb-top mechanism.** A
  bundle no longer attaches by drawing a parallel master↔slave edge; it
  attaches as a synthesized interface pin on the real `tb_top→dut`
  node — the instance that actually owns the AXI ports — built from the
  producer manifest's `signals:` description. No profiler-specific
  `system_view.sv` stub is required.

The two consequences in source are deliberate, not unfinished work:

- `AxiPerfOverlay.contribute()` and `join()` are intentional no-ops.
  The edge-contribution framework amendment once mooted for #60 (so
  `contribute(ctx)` could write to `ctx.edges[i]`) was never needed —
  renderers read the `AxiPerfMap` directly and there is no per-edge
  styling to contribute. This is the as-built design, not deferred
  work.
- The DOT renderer accepts the `axi_perf_map` keyword only for CLI
  uniformity with `json_render`; it `del`s the map and never styles an
  edge with it (`render/dot.py`).

## Quick start

Produce the map with the profiler, then load it into the view.

```bash
# 1. produce axi-perf.json (standalone profiler CLI)
axi-profiler run \
    --filelist design.f --top axi_2x2 \
    --input dump.fst \
    --output axi-perf.json

# 2. render the hierarchy with the overlay attached
uv run rtl-buddy-view \
    --top axi_2x2 --filelist design.f \
    --overlay axi-perf=axi-perf.json
```

The integrated entry point is `rb axi-profile run <test>` (rtl_buddy
wraps the standalone profiler and fills `--tb-prefix` from the test's
testbench name in `tests.yaml`). The default artefact name is
`axi-perf.json`.

The overlay ships as a first-class built-in, so it appears in
`--list-overlays` labelled `built-in` (alongside the other built-ins
`clock`, `clock-tb`, `reset`, and `wave`):

```bash
uv run rtl-buddy-view --list-overlays
# axi-perf  1.0    (built-in)
# clock     1.0    (built-in)
# clock-tb  1.0    (built-in)
# reset     1.0    (built-in)
# wave      1.0    (built-in)
```

To see the metrics in the SPA, serve a `view.json` produced with
`--overlay axi-perf=…` and open the **AXI Performance** tab. With no
axi-perf data present, the tab shows a *No AXI performance data* empty
state prompting you to reload the view with the overlay attached.

> **Name vs key skew.** The user-facing token is hyphenated `axi-perf`
> (the `--overlay` flag, the `overlays_present` entry, the SPA tab id).
> The per-node / per-edge `view.json` metadata key is also hyphenated
> `axi-perf` (`node.overlays['axi-perf']`, `edge.overlays['axi-perf']`).
> The internal renderer / CLI parameter is underscored `axi_perf_map`,
> and the top-level `view.json` source block key is underscored
> `axi_perf` — don't conflate the overlay-name key with those.

## What the AXI tab shows

`AxiPerfView.vue` is a two-pane view — a 280 px bundle list on the
left, a bundle (or interconnect) detail pane on the right — shown only
when the loaded map carries bundles or interconnects. Otherwise it
renders a *No AXI performance data* empty state.

The **bundle list** shows, per bundle: name, endpoints (`{from} →
{to}`), throughput (`R … · W …`), and `max bp {pct}%` colour-classed
by the worst per-channel backpressure (>15 % red, >5 % amber, else
green). An **Interconnects** section lists each `node_path`, its
hottest `master → slave` pair, and total throughput.

The **detail pane** surfaces:

- **Metadata** — Master, Slave, Protocol (`{protocol} / {data_width}b
  data / {id_width}b id`), and Throughput.
- **Theoretical max** — `data_width × clock frequency`, with the clock
  derived from the top-level `axi_perf.clock_period_ns`. Rendered as
  `…/dir @ {clockMhz} MHz ({utilOfMax}% used)`. This row appears only
  when `clock_period_ns` is a positive number; otherwise it renders
  `—`.
- **Outstanding** — `R peak … avg … · W peak … avg …`.
- **Errors** — `SLVERR … · DECERR …`, coloured red when the sum is
  non-zero.
- **Channel utilization** — a grouped Chart.js bar chart over
  `['AR','AW','R','W','B']` with two series, `util %` (blue) and
  `bp %` (amber). `util_pct` and `bp_pct` are explicit percentages
  0–100; the Y axis is hard-capped at 100.
- **AR → R first-data latency** — in **cycles**: `p50 · p95 · p99 ·
  max`, from `latency_cycles.ar_to_r_first`. (The `aw_to_b` write
  latency exists in the data but is not rendered in the tab.)
- **Latency histogram** — a single-series bar chart over
  `ar_to_r_first.hist_log2`, with power-of-two bucket labels (`2^i`).

For an **interconnect**, the detail pane shows total throughput, the
hottest pair, **Fairness (Jain)** as a single 2-dp numeric value (not a
graphical gauge), and the **Starved** master list when non-empty.

A header **time-window chip** mirrors the marimo notebook's brush
window when one is active. It is purely informational — the SPA never
filters its aggregate stats from it; per-bundle stats are pre-computed
by the producer.

> **Throughput units.** `read_bps` / `write_bps` are **bits** per
> second. The tab's formatter divides by 8 and scales in 1024-based
> byte units (B/s … TB/s). The separate schematic-overlay formatter
> (`overlays/axi_perf.js` badges / tooltips) does *not* divide by 8 and
> uses decimal k/M/G — two formatters, two semantics, same underlying
> values.

## How a bundle joins the hierarchy

`AxiPerfMap` holds two parallel lookup indices, built at load time so
renderers don't repeatedly walk the bundle tree:

- **`_bundles_by_interface_instance`** — the interface-port / tb-top
  mechanism (#114), populated only for bundles that carry the
  `interface_instance` field from the producer's verible-interface
  detector. This is the primary, high-confidence path.
- **`_bundles_by_edge`** — the legacy `(master_path, slave_path)` edge
  match (the superseded #60 mechanism), always populated. Kept intact
  for back-compat; regex-detected bundles that omit the interface
  fields fall back to it.

On the per-node path (`_axi_perf_node_contribution`), each bundle
attaches as a `bundle_pins` entry on the node that owns the ports, in
two passes (`_axi_perf_bundle_pins`):

- **Pass 1 — interface pins.** For each port whose `port_kind ==
  "interface"`, the renderer reconstructs the interface-instance path
  from the DUT port's parent-side bind expression (e.g. node
  `tb.i_dut` binding `slv.Slave` → instance `tb.slv`), looks it up via
  `bundle_at_interface_instance`, and on a hit emits a pin anchored on
  the real parsed interface cell. The `interface_instance` value the
  producer wrote must equal this geometrically-derived key, or Pass 1
  falls through.
- **Pass 2 — manifest-described (synthesized) pins.** The escape hatch
  for designs whose AXI ports the CST cannot see (macro-generated flat
  ports). For any bundle not already matched, if `node.instance_path`
  names the bundle's `slave_path` (or `master_path`) the renderer draws
  a synthesized pin from the manifest description. An ancestor-scope
  guard makes the pin land on the DUT rather than the enclosing
  procedural-TB scope, so a bundle between `tb` and `tb.dut` decorates
  `tb.dut` with `peer = tb`.

Each pin carries `port`, `modport`, `interface_instance`, `role`
(`master`/`slave`/`null`, derived from the modport string), `peer`,
`synthetic`, and the full bundle block — so the SPA paints
colour / width / glyph without re-deriving stats.

Both indices are **first-wins on collision** and silently drop
duplicates — a duplicate edge or duplicate `interface_instance` is
treated as a producer bug, and the renderer keeps the first
deterministically rather than double-painting. A malformed producer
artefact can therefore lose bundles silently.

Graceful degradation matches the rest of the analyzer. `AxiPerfMap.is_empty`
is true when no bundles were emitted (typically because the producer's
discover stage matched nothing); a regex-detected bundle with no
`interface_instance` produces no Pass-1 pin; and a fully-unmatched
bundle produces no pin at all.

> **`iter_bundles` flattens one level only.** It yields each top-level
> bundle then its direct children. Deeper nesting is reachable through
> the edge / interface indices (which recurse fully) but **not**
> through `iter_bundles` — renderers walking it for node attachment
> will miss grandchild bundles.

## Schema + compatibility

The loader is the consumer side of the profiler-owned v1
`axi-perf.json` schema. `SUPPORTED_SCHEMA_MAJOR = 1`, and the guard is
**strict equality on the major**, not `>=`:

- `schema_version` missing or not a string, or not in `MAJOR.MINOR`
  form, raises `AxiPerfAnnotationsError` (a `ValueError` subclass).
- Any `major != 1` is rejected — **both v0.x and v2.x fail loudly**
  with "is not supported (consumer expects 1.x); upgrade
  rtl-buddy-view or downgrade the producing rtl-buddy-axi-profiler."

v1.x is **additive-only** by contract: minor drift is tolerated (extra
fields are ignored). The five v1.x additive interface-identity fields
on each bundle — `interface_instance`, `master_port`, `slave_port`,
`master_modport`, `slave_modport` — all default to `None`, so legacy
regex-detected bundles that omit them keep working. A v2.x producer
must be paired with a consumer bump to `SUPPORTED_SCHEMA_MAJOR = 2`;
that bump is a CLI-visible breaking change surfaced via
`--list-overlays`.

Load failures (read error, JSON parse, structural validation, version
mismatch) all raise `AxiPerfAnnotationsError`, so the CLI can surface a
targeted "AXI perf map is malformed" message instead of a stack trace.

## Deep drill-down

The tab is the at-a-glance roll-up. For per-transaction analysis —
brushable timeline, latency CDF, outstanding-depth, ID heatmap, Jain
fairness, throughput (step) — open the rtl-buddy-axi-profiler **marimo
notebook**, which reads the opt-in per-transaction parquet
(`axi-txns.parquet`, emitted behind `--emit-txns-parquet` + the
`[parquet]` extra).

The handoff already exists in-product: the AXI Performance tab's **Open
in marimo ↗** button calls rtl_buddy's
`GET /api/axi-profile/notebook?test=…&suite_dir=…` endpoint, which
launches the notebook and returns its URL. From a shell, `rb
axi-profile notebook <test>` resolves the test's parquet
(`artefacts/axi/<test>/axi-txns.parquet`) and launches the template
directly.

> **Don't conflate the two contracts.** The `axi-perf.json` roll-up
> reports latency in **cycles** plus a `clock_period_ns`. The
> per-transaction parquet (schema v1.1) reports time in **picoseconds**
> (`_ps` columns). The tab and this overlay only ever see the
> cycles-based JSON.

## See also

- [`overlays.md`](overlays.md) — the protocol for writing a
  third-party overlay whose data lands under `node.overlays.<name>`.
  axi-perf follows the same load + no-op-hooks shape as the clock /
  reset built-ins.
- [`view-json-v1.md`](view-json-v1.md) — the locked `view.json` schema.
  `node.overlays` / `edge.overlays` are open objects, so the
  hyphenated `axi-perf` metadata key rides under them without a
  dedicated schema entity.
- [`wave-live-overlay.md`](wave-live-overlay.md) — the live waveform
  overlay; same operator-guide shape, and the SPA hub event path the
  AXI tab's selection / time-window events travel through.
- [rtl-buddy-axi-profiler](https://github.com/rtl-buddy/rtl-buddy-axi-profiler)
  — the producer of `axi-perf.json` and the marimo drill-down notebook.
- README — user-facing `--overlay name=path` quick reference.
- Phase 11 issue
  [#60](https://github.com/rtl-buddy/rtl-buddy-view/issues/60)
  (delivered) — the loader + protocol registration slice.
- [#69](https://github.com/rtl-buddy/rtl-buddy-view/issues/69) — moved
  perf into a dedicated tab and removed per-edge hierarchy styling.
- [#114](https://github.com/rtl-buddy/rtl-buddy-view/issues/114) —
  unified the bundle→node join onto the interface-port / tb-top
  mechanism.
```
