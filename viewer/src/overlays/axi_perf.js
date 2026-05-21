// 'axi-perf' overlay renderer.
//
// Mirror of the Python-side AxiPerfOverlay
// (src/rtl_buddy_view/overlays/axi_perf.py). Consumes the per-edge
// `overlays.axi-perf` block emitted by view.json v1's renderer + the
// per-node `overlays.axi-perf.interconnect` roll-up on interconnect
// nodes.
//
// Visual contribution (prototype scope, Phase 11 follow-up to #60):
// - Edges with axi-perf data get a colored stroke based on max-
//   channel backpressure %: green (≤5%) → yellow (≤15%) → red (>15%).
// - Errors (slverr+decerr > 0) make the stroke dashed.
// - Stroke width scales with total throughput (log10).
//
// Detailed per-edge inspection (channel bars, latency histograms)
// lives in the `AxiPerfPane.vue` sidebar component — see
// components/AxiPerfPane.vue. This module only paints the edge
// strokes.

function cssEscape(s) {
  return CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&')
}

function strokeForBackpressure(bpPct) {
  if (bpPct > 15) return '#dc2626'  // red-600
  if (bpPct > 5) return '#f59e0b'   // amber-500
  return '#16a34a'                  // green-600
}

function strokeWidthForBps(bps) {
  if (bps <= 0) return 1
  // Map [0, 1e10] bps onto [1, 4] stroke width via log10.
  const w = 1 + Math.min(3, Math.max(0, Math.log10(bps) - 5))
  return w.toFixed(2)
}

function edgeMaxBackpressure(perfBlock) {
  if (!perfBlock || !perfBlock.channels) return 0
  let best = 0
  for (const role of ['ar', 'aw', 'r', 'w', 'b']) {
    const ch = perfBlock.channels[role]
    if (ch && typeof ch.bp_pct === 'number' && ch.bp_pct > best) best = ch.bp_pct
  }
  return best
}

function totalBps(perfBlock) {
  if (!perfBlock || !perfBlock.throughput) return 0
  return (perfBlock.throughput.read_bps || 0) + (perfBlock.throughput.write_bps || 0)
}

function hasErrors(perfBlock) {
  if (!perfBlock || !perfBlock.errors) return false
  return (perfBlock.errors.slverr || 0) + (perfBlock.errors.decerr || 0) > 0
}

export const axiPerfOverlay = {
  name: 'axi-perf',

  /**
   * Style every edge whose overlay payload contains the axi-perf
   * block. Idempotent — applying twice produces the same DOM state;
   * applying with `enabled = false` clears the styling.
   */
  apply(svgRoot, graph, enabled) {
    if (!graph || !Array.isArray(graph.edges)) return

    for (const edge of graph.edges) {
      const ov = edge.overlays && edge.overlays['axi-perf']
      const fromEsc = cssEscape(edge.from)
      const toEsc = cssEscape(edge.to)
      const edgeEl = svgRoot.querySelector(
        `[data-edge-from="${fromEsc}"][data-edge-to="${toEsc}"]`,
      )
      if (!edgeEl) continue
      const path = edgeEl.querySelector('path')
      if (!path) continue

      if (!ov || !enabled) {
        path.style.stroke = ''
        path.style.strokeWidth = ''
        path.style.strokeDasharray = ''
        continue
      }

      const bp = edgeMaxBackpressure(ov)
      const bps = totalBps(ov)
      path.style.stroke = strokeForBackpressure(bp)
      path.style.strokeWidth = strokeWidthForBps(bps)
      path.style.strokeDasharray = hasErrors(ov) ? '4 3' : ''
    }
  },
}

/**
 * Find the axi-perf block for the currently-selected edge (used by
 * AxiPerfPane). Returns null when nothing matches.
 */
export function selectedEdgeAxiPerf(graph, selectedEdge) {
  if (!selectedEdge || !graph || !Array.isArray(graph.edges)) return null
  const edge = graph.edges.find(
    (e) => e.from === selectedEdge.from && e.to === selectedEdge.to,
  )
  if (!edge || !edge.overlays) return null
  return edge.overlays['axi-perf'] || null
}

/**
 * Find the axi-perf interconnect roll-up for a node, if present.
 */
export function nodeAxiPerfInterconnect(graph, nodeId) {
  if (!nodeId || !graph || !Array.isArray(graph.nodes)) return null
  const node = graph.nodes.find((n) => n.id === nodeId)
  if (!node || !node.overlays) return null
  const block = node.overlays['axi-perf']
  return block && block.interconnect ? block.interconnect : null
}
