// Edge endpoint stamps on the Hierarchy canvas (rtl-buddy-sch#197).
//
// GraphCanvas stamps ``data-edge-from`` / ``data-edge-to`` on every
// viz.js edge group; edge selection and the clock / reset overlays look
// edges up by those attributes using the plain ids in ``graph.edges``.
// Graphviz keeps ``:port[:compass]`` in the edge title, so a CDC edge
// ``graphToDot`` routed to a per-clock grid cell used to be stamped
// ``top.u_b:in_clk_a`` and matched none of those lookups. These tests
// pin the stamp and both overlays against a happy-dom SVG shaped like
// viz.js output; ``e2e/edge-port-stamp.spec.js`` drives the real canvas.

import { describe, expect, it } from 'vitest'
import { stampEdgeEndpoints } from '../src/layout/netHover.js'
import { getOverlay } from '../src/overlays/index.js'
import { token } from '../src/theme.js'

// One graph edge with two clock pairs renders as two DOT edges, each
// entering its own ``in_<clk>`` cell; a third edge is a plain one with
// only a compass point; a fourth has no title and must stay unstamped.
function svgFixture() {
  const host = document.createElement('div')
  host.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg"><g id="graph0" class="graph">
      <g id="edge1" class="edge"><title>top.u_a&#45;&gt;top.u_b:in_clk_a</title>
        <path d="M0,0 L10,0"/><path class="hit" d="M0,0 L10,0"/></g>
      <g id="edge2" class="edge"><title>top.u_a:out_clk_c:e&#45;&gt;top.u_b:in_clk_c:w</title>
        <g><a><path d="M0,0 L10,0"/></a></g></g>
      <g id="edge3" class="edge"><title>top.u_a:e&#45;&gt;top.u_c</title><path d="M0,0"/></g>
      <g id="edge4" class="edge" data-edge-from="stale" data-edge-to="stale"><path d="M0,0"/></g>
    </g></svg>`
  return host.querySelector('svg')
}

const attrs = (g) => ({
  from: g.getAttribute('data-edge-from'),
  to: g.getAttribute('data-edge-to'),
  fromPort: g.getAttribute('data-edge-from-port'),
  toPort: g.getAttribute('data-edge-to-port'),
})

describe('stampEdgeEndpoints', () => {
  it('stamps plain node ids and puts the port in its own attribute', () => {
    const svg = svgFixture()
    stampEdgeEndpoints(svg)
    expect(attrs(svg.querySelector('#edge1'))).toEqual({
      from: 'top.u_a',
      to: 'top.u_b',
      fromPort: null,
      toPort: 'in_clk_a',
    })
    expect(attrs(svg.querySelector('#edge2'))).toEqual({
      from: 'top.u_a',
      to: 'top.u_b',
      fromPort: 'out_clk_c',
      toPort: 'in_clk_c',
    })
    // A compass point is a side of the node, never a port.
    expect(attrs(svg.querySelector('#edge3'))).toEqual({
      from: 'top.u_a',
      to: 'top.u_c',
      fromPort: null,
      toPort: null,
    })
  })

  it('leaves an untitled edge unstamped, clearing a stale stamp', () => {
    const svg = svgFixture()
    stampEdgeEndpoints(svg)
    expect(attrs(svg.querySelector('#edge4'))).toEqual({
      from: null,
      to: null,
      fromPort: null,
      toPort: null,
    })
  })

  it('makes every rendering of a ported edge findable by its plain ids', () => {
    const svg = svgFixture()
    stampEdgeEndpoints(svg)
    const hits = svg.querySelectorAll('[data-edge-from="top.u_a"][data-edge-to="top.u_b"]')
    expect(Array.from(hits, (g) => g.id)).toEqual(['edge1', 'edge2'])
  })

  it('is a no-op without an SVG', () => {
    expect(() => stampEdgeEndpoints(null)).not.toThrow()
  })
})

describe('overlays paint ported edges', () => {
  const graph = (overlays) => ({
    nodes: [],
    edges: [{ from: 'top.u_a', to: 'top.u_b', port_pairs: [], overlays }],
  })
  // The visible path of each group: the first one, never the hit twin.
  const visible = (svg, id) => svg.querySelector(`#${id} path`)

  it('clock paints every per-pair rendering of a crossing edge, and clears it', () => {
    const svg = svgFixture()
    stampEdgeEndpoints(svg)
    const g = graph({ clock: { crossing: true } })
    getOverlay('clock').apply(svg, g, true)
    for (const id of ['edge1', 'edge2']) {
      expect(visible(svg, id).getAttribute('data-overlay-clock')).toBe('crossing')
      expect(visible(svg, id).getAttribute('stroke-dasharray')).toBe('4,3')
    }
    expect(svg.querySelector('#edge1 path.hit').hasAttribute('data-overlay-clock')).toBe(false)
    expect(visible(svg, 'edge3').hasAttribute('data-overlay-clock')).toBe(false)

    getOverlay('clock').apply(svg, g, false)
    for (const id of ['edge1', 'edge2']) {
      expect(visible(svg, id).hasAttribute('data-overlay-clock')).toBe(false)
    }
  })

  it('clock paints a recognised-safe crossing solid teal, not the hazard dash (#211)', () => {
    const svg = svgFixture()
    stampEdgeEndpoints(svg)
    getOverlay('clock').apply(svg, graph({ clock: { crossing: true, hazard: false } }), true)
    for (const id of ['edge1', 'edge2']) {
      expect(visible(svg, id).getAttribute('data-overlay-clock')).toBe('synchronized')
      expect(visible(svg, id).hasAttribute('stroke-dasharray')).toBe(false)
      expect(visible(svg, id).style.stroke).toBe(token('--reset-sync'))
    }
  })

  it('an explicit hazard: true, or no hazard key at all, stays the dashed hazard', () => {
    for (const clock of [{ crossing: true, hazard: true }, { crossing: true }]) {
      const svg = svgFixture()
      stampEdgeEndpoints(svg)
      getOverlay('clock').apply(svg, graph({ clock }), true)
      expect(visible(svg, 'edge1').getAttribute('data-overlay-clock')).toBe('crossing')
      expect(visible(svg, 'edge1').getAttribute('stroke-dasharray')).toBe('4,3')
    }
  })

  it('an RDC hazard claims a synchronized CDC edge, but never a ⚠CDC one', () => {
    const both = (hazard) =>
      graph({ clock: { crossing: true, hazard }, reset: { crossing: true } })
    let svg = svgFixture()
    stampEdgeEndpoints(svg)
    getOverlay('clock').apply(svg, both(false), true)
    getOverlay('reset').apply(svg, both(false), true)
    expect(visible(svg, 'edge1').getAttribute('data-overlay-reset')).toBe('crossing')
    svg = svgFixture()
    stampEdgeEndpoints(svg)
    getOverlay('clock').apply(svg, both(true), true)
    getOverlay('reset').apply(svg, both(true), true)
    expect(visible(svg, 'edge1').hasAttribute('data-overlay-reset')).toBe(false)
  })

  it('reset paints a ported crossing edge the clock overlay has not claimed', () => {
    const svg = svgFixture()
    stampEdgeEndpoints(svg)
    getOverlay('reset').apply(svg, graph({ reset: { crossing: true } }), true)
    for (const id of ['edge1', 'edge2']) {
      expect(visible(svg, id).getAttribute('data-overlay-reset')).toBe('crossing')
    }
  })
})
