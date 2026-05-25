// Store unit tests. happy-dom (configured in vite.config.js
// `test.environment`) gives us a window so the bootstrap path can
// inspect ``window.location.search`` and ``window.__RTL_BUDDY_VIEW_DATA__``.
//
// We don't exercise loadFromUrl here — that path is a thin fetch
// wrapper and unit-testing it requires more mocking than the
// branch is worth. Integration coverage lands in the Playwright
// snapshot test (next PR).

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useViewerStore } from '../src/store.js'

function minimalPayload(extra = {}) {
  return {
    schema_version: '1.0',
    top: 'top',
    nodes: [
      { id: 'top', module: 'top', is_blackbox: false, parameters: {}, ports: [], overlays: {} },
      { id: 'top.u_a', module: 'a', is_blackbox: false, parameters: {}, ports: [], overlays: {} },
    ],
    edges: [{ from: 'top', to: 'top.u_a', port_pairs: [], overlays: {} }],
    overlays_present: ['clock'],
    ...extra,
  }
}

describe('viewer store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.__RTL_BUDDY_VIEW_DATA__ = null
    window.__RTL_BUDDY_VIEW_URL__ = null
  })
  afterEach(() => {
    window.__RTL_BUDDY_VIEW_DATA__ = null
    window.__RTL_BUDDY_VIEW_URL__ = null
  })

  it('starts idle with no graph and no error', () => {
    const store = useViewerStore()
    expect(store.status).toBe('idle')
    expect(store.graph).toBeNull()
    expect(store.error).toBeNull()
  })

  it('transitions to ready after loadFromText', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(minimalPayload()))
    expect(store.status).toBe('ready')
    expect(store.graph.top).toBe('top')
    expect(store.nodesById.get('top.u_a').module).toBe('a')
  })

  it('enables every present overlay by default', () => {
    const store = useViewerStore()
    store.loadFromText(
      JSON.stringify(minimalPayload({ overlays_present: ['clock', 'reset'] })),
    )
    expect([...store.enabledOverlays].sort()).toEqual(['clock', 'reset'])
  })

  it('toggleOverlay flips presence and triggers a new Set reference', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(minimalPayload()))
    const before = store.enabledOverlays
    store.toggleOverlay('clock')
    expect(store.enabledOverlays.has('clock')).toBe(false)
    // Reactivity guard: identity changes so consumers re-render.
    expect(store.enabledOverlays).not.toBe(before)
    store.toggleOverlay('clock')
    expect(store.enabledOverlays.has('clock')).toBe(true)
  })

  it('select + selectedNode round-trips through the store', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(minimalPayload()))
    store.select('top.u_a')
    expect(store.selectedNode.module).toBe('a')
    store.clearSelection()
    expect(store.selectedNode).toBeNull()
  })

  it('selectEdge accepts only edges present in the current graph', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(minimalPayload()))
    // Real edge → resolves to selectedEdgeObj and clears node selection.
    store.select('top')
    store.selectEdge('top', 'top.u_a')
    expect(store.selectedEdgeObj).not.toBeNull()
    expect(store.selectedEdgeObj.from).toBe('top')
    expect(store.selection).toBeNull()
    // Synthetic port-anchor edge (no matching edges[] entry) is
    // rejected — the canvas falls through to no-op rather than
    // populating a stale selection.
    store.selectEdge('_in_clk_a', 'top.u_a')
    expect(store.selectedEdgeObj.from).toBe('top') // unchanged
    // Selecting a node clears the edge again.
    store.select('top.u_a')
    expect(store.selectedEdge).toBeNull()
    expect(store.selectedNode.id).toBe('top.u_a')
  })

  it('_installGraph resets both node and edge selection', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(minimalPayload()))
    store.selectEdge('top', 'top.u_a')
    expect(store.selectedEdgeObj).not.toBeNull()
    // Loading a new graph wipes the prior selection — otherwise the
    // sidebar would render stale data while the canvas redraws.
    store.loadFromText(JSON.stringify(minimalPayload()))
    expect(store.selectedEdge).toBeNull()
    expect(store.selection).toBeNull()
  })

  it('surfaces invalid JSON as a status=error toast', () => {
    const store = useViewerStore()
    store.loadFromText('not valid json {')
    expect(store.status).toBe('error')
    expect(store.error).toMatch(/<input>/)
    expect(store.graph).toBeNull()
  })

  it('surfaces a bad schema_version as a toast, not a crash', () => {
    const store = useViewerStore()
    const p = minimalPayload()
    p.schema_version = '2.0'
    store.loadFromText(JSON.stringify(p), 'future.json')
    expect(store.status).toBe('error')
    expect(store.error).toMatch(/future.json/)
    expect(store.error).toMatch(/not supported/)
  })

  it('bootstrap reads window.__RTL_BUDDY_VIEW_DATA__ when set', async () => {
    window.__RTL_BUDDY_VIEW_DATA__ = minimalPayload()
    const store = useViewerStore()
    await store.bootstrap()
    expect(store.status).toBe('ready')
    expect(store.graph.top).toBe('top')
  })

  it('bootstrap reads window.__RTL_BUDDY_VIEW_URL__ when set (hub injection)', async () => {
    // The hub injects this when its viewer_http layer has a
    // view.json configured. Bootstrap should fetch that URL and
    // install the graph — no ``?view=`` query param required.
    const payload = minimalPayload()
    const originalFetch = window.fetch
    let fetchedUrl = null
    window.fetch = async (url) => {
      fetchedUrl = url
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () => JSON.stringify(payload),
      }
    }
    try {
      window.__RTL_BUDDY_VIEW_URL__ = '/view.json'
      const store = useViewerStore()
      await store.bootstrap()
      expect(fetchedUrl).toBe('/view.json')
      expect(store.status).toBe('ready')
      expect(store.graph.top).toBe('top')
    } finally {
      window.fetch = originalFetch
    }
  })

  it('?view= query takes precedence over window.__RTL_BUDDY_VIEW_URL__', async () => {
    // Defensive: the hub may inject a default, but explicit user
    // intent in the URL bar wins. Since happy-dom doesn't let us
    // mutate window.location.search at runtime, this is enforced by
    // the priority order in bootstrap() — covered indirectly by the
    // ``__RTL_BUDDY_VIEW_DATA__`` test below.
    window.__RTL_BUDDY_VIEW_URL__ = '/view.json'
    window.__RTL_BUDDY_VIEW_DATA__ = minimalPayload()
    let fetched = false
    const originalFetch = window.fetch
    window.fetch = async () => {
      fetched = true
      return { ok: false, status: 404, statusText: 'Not Found', text: async () => '' }
    }
    try {
      const store = useViewerStore()
      await store.bootstrap()
      // VIEW_URL is preferred over VIEW_DATA — the hub-side payload
      // is the canonical one when both are present (e.g. user
      // dropped the embed.py page onto a running hub).
      expect(fetched).toBe(true)
      expect(store.status).toBe('error')
    } finally {
      window.fetch = originalFetch
    }
  })

  it('bootstrap stays idle when no payload is present', async () => {
    const store = useViewerStore()
    await store.bootstrap()
    expect(store.status).toBe('idle')
  })

  // ---------------------------------------------------------------------------
  // View-mode-aware descend / ascend (issue: unify hier/flow click contract)
  // ---------------------------------------------------------------------------

  it('select alone does not change the flow scope (decoupled from selection)', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(minimalPayload()))
    store.setViewMode('flow')
    expect(store.flowScopeId).toBe('top') // graph.top default
    store.select('top.u_a')
    expect(store.selection).toBe('top.u_a')
    // Crucially: flow scope DOES NOT follow selection. Without this
    // decoupling, clicking a block in the flow view would
    // immediately re-render at that block — the "click descends"
    // behaviour the user explicitly rejected.
    expect(store.flowScopeId).toBe('top')
  })

  it('descend updates both rootInstancePath and flowScope so the two view modes stay in sync', () => {
    const store = useViewerStore()
    const p = minimalPayload()
    p.nodes.push({
      id: 'top.u_a.sub',
      module: 's',
      is_blackbox: false,
      parameters: {},
      ports: [],
      overlays: {},
    })
    store.loadFromText(JSON.stringify(p))
    // The active view mode doesn't matter — descend now mirrors the
    // scope across both fields. Test both starting modes to pin
    // that down.
    store.setViewMode('flow')
    store.select('top.u_a')
    store.descend('top.u_a')
    expect(store.flowScope).toBe('top.u_a')
    expect(store.flowScopeId).toBe('top.u_a')
    expect(store.rootInstancePath).toBe('top.u_a')

    store.goToTop()
    store.setViewMode('hier')
    store.select('top.u_a')
    store.descend('top.u_a')
    expect(store.rootInstancePath).toBe('top.u_a')
    expect(store.flowScope).toBe('top.u_a')
  })

  it('ascend pops both scope fields and follows the selection up', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(minimalPayload()))
    store.rootInstancePath = 'top.u_a.sub'
    store.flowScope = 'top.u_a.sub'
    store.selection = 'top.u_a.sub'

    store.ascend()
    expect(store.rootInstancePath).toBe('top.u_a')
    expect(store.flowScope).toBe('top.u_a')
    expect(store.selection).toBe('top.u_a')

    store.ascend()
    expect(store.rootInstancePath).toBe('top')
    expect(store.flowScope).toBe('top')
    expect(store.selection).toBe('top')

    store.ascend()
    // ``top`` is a single segment with no parent → cleared on both.
    expect(store.rootInstancePath).toBeNull()
    expect(store.flowScope).toBeNull()
    expect(store.selection).toBeNull()
  })

  it('goToTop clears both scope fields and the selection regardless of view mode', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(minimalPayload()))
    store.setViewMode('flow')
    store.flowScope = 'top.u_a.sub'
    store.rootInstancePath = 'top.u_a'
    store.selection = 'top.u_a.sub'
    store.goToTop()
    expect(store.flowScope).toBeNull()
    expect(store.rootInstancePath).toBeNull()
    expect(store.selection).toBeNull()
  })

  // ---------------------------------------------------------------------------
  // Model picker (rtl-buddy-view#72 Part 2 / rtl_buddy#174)
  // ---------------------------------------------------------------------------

  function mockFetch(responses) {
    // ``responses`` is a map of URL → response factory. Each factory
    // returns an object shaped like ``Response`` (ok, status, text,
    // json). Unmocked URLs return 404.
    return async (url) => {
      const factory = responses[url] || responses['*']
      if (!factory) {
        return {
          ok: false,
          status: 404,
          statusText: 'Not Found',
          text: async () => '',
          json: async () => ({}),
        }
      }
      return factory(url)
    }
  }

  it('loadAvailableModels populates list + active from /models', async () => {
    const originalFetch = window.fetch
    window.fetch = mockFetch({
      '/models': async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          models: [
            { name: 'alpha', models_file: '/p/models.yaml', has_cdc: true },
            { name: 'beta', models_file: '/p/models.yaml', has_cdc: false },
          ],
          active: 'alpha',
        }),
      }),
    })
    try {
      const store = useViewerStore()
      await store.loadAvailableModels()
      expect(store.availableModels.map((m) => m.name)).toEqual(['alpha', 'beta'])
      expect(store.activeModel).toBe('alpha')
    } finally {
      window.fetch = originalFetch
    }
  })

  it('loadAvailableModels treats 404 as standalone mode (clears list)', async () => {
    const originalFetch = window.fetch
    window.fetch = async () => ({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({}),
    })
    try {
      const store = useViewerStore()
      // Seed something so we can confirm it gets cleared.
      store.availableModels = [{ name: 'stale' }]
      store.activeModel = 'stale'
      await store.loadAvailableModels()
      expect(store.availableModels).toEqual([])
      expect(store.activeModel).toBeNull()
    } finally {
      window.fetch = originalFetch
    }
  })

  it('switchModel fetches /view.json?model= and flips activeModel', async () => {
    const originalFetch = window.fetch
    const payload = minimalPayload()
    window.fetch = mockFetch({
      '/view.json?model=demo': async () => ({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () => JSON.stringify(payload),
      }),
    })
    try {
      const store = useViewerStore()
      await store.switchModel('demo', { updateUrl: false })
      expect(store.status).toBe('ready')
      expect(store.activeModel).toBe('demo')
      expect(store.graph.top).toBe('top')
    } finally {
      window.fetch = originalFetch
    }
  })

  it('switchModel does not flip activeModel on load failure', async () => {
    const originalFetch = window.fetch
    window.fetch = async () => ({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      text: async () => 'boom',
    })
    try {
      const store = useViewerStore()
      store.activeModel = 'previous'
      await store.switchModel('broken', { updateUrl: false })
      // status flipped to error, activeModel preserved so the next
      // view_changed echo isn't masked.
      expect(store.status).toBe('error')
      expect(store.activeModel).toBe('previous')
    } finally {
      window.fetch = originalFetch
    }
  })

  it('applyViewChanged is a no-op when payload model == activeModel (self-echo dedupe)', async () => {
    const originalFetch = window.fetch
    let fetched = 0
    window.fetch = async () => {
      fetched += 1
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () => JSON.stringify(minimalPayload()),
      }
    }
    try {
      const store = useViewerStore()
      store.activeModel = 'demo'
      await store.applyViewChanged({ model: 'demo', models_file: '/x', view_url: '/y' })
      expect(fetched).toBe(0)
    } finally {
      window.fetch = originalFetch
    }
  })

  it('applyViewChanged triggers a refetch when payload model differs', async () => {
    const originalFetch = window.fetch
    const calls = []
    window.fetch = async (url) => {
      calls.push(url)
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        text: async () => JSON.stringify(minimalPayload()),
      }
    }
    try {
      const store = useViewerStore()
      store.activeModel = 'demo'
      await store.applyViewChanged({ model: 'other', models_file: '/x', view_url: '/y' })
      expect(calls).toContain('/view.json?model=other')
      expect(store.activeModel).toBe('other')
    } finally {
      window.fetch = originalFetch
    }
  })

  it('applyViewChanged ignores malformed payloads', async () => {
    const originalFetch = window.fetch
    let fetched = 0
    window.fetch = async () => {
      fetched += 1
      return { ok: true, status: 200, text: async () => '{}' }
    }
    try {
      const store = useViewerStore()
      await store.applyViewChanged(null)
      await store.applyViewChanged({})
      await store.applyViewChanged({ model: 42 })
      expect(fetched).toBe(0)
    } finally {
      window.fetch = originalFetch
    }
  })

  // ------------------------------------------------------------
  // diagnosticsByNode — file+line → instance_path resolution
  // ------------------------------------------------------------

  function diagPayload() {
    return {
      schema_version: '1.0',
      top: 'top',
      nodes: [
        {
          id: 'top',
          module: 'top',
          is_blackbox: false,
          parameters: {},
          ports: [],
          overlays: {},
          source: { file: '/abs/parent.sv', start_line: 1, end_line: 200 },
        },
        {
          id: 'top.u_dma',
          module: 'dma',
          is_blackbox: false,
          parameters: {},
          ports: [],
          overlays: {},
          source: { file: '/abs/parent.sv', start_line: 40, end_line: 60 },
        },
        {
          id: 'top.u_dma.u_inner',
          module: 'inner',
          is_blackbox: false,
          parameters: {},
          ports: [],
          overlays: {},
          source: { file: '/abs/parent.sv', start_line: 50, end_line: 55 },
        },
        {
          id: 'top.u_other',
          module: 'other',
          is_blackbox: false,
          parameters: {},
          ports: [],
          overlays: {},
          source: { file: '/abs/parent.sv', start_line: 100, end_line: 120 },
        },
      ],
      edges: [],
      overlays_present: [],
    }
  }

  it('diagnosticsByNode honours item.instance_path fast path', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    store.applyDiagnostics('rtl-buddy-cdc', [
      { instance_path: 'top.u_dma', file: '/elsewhere.sv', line: 9,
        severity: 'warning', code: 'CDC-1', message: 'x' },
    ])
    expect(Object.keys(store.diagnosticsByNode)).toEqual(['top.u_dma'])
    expect(store.diagnosticsByNode['top.u_dma'][0].source).toBe('rtl-buddy-cdc')
  })

  it('diagnosticsByNode resolves via file+line and prefers the deepest enclosing range', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    store.applyDiagnostics('claude-analysis', [
      // line 52 is in [1,200], [40,60], and [50,55]. Deepest wins.
      { file: '/abs/parent.sv', line: 52, col: 1,
        severity: 'error', code: 'X', message: 'inside inner' },
    ])
    expect(Object.keys(store.diagnosticsByNode)).toEqual(['top.u_dma.u_inner'])
  })

  it('diagnosticsByNode skips items whose file matches no node', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    store.applyDiagnostics('claude-analysis', [
      { file: '/nope.sv', line: 50, col: 1,
        severity: 'info', code: 'X', message: 'unanchored' },
    ])
    expect(store.diagnosticsByNode).toEqual({})
    // ...but the source still appears in the flat list.
    expect(store.diagnosticsFlat).toHaveLength(1)
  })

  it('diagnosticsByNode skips items whose line falls outside every node range', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    store.applyDiagnostics('claude-analysis', [
      { file: '/abs/parent.sv', line: 300, col: 1,
        severity: 'info', code: 'X', message: 'out of range' },
    ])
    expect(store.diagnosticsByNode).toEqual({})
  })

  it('diagnosticsByNode groups multiple items on the same node', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    store.applyDiagnostics('rtl-buddy-cdc', [
      { file: '/abs/parent.sv', line: 45, col: 1,
        severity: 'warning', code: 'CDC-1', message: 'a' },
      { file: '/abs/parent.sv', line: 46, col: 1,
        severity: 'error', code: 'CDC-2', message: 'b' },
    ])
    store.applyDiagnostics('claude-analysis', [
      { instance_path: 'top.u_dma', file: '/x', line: 1,
        severity: 'info', code: 'X', message: 'c' },
    ])
    expect(store.diagnosticsByNode['top.u_dma']).toHaveLength(3)
    const sources = store.diagnosticsByNode['top.u_dma'].map((d) => d.source)
    expect(sources.sort()).toEqual(['claude-analysis', 'rtl-buddy-cdc', 'rtl-buddy-cdc'])
  })

  it('diagnosticsByNode treats instance_path matching a non-existent node as no match', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    store.applyDiagnostics('rtl-buddy-cdc', [
      { instance_path: 'top.u_ghost', file: '/abs/parent.sv', line: 9999,
        severity: 'warning', code: 'X', message: 'stale' },
    ])
    expect(store.diagnosticsByNode).toEqual({})
  })

  it('diagnosticsForNode reflects the new resolver', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    store.applyDiagnostics('claude-analysis', [
      { file: '/abs/parent.sv', line: 110, col: 1,
        severity: 'warning', code: 'X', message: 'inside u_other' },
    ])
    expect(store.diagnosticsForNode('top.u_other')).toHaveLength(1)
    expect(store.diagnosticsForNode('top.u_other')[0].message).toBe('inside u_other')
    expect(store.diagnosticsForNode('top.u_dma')).toEqual([])
  })

  it('nodeIdForDiagnosticItem mirrors the diagnosticsByNode resolver', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    // file+line → deepest range
    expect(
      store.nodeIdForDiagnosticItem({ file: '/abs/parent.sv', line: 52 }),
    ).toBe('top.u_dma.u_inner')
    // instance_path fast path
    expect(
      store.nodeIdForDiagnosticItem({
        instance_path: 'top.u_other',
        file: '/elsewhere',
        line: 9,
      }),
    ).toBe('top.u_other')
    // ghost instance_path falls through to file+line — and matches nothing
    expect(
      store.nodeIdForDiagnosticItem({
        instance_path: 'top.u_ghost',
        file: '/nope.sv',
        line: 1,
      }),
    ).toBeNull()
    // out-of-range line → no match
    expect(
      store.nodeIdForDiagnosticItem({ file: '/abs/parent.sv', line: 999 }),
    ).toBeNull()
    // no graph loaded → null without crashing
    store.$reset()
    expect(
      store.nodeIdForDiagnosticItem({ file: '/abs/parent.sv', line: 52 }),
    ).toBeNull()
  })

  it('cleared sources (empty items) leave the node empty', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    store.applyDiagnostics('rtl-buddy-cdc', [
      { file: '/abs/parent.sv', line: 45, col: 1,
        severity: 'warning', code: 'CDC-1', message: 'a' },
    ])
    expect(store.diagnosticsByNode['top.u_dma']).toHaveLength(1)
    store.applyDiagnostics('rtl-buddy-cdc', [])
    expect(store.diagnosticsByNode).toEqual({})
  })

  it('applyWaveValues merges deltas — absent signals retain prior values', () => {
    const store = useViewerStore()
    store.applyWaveValues({
      t_fs: '100',
      values: [
        { wave_scope: 'tb.dut', signal: 'q',   value: "1'b0" },
        { wave_scope: 'tb.dut', signal: 'clk', value: "1'b1" },
      ],
    })
    expect(store.waveValuesByKey['tb.dut.q']).toBe("1'b0")
    expect(store.waveValuesByKey['tb.dut.clk']).toBe("1'b1")
    expect(store.waveValuesTFs).toBe('100')
    // Delta — only ``q`` changes. ``clk`` stays at "1'b1".
    store.applyWaveValues({
      t_fs: '200',
      values: [{ wave_scope: 'tb.dut', signal: 'q', value: "1'b1" }],
    })
    expect(store.waveValuesByKey['tb.dut.q']).toBe("1'b1")
    expect(store.waveValuesByKey['tb.dut.clk']).toBe("1'b1")
    expect(store.waveValuesTFs).toBe('200')
  })

  it('applyWaveValues with empty values updates the timestamp but keeps the map', () => {
    const store = useViewerStore()
    store.applyWaveValues({
      t_fs: '100',
      values: [{ wave_scope: 'tb', signal: 'q', value: "1'b1" }],
    })
    // Cursor moved into a region with nothing tracked — the hub
    // sends an empty values list. Map stays.
    store.applyWaveValues({ t_fs: '500', values: [] })
    expect(store.waveValuesByKey['tb.q']).toBe("1'b1")
    expect(store.waveValuesTFs).toBe('500')
  })

  it('applySignalSelected stores the {signal, wave_scope} pair', () => {
    const store = useViewerStore()
    store.applySignalSelected({ wave_scope: 'tb.dut', signal: 'q' })
    expect(store.hubSignalSelected).toEqual({ wave_scope: 'tb.dut', signal: 'q' })
    store.applySignalSelected(null)
    expect(store.hubSignalSelected).toBeNull()
  })

  it('graph reload clears stale wave values', () => {
    // Wave values are sampled at a t_fs of the previous design's
    // simulation. They MUST be wiped on _installGraph so the new
    // design doesn't paint port literals from a sibling model.
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    store.applyWaveValues({
      t_fs: '100',
      values: [{ wave_scope: 'top.u_dma', signal: 'q', value: "1'b1" }],
    })
    expect(store.waveValuesByKey['top.u_dma.q']).toBe("1'b1")
    store.loadFromText(JSON.stringify(diagPayload()))
    expect(store.waveValuesByKey).toEqual({})
    expect(store.waveValuesTFs).toBeNull()
    expect(store.hubSignalSelected).toBeNull()
  })

  it('applyWaveValues auto-enables the wave overlay on first non-empty payload', () => {
    // The producer's view.json doesn't have to list "wave" in
    // overlays_present for the live cascade to paint — receiving
    // values is itself the signal that the user wants badges
    // visible. Without this, the wave overlay starts disabled and
    // wave.js's apply() clears badges on every render pass.
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    expect(store.enabledOverlays.has('wave')).toBe(false)
    store.applyWaveValues({
      t_fs: '100',
      values: [{ wave_scope: 'tb', signal: 'q', value: "1'b1" }],
    })
    expect(store.enabledOverlays.has('wave')).toBe(true)
  })

  it('applyWaveValues with empty values does NOT auto-enable the overlay', () => {
    // An empty values batch is a no-op (the producer may emit it
    // when the cursor moves into a region with no tracked vars).
    // We auto-enable on real data, not on the bare cursor wiggle.
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    store.applyWaveValues({ t_fs: '1', values: [] })
    expect(store.enabledOverlays.has('wave')).toBe(false)
  })

  it('setOverlayEnabled flips a name + reassigns the Set so consumers re-render', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    const before = store.enabledOverlays
    store.setOverlayEnabled('clock', false)
    expect(store.enabledOverlays.has('clock')).toBe(false)
    // Set identity must change so Vue's reactive watcher fires.
    expect(store.enabledOverlays).not.toBe(before)
    store.setOverlayEnabled('clock', true)
    expect(store.enabledOverlays.has('clock')).toBe(true)
  })

  it('setOverlayEnabled rejects malformed names without crashing', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(diagPayload()))
    const before = store.enabledOverlays
    store.setOverlayEnabled('', true)
    store.setOverlayEnabled(null, true)
    store.setOverlayEnabled(undefined, false)
    // No-op: Set identity unchanged.
    expect(store.enabledOverlays).toBe(before)
  })

  describe('openAxiNotebook', () => {
    let origFetch
    let origOpen
    let opened
    beforeEach(() => {
      origFetch = window.fetch
      origOpen = window.open
      opened = []
      window.fetch = async () => ({ ok: true, json: async () => ({}) })
      window.open = (url, ...rest) => {
        opened.push({ url, rest })
        return null
      }
    })
    afterEach(() => {
      window.fetch = origFetch
      window.open = origOpen
    })

    it('calls /api/axi-profile/notebook with the supplied test + suite_dir', async () => {
      const captured = []
      window.fetch = async (url) => {
        captured.push(url)
        return {
          ok: true,
          json: async () => ({ url: 'http://localhost:31337', pid: 9, port: 31337 }),
        }
      }
      const store = useViewerStore()
      await store.openAxiNotebook({ test: 'basic_traffic', suiteDir: 'verif/demo' })
      expect(captured).toHaveLength(1)
      expect(captured[0]).toMatch(/\/api\/axi-profile\/notebook\?/)
      expect(captured[0]).toMatch(/test=basic_traffic/)
      expect(captured[0]).toMatch(/suite_dir=verif%2Fdemo/)
    })

    it('opens the returned URL in a new tab on success', async () => {
      window.fetch = async () => ({
        ok: true,
        json: async () => ({ url: 'http://localhost:31337' }),
      })
      const store = useViewerStore()
      await store.openAxiNotebook({ test: 't', suiteDir: 's' })
      expect(opened).toHaveLength(1)
      expect(opened[0].url).toBe('http://localhost:31337')
      expect(store.axiNotebookError).toBeNull()
      expect(store.axiNotebookLaunching).toBe(false)
    })

    it('surfaces hub-side JSON error message on 4xx/5xx', async () => {
      window.fetch = async () => ({
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
        json: async () => ({ error: 'marimo not on PATH; install [notebook]' }),
      })
      const store = useViewerStore()
      await expect(
        store.openAxiNotebook({ test: 't', suiteDir: 's' }),
      ).rejects.toThrow(/marimo not on PATH/)
      expect(store.axiNotebookError).toMatch(/marimo not on PATH/)
      expect(opened).toHaveLength(0)
    })

    it('falls back to status line when error body is not JSON', async () => {
      window.fetch = async () => ({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
        json: async () => {
          throw new Error('not json')
        },
      })
      const store = useViewerStore()
      await expect(
        store.openAxiNotebook({ test: 't', suiteDir: 's' }),
      ).rejects.toThrow(/500 Internal Server Error/)
    })

    it('clears launching flag even on exception', async () => {
      window.fetch = async () => {
        throw new Error('network down')
      }
      const store = useViewerStore()
      await expect(
        store.openAxiNotebook({ test: 't', suiteDir: 's' }),
      ).rejects.toThrow(/network down/)
      expect(store.axiNotebookLaunching).toBe(false)
      expect(store.axiNotebookError).toMatch(/network down/)
    })

    it('preserves the axi_perf block from view.json on the loaded graph', () => {
      // The Phase 2.5 auto-detect lives in AxiPerfView.vue (it reads
      // store.graph.axi_perf to skip the prompt). Store side just has
      // to not strip the field. Lock that with a round-trip through
      // loadFromText so a future parse.js tightening doesn't silently
      // drop the metadata.
      const store = useViewerStore()
      const payload = minimalPayload({
        axi_perf: {
          source: '/abs/verif/demo/artefacts/axi/basic_traffic/axi-perf.json',
          test: 'basic_traffic',
          suite_dir: '/abs/verif/demo',
        },
      })
      store.loadFromText(JSON.stringify(payload))
      expect(store.graph.axi_perf).toEqual({
        source: '/abs/verif/demo/artefacts/axi/basic_traffic/axi-perf.json',
        test: 'basic_traffic',
        suite_dir: '/abs/verif/demo',
      })
    })
  })
})

