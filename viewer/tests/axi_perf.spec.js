// Tests for the axi-perf overlay JS module.
//
// apply() is exercised against an SVG mock; the AxiPerfPane.vue
// presentational logic isn't covered here (it's a Playwright /
// e2e concern), but the helper selectors are.

import { describe, expect, it, vi } from 'vitest'
import { JSDOM } from 'happy-dom'

import { axiPerfOverlay } from '../src/overlays/axi_perf.js'
import {
  selectedEdgeAxiPerf,
  nodeAxiPerfInterconnect,
  bundlePinVisual,
  isBoundaryPeer,
  formatBps,
  aggregateNodeBundles,
} from '../src/overlays/axi_perf.js'
import { getOverlay } from '../src/overlays/index.js'

function _bundleBlock(overrides = {}) {
  return {
    name: 'cpu_to_dram',
    protocol: 'AXI4',
    data_width: 64,
    id_width: 4,
    default_view: 'parent',
    channels: {
      ar: { util_pct: 30, bp_pct: 5, peak_occ: 8, txns: 1000 },
      aw: { util_pct: 15, bp_pct: 1, peak_occ: 4, txns: 500 },
      r:  { util_pct: 60, bp_pct: 20, peak_occ: 12, beats: 8000 },
      w:  { util_pct: 35, bp_pct: 8, peak_occ: 6, beats: 2000 },
      b:  { util_pct: 5, bp_pct: 0, peak_occ: 2, txns: 500 },
    },
    throughput: { read_bps: 1e9, write_bps: 5e8 },
    outstanding: { read_peak: 12, read_avg: 6, write_peak: 4, write_avg: 2 },
    latency_cycles: {
      ar_to_r_first: { p50: 10, p95: 50, p99: 100, max: 200, hist_log2: Array(16).fill(0) },
      aw_to_b: { p50: 20, p95: 60, p99: 120, max: 240, hist_log2: Array(16).fill(0) },
    },
    errors: { slverr: 0, decerr: 0 },
    ...overrides,
  }
}

describe('axi-perf overlay registry', () => {
  it('is registered by name in the BUILTINS map', () => {
    expect(getOverlay('axi-perf')).not.toBeNull()
    expect(getOverlay('axi-perf').name).toBe('axi-perf')
  })
})

describe('selectedEdgeAxiPerf', () => {
  it('returns the bundle block when the selected edge matches', () => {
    const graph = {
      edges: [{
        from: 'soc.u_cpu',
        to: 'soc.u_dram',
        overlays: { 'axi-perf': _bundleBlock() },
      }],
    }
    const sel = { from: 'soc.u_cpu', to: 'soc.u_dram' }
    expect(selectedEdgeAxiPerf(graph, sel).name).toBe('cpu_to_dram')
  })

  it('returns null when the selected edge has no axi-perf overlay', () => {
    const graph = {
      edges: [{ from: 'a', to: 'b', overlays: {} }],
    }
    expect(selectedEdgeAxiPerf(graph, { from: 'a', to: 'b' })).toBeNull()
  })

  it('returns null when nothing is selected', () => {
    expect(selectedEdgeAxiPerf({ edges: [] }, null)).toBeNull()
  })
})

describe('nodeAxiPerfInterconnect', () => {
  it('returns the interconnect roll-up when the node has one', () => {
    const graph = {
      nodes: [{
        id: 'soc.u_xbar',
        overlays: {
          'axi-perf': {
            interconnect: {
              total_read_bps: 2e9,
              total_write_bps: 1e9,
              hottest_master: 'soc.u_cpu',
              hottest_slave: 'soc.u_dram',
              arbitration: { fairness_jain: 0.85, starved_masters: [] },
            },
          },
        },
      }],
    }
    const ic = nodeAxiPerfInterconnect(graph, 'soc.u_xbar')
    expect(ic.hottest_master).toBe('soc.u_cpu')
    expect(ic.arbitration.fairness_jain).toBeCloseTo(0.85)
  })

  it('returns null when the node has no axi-perf overlay', () => {
    const graph = { nodes: [{ id: 'a', overlays: {} }] }
    expect(nodeAxiPerfInterconnect(graph, 'a')).toBeNull()
  })
})

