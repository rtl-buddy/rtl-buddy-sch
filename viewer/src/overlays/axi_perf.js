// 'axi-perf' overlay renderer (Phase 11).
//
// Per the design discussion: the AXI view lives in its own tab and
// does NOT co-exist with the hierarchy view's CDC / reset / phys
// overlays. So `apply()` is a deliberate no-op — the AXI rendering
// happens in `components/AxiPerfView.vue` (a full-tab component),
// not as a per-edge paint on the hierarchy SVG.
//
// The `selectedEdgeAxiPerf` / `nodeAxiPerfInterconnect` helpers
// stay because the AxiPerfView reads them to find the matching
// bundle / interconnect data.

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
   * No-op on the hierarchy view: per the design, AXI rendering is
   * a separate tab (AxiPerfView.vue), not a per-edge overlay on
   * the existing canvas. The function exists so the overlay
   * registry's iteration still has a uniform call signature.
   */
  apply(_svgRoot, _graph, _enabled) {
    // Intentionally empty. See module docstring.
  },
}

// Retained for potential future use (or tests) — currently unused
// by the per-tab AxiPerfView, which walks graph.edges directly.
export { cssEscape, strokeForBackpressure, strokeWidthForBps, edgeMaxBackpressure, totalBps, hasErrors }

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
