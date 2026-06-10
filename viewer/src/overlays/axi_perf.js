// 'axi-perf' overlay renderer (Phase 11 → interface-pin unification).
//
// The AXI performance overlay now paints ONTO the existing tb-top /
// interface-port mechanism: each ``node.overlays['axi-perf'].bundle_pins``
// entry decorates the interface-port pin the block-flow layout already
// draws for that bundle (the ▶▶ ``bf-iface:`` cell, or — when the view
// flattened the interface into per-signal ports — the group of
// ``bf-in:/bf-out:<node>:<port>.*`` cells). Decoration is:
//
//   * an outline around the pin, coloured by peak backpressure
//     (green / amber / red),
//   * a compact badge with aggregate throughput + an error glyph,
//   * a short "boundary stub" marker when the bundle's peer endpoint is
//     not a sibling instance (e.g. a procedural testbench master/slave
//     with no module-instance node), so the pin reads as "connects out
//     of the drawn scope" rather than dangling.
//
// The deep-dive per-channel surface stays in ``AxiPerfView.vue``; this
// is the at-a-glance heatmap layer on the schematic. Decoration is
// append-only (never mutates the cell's own styles) so toggling the
// overlay off restores the pristine canvas — same discipline as the
// wave overlay.

import { formatBandwidth } from '../format.js'

const PIN_CLASS = 'rb-axi-pin' // outline rect around the decorated pin
const BADGE_CLASS = 'rb-axi-badge' // throughput / error text badge
const STUB_CLASS = 'rb-axi-stub' // boundary-peer stub marker
const PIN_ATTR = 'data-axi-pin' // marker stamped on decorated cells

