// 'clock' overlay renderer.
//
// Mirror of the Python-side ClockOverlay (`src/rtl_buddy_view/
// overlays/clock.py`): consumes the per-node ``overlays.clock``
// block emitted by view.json v1's renderer, and the per-edge
// ``overlays.clock.crossing`` flag.
//
// The visual contribution is: fill the node's <g> with a
// clock-keyed pastel; mark edges where ``overlays.clock.crossing``
// is true with a CDC accent.

const PALETTE = [
  '#dbeafe', // blue
  '#dcfce7', // green
  '#fef9c3', // yellow
  '#fce7f3', // pink
  '#ede9fe', // purple
  '#fed7aa', // orange
  '#cffafe', // cyan
]

// Same hashing scheme as the dot renderer's ``_palette_color`` so
// the same clock lands in the same colour across the desktop +
// browser views (handy when reviewing a screenshot against a
// terminal capture).
async function djb2(str) {
  let hash = 5381
  for (let i = 0; i < str.length; i++) hash = ((hash << 5) + hash + str.charCodeAt(i)) >>> 0
  return hash
}

// Build the canonical clock→colour assignment for a graph. Both
// ``apply`` and ``legend`` must use this so the swatch shown in the
// panel matches the fill painted on each node. Sorted-alphabetical
// gives a stable order independent of node traversal — a clock's
// colour doesn't move when the user rolls back to an earlier
// view.json.
function buildClockPalette(graph) {
  const seen = new Set()
  for (const node of graph.nodes || []) {
    const ov = node.overlays && node.overlays.clock
    if (ov && ov.clock) seen.add(ov.clock)
  }
  // Producers may also publish a top-level clock manifest so
  // single-source-of-truth clocks (declared in SDC but bound to no
  // flop) still appear in the legend — see view-v1's optional
  // ``overlay_meta.clock.clocks`` field.
  const meta = graph.overlay_meta && graph.overlay_meta.clock
  if (meta && Array.isArray(meta.clocks)) {
    for (const entry of meta.clocks) {
      const name = typeof entry === 'string' ? entry : entry && entry.name
      if (typeof name === 'string' && name.length > 0) seen.add(name)
    }
  }
  const sorted = Array.from(seen).sort()
  const out = new Map()
  sorted.forEach((name, idx) => {
    out.set(name, PALETTE[idx % PALETTE.length])
  })
  return out
}

export const clockOverlay = {
  name: 'clock',
  /**
   * Apply the overlay's per-node + per-edge contributions to the
   * SVG that ``GraphCanvas`` has rendered. Idempotent: applying
   * twice produces the same DOM state. Tools that toggle the
   * overlay just call this with ``enabled = false`` to clear.
   */
  apply(svgRoot, graph, enabled) {
    const palette = buildClockPalette(graph)
    for (const node of graph.nodes) {
      const ov = node.overlays && node.overlays.clock
      const group = svgRoot.querySelector(
        `[data-node-id="${cssEscape(node.id)}"]`,
      )
      if (!group) continue
      const shape = group.querySelector('polygon, ellipse, rect, path')
      if (!shape) continue
      if (enabled && ov && ov.clock && palette.has(ov.clock)) {
        // Only override the inline style — leave the polygon's
        // ``fill=`` attribute alone. Graphviz sets the attribute
        // from the DOT's global ``node [fillcolor=...]`` default
        // (neutral grey), and on toggle-off we restore that floor
        // by clearing inline only. Stripping the attribute itself
        // bottoms out at SVG's default of *black*, which made
        // unchecking the overlay turn modules black (#57 follow-up).
        shape.style.fill = palette.get(ov.clock)
        group.setAttribute('data-overlay-clock', ov.clock)
      } else {
        shape.style.fill = ''
        group.removeAttribute('data-overlay-clock')
      }
    }
    for (const edge of graph.edges) {
      const ov = edge.overlays && edge.overlays.clock
      const path = svgRoot.querySelector(
        `[data-edge-from="${cssEscape(edge.from)}"][data-edge-to="${cssEscape(edge.to)}"] path`,
      )
      if (!path) continue
      if (enabled && ov && ov.crossing) {
        path.setAttribute('stroke', '#dc2626')
        path.setAttribute('stroke-dasharray', '4,3')
      } else {
        path.setAttribute('stroke', '')
        path.removeAttribute('stroke-dasharray')
      }
    }
  },
  /** Per-overlay legend payload for OverlayPanel.vue. */
  legend(graph) {
    const palette = buildClockPalette(graph)
    const entries = Array.from(palette.entries()).map(([label, swatch]) => ({
      label,
      swatch,
      kind: 'fill',
    }))
    // Conditionally surface the CDC edge style — only when at
    // least one edge in the current graph is flagged as a
    // crossing. Filters automatically when a new view.json
    // without crossings is loaded.
    const hasCdcCrossing = (graph?.edges || []).some(
      (e) => e.overlays && e.overlays.clock && e.overlays.clock.crossing,
    )
    if (hasCdcCrossing) {
      entries.push({
        label: 'CDC crossing',
        swatch: '#dc2626',
        kind: 'dashed-line',
      })
    }
    return entries
  },
}

// CSS.escape is widely available but not on every JSDOM build;
// fall back to a conservative escaper for selector use.
function cssEscape(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s)
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`)
}

// djb2 is unused today but pinned so future palette assignments
// can switch from "first-seen" to "hash-stable" without rewriting
// the surface — see note above.
export { djb2 }
