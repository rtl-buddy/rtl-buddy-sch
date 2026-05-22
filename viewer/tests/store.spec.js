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
})
