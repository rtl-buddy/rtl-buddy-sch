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
 *
 * When ``graph.layout.dot`` is a non-empty string (set by Python
 * --format json --embed-layout, default on), the renderer hands it
 * directly to viz.js — the desktop terminal output and the browser
 * schematic then share a single layout (clusters, port-rank
 * anchors, edge labels, clock-domain palette). Producers that omit
 * the field fall back to the in-JS ``graphToDot`` builder, which
 * keeps drag-drop view.json files (no Python producer in the loop)
 * working without a behaviour change.
 */
export async function layoutGraph(graph) {
  const viz = await getViz()
  const dot = pickDot(graph)
  // ``viz.renderSVGElement`` runs the full layout pipeline (dot
  // engine, default) and returns a DOM-ready ``<svg>`` element.
  // The string form is easier to test against snapshots so we
  // serialise here.
  const svg = viz.renderSVGElement(dot)
  return svg.outerHTML
}

/**
 * Lay out an arbitrary DOT string and return the SVG. Lets
 * alternate renderers (block-flow, future formats) share the same
 * viz.js bootstrap path without going through view.json.
 */
export async function layoutDot(dot) {
  const viz = await getViz()
  const svg = viz.renderSVGElement(dot)
  return svg.outerHTML
}

// Pick the DOT source to feed viz.js: prefer the producer-supplied
// ``layout.dot`` when it looks usable; fall back to the in-JS
// builder otherwise. Exported for tests so the precedence rule is
// pinnable without standing up the WASM engine.
export function pickDot(graph) {
  const embedded = graph?.layout?.dot
  if (typeof embedded === 'string' && embedded.trim().length > 0) {
    return embedded
  }
  return graphToDot(graph)
}

/**
 * Generate a DOT description of ``graph`` suitable for
 * dot/Graphviz layout. We emit the topology only (nodes + edges) —
 * overlay styling is applied by the GraphCanvas component as CSS
 * classes after layout, so toggling an overlay never re-runs
 * viz.js. (Layout is the expensive step; restyling is free.)
 *
 * The root node (``graph.top``) becomes an outer ``cluster_top``
 * subgraph so the descend / subtree view gets a labelled frame
 * around it (matches the producer-supplied embedded DOT's
 * convention). Without this, the descend canvas was a flat list
 * of children with no scope title.
 */
export function graphToDot(graph) {
  const lines = []
  lines.push('digraph view {')
  lines.push('  rankdir="LR";')
  lines.push('  compound=true;')
  lines.push('  node [shape=box, style="rounded,filled", fillcolor="#f5f5f5"];')
  lines.push('  edge [arrowsize=0.7];')

  const rootId = graph.top
  const rootNode = graph.nodes.find((n) => n.id === rootId)
  const rootLabel = rootNode ? scopeLabel(rootNode) : (rootId || '')
  const childNodes = graph.nodes.filter((n) => n.id !== rootId)
  const childIds = new Set(childNodes.map((n) => n.id))

  if (rootNode) {
    lines.push('  subgraph cluster_top {')
    lines.push(`    label="${labelEscape(rootLabel)}";`)
    lines.push('    labelloc="t";')
    lines.push('    style="rounded";')
    lines.push('    color="#94a3b8";')
    lines.push('    penwidth=2;')
    for (const node of childNodes) {
      const label = nodeLabel(node)
      lines.push(`    ${dotId(node.id)} [label="${labelEscape(label)}"];`)
    }
    lines.push('  }')
    // Emit the root's own ``data-node-id`` anchor so click handlers
    // and overlays can still find it. The cluster <g> picks up
    // ``data-node-id`` separately via ``cluster_lookup`` (set below)
    // — but only if we tell the DOM what cluster→id mapping is.
  } else {
    for (const node of childNodes) {
      const label = nodeLabel(node)
      lines.push(`  ${dotId(node.id)} [label="${labelEscape(label)}"];`)
    }
  }

  // Skip edges that touch the root — its visual representation is the
  // cluster frame, not a node, so a ``rootId -> child`` arrow would
  // dangle. Inner edges (child→grandchild) render normally. CDC-flagged
  // edges get a ``src_clk → dst_clk`` label so the clock pair is
  // visible without clicking through to EdgeDetail.
  for (const edge of graph.edges) {
    if (edge.from === rootId || edge.to === rootId) continue
    if (!childIds.has(edge.from) || !childIds.has(edge.to)) continue
    const label = edgeLabel(edge)
    const attrs = label ? ` [label="${labelEscape(label)}", fontsize=9, fontcolor="#dc2626"]` : ''
    lines.push(`  ${dotId(edge.from)} -> ${dotId(edge.to)}${attrs};`)
  }
  lines.push('}')
  return lines.join('\n')
}

// Cluster-frame title: prefer the instance name (familiar to the
// user from the breadcrumb), fall back to module name for
// blackboxes or the top of the full graph where the instance name
// might be empty.
function scopeLabel(node) {
  return node.instance_name || node.module || node.id || ''
}

// Label for a CDC-flagged edge: dedupe the per-flop pairs (already
// deduped in json_render.py) into ``src → dst`` lines. Multiple
// distinct pairs stack vertically. Returns empty string when the
// edge has no CDC overlay.
function edgeLabel(edge) {
  const ov = edge?.overlays?.clock
  if (!ov || !Array.isArray(ov.pairs) || ov.pairs.length === 0) return ''
  return ov.pairs.map((p) => `${p.src_clock} → ${p.dst_clock}`).join('\\n')
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
