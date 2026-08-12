// The SPA's colour decisions, each written exactly once.
//
// Before this module the same three mappings lived in several places
// and had already drifted:
//
//   - the AXI backpressure ramp was defined in ``overlays/axi_perf.js``,
//     ``NodeDetail.vue`` and ``AxiPerfView.vue`` with three sets of class
//     names and two different ambers (#f59e0b vs #d97706);
//   - the clock palette existed in ``overlays/clock.js`` and
//     ``layout/viz.js``, over *different* clock sets — so the legend
//     swatch and the DOT-baked HTML cell could disagree about a clock's
//     colour;
//   - the coverage ramp's lightness was a literal in ``overlays/coverage.js``.
//
// Every colour here is read from a design token at call time via
// ``theme.js::token``, so a theme flip re-colours the canvas as soon as
// the caller re-draws.

import { token } from './theme.js'

// ---------------------------------------------------------------------
// Clock-domain palette
// ---------------------------------------------------------------------

/** Token names of the seven clock pastels, in assignment order. */
export const CLOCK_PALETTE_TOKENS = [
  '--clk-1',
  '--clk-2',
  '--clk-3',
  '--clk-4',
  '--clk-5',
  '--clk-6',
  '--clk-7',
]

/** Resolve the clock palette to concrete colours for the current theme. */
export function clockPalette() {
  return CLOCK_PALETTE_TOKENS.map((name) => token(name))
}

/**
 * ``<unconstrained>``-style pseudo clocks: signals the SDC binds to no
 * clock (often resets). They anchor no direction, so they never take a
 * palette slot. Same rule as ``dot.py::_crossing_pairs_into``.
 */
export function isUnconstrained(clockName) {
  return (
    typeof clockName === 'string' &&
    clockName.startsWith('<') &&
    clockName.endsWith('>')
  )
}

/**
 * The canonical clock → colour assignment for a graph.
 *
 * The clock set is the UNION of every place a clock name can appear:
 * per-node ``overlays.clock.clock``, the producer's optional
 * ``overlay_meta.clock.clocks`` manifest, and both ends of every edge's
 * ``overlays.clock.pairs``. Taking the union is what makes the overlay
 * legend, the node fills and the DOT-baked bridge cells agree — indices
 * are assigned over a sorted list, so two callers looking at two
 * different sets would hand the same clock two different colours.
 *
 * Returns ``Map<clockName, colour>``.
 */
export function buildClockPalette(graph) {
  const seen = new Set()
  for (const node of graph?.nodes || []) {
    const ov = node && node.overlays && node.overlays.clock
    if (ov && ov.clock) seen.add(ov.clock)
  }
  const meta = graph?.overlay_meta && graph.overlay_meta.clock
  if (meta && Array.isArray(meta.clocks)) {
    for (const entry of meta.clocks) {
      const name = typeof entry === 'string' ? entry : entry && entry.name
      if (typeof name === 'string' && name.length > 0) seen.add(name)
    }
  }
  for (const edge of graph?.edges || []) {
    const pairs = edge?.overlays?.clock?.pairs
    if (!Array.isArray(pairs)) continue
    for (const p of pairs) {
      if (!p) continue
      if (typeof p.src_clock === 'string' && !isUnconstrained(p.src_clock)) {
        seen.add(p.src_clock)
      }
      if (typeof p.dst_clock === 'string' && !isUnconstrained(p.dst_clock)) {
        seen.add(p.dst_clock)
      }
    }
  }
  const colours = clockPalette()
  const out = new Map()
  Array.from(seen)
    .sort()
    .forEach((name, idx) => out.set(name, colours[idx % colours.length]))
  return out
}

// ---------------------------------------------------------------------
// AXI backpressure ramp
// ---------------------------------------------------------------------

/** Percentage thresholds. Above ``hi`` is red; above ``mid`` is amber. */
export const BP_THRESHOLDS = { mid: 5, hi: 15 }

/** ``'lo' | 'mid' | 'hi'`` for a backpressure percentage. */
export function bpLevel(bpPct) {
  const v = typeof bpPct === 'number' ? bpPct : 0
  if (v > BP_THRESHOLDS.hi) return 'hi'
  if (v > BP_THRESHOLDS.mid) return 'mid'
  return 'lo'
}

/** Concrete colour for a backpressure percentage (canvas strokes). */
export function bpColor(bpPct) {
  const level = bpLevel(bpPct)
  if (level === 'hi') return token('--err')
  if (level === 'mid') return token('--warn')
  return token('--ok')
}

// ---------------------------------------------------------------------
// Coverage ramp
// ---------------------------------------------------------------------

/**
 * Continuous coverage tint: red (0%) → green (100%) at the lightness
 * the active theme pins via ``--cov-l`` (82% light, 38% dark). The
 * shared hub sheet documents this exact expression, so the schematic
 * overlay, the graph pane and the coverage app all land on one ramp.
 */
export function coverageColor(pct) {
  const clamped = Math.max(0, Math.min(100, typeof pct === 'number' ? pct : 0))
  const hue = Math.round(clamped * 1.2)
  return `hsl(${hue}, 70%, ${token('--cov-l')})`
}

/** The "this module has no LCOV data" fill. */
export function coverageNoDataColor() {
  return token('--cov-none')
}