describe('axi-perf pure helpers', () => {
  it('formatBps scales to k/M/G', () => {
    expect(formatBps(0)).toBe('0')
    expect(formatBps(512)).toBe('512')
    expect(formatBps(2_500)).toBe('2.5k')
    expect(formatBps(7.6e8)).toBe('760.0M')
    expect(formatBps(2e9)).toBe('2.0G')
  })

  it('bundlePinVisual maps backpressure → colour and role → arrow', () => {
    // r-channel bp_pct=20 (>15) → red; slave → ◀
    const hi = bundlePinVisual({ role: 'slave', bundle: _bundleBlock() })
    expect(hi.color).toBe('#dc2626')
    expect(hi.arrow).toBe('◀')
    expect(hi.role).toBe('slave')
    expect(hi.hasErrors).toBe(false)
    // Low backpressure everywhere → green; master → ▶
    const lo = bundlePinVisual({
      role: 'master',
      bundle: _bundleBlock({
        channels: { r: { util_pct: 1, bp_pct: 1, peak_occ: 1, beats: 1 } },
        errors: { slverr: 2, decerr: 0 },
      }),
    })
    expect(lo.color).toBe('#16a34a')
    expect(lo.arrow).toBe('▶')
    expect(lo.hasErrors).toBe(true)
  })

  it('isBoundaryPeer flags absent / non-node / ancestor peers', () => {
    const ids = new Set(['tb', 'tb.i_dut', 'tb.u_other'])
    // No peer → boundary.
    expect(isBoundaryPeer('tb.i_dut', null, ids)).toBe(true)
    // Peer not a node → boundary.
    expect(isBoundaryPeer('tb.i_dut', 'tb.ghost', ids)).toBe(true)
    // Peer is an ancestor scope (procedural tb master) → boundary.
    expect(isBoundaryPeer('tb.i_dut', 'tb', ids)).toBe(true)
    // Peer is a real sibling instance → NOT a boundary.
    expect(isBoundaryPeer('tb.i_dut', 'tb.u_other', ids)).toBe(false)
  })
})

