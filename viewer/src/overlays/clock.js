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

export const clockOverlay = {
  name: 'clock',
  /**
   * Apply the overlay's per-node + per-edge contributions to the
   * SVG that ``GraphCanvas`` has rendered. Idempotent: applying
   * twice produces the same DOM state. Tools that toggle the
   * overlay just call this with ``enabled = false`` to clear.
   */
  apply(svgRoot, graph, enabled) {
    const clockFill = new Map() // clock name → palette colour
    let nextIdx = 0
    const colourFor = (clock) => {
      let c = clockFill.get(clock)
      if (!c) {
        c = PALETTE[nextIdx++ % PALETTE.length]
        clockFill.set(clock, c)
      }
      return c
    }
    for (const node of graph.nodes) {
      const ov = node.overlays && node.overlays.clock
      const group = svgRoot.querySelector(
        `[data-node-id="${cssEscape(node.id)}"]`,
      )
      if (!group) continue
      const shape = group.querySelector('polygon, ellipse, rect, path')
      if (!shape) continue
      if (enabled && ov && ov.clock) {
        shape.style.fill = colourFor(ov.clock)
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
    const clocks = new Set()
    for (const node of graph.nodes) {
      const ov = node.overlays && node.overlays.clock
      if (ov && ov.clock) clocks.add(ov.clock)
    }
    return Array.from(clocks)
      .sort()
      .map((clock, idx) => ({
        label: clock,
        swatch: PALETTE[idx % PALETTE.length],
      }))
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
