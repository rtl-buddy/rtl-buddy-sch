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

describe('axiPerfOverlay.apply', () => {
  function _buildSvg() {
    // happy-dom is the test env, so document is available.
    const root = document.createElement('svg')
    const edgeGroup = document.createElement('g')
    edgeGroup.setAttribute('data-edge-from', 'soc.u_cpu')
    edgeGroup.setAttribute('data-edge-to', 'soc.u_dram')
    const path = document.createElement('path')
    edgeGroup.appendChild(path)
    root.appendChild(edgeGroup)
    return { root, path }
  }

  it('styles edges with high backpressure red', () => {
    const { root, path } = _buildSvg()
    const graph = {
      edges: [{
        from: 'soc.u_cpu',
        to: 'soc.u_dram',
        overlays: {
          'axi-perf': _bundleBlock({
            channels: {
              ar: { util_pct: 30, bp_pct: 5, peak_occ: 8, txns: 1000 },
              aw: { util_pct: 15, bp_pct: 1, peak_occ: 4, txns: 500 },
              r:  { util_pct: 60, bp_pct: 50, peak_occ: 12, beats: 8000 }, // bp 50%
              w:  { util_pct: 35, bp_pct: 8, peak_occ: 6, beats: 2000 },
              b:  { util_pct: 5, bp_pct: 0, peak_occ: 2, txns: 500 },
            },
          }),
        },
      }],
    }
    axiPerfOverlay.apply(root, graph, true)
    // happy-dom keeps the hex; jsdom would normalize to rgb(). Accept either.
    expect(['rgb(220, 38, 38)', '#dc2626']).toContain(path.style.stroke)
  })

  it('clears styling when disabled', () => {
    const { root, path } = _buildSvg()
    const graph = {
      edges: [{
        from: 'soc.u_cpu',
        to: 'soc.u_dram',
        overlays: { 'axi-perf': _bundleBlock() },
      }],
    }
    axiPerfOverlay.apply(root, graph, true)
    expect(path.style.stroke).not.toBe('')
    axiPerfOverlay.apply(root, graph, false)
    expect(path.style.stroke).toBe('')
  })

  it('dashes the stroke when errors are present', () => {
    const { root, path } = _buildSvg()
    const graph = {
      edges: [{
        from: 'soc.u_cpu',
        to: 'soc.u_dram',
        overlays: {
          'axi-perf': _bundleBlock({ errors: { slverr: 2, decerr: 1 } }),
        },
      }],
    }
    axiPerfOverlay.apply(root, graph, true)
    expect(path.style.strokeDasharray).toBe('4 3')
  })

  it('is a no-op on edges without an axi-perf overlay', () => {
    const { root, path } = _buildSvg()
    const graph = {
      edges: [{ from: 'soc.u_cpu', to: 'soc.u_dram', overlays: {} }],
    }
    axiPerfOverlay.apply(root, graph, true)
    expect(path.style.stroke).toBe('')
  })

  it('survives an edge whose SVG group is absent (graph⇄DOM drift)', () => {
    const root = document.createElement('svg')
    const graph = {
      edges: [{
        from: 'orphan',
        to: 'orphan2',
        overlays: { 'axi-perf': _bundleBlock() },
      }],
    }
    expect(() => axiPerfOverlay.apply(root, graph, true)).not.toThrow()
  })
})
