// viz.js (Graphviz WASM) layout wrapper.
//
// Takes a parsed view.json graph, converts it to DOT, runs viz.js,
// and returns an SVG string the GraphCanvas component renders into
// the DOM. We deliberately don't try to parse the SVG into Vue's
// virtual DOM — viz.js produces production-quality SVG already and
// re-wrapping every node/edge in Vue templates would lose that. The
// viewer hangs DOM listeners off the SVG via data attributes after
// it's mounted (see GraphCanvas.vue).
//
// viz.js is loaded lazily on first use so the initial bundle (the
// Vue + Pinia shell) stays small enough to drag-drop a 300 KB
// view.json into the browser before the WASM is fetched.

let _vizPromise = null

async function getViz() {
  if (_vizPromise == null) {
    // ``instance()`` returns a single shared Viz instance per
    // page lifetime — viz.js is internally thread-safe and the
    // WASM module's setup cost (~150 ms) is the dominant
    // first-render expense, so we cache aggressively.
    const { instance } = await import('@viz-js/viz')
    _vizPromise = instance()
  }
  return _vizPromise
}

/**
 * Layout ``graph`` (a view.json v1 payload) and return an SVG
 * string. Throws on layout errors; the store catches and surfaces
 * via the toast pipeline.
 */
export async function layoutGraph(graph) {
  const viz = await getViz()
  const dot = graphToDot(graph)
  // ``viz.renderSVGElement`` runs the full layout pipeline (dot
  // engine, default) and returns a DOM-ready ``<svg>`` element.
  // The string form is easier to test against snapshots so we
  // serialise here.
  const svg = viz.renderSVGElement(dot)
  return svg.outerHTML
}

/**
 * Generate a DOT description of ``graph`` suitable for
 * dot/Graphviz layout. We emit the topology only (nodes + edges) —
 * overlay styling is applied by the GraphCanvas component as CSS
 * classes after layout, so toggling an overlay never re-runs
 * viz.js. (Layout is the expensive step; restyling is free.)
 */
export function graphToDot(graph) {
  const lines = []
  lines.push('digraph view {')
  lines.push('  rankdir="LR";')
  lines.push('  node [shape=box, style="rounded,filled", fillcolor="#f5f5f5"];')
  lines.push('  edge [arrowsize=0.7];')
  for (const node of graph.nodes) {
    const label = nodeLabel(node)
    // ``label`` carries deliberate ``\n`` line-break markers that
    // Graphviz interprets as newlines; passing it through
    // ``dotEscape`` would double those backslashes and disable the
    // line-break behavior. Escape quotes only here.
    lines.push(`  ${dotId(node.id)} [label="${labelEscape(label)}"];`)
  }
  for (const edge of graph.edges) {
    lines.push(`  ${dotId(edge.from)} -> ${dotId(edge.to)};`)
  }
  lines.push('}')
  return lines.join('\n')
}

function nodeLabel(node) {
  // Two-line label: instance name on top, module name underneath.
  // Blackboxes carry their module name only (no instance, by
  // definition — they're modules we never resolved) plus the
  // ``(blackbox)`` suffix the renderer convention uses.
  const inst = node.instance_name || node.module
  const lines = [inst, node.module]
  if (node.is_blackbox) lines.push('(blackbox)')
  return lines.filter((line, idx) => idx === 0 || line !== lines[idx - 1]).join('\\n')
}

// DOT node IDs must be alphanumeric (no dots); use the instance
// path as a literal quoted string. Quoting always is simpler than
// trying to escape only dotted ids.
export function dotId(id) {
  return `"${dotEscape(id)}"`
}

export function dotEscape(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

// Label-specific escape: quotes only. Keeps ``\n`` newline markers
// intact (Graphviz interprets them as line breaks within the label).
export function labelEscape(s) {
  return String(s).replace(/"/g, '\\"')
}
