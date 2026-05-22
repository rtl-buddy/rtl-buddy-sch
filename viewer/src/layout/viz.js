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

  // Pre-compute the clock palette + per-node CDC pair list so each
  // child node can opt into the HTML-TABLE / striped-fill rendering
  // mirrored from ``dot.py``. Both helpers walk the graph once.
  const palette = buildClockPalette(graph)
  const pairsByNode = collectCdcPairsByNode(graph)

  // Virtual stand-in source for crossings whose ``src_clock`` is
  // ``<unconstrained>`` — typically reset / async-clear ports that
  // the CDC tool flagged as async paths but the SDC didn't bind to
  // a real clock. The edges still need to be drawn (the user has
  // to know they exist) so we route them from this placeholder
  // instead of dropping them on the floor.
  const UNCONSTRAINED_ID = '_rb_unconstrained'
  const needUnconstrained = (graph.edges || []).some((e) =>
    (e?.overlays?.clock?.pairs || []).some(
      (p) => p && isUnconstrained(p.src_clock),
    ),
  )

  if (rootNode) {
    lines.push('  subgraph cluster_top {')
    lines.push(`    label="${labelEscape(rootLabel)}";`)
    lines.push('    labelloc="t";')
    lines.push('    style="rounded";')
    lines.push('    color="#94a3b8";')
    lines.push('    penwidth=2;')
    if (needUnconstrained) {
      lines.push(`    ${dotId(UNCONSTRAINED_ID)} [shape=plaintext, ` +
        `label="?", fontcolor="#dc2626", fontsize=14, ` +
        `tooltip="async crossing whose src_clock is unconstrained in SDC"];`)
    }
    for (const node of childNodes) {
      lines.push('    ' + emitNode(node, palette, pairsByNode))
    }
    lines.push('  }')
  } else {
    if (needUnconstrained) {
      lines.push(`  ${dotId(UNCONSTRAINED_ID)} [shape=plaintext, ` +
        `label="?", fontcolor="#dc2626", fontsize=14, ` +
        `tooltip="async crossing whose src_clock is unconstrained in SDC"];`)
    }
    for (const node of childNodes) {
      lines.push('  ' + emitNode(node, palette, pairsByNode))
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
    const allPairs = edge?.overlays?.clock?.pairs || []
    // Distinct non-unconstrained pairs become their own DOT edges
    // (one per crossing direction), each pinned + labelled with
    // that specific src→dst pair. Without splitting, ``pairs[0]``
    // wins port-pinning lottery — and when ``<unconstrained>``
    // sorts first the edge falls back to auto-routing even though
    // a real-clock pair was available.
    const realPairs = allPairs.filter(
      (p) => p && typeof p.src_clock === 'string' && !isUnconstrained(p.src_clock),
    )
    const unconPairs = allPairs.filter(
      (p) => p && typeof p.src_clock === 'string' && isUnconstrained(p.src_clock),
    )
    if (realPairs.length === 0 && unconPairs.length === 0) {
      // Plain containment edge — no CDC at all.
      lines.push(`  ${dotId(edge.from)} -> ${dotId(edge.to)};`)
      continue
    }
    for (const p of realPairs) {
      const label = `${p.src_clock} → ${p.dst_clock}`
      const attrs = ` [label="${labelEscape(label)}", color="#dc2626", style="dashed", fontcolor="#dc2626", fontsize=9]`
      // Pin to the cells that name the data domain of THIS specific
      // pair on each end. The grid columns are ``in_<src>`` on the
      // left and ``out_<dst>`` on the right; ``portRefForPair``
      // resolves the right side for ``outgoing`` and the left for
      // ``incoming`` so the arrow visually flows across the box.
      const fromRef = portRefForPair(edge.from, p, 'outgoing', pairsByNode)
      const toRef = portRefForPair(edge.to, p, 'incoming', pairsByNode)
      lines.push(`  ${fromRef} -> ${toRef}${attrs};`)
    }
    // Unconstrained pairs route from the virtual ``_rb_unconstrained``
    // placeholder source rather than from ``edge.from`` — the actual
    // source isn't a real clock domain so there's no cell to pin to
    // on the from side. The destination side still pins to its
    // ``in_<dst>`` cell when the dst node has a grid, so the user
    // still gets a visual line into the receiving clock.
    for (const p of unconPairs) {
      const label = `${p.src_clock} → ${p.dst_clock}`
      const attrs = ` [label="${labelEscape(label)}", color="#dc2626", style="dashed", fontcolor="#dc2626", fontsize=9]`
      // Repurpose the in_<dst> cell on the destination side — the
      // pair has no usable src clock, so anchor by dst instead.
      const toRef = portRefForUnconstrainedDst(edge.to, p, pairsByNode)
      lines.push(`  ${dotId(UNCONSTRAINED_ID)} -> ${toRef}${attrs};`)
    }
  }
  lines.push('}')
  return lines.join('\n')
}

