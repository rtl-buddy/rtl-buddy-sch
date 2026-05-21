// Overlay registry — JS side. Mirrors the Python OverlayRegistry
// in src/rtl_buddy_view/overlays/__init__.py so the toggles UI has
// a single place to look for built-in overlay names.
//
// Loading an unknown overlay (a name in ``graph.overlays_present``
// that has no JS module here) is a soft miss: the panel shows the
// name with an "unknown" tag and contributes nothing to render —
// never an error. This matches the issue's requirement that the
// viewer keeps working even when the producer ships a new overlay
// the consumer doesn't know about yet.

import { axiPerfOverlay } from './axi_perf.js'
import { clockOverlay } from './clock.js'
import { resetOverlay } from './reset.js'

const BUILTINS = {
  'axi-perf': axiPerfOverlay,
  clock: clockOverlay,
  reset: resetOverlay,
}

export function getOverlay(name) {
  return BUILTINS[name] || null
}

export function applyOverlays(svgRoot, graph, enabledOverlays) {
  for (const name of graph.overlays_present) {
    const overlay = getOverlay(name)
    if (!overlay) continue
    overlay.apply(svgRoot, graph, enabledOverlays.has(name))
  }
}

export function overlaySummary(graph) {
  return graph.overlays_present.map((name) => ({
    name,
    known: !!getOverlay(name),
  }))
}