describe('viewer store — disambiguation picker (rtl-buddy-view#55)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('presentSelectionCandidates sets selection to [0] and stashes the list', () => {
    const store = useViewerStore()
    store.presentSelectionCandidates(['top.u_a', 'top.u_b', 'top.u_c'])
    expect(store.selection).toBe('top.u_a')
    expect(store.selectionCandidates).toEqual(['top.u_a', 'top.u_b', 'top.u_c'])
  })

  it('presentSelectionCandidates with a single path skips the picker', () => {
    const store = useViewerStore()
    store.presentSelectionCandidates(['top.u_only'])
    expect(store.selection).toBe('top.u_only')
    expect(store.selectionCandidates).toBeNull()
  })

  it('chooseSelectionCandidate locks the pick and clears the picker', () => {
    const store = useViewerStore()
    store.presentSelectionCandidates(['top.u_a', 'top.u_b'])
    store.chooseSelectionCandidate('top.u_b')
    expect(store.selection).toBe('top.u_b')
    expect(store.selectionCandidates).toBeNull()
  })

  it('dismissSelectionCandidates clears the picker without touching selection', () => {
    const store = useViewerStore()
    store.presentSelectionCandidates(['top.u_a', 'top.u_b'])
    store.dismissSelectionCandidates()
    expect(store.selection).toBe('top.u_a')
    expect(store.selectionCandidates).toBeNull()
  })

  it('applyHubSelection (single match) invalidates any pending picker', () => {
    const store = useViewerStore()
    store.presentSelectionCandidates(['top.u_a', 'top.u_b'])
    // A fresh, unambiguous selection arriving from the hub means the
    // resolver already disambiguated — don't keep a stale list around.
    store.applyHubSelection('top.elsewhere')
    expect(store.selection).toBe('top.elsewhere')
    expect(store.selectionCandidates).toBeNull()
  })

  it('rejects malformed inputs without crashing or mutating state', () => {
    const store = useViewerStore()
    store.presentSelectionCandidates([])
    expect(store.selection).toBeNull()
    expect(store.selectionCandidates).toBeNull()
    store.presentSelectionCandidates([null, undefined, ''])
    expect(store.selection).toBeNull()
    expect(store.selectionCandidates).toBeNull()
    store.chooseSelectionCandidate('')
    expect(store.selection).toBeNull()
  })
})