// ---------------------------------------------------------------------------
// Per-node CDC clock-box rendering (mirrors dot.py's _emit_html_grid_node /
// _crossing_pairs_into / _fill_for). Three render paths, picked by the
// number of distinct (src_clock, dst_clock) crossing pairs a node's subtree
// receives:
//
//   0 pairs  → plain rounded box (clock overlay paints fill at runtime)
//   1 pair   → striped two-tone fill ``<src_color>:<dst_color>``
//   2+ pairs → HTML-TABLE: header text row + one (src | dst) row per pair
// ---------------------------------------------------------------------------

// Same palette + sorted-alphabetical assignment scheme used by the
// clock-overlay renderer in ``overlays/clock.js`` — kept here as a
// local constant so the DOT generator can bake matching colours
// directly into HTML cells / striped fills without depending on
// overlay-apply order.
const CLOCK_PALETTE = [
  '#dbeafe', '#dcfce7', '#fef9c3', '#fce7f3',
  '#ede9fe', '#fed7aa', '#cffafe',
]

function buildClockPalette(graph) {
  const seen = new Set()
  for (const node of graph.nodes || []) {
    const ov = node.overlays && node.overlays.clock
    if (ov && ov.clock) seen.add(ov.clock)
  }
  for (const edge of graph.edges || []) {
    const pairs = edge?.overlays?.clock?.pairs
    if (!Array.isArray(pairs)) continue
    for (const p of pairs) {
      if (typeof p.src_clock === 'string' && !isUnconstrained(p.src_clock)) {
        seen.add(p.src_clock)
      }
      if (typeof p.dst_clock === 'string') seen.add(p.dst_clock)
    }
  }
  const sorted = Array.from(seen).sort()
  const out = new Map()
  sorted.forEach((name, idx) => out.set(name, CLOCK_PALETTE[idx % CLOCK_PALETTE.length]))
  return out
}

// ``<unconstrained>`` sources (signals not bound to a clock in the
// SDC — often reset ports) get filtered out to match dot.py's
// ``_crossing_pairs_into``: a real clock pair anchors a direction;
// an unconstrained source doesn't.
function isUnconstrained(clockName) {
  return clockName.startsWith('<') && clockName.endsWith('>')
}

