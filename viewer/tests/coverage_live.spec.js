// Live coverage: the fetch gate, the elaborated→source name rule,
// the per-module join, the ramp, and the canvas overlay's
// what-gets-tinted decisions.
//
// The join is the part worth testing hardest — it is the only place
// where two independently-produced name spaces (Verilator's
// elaborated module names and view.json's source names) have to meet.

import { describe, expect, it, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import {
  COV_ROUTE,
  baseModuleName,
  covColor,
  covGeneratedDate,
  covServed,
  covSummaryText,
  loadCovData,
  moduleCoverage,
} from '../src/covData.js'
import { coverageLiveOverlay } from '../src/overlays/coverage_live.js'
import { getOverlay, overlaySummary } from '../src/overlays/index.js'
import { useViewerStore } from '../src/store.js'

// --- helpers ---------------------------------------------------------

function totals(spec) {
  // ``{line: [found, hit]}`` → the payload's totals shape.
  const out = {}
  for (const [metric, [found, hit]] of Object.entries(spec)) {
    out[metric] = { found, hit, ratio: found > 0 ? hit / found : null }
  }
  return out
}

function fakeWindow(covUrl) {
  return covUrl === undefined ? {} : { __RTL_BUDDY_COV_URL__: covUrl }
}

// --- name rule -------------------------------------------------------

describe('baseModuleName', () => {
  it('strips one Verilator parameterization suffix', () => {
    expect(baseModuleName('ip_cdc_sync__W4')).toBe('ip_cdc_sync')
    expect(baseModuleName('ip_async_fifo__DB13')).toBe('ip_async_fifo')
  })

  it('leaves unparameterized names alone', () => {
    expect(baseModuleName('counter')).toBe('counter')
    expect(baseModuleName('axi_lite_slave')).toBe('axi_lite_slave')
    // A single underscore is not a suffix marker.
    expect(baseModuleName('fifo_W4')).toBe('fifo_W4')
  })

  it('strips exactly ONE group, not every group', () => {
    expect(baseModuleName('axi__lite__W8')).toBe('axi__lite')
    expect(baseModuleName('a__b__c__d')).toBe('a__b__c')
  })

  it('refuses to strip when nothing would survive', () => {
    expect(baseModuleName('__W8')).toBe('__W8')
  })

  it('is total over junk input', () => {
    expect(baseModuleName('')).toBe('')
    expect(baseModuleName(null)).toBe('')
    expect(baseModuleName(undefined)).toBe('')
  })
})

// --- the join --------------------------------------------------------

describe('moduleCoverage', () => {
  it('keys a module under both its elaborated and its source name', () => {
    const map = moduleCoverage({
      files: [
        {
          path: 'rtl/ip_cdc_sync.sv',
          modules: ['ip_cdc_sync__W4'],
          totals: totals({ line: [10, 8] }),
        },
      ],
    })
    expect(map.get('ip_cdc_sync').line).toEqual({ found: 10, hit: 8, ratio: 0.8 })
    expect(map.get('ip_cdc_sync__W4').line).toEqual({ found: 10, hit: 8, ratio: 0.8 })
  })

  it('sums counts across the files that make up one module', () => {
    const map = moduleCoverage({
      files: [
        { path: 'a.sv', modules: ['big'], totals: totals({ line: [100, 50], branch: [20, 5] }) },
        { path: 'b.sv', modules: ['big'], totals: totals({ line: [300, 300], branch: [4, 4] }) },
      ],
    })
    // Ratios come from summed counts, never from averaged ratios:
    // 350/400, not the midpoint of 50% and 100%.
    expect(map.get('big').line).toEqual({ found: 400, hit: 350, ratio: 0.875 })
    expect(map.get('big').branch).toEqual({ found: 24, hit: 9, ratio: 0.375 })
  })

  it('folds every parameterization of a module into the source name', () => {
    const map = moduleCoverage({
      files: [
        { path: 'sync.sv', modules: ['ip_cdc_sync__W4'], totals: totals({ line: [10, 10] }) },
        { path: 'sync_w8.sv', modules: ['ip_cdc_sync__W8'], totals: totals({ line: [10, 0] }) },
      ],
    })
    expect(map.get('ip_cdc_sync').line).toEqual({ found: 20, hit: 10, ratio: 0.5 })
    // …while each variant keeps its own exact-name bucket.
    expect(map.get('ip_cdc_sync__W4').line.ratio).toBe(1)
    expect(map.get('ip_cdc_sync__W8').line.ratio).toBe(0)
  })

  it('counts a file once per distinct key when it lists two variants', () => {
    const map = moduleCoverage({
      files: [
        {
          path: 'sync.sv',
          modules: ['ip_cdc_sync__W4', 'ip_cdc_sync__W8'],
          totals: totals({ line: [10, 5] }),
        },
      ],
    })
    // The base bucket must not be double-counted to 20 lines.
    expect(map.get('ip_cdc_sync').line).toEqual({ found: 10, hit: 5, ratio: 0.5 })
  })

  it('gives an exact-named module its own bucket, ahead of the stripped one', () => {
    // A source module genuinely called ``axi__lite__W8`` coexisting
    // with a source module called ``axi__lite``: the exact key must
    // resolve to its own file's numbers, not to the union.
    const map = moduleCoverage({
      files: [
        { path: 'w8.sv', modules: ['axi__lite__W8'], totals: totals({ line: [10, 1] }) },
        { path: 'lite.sv', modules: ['axi__lite'], totals: totals({ line: [10, 9] }) },
      ],
    })
    expect(map.get('axi__lite__W8').line).toEqual({ found: 10, hit: 1, ratio: 0.1 })
    expect(map.get('axi__lite').line.found).toBe(20)
  })

  it('attributes a multi-module file to every module in it', () => {
    // Documented approximation: coverage is per FILE, so both modules
    // report the file's aggregate.
    const map = moduleCoverage({
      files: [{ path: 'pair.sv', modules: ['m_a', 'm_b'], totals: totals({ line: [8, 4] }) }],
    })
    expect(map.get('m_a').line.ratio).toBe(0.5)
    expect(map.get('m_b').line.ratio).toBe(0.5)
  })

  it('reports a metric with no data as ratio null, not zero', () => {
    const map = moduleCoverage({
      files: [{ path: 'a.sv', modules: ['m'], totals: totals({ line: [4, 0], toggle: [0, 0] }) }],
    })
    expect(map.get('m').line.ratio).toBe(0)
    expect(map.get('m').toggle.ratio).toBeNull()
    // Metrics absent from the payload entirely behave the same way.
    expect(map.get('m').expression.ratio).toBeNull()
  })

  it('is total over missing / malformed payloads', () => {
    expect(moduleCoverage(null).size).toBe(0)
    expect(moduleCoverage({}).size).toBe(0)
    expect(moduleCoverage({ files: 'nope' }).size).toBe(0)
    expect(moduleCoverage({ files: [{ path: 'a.sv' }] }).size).toBe(0)
    expect(moduleCoverage({ files: [{ modules: ['m'], totals: {} }] }).get('m').line.ratio).toBeNull()
  })
})

// --- ramp ------------------------------------------------------------

describe('covColor', () => {
  it('runs red → amber → green across 0..100', () => {
    // hue = pct * 1.2, at the token sheet's --cov-l lightness.
    expect(covColor(0)).toBe('hsl(0, 70%, 82%)')
    expect(covColor(50)).toBe('hsl(60, 70%, 82%)')
    expect(covColor(100)).toBe('hsl(120, 70%, 82%)')
  })

  it('clamps out-of-range percentages onto the ramp', () => {
    expect(covColor(-10)).toBe('hsl(0, 70%, 82%)')
    expect(covColor(150)).toBe('hsl(120, 70%, 82%)')
  })

  it('renders no-data as the explicit grey, not as 0%', () => {
    expect(covColor(null)).toBe('#e5e7eb')
    expect(covColor(undefined)).toBe('#e5e7eb')
    expect(covColor(NaN)).toBe('#e5e7eb')
    expect(covColor(0)).not.toBe(covColor(null))
  })
})

describe('covSummaryText', () => {
  it('prints one letter-prefixed percentage per metric with data', () => {
    const map = moduleCoverage({
      files: [
        {
          path: 'a.sv',
          modules: ['m'],
          totals: totals({
            line: [1000, 859],
            branch: [1000, 805],
            toggle: [1000, 789],
            expression: [1000, 892],
            cover: [1000, 967],
          }),
        },
      ],
    })
    expect(covSummaryText(map.get('m'))).toBe(
      'L 85.9% · B 80.5% · T 78.9% · E 89.2% · C 96.7%',
    )
  })

  it('skips metrics with no data rather than printing 0%', () => {
    const map = moduleCoverage({
      files: [{ path: 'a.sv', modules: ['m'], totals: totals({ line: [10, 5] }) }],
    })
    expect(covSummaryText(map.get('m'))).toBe('L 50.0%')
    expect(covSummaryText(null)).toBe('')
  })
})

describe('covGeneratedDate', () => {
  it('renders an ISO timestamp as a plain day', () => {
    expect(covGeneratedDate({ generated_at: '2026-08-07T11:22:33Z' })).toBe('2026-08-07')
  })

  it('passes unparseable values through and empties on absence', () => {
    expect(covGeneratedDate({ generated_at: 'run 42' })).toBe('run 42')
    expect(covGeneratedDate({})).toBe('')
    expect(covGeneratedDate(null)).toBe('')
  })
})

// --- fetch gate ------------------------------------------------------

describe('loadCovData', () => {
  it('does not fetch at all without the hub gate global', async () => {
    const fetchImpl = vi.fn()
    expect(covServed(fakeWindow())).toBe(false)
    expect(await loadCovData(fakeWindow(), fetchImpl)).toBeNull()
    expect(await loadCovData(fakeWindow(''), fetchImpl)).toBeNull()
    expect(await loadCovData(null, fetchImpl)).toBeNull()
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('fetches the constant /cov.json route when gated on', async () => {
    const payload = { totals: {}, files: [], generated_at: '2026-08-07T00:00:00Z' }
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => payload })
    const out = await loadCovData(fakeWindow('/cov'), fetchImpl)
    expect(out).toBe(payload)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    // The global gates; it is never the URL (the hub sets it to the
    // PANE route, which is what hubApps.js links).
    expect(fetchImpl.mock.calls[0][0]).toBe(COV_ROUTE)
  })

  it('resolves null on every failure mode instead of throwing', async () => {
    const w = fakeWindow('/cov')
    expect(await loadCovData(w, vi.fn().mockResolvedValue({ ok: false }))).toBeNull()
    expect(await loadCovData(w, vi.fn().mockRejectedValue(new Error('offline')))).toBeNull()
    expect(
      await loadCovData(
        w,
        vi.fn().mockResolvedValue({
          ok: true,
          json: async () => {
            throw new SyntaxError('not JSON')
          },
        }),
      ),
    ).toBeNull()
    expect(await loadCovData(w, vi.fn().mockResolvedValue({ ok: true, json: async () => null }))).toBeNull()
  })
})

// --- store -----------------------------------------------------------

describe('store coverage state', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('starts empty and off-the-wire, with an empty module map', () => {
    const store = useViewerStore()
    expect(store.covData).toBeNull()
    expect(store.covByModule.size).toBe(0)
    expect(store.covEnabled).toBe(true)
  })

  it('loadCoverage runs at most once per session', async () => {
    const store = useViewerStore()
    await store.loadCoverage()
    expect(store.covLoadStarted).toBe(true)
    expect(store.covData).toBeNull()
    // A second call (e.g. a model switch reinstalling the graph) is a
    // no-op rather than a second fetch.
    await store.loadCoverage()
    expect(store.covData).toBeNull()
  })

  it('toggling the tint sticks for the session', () => {
    const store = useViewerStore()
    store.toggleCoverageTint()
    expect(store.covEnabled).toBe(false)
    expect(store.covEnabledTouched).toBe(true)
  })

  it('joins covData to modules through the getter', () => {
    const store = useViewerStore()
    store.covData = {
      files: [{ path: 'a.sv', modules: ['counter__W8'], totals: totals({ line: [4, 3] }) }],
    }
    expect(store.covByModule.get('counter').line.ratio).toBe(0.75)
  })
})

