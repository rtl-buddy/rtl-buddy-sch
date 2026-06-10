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
import { coverageOverlay } from './coverage.js'
import { resetOverlay } from './reset.js'
import { waveOverlay } from './wave.js'

const BUILTINS = {
  'axi-perf': axiPerfOverlay,
  clock: clockOverlay,
  coverage: coverageOverlay,
  reset: resetOverlay,
  wave: waveOverlay,
}

export function getOverlay(name) {
  return BUILTINS[name] || null
}

// ``context`` is an optional bag of dynamic state overlays may
// consult — e.g. the live wave-values map sourced from
// ``wave_values_changed`` hub events. The clock/reset/axi-perf
// overlays ignore it; only the wave overlay reads it today. Adding
// new keys is back-compat by construction.
export function applyOverlays(svgRoot, graph, enabledOverlays, context = {}) {
  // Always include 'wave' in the iteration. The Phase-8 producer
  // writes node.overlays.wave on each node (so graph.overlays_present
  // already lists it), but in the Phase-9 live path the values come
  // from the hub at runtime and ``overlays_present`` may not yet
  // include 'wave'. The overlay's apply() handles both — making it
  // unconditional means a freshly-loaded design with no static wave
  // block can still paint badges as soon as the first hub event
  // arrives.
  const names = new Set(graph.overlays_present)
  names.add('wave')
  for (const name of names) {
    const overlay = getOverlay(name)
    if (!overlay) continue
    overlay.apply(svgRoot, graph, enabledOverlays.has(name), context)
  }
}

export function overlaySummary(graph) {
  return graph.overlays_present.map((name) => ({
    name,
    known: !!getOverlay(name),
  }))
}
