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
  })
  afterEach(() => {
    window.__RTL_BUDDY_VIEW_DATA__ = null
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

  it('bootstrap stays idle when no payload is present', async () => {
    const store = useViewerStore()
    await store.bootstrap()
    expect(store.status).toBe('idle')
  })
})
