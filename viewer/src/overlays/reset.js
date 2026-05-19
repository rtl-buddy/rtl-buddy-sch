// 'reset' overlay renderer.
//
// Mirror of the Python-side ResetOverlay (`src/rtl_buddy_view/
// overlays/reset.py`): consumes the per-node ``overlays.reset``
// block and the per-edge ``overlays.reset.crossing`` flag from
// view.json v1.
//
// Visual contributions:
//   - Synchroniser-set members get a teal border (#0d9488).
//   - RDC-crossing edges get a dashed-orange stroke (#ea580c).
//   - Other flops with a reset binding pick up a faint orange
//     border so the reset overlay reads as a distinct family
//     from the clock overlay's fill-based palette.

export const resetOverlay = {
  name: 'reset',
  apply(svgRoot, graph, enabled) {
    for (const node of graph.nodes) {
      const ov = node.overlays && node.overlays.reset
      const group = svgRoot.querySelector(
        `[data-node-id="${cssEscape(node.id)}"]`,
      )
      if (!group) continue
      const shape = group.querySelector('polygon, ellipse, rect, path')
      if (!shape) continue
      if (enabled && ov) {
        const stroke = ov.is_synchronizer ? '#0d9488' : '#ea580c'
        shape.setAttribute('stroke', stroke)
        shape.setAttribute('stroke-width', '2')
        group.setAttribute('data-overlay-reset', ov.reset || (ov.is_synchronizer ? 'sync' : ''))
      } else {
        shape.removeAttribute('stroke')
        shape.removeAttribute('stroke-width')
        group.removeAttribute('data-overlay-reset')
      }
    }
    for (const edge of graph.edges) {
      const ov = edge.overlays && edge.overlays.reset
      const path = svgRoot.querySelector(
        `[data-edge-from="${cssEscape(edge.from)}"][data-edge-to="${cssEscape(edge.to)}"] path`,
      )
      if (!path) continue
      if (enabled && ov && ov.crossing) {
        // Only override colour when the clock overlay hasn't
        // already claimed the edge (CDC red wins on dual-issue
        // edges — same precedence as the dot renderer).
        const existing = path.getAttribute('stroke')
        if (!existing || existing === '#ea580c') {
          path.setAttribute('stroke', '#ea580c')
          path.setAttribute('stroke-dasharray', '6,3')
        }
      } else if (path.getAttribute('stroke') === '#ea580c') {
        path.removeAttribute('stroke')
        path.removeAttribute('stroke-dasharray')
      }
    }
  },
  legend() {
    return [
      { label: 'reset-binding', swatch: '#ea580c' },
      { label: 'reset-synchroniser', swatch: '#0d9488' },
    ]
  },
}

function cssEscape(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s)
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`)
}
