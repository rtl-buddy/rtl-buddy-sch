// Tests for the Hierarchy canvas's net hover-highlight (rtl-buddy-sch#142).
//
// ``netHighlightFor`` is the decision — which edges light up for a
// hover — and is pinned here as a pure function over edge records.
// ``readEdgeDescriptors`` is the one DOM read that builds those records
// from a viz.js SVG; it gets a happy-dom fixture shaped like what
// viz.js actually emits (titles keep ports and compass points, label
// lines are separate ``<text>`` elements). The live hover wiring in
// GraphCanvas is exercised in a real browser by ``e2e/net-hover.spec.js``.

import { describe, expect, it } from 'vitest'
import {
  NO_EDGES,
  anchorNet,
  describeEdge,
  isNetLabel,
  netHighlightFor,
  parseEndpoint,
  readEdgeDescriptors,
} from '../src/layout/netHover.js'

describe('parseEndpoint', () => {
  it('drops a compass point — it is a side of the node, not a port', () => {
    expect(parseEndpoint('blk_top.u_cons:e')).toEqual({ node: 'blk_top.u_cons', port: null })
    expect(parseEndpoint('_out_x:w')).toEqual({ node: '_out_x', port: null })
  })
  it('keeps a named port, with or without a trailing compass point', () => {
    expect(parseEndpoint('top.u_a:out_clk_a')).toEqual({ node: 'top.u_a', port: 'out_clk_a' })
    expect(parseEndpoint('top.u_a:out_clk_a:e')).toEqual({ node: 'top.u_a', port: 'out_clk_a' })
  })
  it('a bare node id has no port; junk degrades to an empty node', () => {
    expect(parseEndpoint(' top.u_a ')).toEqual({ node: 'top.u_a', port: null })
    expect(parseEndpoint('')).toEqual({ node: '', port: null })
    expect(parseEndpoint(null)).toEqual({ node: '', port: null })
  })
})

describe('anchorNet / isNetLabel', () => {
  it('names the scope-port net behind an anchor node', () => {
    expect(anchorNet('_in_cmd_in')).toBe('cmd_in')
    expect(anchorNet('_out_result_out')).toBe('result_out')
    expect(anchorNet('top.u_in_x')).toBeNull()
    expect(anchorNet('_rb_unconstrained')).toBeNull()
  })
  it('accepts net and bundle names, rejects prose label lines', () => {
    expect(isNetLabel('prod_valid')).toBe(true)
    expect(isNetLabel('prod_payload_q[7:0]')).toBe(true)
    expect(isNetLabel('cmd_bus')).toBe(true)
    // CDC labels and the overflow line are not nets.
    expect(isNetLabel('clk_a → clk_b')).toBe(false)
    expect(isNetLabel('…(+3 more)')).toBe(false)
    expect(isNetLabel('  ')).toBe(false)
  })
})

describe('describeEdge', () => {
  it('keys an anchor edge on its net', () => {
    const e = describeEdge({ id: 'edge1', title: '_in_cmd_in:e->blk_top.u_prod' })
    expect(e).toMatchObject({ id: 'edge1', from: '_in_cmd_in', to: 'blk_top.u_prod' })
    expect([...e.nets]).toEqual(['net:cmd_in'])
  })
  it('keys a dataflow edge on its label nets, and a port-sourced edge on its driver', () => {
    const flow = describeEdge({
      id: 'edge2',
      title: 'top.u_a->top.u_b',
      labels: ['valid', 'data[7:0]', '…(+2 more)'],
    })
    expect([...flow.nets].sort()).toEqual(['net:data[7:0]', 'net:valid'])
    const cdc = describeEdge({
      id: 'edge3',
      title: 'top.u_a:out_clk_a->top.u_b:in_clk_a',
      labels: ['clk_a → clk_b'],
    })
    expect([...cdc.nets]).toEqual(['drv:top.u_a:out_clk_a'])
    expect(cdc).toMatchObject({ fromPort: 'out_clk_a', toPort: 'in_clk_a' })
  })
  it('rejects records it cannot join back to an element or an endpoint', () => {
    expect(describeEdge({ id: '', title: 'a->b' })).toBeNull()
    expect(describeEdge({ id: 'e', title: 'no arrow' })).toBeNull()
    expect(describeEdge({ id: 'e', title: '->b' })).toBeNull()
  })
})