// Collect deduped CDC (src, dst) pairs per node by walking each
// edge's ``overlays.clock.pairs`` and attributing the pairs to the
// destination node AND every ancestor up the dotted path. This
// matches dot.py's ``_crossing_pairs_into`` which uses
// ``dst.startswith(instance_path + '.')`` — a crossing terminating
// inside a sub-block also surfaces on the enclosing block, so
// "u_rb has both directions" reads correctly at the rb level even
// though the actual flops live inside sub-syncs.
function collectCdcPairsByNode(graph) {
  const buckets = new Map() // id -> Map<"src|dst", {src,dst,flops}>
  for (const edge of graph.edges || []) {
    const pairs = edge?.overlays?.clock?.pairs
    if (!Array.isArray(pairs) || pairs.length === 0) continue
    let cur = edge.to
    while (cur) {
      if (!buckets.has(cur)) buckets.set(cur, new Map())
      const bucket = buckets.get(cur)
      for (const p of pairs) {
        if (!p || typeof p.src_clock !== 'string') continue
        if (isUnconstrained(p.src_clock)) continue
        const key = `${p.src_clock}|${p.dst_clock}`
        const existing = bucket.get(key)
        if (existing) existing.flops += p.flops || 0
        else bucket.set(key, { ...p })
      }
      const lastDot = cur.lastIndexOf('.')
      if (lastDot < 0) break
      cur = cur.substring(0, lastDot)
    }
  }
  const out = new Map()
  for (const [id, bucket] of buckets) {
    const list = Array.from(bucket.values()).sort(
      (a, b) =>
        a.src_clock.localeCompare(b.src_clock) ||
        a.dst_clock.localeCompare(b.dst_clock),
    )
    out.set(id, list)
  }
  return out
}

function emitNode(node, palette, pairsByNode) {
  const pairs = pairsByNode.get(node.id) || []
  if (pairs.length >= 2) return emitGridNode(node, palette, pairs)
  if (pairs.length === 1) return emitStripedNode(node, palette, pairs[0])
  const label = nodeLabel(node)
  return `${dotId(node.id)} [label="${labelEscape(label)}"];`
}

// HTML-TABLE node label for a CDC bridge node with two or more
// distinct crossing pairs. The grid columns map to PHYSICAL PORT
// SIDES: input clocks on the LEFT, output clocks on the RIGHT
// (rankdir=LR convention). A clock can appear on both sides for a
// bidirectional bridge (FIFO) where it drives both an input and
// an output port.
//
// Derivation rule from the (src, dst) pairs of crossings entering
// this subtree:
//   input_clocks  = { p.src_clock | p ∈ pairs }
//   output_clocks = { p.dst_clock | p ∈ pairs }
// (A flop captures data on dst_clock — that captured signal leaves
// the bridge on dst_clock; the data that entered did so on
// src_clock. For sync flops this collapses to "in=src, out=dst";
// for FIFOs both clocks appear on both sides because both
// directions of pointer crossings get aggregated.)
function emitGridNode(node, palette, pairs) {
  const header = [node.instance_name || node.module, node.module]
    .filter((line, idx, arr) => idx === 0 || line !== arr[idx - 1])
    .map((line) => htmlLabelEscape(line))
    .join('<BR ALIGN="LEFT"/>')

  const inputClocks = sortedUnique(pairs.map((p) => p.src_clock))
  const outputClocks = sortedUnique(pairs.map((p) => p.dst_clock))
  const rowCount = Math.max(inputClocks.length, outputClocks.length, 1)

  const rows = [
    `<TR><TD COLSPAN="2" BORDER="0" ALIGN="LEFT">` +
      `<FONT FACE="Courier,monospace">${header}<BR ALIGN="LEFT"/></FONT></TD></TR>`,
  ]
  for (let i = 0; i < rowCount; i++) {
    const inClk = inputClocks[i]
    const outClk = outputClocks[i]
    const cells = []
    if (inClk) {
      const color = palette.get(inClk) || '#f1f5f9'
      cells.push(
        `<TD BGCOLOR="${color}" PORT="in_${htmlLabelEscape(inClk)}" WIDTH="60">` +
          `<FONT POINT-SIZE="10">${htmlLabelEscape(inClk)}</FONT></TD>`,
      )
    } else {
      cells.push('<TD BORDER="0"></TD>')
    }
    if (outClk) {
      const color = palette.get(outClk) || '#f1f5f9'
      cells.push(
        `<TD BGCOLOR="${color}" PORT="out_${htmlLabelEscape(outClk)}" WIDTH="60">` +
          `<FONT POINT-SIZE="10">${htmlLabelEscape(outClk)}</FONT></TD>`,
      )
    } else {
      cells.push('<TD BORDER="0"></TD>')
    }
    rows.push(`<TR>${cells.join('')}</TR>`)
  }
  const table =
    '<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4">' +
    rows.join('') +
    '</TABLE>>'
  return `${dotId(node.id)} [label=${table}, shape=plaintext];`
}