describe('axiPerfOverlay.apply (interface-pin paint)', () => {
  // Build a mock block-flow SVG: one node group with a single ▶▶
  // interface cell (data-bf-id="bf-iface:tb.i_dut:slv"). Tracks every
  // element appended to the group + every element "removed" so we can
  // assert paint and toggle-off-clear.
  function _mockSvg() {
    const appended = []
    const stamped = [] // cells stamped with data-axi-pin
    const cell = {
      attrs: { 'data-bf-id': 'bf-iface:tb.i_dut:slv' },
      getAttribute(k) { return this.attrs[k] ?? null },
      setAttribute(k, v) { this.attrs[k] = v; if (k === 'data-axi-pin') stamped.push(this) },
      removeAttribute(k) { delete this.attrs[k] },
      getBBox: () => ({ x: 10, y: 20, width: 40, height: 12 }),
    }
    const group = {
      querySelector(sel) {
        // Strip CSS.escape backslashes so the mock matches the literal
        // attribute value (real DOM treats "tb\.i_dut" == "tb.i_dut").
        return sel.replace(/\\/g, '').includes('bf-iface:tb.i_dut:slv') ? cell : null
      },
      querySelectorAll() { return [cell] },
      appendChild(el) { appended.push(el); el._parent = group },
    }
    const allDecor = []
    const svgRoot = {
      querySelector(sel) {
        return sel.replace(/\\/g, '').includes('data-node-id="tb.i_dut"') ? group : null
      },
      querySelectorAll(sel) {
        if (sel.includes('data-axi-pin')) return stamped.slice()
        // class-based clear query → return appended decoration elements.
        return appended.filter((e) => allDecor.includes(e))
      },
    }
    return { svgRoot, group, cell, appended, stamped, allDecor }
  }

  function _graph() {
    return {
      nodes: [
        {
          id: 'tb.i_dut',
          overlays: {
            'axi-perf': {
              bundle_pins: [
                {
                  port: 'slv',
                  modport: 'Slave',
                  role: 'slave',
                  interface_instance: 'tb.slv',
                  peer: 'tb', // procedural master → boundary
                  bundle: _bundleBlock(),
                },
              ],
            },
          },
        },
      ],
      edges: [],
    }
  }

  function _withMockedDom(fn) {
    const NS = 'http://www.w3.org/2000/svg'
    const orig = global.document
    const created = []
    global.document = {
      createElementNS(_ns, tag) {
        const el = {
          tag,
          attrs: {},
          textContent: '',
          children: [],
          setAttribute(k, v) { this.attrs[k] = v },
          getAttribute(k) { return this.attrs[k] ?? null },
          appendChild(c) { this.children.push(c) },
          remove() { this._removed = true },
        }
        created.push(el)
        return el
      },
    }
    try {
      return fn(created, NS)
    } finally {
      global.document = orig
    }
  }

  it('decorates the interface pin, stamps the cell, and draws a boundary stub', () => {
    _withMockedDom((created) => {
      const { svgRoot, cell, appended, allDecor } = _mockSvg()
      const graph = _graph()
      // route created elements into the clear-query result set
      const origPush = appended.push.bind(appended)
      appended.push = (el) => { allDecor.push(el); return origPush(el) }

      axiPerfOverlay.apply(svgRoot, graph, true)

      // Cell stamped with the interface-instance identity.
      expect(cell.getAttribute('data-axi-pin')).toBe('tb.slv')
      // r-channel bp_pct=20 → red outline + badge.
      const outline = created.find((e) => e.attrs.class === 'rb-axi-pin')
      expect(outline).toBeTruthy()
      expect(outline.attrs.stroke).toBe('#dc2626')
      const badge = created.find((e) => e.attrs.class === 'rb-axi-badge')
      expect(badge).toBeTruthy()
      // slave role → ◀ arrow; boundary peer → ·ext tag.
      expect(badge.textContent).toContain('◀')
      expect(badge.textContent).toContain('·ext')
      expect(badge.textContent).toContain('G') // 1e9 + 5e8 = 1.5G throughput
      // Boundary peer → dashed stub.
      const stub = created.find((e) => e.attrs.class === 'rb-axi-stub')
      expect(stub).toBeTruthy()
      expect(stub.attrs['stroke-dasharray']).toBe('2,2')
    })
  })

  it('clears all decoration + cell stamps when toggled off', () => {
    _withMockedDom(() => {
      const { svgRoot, cell, appended, allDecor } = _mockSvg()
      const graph = _graph()
      const origPush = appended.push.bind(appended)
      appended.push = (el) => { allDecor.push(el); return origPush(el) }

      axiPerfOverlay.apply(svgRoot, graph, true)
      const painted = appended.filter((e) => allDecor.includes(e))
      expect(painted.length).toBeGreaterThan(0)
      expect(cell.getAttribute('data-axi-pin')).toBe('tb.slv')

      axiPerfOverlay.apply(svgRoot, graph, false)
      // Every painted decoration element was removed...
      for (const el of painted) expect(el._removed).toBe(true)
      // ...and the cell stamp was cleared.
      expect(cell.getAttribute('data-axi-pin')).toBeNull()
    })
  })

  it('does nothing for a graph with no bundle_pins', () => {
    _withMockedDom((created) => {
      const { svgRoot } = _mockSvg()
      const graph = { nodes: [{ id: 'tb.i_dut', overlays: {} }], edges: [] }
      axiPerfOverlay.apply(svgRoot, graph, true)
      expect(created.find((e) => e.attrs.class === 'rb-axi-pin')).toBeUndefined()
    })
  })

  it('falls back to an aggregate node badge in hier view (no interface cells)', () => {
    _withMockedDom((created) => {
      // Group has NO bf cells (hier view) but DOES have a shape.
      const appended = []
      const shape = { getBBox: () => ({ x: 5, y: 6, width: 80, height: 40 }) }
      const group = {
        querySelector(sel) {
          if (sel.includes('bf-iface')) return null
          if (sel.includes('polygon')) return shape
          return null
        },
        querySelectorAll(sel) {
          if (sel.includes('data-bf-id')) return [] // no port cells
          return []
        },
        appendChild(el) { appended.push(el) },
      }
      const svgRoot = {
        querySelector: (sel) =>
          sel.replace(/\\/g, '').includes('data-node-id="tb.i_dut"') ? group : null,
        querySelectorAll: () => [],
      }
      axiPerfOverlay.apply(svgRoot, _graph(), true)
      const badge = created.find((e) => e.attrs.class === 'rb-axi-badge')
      expect(badge).toBeTruthy()
      expect(badge.textContent).toContain('AXI')
      expect(badge.textContent).toContain('G') // 1.5G aggregate throughput
      // Interactive: stamped for the click-to-open handler + a <title>
      // tooltip child carrying the per-bundle breakdown.
      expect(badge.attrs['data-axi-open']).toBe('tb.i_dut')
      expect(badge.attrs['pointer-events']).toBe('auto')
      const title = badge.children.find((c) => c.tag === 'title')
      expect(title).toBeTruthy()
      expect(title.textContent).toContain('Click to open AXI Performance')
      expect(title.textContent).toContain('Total')
      // No per-pin outline in the hier fallback path.
      expect(created.find((e) => e.attrs.class === 'rb-axi-pin')).toBeUndefined()
    })
  })
})

describe('aggregateNodeBundles', () => {
  it('takes worst-case backpressure, sums throughput, OR-s errors', () => {
    const agg = aggregateNodeBundles([
      { bundle: _bundleBlock({
        channels: { r: { util_pct: 1, bp_pct: 1, peak_occ: 1, beats: 1 } },
        throughput: { read_bps: 1e9, write_bps: 0 },
        errors: { slverr: 0, decerr: 0 },
      }) },
      { bundle: _bundleBlock({
        channels: { r: { util_pct: 1, bp_pct: 30, peak_occ: 1, beats: 1 } }, // >15 → red
        throughput: { read_bps: 1e9, write_bps: 0 },
        errors: { slverr: 1, decerr: 0 },
      }) },
    ])
    expect(agg.count).toBe(2)
    expect(agg.color).toBe('#dc2626') // worst (30%) wins
    expect(agg.totalBps).toBe(2e9)
    expect(agg.hasErrors).toBe(true)
    expect(agg.label).toContain('⚠')
  })

  it('is empty-safe', () => {
    expect(aggregateNodeBundles([]).count).toBe(0)
    expect(aggregateNodeBundles(undefined).count).toBe(0)
  })
})