// A block-diagram-shaped scope: two scope inputs, a producer feeding a
// consumer, the consumer driving a scope output, plus a CDC port grid
// fanning out of one driver port.
const EDGES = [
  describeEdge({ id: 'in_cmd', title: '_in_cmd:e->top.u_prod' }),
  describeEdge({ id: 'in_cmd2', title: '_in_cmd:e->top.u_cons' }),
  describeEdge({ id: 'in_cfg', title: '_in_cfg:e->top.u_cons' }),
  describeEdge({ id: 'flow', title: 'top.u_prod:e->top.u_cons:w', labels: ['valid', 'payload'] }),
  describeEdge({ id: 'fan', title: 'top.u_prod:e->top.u_mon:w', labels: ['valid'] }),
  describeEdge({ id: 'out', title: 'top.u_cons:e->_out_result:w' }),
  describeEdge({ id: 'res', title: 'top.u_mon:e->top.u_log:w', labels: ['result'] }),
  describeEdge({ id: 'cdc1', title: 'top.u_x:out_a->top.u_y:in_a', labels: ['a → b'] }),
  describeEdge({ id: 'cdc2', title: 'top.u_x:out_a->top.u_z:in_a', labels: ['a → b'] }),
  describeEdge({ id: 'cdc3', title: 'top.u_x:out_b->top.u_y:in_b', labels: ['b → a'] }),
]

const ids = (set) => [...set].sort()

describe('netHighlightFor', () => {
  it('returns the shared empty answer for no hover / no edges / unknown edge', () => {
    expect(netHighlightFor(EDGES, {})).toBe(NO_EDGES)
    expect(netHighlightFor([], { edgeId: 'flow' })).toBe(NO_EDGES)
    expect(netHighlightFor(null, { nodeId: 'top.u_prod' })).toBe(NO_EDGES)
    expect(netHighlightFor(EDGES, { edgeId: 'nope' })).toBe(NO_EDGES)
    expect(netHighlightFor(EDGES, { nodeId: 'top.nobody' })).toBe(NO_EDGES)
  })

  it('an anchor edge lights every edge of its net', () => {
    expect(ids(netHighlightFor(EDGES, { edgeId: 'in_cmd' }))).toEqual(['in_cmd', 'in_cmd2'])
  })

  it('a dataflow edge lights the fan-out that shares one of its nets', () => {
    // ``flow`` carries valid + payload; ``fan`` carries valid too.
    expect(ids(netHighlightFor(EDGES, { edgeId: 'flow' }))).toEqual(['fan', 'flow'])
    // …but ``fan`` only carries valid, so it does not pull ``in_cmd``.
    expect(ids(netHighlightFor(EDGES, { edgeId: 'fan' }))).toEqual(['fan', 'flow'])
  })

  it('a label net and a port anchor of the same name are one net', () => {
    expect(ids(netHighlightFor(EDGES, { edgeId: 'out' }))).toEqual(['out', 'res'])
  })

  it('edges leaving one driver port are one net; another port is not', () => {
    expect(ids(netHighlightFor(EDGES, { edgeId: 'cdc1' }))).toEqual(['cdc1', 'cdc2'])
    expect(ids(netHighlightFor(EDGES, { edgeId: 'cdc3' }))).toEqual(['cdc3'])
  })

  it('an instance lights what is attached to it, and nothing beyond', () => {
    expect(ids(netHighlightFor(EDGES, { nodeId: 'top.u_cons' }))).toEqual([
      'flow',
      'in_cfg',
      'in_cmd2',
      'out',
    ])
  })

  it('a port anchor lights its net, including a dataflow edge that carries it', () => {
    expect(ids(netHighlightFor(EDGES, { nodeId: '_out_result' }))).toEqual(['out', 'res'])
    expect(ids(netHighlightFor(EDGES, { nodeId: '_in_cmd' }))).toEqual(['in_cmd', 'in_cmd2'])
  })
})

describe('readEdgeDescriptors', () => {
  // Shaped like viz.js output: the edge title keeps ports + compass
  // points (HTML-escaped ``->``), xlabel lines are one <text> each, and
  // a tooltip wraps the drawing in a nested <g><a>.
  function svgFixture() {
    const host = document.createElement('div')
    host.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg"><g id="graph0" class="graph">
        <g id="node1" class="node"><title>_in_cmd</title><text>cmd ▶</text></g>
        <g id="edge1" class="edge"><title>_in_cmd:e&#45;&gt;top.u_prod</title>
          <path d="M0,0 L10,0"/><polygon points="0,0"/></g>
        <g id="edge2" class="edge"><title>top.u_prod:e&#45;&gt;top.u_cons:w</title>
          <g id="a_edge2"><a><path d="M0,0 L10,0"/><text>valid</text><text>…(+1 more)</text></a></g></g>
        <g class="edge"><title>no_id&#45;&gt;skipped</title><path d="M0,0"/></g>
      </g></svg>`
    return host.querySelector('svg')
  }

  it('reads one record per identifiable edge, in document order', () => {
    const recs = readEdgeDescriptors(svgFixture())
    expect(recs.map((r) => r.id)).toEqual(['edge1', 'edge2'])
    expect(recs[0]).toMatchObject({ from: '_in_cmd', to: 'top.u_prod', fromPort: null })
    expect([...recs[0].nets]).toEqual(['net:cmd'])
    expect([...recs[1].nets]).toEqual(['net:valid'])
  })

  it('is empty without an SVG', () => {
    expect(readEdgeDescriptors(null)).toEqual([])
  })
})