function cssEscape(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s)
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`)
}

function strokeForBackpressure(bpPct) {
  if (bpPct > 15) return '#dc2626' // red-600
  if (bpPct > 5) return '#f59e0b' // amber-500
  return '#16a34a' // green-600
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

// Human-readable bandwidth — bytes/s, decimal (MB/s, GB/s), shared with the
// AXI Performance tab and node detail so all views agree. `read_bps` is
// bits/s (profiler convention); formatBandwidth does the bits->bytes +
// decimal scale and returns a unit-suffixed string ("2.20 GB/s").
const formatBps = formatBandwidth

// Collapse a bundle_pin into the visual primitives the canvas paints.
// Pure (no DOM) so the colour/width/label mapping is unit-testable.
export function bundlePinVisual(pin) {
  const b = (pin && pin.bundle) || null
  const bp = edgeMaxBackpressure(b)
  const bps = totalBps(b)
  const role = pin && (pin.role === 'master' || pin.role === 'slave') ? pin.role : null
  const arrow = role === 'master' ? '▶' : role === 'slave' ? '◀' : '↔'
  return {
    color: strokeForBackpressure(bp),
    strokeWidth: Number(strokeWidthForBps(bps)),
    bpPct: bp,
    totalBps: bps,
    hasErrors: hasErrors(b),
    role,
    arrow,
  }
}

// A pin's peer endpoint is a "boundary" (drawn as a stub, not an edge to
// a sibling box) when it doesn't resolve to a sibling instance node:
// either it's absent, not a node at all, or an ancestor/container of
// this node (the common case for a procedural-testbench master/slave,
// where ``peer`` is the tb-top scope). Pure; tested directly.
export function isBoundaryPeer(nodeId, peer, nodeIds) {
  if (!peer || typeof peer !== 'string') return true
  if (!nodeIds || !nodeIds.has(peer)) return true
  // Ancestor of this node (peer is a prefix scope, e.g. the tb-top).
  if (nodeId === peer || (typeof nodeId === 'string' && nodeId.startsWith(`${peer}.`))) {
    return true
  }
  return false
}

// Resolve the SVG cell(s) that represent ``port`` on ``nodeId`` in the
// block-flow layout: the single ▶▶ ``bf-iface:`` cell when the interface
// is unflattened, else the group of flattened ``bf-in:/bf-out:`` signal
// cells that share the ``<port>.`` prefix.
function pinCells(group, nodeId, port) {
  if (!group || typeof group.querySelector !== 'function') return []
  const iface = group.querySelector(
    `[data-bf-id="bf-iface:${cssEscape(nodeId)}:${cssEscape(port)}"]`,
  )
  if (iface) return [iface]
  if (typeof group.querySelectorAll !== 'function') return []
  const inPrefix = `bf-in:${nodeId}:${port}.`
  const outPrefix = `bf-out:${nodeId}:${port}.`
  const out = []
  for (const cell of Array.from(group.querySelectorAll('[data-bf-id]'))) {
    const v = cell.getAttribute('data-bf-id') || ''
    if (v.startsWith(inPrefix) || v.startsWith(outPrefix)) out.push(cell)
  }
  return out
}

// Union bounding box over a set of cells. Returns null when no cell
// yields a usable getBBox (e.g. detached nodes / headless contexts).
function unionBBox(cells) {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  let any = false
  for (const cell of cells) {
    if (typeof cell.getBBox !== 'function') continue
    let bb
    try {
      bb = cell.getBBox()
    } catch {
      continue
    }
    if (!bb || !isFinite(bb.x) || !isFinite(bb.y)) continue
    minX = Math.min(minX, bb.x)
    minY = Math.min(minY, bb.y)
    maxX = Math.max(maxX, bb.x + bb.width)
    maxY = Math.max(maxY, bb.y + bb.height)
    any = true
  }
  if (!any) return null
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY }
}

function clearDecorations(svgRoot) {
  if (!svgRoot || typeof svgRoot.querySelectorAll !== 'function') return
  for (const el of Array.from(
    svgRoot.querySelectorAll(`.${PIN_CLASS}, .${BADGE_CLASS}, .${STUB_CLASS}`),
  )) {
    el.remove()
  }
  for (const cell of Array.from(svgRoot.querySelectorAll(`[${PIN_ATTR}]`))) {
    cell.removeAttribute(PIN_ATTR)
  }
}

function decoratePin(group, cells, vis, pin, boundary) {
  const NS = 'http://www.w3.org/2000/svg'
  // Stamp the cell(s) so the join is inspectable / clearable even when
  // geometry isn't available (headless capture, tests). The colour
  // rides along so a CSS-driven theme could pick it up too.
  for (const cell of cells) {
    if (typeof cell.setAttribute === 'function') {
      cell.setAttribute(PIN_ATTR, pin.interface_instance || pin.port || '')
    }
  }
  const bbox = unionBBox(cells)
  if (!bbox) return // no geometry → cells still stamped; skip the visuals

  const pad = 2
  const outline = document.createElementNS(NS, 'rect')
  outline.setAttribute('class', PIN_CLASS)
  outline.setAttribute('x', String(bbox.x - pad))
  outline.setAttribute('y', String(bbox.y - pad))
  outline.setAttribute('width', String(bbox.width + 2 * pad))
  outline.setAttribute('height', String(bbox.height + 2 * pad))
  outline.setAttribute('fill', 'none')
  outline.setAttribute('stroke', vis.color)
  outline.setAttribute('stroke-width', String(Math.max(1.5, vis.strokeWidth)))
  outline.setAttribute('rx', '2')
  outline.setAttribute('pointer-events', 'none')
  group.appendChild(outline)

  // Badge: role arrow + aggregate throughput + boundary/error glyphs.
  // Slave pins sit on the left edge → hang the badge left; master/other
  // hang right, mirroring the wave overlay's in/out convention.
  let label = `${vis.arrow} ${formatBps(vis.totalBps)}`
  if (boundary) label += ' ·ext'
  if (vis.hasErrors) label += ' ⚠'
  const badge = document.createElementNS(NS, 'text')
  badge.setAttribute('class', BADGE_CLASS)
  badge.setAttribute('font-family', 'ui-monospace, Menlo, Consolas, monospace')
  badge.setAttribute('font-size', '9')
  badge.setAttribute('fill', vis.color)
  badge.setAttribute('pointer-events', 'none')
  const gap = 4
  if (vis.role === 'slave') {
    badge.setAttribute('x', String(bbox.x - pad - gap))
    badge.setAttribute('text-anchor', 'end')
  } else {
    badge.setAttribute('x', String(bbox.x + bbox.width + pad + gap))
    badge.setAttribute('text-anchor', 'start')
  }
  badge.setAttribute('y', String(bbox.y + bbox.height / 2))
  badge.setAttribute('dominant-baseline', 'middle')
  badge.textContent = label
  group.appendChild(badge)

  // Boundary stub: a short dashed tick off the pin pointing out of the
  // drawn scope, marking the procedural / external peer.
  if (boundary) {
    const stub = document.createElementNS(NS, 'line')
    stub.setAttribute('class', STUB_CLASS)
    const yMid = bbox.y + bbox.height / 2
    if (vis.role === 'slave') {
      stub.setAttribute('x1', String(bbox.x - pad))
      stub.setAttribute('x2', String(bbox.x - pad - gap))
    } else {
      stub.setAttribute('x1', String(bbox.x + bbox.width + pad))
      stub.setAttribute('x2', String(bbox.x + bbox.width + pad + gap))
    }
    stub.setAttribute('y1', String(yMid))
    stub.setAttribute('y2', String(yMid))
    stub.setAttribute('stroke', vis.color)
    stub.setAttribute('stroke-width', '1.5')
    stub.setAttribute('stroke-dasharray', '2,2')
    stub.setAttribute('pointer-events', 'none')
    group.appendChild(stub)
  }
}

// Aggregate one node's bundle pins into a single summary badge for the
// hier view (which draws no per-port interface cells). Worst-case
// backpressure colour, summed throughput, OR'd errors. Pure mapping
// split out for testability.
export function aggregateNodeBundles(pins) {
  let worstBp = 0
  let bps = 0
  let errs = false
  let count = 0
  for (const pin of pins || []) {
    const v = bundlePinVisual(pin)
    if (v.bpPct > worstBp) worstBp = v.bpPct
    bps += v.totalBps
    errs = errs || v.hasErrors
    count += 1
  }
  return {
    color: strokeForBackpressure(worstBp),
    totalBps: bps,
    worstBp,
    hasErrors: errs,
    count,
    label: `AXI ${formatBps(bps)}${errs ? ' ⚠' : ''}`,
  }
}

// Multi-line hover text for the aggregate badge: per-bundle throughput
// + backpressure, then the totals and a click hint. Newlines render as
// a multi-line native tooltip via an SVG <title>.
function aggregateTooltip(pins, agg) {
  const rank = (r) => (r === 'master' ? 0 : r === 'slave' ? 1 : 2)
  const lines = [`AXI bundles (${agg.count}):`]
  for (const pin of [...pins].sort(
    (a, b) => rank(a.role) - rank(b.role) || (a.port < b.port ? -1 : 1),
  )) {
    const v = bundlePinVisual(pin)
    lines.push(
      `  ${pin.port} (${pin.role || '—'}): ${formatBps(v.totalBps)}` +
        ` · bp ${v.bpPct.toFixed(0)}%${v.hasErrors ? ' ⚠ errors' : ''}`,
    )
  }
  lines.push(
    `Total ${formatBps(agg.totalBps)} · worst bp ${agg.worstBp.toFixed(0)}%`,
  )
  lines.push('Click to open AXI Performance')
  return lines.join('\n')
}

function paintNodeAggregate(group, pins, nodeId) {
  const agg = aggregateNodeBundles(pins)
  if (agg.count === 0) return
  const shape =
    typeof group.querySelector === 'function'
      ? group.querySelector('polygon, ellipse, rect, path')
      : null
  if (!shape || typeof shape.getBBox !== 'function') return
  let bbox
  try {
    bbox = shape.getBBox()
  } catch {
    return
  }
  if (!bbox || !isFinite(bbox.x) || !isFinite(bbox.y)) return
  const NS = 'http://www.w3.org/2000/svg'
  const inset = 4
  const badge = document.createElementNS(NS, 'text')
  badge.setAttribute('class', BADGE_CLASS)
  badge.setAttribute('font-family', 'ui-monospace, Menlo, Consolas, monospace')
  badge.setAttribute('font-size', '10')
  badge.setAttribute('font-weight', 'bold')
  badge.setAttribute('fill', agg.color)
  // Interactive: hover shows the per-bundle breakdown (SVG <title>);
  // left-click opens the AXI Performance tab — GraphCanvas.onClick
  // detects the click via the data-axi-open marker.
  badge.setAttribute('pointer-events', 'auto')
  badge.setAttribute('cursor', 'pointer')
  if (nodeId) badge.setAttribute('data-axi-open', nodeId)
  // Top-RIGHT corner: the node name label sits at the top-left, so a
  // left-anchored badge overlapped it. Anchor at the right edge.
  badge.setAttribute('x', String(bbox.x + bbox.width - inset))
  badge.setAttribute('y', String(bbox.y + inset))
  badge.setAttribute('text-anchor', 'end')
  badge.setAttribute('dominant-baseline', 'hanging')
  badge.textContent = agg.label
  // Append the tooltip AFTER textContent (textContent would otherwise
  // wipe child nodes).
  const title = document.createElementNS(NS, 'title')
  title.textContent = aggregateTooltip(pins, agg)
  badge.appendChild(title)
  group.appendChild(badge)
}

export const axiPerfOverlay = {
  name: 'axi-perf',

  /**
   * Paint per-bundle perf decoration onto interface-port pins.
   * Idempotent (clears prior decoration first); toggling ``enabled``
   * off restores the pristine canvas. Reads
   * ``node.overlays['axi-perf'].bundle_pins`` produced by
   * json_render's interface-instance join.
   */
  apply(svgRoot, graph, enabled, _context = {}) {
    if (!svgRoot || !graph || !Array.isArray(graph.nodes)) return
    // Always clear first so re-apply / toggle is idempotent.
    clearDecorations(svgRoot)
    if (!enabled) return
    const nodeIds = new Set(graph.nodes.map((n) => n && n.id).filter(Boolean))
    for (const node of graph.nodes) {
      const block = node && node.overlays && node.overlays['axi-perf']
      const pins = block && Array.isArray(block.bundle_pins) ? block.bundle_pins : null
      if (!pins || pins.length === 0) continue
      const group = svgRoot.querySelector(`[data-node-id="${cssEscape(node.id)}"]`)
      if (!group) continue
      let paintedAnyPin = false
      for (const pin of pins) {
        if (!pin || typeof pin.port !== 'string') continue
        const cells = pinCells(group, node.id, pin.port)
        if (cells.length === 0) continue
        const vis = bundlePinVisual(pin)
        const boundary = isBoundaryPeer(node.id, pin.peer, nodeIds)
        decoratePin(group, cells, vis, pin, boundary)
        paintedAnyPin = true
      }
      // Hier view (and any layout without per-port interface cells) has
      // nothing to anchor a pin badge to. Fall back to one aggregate
      // badge on the node's box so the bundle's perf is still visible
      // at a glance in the default view — mirrors the wave overlay's
      // hier-view fallback.
      if (!paintedAnyPin) {
        paintNodeAggregate(group, pins, node.id)
      }
    }
  },

  /**
   * Legend entries for OverlayPanel.vue — the backpressure colour
   * scale + an error note, only when the graph actually carries
   * bundle pins (keeps the panel honest about what's on screen).
   */
  legend(graph) {
    const nodes = (graph && Array.isArray(graph.nodes) && graph.nodes) || []
    const hasPins = nodes.some((n) => {
      const b = n && n.overlays && n.overlays['axi-perf']
      return b && Array.isArray(b.bundle_pins) && b.bundle_pins.length > 0
    })
    if (!hasPins) return []
    return [
      { label: 'AXI backpressure low', swatch: '#16a34a', kind: 'stroke' },
      { label: 'AXI backpressure med', swatch: '#f59e0b', kind: 'stroke' },
      { label: 'AXI backpressure high', swatch: '#dc2626', kind: 'stroke' },
    ]
  },
}

// Retained helpers — used by AxiPerfView.vue (per-tab deep dive) and
// the unit tests.
export {
  cssEscape,
  strokeForBackpressure,
  strokeWidthForBps,
  edgeMaxBackpressure,
  totalBps,
  hasErrors,
  formatBps,
}

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