// --- overlay ---------------------------------------------------------

// Minimal stand-ins for the SVG shapes Graphviz emits. The shape
// COUNT is the load-bearing part: a plain rounded box is one <path>,
// a striped CDC box is three <polygon>s, an HTML-table CDC grid is a
// backdrop <path> plus two <polygon>s per cell.
function fakeGroup(id, { shapes = 1, cluster = false } = {}) {
  const attrs = new Map()
  const shapeEls = Array.from({ length: shapes }, () => ({ style: {} }))
  return {
    id,
    shapeEls,
    classList: { contains: (c) => cluster && c === 'cluster' },
    querySelectorAll: () => shapeEls,
    setAttribute: (k, v) => attrs.set(k, v),
    getAttribute: (k) => (attrs.has(k) ? attrs.get(k) : null),
    hasAttribute: (k) => attrs.has(k),
    removeAttribute: (k) => attrs.delete(k),
  }
}

function fakeSvg(groups) {
  return {
    querySelector: (sel) => {
      const m = /\[data-node-id="(.*)"\]/.exec(sel)
      // The overlay runs ids through CSS.escape, so the dots in
      // instance paths come back backslash-escaped.
      return (m && groups[m[1].replace(/\\/g, '')]) || null
    },
  }
}

describe('coverage-live overlay', () => {
  const graph = {
    nodes: [
      { id: 'top', module: 'top' },
      { id: 'top.u_a', module: 'mod_a' },
      { id: 'top.u_b', module: 'mod_b' },
      { id: 'top.u_cdc', module: 'ip_cdc_sync' },
      { id: 'top.u_grid', module: 'ip_bridge' },
      { id: 'top.u_unknown', module: 'never_simulated' },
    ],
    edges: [],
    overlays_present: [],
  }
  const covByModule = moduleCoverage({
    files: [
      { path: 'a.sv', modules: ['mod_a'], totals: totals({ line: [10, 10] }) },
      { path: 'b.sv', modules: ['mod_b'], totals: totals({ line: [10, 2] }) },
      { path: 'sync.sv', modules: ['ip_cdc_sync__W4'], totals: totals({ line: [10, 5] }) },
      { path: 'br.sv', modules: ['ip_bridge'], totals: totals({ line: [10, 5] }) },
      { path: 'top.sv', modules: ['top'], totals: totals({ line: [10, 5] }) },
      { path: 'n.sv', modules: ['never_simulated'], totals: totals({ line: [0, 0] }) },
    ],
  })

  function scene() {
    return {
      top: fakeGroup('top', { cluster: true }),
      'top.u_a': fakeGroup('top.u_a'),
      'top.u_b': fakeGroup('top.u_b'),
      'top.u_cdc': fakeGroup('top.u_cdc', { shapes: 3 }),
      'top.u_grid': fakeGroup('top.u_grid', { shapes: 5 }),
      'top.u_unknown': fakeGroup('top.u_unknown'),
    }
  }

  it('tints plain leaf boxes by their module LINE ratio', () => {
    const groups = scene()
    coverageLiveOverlay.apply(fakeSvg(groups), graph, true, { covByModule })
    expect(groups['top.u_a'].shapeEls[0].style.fill).toBe(covColor(100))
    expect(groups['top.u_b'].shapeEls[0].style.fill).toBe(covColor(20))
    expect(groups['top.u_a'].getAttribute('data-overlay-coverage-live')).toBe('100')
  })

  it('joins through the parameterization suffix', () => {
    const groups = scene()
    coverageLiveOverlay.apply(fakeSvg(groups), graph, true, { covByModule })
    // node.module is the SOURCE name; the payload carried __W4.
    expect(groups['top.u_cdc'].shapeEls.every((s) => s.style.fill === undefined)).toBe(true)
    // …but the join itself found it — the skip is structural, not a miss.
    expect(covByModule.get('ip_cdc_sync').line.ratio).toBe(0.5)
  })

  it('leaves clusters, striped CDC boxes and CDC grids untouched', () => {
    const groups = scene()
    coverageLiveOverlay.apply(fakeSvg(groups), graph, true, { covByModule })
    for (const id of ['top', 'top.u_cdc', 'top.u_grid']) {
      for (const shape of groups[id].shapeEls) expect(shape.style.fill).toBeUndefined()
      expect(groups[id].hasAttribute('data-overlay-coverage-live')).toBe(false)
    }
  })

  it('paints a module with zero found lines as no-data grey', () => {
    const groups = scene()
    coverageLiveOverlay.apply(fakeSvg(groups), graph, true, { covByModule })
    expect(groups['top.u_unknown'].shapeEls[0].style.fill).toBe(covColor(null))
    expect(groups['top.u_unknown'].getAttribute('data-overlay-coverage-live')).toBe('no-data')
  })

  it('says nothing at all when disabled or when there is no data', () => {
    const groups = scene()
    coverageLiveOverlay.apply(fakeSvg(groups), graph, false, { covByModule })
    expect(groups['top.u_a'].shapeEls[0].style.fill).toBeUndefined()
    coverageLiveOverlay.apply(fakeSvg(groups), graph, true, {})
    expect(groups['top.u_a'].shapeEls[0].style.fill).toBeUndefined()
  })

  it('is idempotent, and clears its own fill on toggle-off', () => {
    const groups = scene()
    const svg = fakeSvg(groups)
    coverageLiveOverlay.apply(svg, graph, true, { covByModule })
    coverageLiveOverlay.apply(svg, graph, true, { covByModule })
    expect(groups['top.u_a'].shapeEls[0].style.fill).toBe(covColor(100))
    coverageLiveOverlay.apply(svg, graph, false, { covByModule })
    expect(groups['top.u_a'].shapeEls[0].style.fill).toBe('')
    expect(groups['top.u_a'].hasAttribute('data-overlay-coverage-live')).toBe(false)
  })

  it('does not clear a fill another overlay has claimed', () => {
    // Toggle-off ordering: applyOverlays runs first and repaints the
    // clock tint, so our clear branch must leave it alone.
    const groups = scene()
    const svg = fakeSvg(groups)
    coverageLiveOverlay.apply(svg, graph, true, { covByModule })
    groups['top.u_a'].setAttribute('data-overlay-clock', 'clk_a')
    groups['top.u_a'].shapeEls[0].style.fill = '#dbeafe'
    coverageLiveOverlay.apply(svg, graph, false, { covByModule })
    expect(groups['top.u_a'].shapeEls[0].style.fill).toBe('#dbeafe')
  })

  it('survives a graph whose nodes have no rendered group', () => {
    expect(() =>
      coverageLiveOverlay.apply(fakeSvg({}), graph, true, { covByModule }),
    ).not.toThrow()
    expect(() => coverageLiveOverlay.apply(null, graph, true, { covByModule })).not.toThrow()
  })

  it('is reachable through the registry but never listed as a payload overlay', () => {
    expect(getOverlay('coverage-live')).toBe(coverageLiveOverlay)
    // ``overlays_present`` is the producer's list; the hub's live
    // overlay is not on it, so the panel's payload section can't
    // show it twice.
    expect(overlaySummary({ overlays_present: ['clock'] })).toEqual([
      { name: 'clock', known: true },
    ])
    expect(coverageLiveOverlay.legend().map((e) => e.label)).toEqual([
      '0% lines',
      '50% lines',
      '100% lines',
      'no coverage data',
    ])
  })
})