function sortedUnique(arr) {
  return Array.from(new Set(arr)).sort()
}

// Striped two-tone fill for nodes with exactly one (src, dst) pair
// — typically a single-stage sync flop where data flows from src to
// dst. ``style="rounded,striped"`` splits the box left-half / right-
// half by the colon-separated palette pair.
function emitStripedNode(node, palette, pair) {
  const label = nodeLabel(node)
  const srcColor = palette.get(pair.src_clock) || '#f1f5f9'
  const dstColor = palette.get(pair.dst_clock) || '#f1f5f9'
  const attrs = [
    `label="${labelEscape(label)}"`,
    'style="rounded,striped"',
    `fillcolor="${srcColor}:${dstColor}"`,
  ]
  return `${dotId(node.id)} [${attrs.join(', ')}];`
}

// HTML labels in DOT are XML-ish — escape the conventional set.
function htmlLabelEscape(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// Build the DOT ``node:port`` reference for one endpoint of a CDC
// edge anchored to a specific (src_clock, dst_clock) pair.
//
// Grid layout convention: input clocks on the LEFT (``in_<clk>``),
// output clocks on the RIGHT (``out_<clk>``). For an edge carrying
// the crossing pair, the data is in src_clock domain while in
// flight (the sync flop downstream captures it into dst_clock).
//
//   - ``'outgoing'`` (FROM node): pin to ``out_<src_clock>`` —
//     visually, the arrow leaves the right-side cell labelled with
//     the src clock. The cell exists when this node's grid has
//     ``src_clock`` on its dst-aggregate side (some crossing in
//     this node's subtree captures at this clock).
//   - ``'incoming'`` (TO node): pin to ``in_<src_clock>`` —
//     enters the left-side cell labelled with the src clock.
//
// Plain / striped / no-grid nodes get bare ``"node_id"`` and
// viz.js auto-routes to the box.
function portRefForPair(nodeId, pair, direction, pairsByNode) {
  const id = dotId(nodeId)
  const ownPairs = pairsByNode.get(nodeId) || []
  if (ownPairs.length < 2) return id
  if (!pair || typeof pair.src_clock !== 'string') return id
  const cellSide = direction === 'outgoing' ? 'out' : 'in'
  const clock = pair.src_clock
  // The cell exists in the grid iff this clock appears on the
  // matching side of *some* pair the node aggregated:
  //   - ``in_<clk>`` cells come from ``p.src_clock`` of any pair
  //   - ``out_<clk>`` cells come from ``p.dst_clock`` of any pair
  const hasCell = ownPairs.some((q) =>
    cellSide === 'in' ? q.src_clock === clock : q.dst_clock === clock,
  )
  if (!hasCell) return id
  return `${id}:"${cellSide}_${clock}"`
}

// Special-case anchor for unconstrained pairs. ``src_clock`` is
// ``<unconstrained>`` and not a real cell label, so we anchor on
// the destination side by ``dst_clock`` (the receiving clock where
// the sync flop sits) — the in_<dst_clock> cell exists when the
// destination is a grid node and its dst_clock matches.
function portRefForUnconstrainedDst(nodeId, pair, pairsByNode) {
  const id = dotId(nodeId)
  const ownPairs = pairsByNode.get(nodeId) || []
  if (ownPairs.length < 2) return id
  if (!pair || typeof pair.dst_clock !== 'string') return id
  const clock = pair.dst_clock
  const hasCell = ownPairs.some((q) => q.src_clock === clock)
  if (!hasCell) return id
  return `${id}:"in_${clock}"`
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
