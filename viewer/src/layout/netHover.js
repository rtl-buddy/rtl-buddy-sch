// Net hover-highlight for the Hierarchy canvas (rtl-buddy-sch#142).
//
// The Schematic tab already lights a net under the cursor
// (``highlightFor`` in ``elkSchematic.js``), but it owns a draw model
// with pin ids on every wire end. The Hierarchy canvas has no such
// model: it is a viz.js SVG whose only record of an edge is the
// Graphviz ``<title>`` (``from[:port]->to[:port]``) and whatever label
// text Graphviz placed inside the edge's group. So this module works in
// two layers:
//
// 1. ``readEdgeDescriptors(svg)`` — the one DOM read. Turns each
//    ``g.edge`` into a plain ``{id, from, fromPort, to, toPort, nets}``
//    record, once per layout.
// 2. ``netHighlightFor(edges, hover)`` — a pure function over those
//    records that answers "which edges light up", the same shape of
//    question ``highlightFor`` answers for the schematic.
//
// What counts as "the same net" is deliberately conservative — a
// hover that lights half the canvas tells the reader nothing:
//
// - a scope port anchor (``_in_<net>`` / ``_out_<net>``, emitted by the
//   producer's block-diagram DOT) IS a net, named by its suffix;
// - a block-diagram dataflow edge carries its net names as its label
//   lines (one ``<text>`` per ``\l`` line), and a bundle edge carries
//   the bundle name — both are names in the same scope namespace the
//   anchors use, so ``u_a -> u_b`` labelled ``result`` and
//   ``u_b -> _out_result`` are one net;
// - an edge leaving a named port (``"a":out_clk``, the CDC port grids
//   ``graphToDot`` builds) shares a driver with every other edge leaving
//   that port — the schematic's "fan-out from one source pin is one
//   piece of copper" rule.
//
// Label lines with whitespace are not net names (the CDC ``a → b``
// labels, the ``…(+N more)`` overflow line) and are ignored.

/** The empty answer, shared so a null hover allocates nothing. */
export const NO_EDGES = Object.freeze(new Set())

// Graphviz compass points. ``"a":e`` in an edge title is a side of the
// node, not a port on it.
const COMPASS = new Set(['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw', 'c', '_'])

const ANCHOR_RE = /^_(in|out)_(.+)$/

/**
 * Split one side of a Graphviz edge title into node id and port.
 *
 * ``"blk_top.u_cons:e"`` → ``{node: 'blk_top.u_cons', port: null}``
 * (compass point dropped), ``"a:out_clk"`` → ``{node: 'a', port:
 * 'out_clk'}``, ``"a:out_clk:e"`` → the same. Instance paths never
 * contain ``:``, so the first colon is the node/port boundary.
 */
export function parseEndpoint(raw) {
  const s = typeof raw === 'string' ? raw.trim() : ''
  if (!s) return { node: '', port: null }
  const parts = s.split(':')
  const node = parts[0]
  const port = parts.length > 1 && !COMPASS.has(parts[1]) ? parts[1] : null
  return { node, port }
}

/** The scope-port net a ``_in_<net>`` / ``_out_<net>`` anchor stands for, or null. */
export function anchorNet(nodeId) {
  const m = typeof nodeId === 'string' ? ANCHOR_RE.exec(nodeId) : null
  return m ? m[2] : null
}

/** Whether one label line of an edge is a net (or bundle) name. */
export function isNetLabel(text) {
  if (typeof text !== 'string') return false
  const s = text.trim()
  return s.length > 0 && !/\s/.test(s) && !s.startsWith('…')
}

/**
 * Build one edge record from its Graphviz title and label lines.
 *
 * ``nets`` is the edge's set of net keys — the thing two edges must
 * share to be the same net. Namespaced so a net called ``a`` and a
 * driver port on node ``a`` can never collide.
 */
export function describeEdge({ id, title, labels = [] }) {
  if (!id || typeof title !== 'string') return null
  const arrow = title.indexOf('->')
  if (arrow < 0) return null
  const from = parseEndpoint(title.slice(0, arrow))
  const to = parseEndpoint(title.slice(arrow + 2))
  if (!from.node || !to.node) return null
  const nets = new Set()
  for (const end of [from.node, to.node]) {
    const net = anchorNet(end)
    if (net !== null) nets.add(`net:${net}`)
  }
  for (const line of labels) {
    if (isNetLabel(line)) nets.add(`net:${line.trim()}`)
  }
  if (from.port) nets.add(`drv:${from.node}:${from.port}`)
  return {
    id,
    from: from.node,
    fromPort: from.port,
    to: to.node,
    toPort: to.port,
    nets,
  }
}

/**
 * Which edges light up for a hover. Returns a Set of edge ids.
 *
 * - ``edgeId`` — the reader is pointing at a wire and means *the net*:
 *   that edge plus every edge sharing one of its net keys.
 * - ``nodeId`` — the reader is pointing at an endpoint and means
 *   *everything attached to it*: every edge with that node at either
 *   end. A port anchor is a net in its own right, so its net's other
 *   edges (a dataflow edge labelled with the same name) come along; an
 *   instance is not — it touches many nets, and pulling all of them in
 *   would light the scope.
 */
export function netHighlightFor(edges, { edgeId = null, nodeId = null } = {}) {
  if (!Array.isArray(edges) || edges.length === 0 || (!edgeId && !nodeId)) {
    return NO_EDGES
  }
  const hot = new Set()
  let keys = null
  if (edgeId) {
    const self = edges.find((e) => e && e.id === edgeId)
    if (!self) return NO_EDGES
    hot.add(self.id)
    keys = self.nets
  } else {
    for (const e of edges) {
      if (e && (e.from === nodeId || e.to === nodeId)) hot.add(e.id)
    }
    const net = anchorNet(nodeId)
    if (net !== null) keys = new Set([`net:${net}`])
  }
  if (keys && keys.size > 0) {
    for (const e of edges) {
      if (!e || hot.has(e.id)) continue
      for (const k of e.nets) {
        if (keys.has(k)) {
          hot.add(e.id)
          break
        }
      }
    }
  }
  return hot.size > 0 ? hot : NO_EDGES
}

/**
 * Read every rendered ``g.edge`` into an edge record. The one DOM pass;
 * call it once per layout, not per mouse event.
 *
 * Keyed on the group's ``id`` (viz.js numbers them ``edge1…``, and the
 * block-flow builder sets its own), so a record maps straight back to
 * its element. Edges without an id or a parseable title are skipped —
 * they cannot be hovered meaningfully.
 */
export function readEdgeDescriptors(svgRoot) {
  if (!svgRoot) return []
  const out = []
  for (const group of svgRoot.querySelectorAll('g.edge')) {
    const titleEl = group.querySelector('title')
    // Every ``<text>`` in an edge group is one of its label lines
    // (``label`` / ``xlabel``, one element per ``\l`` line), wherever
    // a tooltip/URL ``<a>`` wrapper put it.
    const labels = Array.from(group.querySelectorAll('text')).map((t) => t.textContent || '')
    const rec = describeEdge({
      id: group.getAttribute('id'),
      title: titleEl ? titleEl.textContent : null,
      labels,
    })
    if (rec) out.push(rec)
  }
  return out
}
