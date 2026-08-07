// useHub() + store reducer tests.
//
// These tests do not stand up a real WebSocket; they exercise the
// envelope dispatch directly via the _testing surface. The Playwright
// suite at viewer/e2e/hub.spec.js is the end-to-end coverage that
// actually drives a mock /ws server.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useViewerStore } from '../src/store.js'
import { useHub, initHub, _testing } from '../src/composables/useHub.js'
import { registerSvgProvider, unregisterSvgProvider } from '../src/capture.js'

class MockSocket {
  constructor(url) {
    this.url = url
    this.readyState = 0
    this.sent = []
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
  }
  open() {
    this.readyState = 1
    if (this.onopen) this.onopen({})
  }
  receive(env) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(env) })
  }
  send(text) { this.sent.push(text) }
  close() {
    this.readyState = 3
    if (this.onclose) this.onclose({})
  }
}

function env(type, kind, payload, origin = 'wave') {
  return {
    v: 1,
    id: '00000000-0000-4000-8000-000000000000',
    origin,
    kind,
    type,
    payload,
  }
}

describe('useHub envelope dispatch', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
    _testing.reset()
    _testing.setStore({
      applyHubCursorTime: (t) => store.applyHubCursorTime(t),
      applyHubSelection: (id) => store.applyHubSelection(id),
      applyHubScope: (p) => store.applyHubScope(p),
      applyDiagnostics: (s, items) => store.applyDiagnostics(s, items),
      applyHubError: (e) => store.applyHubError(e),
      applyViewChanged: (p) => store.applyViewChanged(p),
      applyWaveValues: (p) => store.applyWaveValues(p),
      applySignalSelected: (p) => store.applySignalSelected(p),
      setOverlayEnabled: (name, enabled) =>
        store.setOverlayEnabled(name, enabled),
      presentSelectionCandidates: (paths) =>
        store.presentSelectionCandidates(paths),
      chooseSelectionCandidate: (path) => store.chooseSelectionCandidate(path),
      dismissSelectionCandidates: () => store.dismissSelectionCandidates(),
    })
  })
  afterEach(() => {
    _testing.reset()
  })

  it('welcome flips state to ready and records peers', () => {
    const hub = useHub()
    _testing.applyEnvelope(
      env('welcome', 'response', {
        server_version: '1.0.0',
        registered_clients: ['view', 'wave', 'src'],
      }),
    )
    expect(hub.state.value).toBe('ready')
    expect(hub.serverVersion.value).toBe('1.0.0')
    expect(hub.peers.value).toEqual(['view', 'wave', 'src'])
  })

  it('wasEverReady latches on first welcome and survives state transitions', () => {
    // A fresh tab starts with the flag clear so HubStatus doesn't
    // show "reconnecting" while we're just connecting for the
    // first time.
    const hub = useHub()
    expect(hub.wasEverReady.value).toBe(false)
    _testing.applyEnvelope(
      env('welcome', 'response', {
        server_version: '1.0.0',
        registered_clients: ['view'],
      }),
    )
    expect(hub.wasEverReady.value).toBe(true)
    // A hub-side error (server kicked us out) doesn't clear the
    // latch — the banner SHOULD show because we were online and
    // then weren't.
    _testing.applyEnvelope({
      v: 1,
      id: '00000000-0000-4000-8000-00000000000a',
      origin: 'wave',
      kind: 'error',
      type: 'error',
      payload: { code: 'not_connected', message: 'wave dropped' },
    })
    expect(hub.wasEverReady.value).toBe(true)
  })

  it('cursor_time_changed from wave updates store, but echo from view is ignored', () => {
    _testing.applyEnvelope(env('cursor_time_changed', 'event', { t_fs: '1234567890' }, 'wave'))
    expect(store.hubCursorTimeFs).toBe('1234567890')
    _testing.applyEnvelope(env('cursor_time_changed', 'event', { t_fs: '99' }, 'view'))
    expect(store.hubCursorTimeFs).toBe('1234567890')
  })

  it('selection_changed with a single-string instance_path applies it', () => {
    _testing.applyEnvelope(
      env('selection_changed', 'event', { instance_path: 'top.u_fifo' }, 'wave'),
    )
    expect(store.selection).toBe('top.u_fifo')
    expect(store.selectionCandidates).toBeNull()
  })

  it('selection_changed picks [0] and surfaces the picker for multi-match arrays', () => {
    _testing.applyEnvelope(
      env(
        'selection_changed',
        'event',
        { instance_path: ['top.u_fifo', 'top.u_dut', 'top.u_other'] },
        'wave',
      ),
    )
    // Smallest-range default still applied immediately so the canvas
    // pans/zooms to ``[0]`` in the common case.
    expect(store.selection).toBe('top.u_fifo')
    // Full list surfaces so SelectionCandidates.vue can render the
    // picker.
    expect(store.selectionCandidates).toEqual([
      'top.u_fifo',
      'top.u_dut',
      'top.u_other',
    ])
  })

  it('selection_changed with a single-element array does not open the picker', () => {
    // The hub only ever serialises ``instance_path`` as an array when
    // there are multiple matches, but a future producer might emit a
    // one-element array — that should still apply the selection without
    // popping the disambiguation UI.
    _testing.applyEnvelope(
      env('selection_changed', 'event', { instance_path: ['top.u_only'] }, 'wave'),
    )
    expect(store.selection).toBe('top.u_only')
    expect(store.selectionCandidates).toBeNull()
  })

  it('selection_changed from view origin does not loop back', () => {
    _testing.applyEnvelope(env('selection_changed', 'event', { instance_path: 'top.u_x' }, 'view'))
    expect(store.selection).toBeNull()
  })

  it('diagnostics_set stashes items keyed by source', () => {
    _testing.applyEnvelope(
      env('diagnostics_set', 'event', {
        source: 'rtl-buddy-cdc',
        items: [{ file: '/a.sv', line: 3, severity: 'error', message: 'no sync' }],
      }),
    )
    expect(store.diagnosticsBySource['rtl-buddy-cdc']).toHaveLength(1)
  })

  it('view_changed forwards payload to store.applyViewChanged', async () => {
    // Mock fetch so applyViewChanged → switchModel can complete
    // without standing up a real /view.json endpoint.
    const originalFetch = window.fetch
    window.fetch = async () => ({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: async () =>
        JSON.stringify({
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
            },
          ],
          edges: [],
          overlays_present: [],
        }),
    })
    try {
      _testing.applyEnvelope(
        env(
          'view_changed',
          'event',
          {
            model: 'demo',
            models_file: '/p/models.yaml',
            view_url: '/view.json?model=demo',
          },
          'cli',
        ),
      )
      // applyViewChanged is async; let microtasks drain.
      await new Promise((r) => setTimeout(r, 0))
      await new Promise((r) => setTimeout(r, 0))
      expect(store.activeModel).toBe('demo')
    } finally {
      window.fetch = originalFetch
    }
  })

  it('diagnostics_set with empty items clears that source only', () => {
    _testing.applyEnvelope(
      env('diagnostics_set', 'event', {
        source: 'rtl-buddy-cdc',
        items: [{ file: '/a.sv', line: 3, severity: 'error', message: 'no sync' }],
      }),
    )
    _testing.applyEnvelope(
      env('diagnostics_set', 'event', {
        source: 'rtl-buddy-lint',
        items: [{ file: '/a.sv', line: 4, severity: 'warning', message: 'lint' }],
      }),
    )
    _testing.applyEnvelope(env('diagnostics_set', 'event', { source: 'rtl-buddy-cdc', items: [] }))
    expect(store.diagnosticsBySource['rtl-buddy-cdc']).toBeUndefined()
    expect(store.diagnosticsBySource['rtl-buddy-lint']).toHaveLength(1)
  })

  it('error envelope populates hub.lastError and store.hubError', () => {
    const hub = useHub()
    _testing.applyEnvelope({
      v: 1,
      id: '00000000-0000-4000-8000-000000000001',
      origin: 'wave',
      kind: 'error',
      type: 'error',
      payload: { code: 'not_connected', message: 'no wave client' },
    })
    expect(hub.lastError.value?.code).toBe('not_connected')
    expect(store.hubError?.code).toBe('not_connected')
  })

  it('bye removes the leaving peer from the peers list', () => {
    const hub = useHub()
    // Seed peers via a welcome — same pathway production uses.
    _testing.applyEnvelope(
      env('welcome', 'response', {
        server_version: '1.0.0',
        registered_clients: ['view', 'src', 'wave'],
      }),
    )
    expect(hub.peers.value).toEqual(['view', 'src', 'wave'])

    _testing.applyEnvelope(env('bye', 'event', {}, 'src'))
    expect(hub.peers.value).toEqual(['view', 'wave'])

    _testing.applyEnvelope(env('bye', 'event', {}, 'wave'))
    expect(hub.peers.value).toEqual(['view'])
  })

  it('bye with cli origin or missing origin is a no-op', () => {
    const hub = useHub()
    _testing.applyEnvelope(
      env('welcome', 'response', { server_version: '1.0', registered_clients: ['view', 'src'] }),
    )
    // `cli` is the hub itself, not an adapter peer.
    _testing.applyEnvelope(env('bye', 'event', {}, 'cli'))
    expect(hub.peers.value).toEqual(['view', 'src'])
    // Defensive: an envelope with no origin shouldn't crash or wipe the list.
    _testing.applyEnvelope({ v: 1, id: 'x', kind: 'event', type: 'bye' })
    expect(hub.peers.value).toEqual(['view', 'src'])
  })

  it('peer_joined appends the new peer to the list', () => {
    const hub = useHub()
    // Start with just the SPA itself — mimics the case where the SPA
    // connected first and another adapter joins later (the bug PR #54
    // identified live: nvim started after the browser tab opened and
    // the popover never went green for `src`).
    _testing.applyEnvelope(
      env('welcome', 'response', { server_version: '1.0', registered_clients: ['view'] }),
    )
    expect(hub.peers.value).toEqual(['view'])

    _testing.applyEnvelope(env('peer_joined', 'event', {}, 'src'))
    expect(hub.peers.value).toEqual(['view', 'src'])

    _testing.applyEnvelope(env('peer_joined', 'event', {}, 'wave'))
    expect(hub.peers.value).toEqual(['view', 'src', 'wave'])
  })

  it('peer_joined is idempotent and rejects cli / missing origin', () => {
    const hub = useHub()
    _testing.applyEnvelope(
      env('welcome', 'response', { server_version: '1.0', registered_clients: ['view', 'src'] }),
    )
    // Duplicate join for an already-listed peer must not double-add.
    _testing.applyEnvelope(env('peer_joined', 'event', {}, 'src'))
    expect(hub.peers.value).toEqual(['view', 'src'])
    // cli is the hub itself.
    _testing.applyEnvelope(env('peer_joined', 'event', {}, 'cli'))
    expect(hub.peers.value).toEqual(['view', 'src'])
    // Missing origin shouldn't crash or wipe the list.
    _testing.applyEnvelope({ v: 1, id: 'x', kind: 'event', type: 'peer_joined' })
    expect(hub.peers.value).toEqual(['view', 'src'])
  })

  it('wave_values_changed merges into store after the coalesce flush', () => {
    _testing.applyEnvelope(
      env(
        'wave_values_changed',
        'event',
        {
          t_fs: '12500000',
          values: [
            { wave_scope: 'tb.dut', signal: 'q', value: "1'b1" },
            { wave_scope: 'tb.dut', signal: 'clk', value: "1'b0" },
          ],
        },
        'wave',
      ),
    )
    // Coalesce buffer is pending — store hasn't been written yet.
    expect(store.waveValuesByKey).toEqual({})
    _testing.flushWaveValues()
    expect(store.waveValuesByKey['tb.dut.q']).toBe("1'b1")
    expect(store.waveValuesByKey['tb.dut.clk']).toBe("1'b0")
    expect(store.waveValuesTFs).toBe('12500000')
  })

  it('wave_values_changed bursts coalesce — latest sample wins per signal', () => {
    // Three back-to-back envelopes for the same signal; only the
    // last value should reach the store (the renderer redraws once,
    // not three times).
    for (const v of ["32'h0", "32'h1", "32'hDEAD"]) {
      _testing.applyEnvelope(
        env(
          'wave_values_changed',
          'event',
          { t_fs: '100', values: [{ wave_scope: 'tb', signal: 'd', value: v }] },
          'wave',
        ),
      )
    }
    _testing.flushWaveValues()
    expect(store.waveValuesByKey['tb.d']).toBe("32'hDEAD")
  })

  it('wave_values_changed echoed from view is ignored (loop-prevention)', () => {
    _testing.applyEnvelope(
      env(
        'wave_values_changed',
        'event',
        { t_fs: '1', values: [{ wave_scope: 'tb', signal: 'q', value: "1'b1" }] },
        'view',
      ),
    )
    _testing.flushWaveValues()
    expect(store.waveValuesByKey).toEqual({})
  })

  it('signal_selected from wave updates store; echo from view is ignored', () => {
    _testing.applyEnvelope(
      env('signal_selected', 'event', { wave_scope: 'tb.dut', signal: 'q' }, 'wave'),
    )
    expect(store.hubSignalSelected).toEqual({ wave_scope: 'tb.dut', signal: 'q' })
    _testing.applyEnvelope(
      env('signal_selected', 'event', { wave_scope: 'tb.dut', signal: 'other' }, 'view'),
    )
    expect(store.hubSignalSelected.signal).toBe('q')
  })

  it('view_overlay_set request flips the named overlay + replies ok', () => {
    // Remote control: any peer can ask the SPA to toggle an
    // overlay layer. Lets agents / CLI / nvim flag overlays the
    // user should look at without needing to click the panel.
    _testing.applyEnvelope({
      v: 1,
      id: '00000000-0000-4000-8000-00000000abcd',
      origin: 'cli',
      kind: 'request',
      type: 'view_overlay_set',
      payload: { name: 'clock', enabled: false },
    })
    expect(store.enabledOverlays.has('clock')).toBe(false)
    _testing.applyEnvelope({
      v: 1,
      id: '00000000-0000-4000-8000-00000000abce',
      origin: 'cli',
      kind: 'request',
      type: 'view_overlay_set',
      payload: { name: 'clock', enabled: true },
    })
    expect(store.enabledOverlays.has('clock')).toBe(true)
  })

  it('unknown types are silently ignored (protocol §11)', () => {
    expect(() =>
      _testing.applyEnvelope(env('future_type', 'event', { whatever: true })),
    ).not.toThrow()
  })

  it('malformed envelopes are ignored', () => {
    expect(() => _testing.applyEnvelope(null)).not.toThrow()
    expect(() => _testing.applyEnvelope({})).not.toThrow()
    expect(() => _testing.applyEnvelope({ v: 2, type: 'welcome' })).not.toThrow()
  })
})

describe('useHub.notifyClick', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
    _testing.reset()
    _testing.setStore({
      applyHubCursorTime: (t) => store.applyHubCursorTime(t),
      applyHubSelection: (id) => store.applyHubSelection(id),
      applyHubScope: (p) => store.applyHubScope(p),
      applyDiagnostics: (s, items) => store.applyDiagnostics(s, items),
      applyHubError: (e) => store.applyHubError(e),
    })
  })
  afterEach(() => { _testing.reset() })

  it('emits selection_changed with origin: view when ready', () => {
    const sock = new MockSocket('ws://stub/ws')
    _testing.setWsFactory(() => sock)
    initHub({ store })
    sock.open()
    sock.receive(env('welcome', 'response', { server_version: '1.0', registered_clients: ['view'] }))

    const hub = useHub()
    hub.notifyClick({ id: 'top.u_fifo', link: 'rtlbuddy://top.u_fifo' })

    // hello + selection_changed
    expect(sock.sent.length).toBe(2)
    const sent = JSON.parse(sock.sent[1])
    expect(sent.type).toBe('selection_changed')
    expect(sent.origin).toBe('view')
    expect(sent.payload.instance_path).toBe('top.u_fifo')
  })

  it('is a no-op (does NOT open node.link) when not connected', () => {
    // Single-click is a *selection* gesture; opening a tab on every
    // click broke the dblclick-to-descend flow whenever the OS
    // didn't have a handler for rtlbuddy:// (very common — most
    // standalone deployments). The store's select happens in
    // GraphCanvas.onClick separately; this composable just no-ops
    // when offline. Right-click (``requestOpenSource``) still
    // falls back to window.open — that's an explicit "open source".
    const opened = []
    const realOpen = window.open
    window.open = (url) => { opened.push(url) }
    try {
      const hub = useHub()
      hub.notifyClick({ id: 'top.u_x', link: 'rtlbuddy:///x.sv?line=1' })
      expect(opened).toEqual([])
    } finally {
      window.open = realOpen
    }
  })

  it('still records lastClick when offline so the SPA can react locally', () => {
    const hub = useHub()
    const node = { id: 'top.u_y', link: 'rtlbuddy:///y.sv' }
    hub.notifyClick(node)
    expect(hub.lastClick.value).toStrictEqual(node)
  })
})

describe('useHub disambiguation picker (rtl-buddy-view#55)', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
    _testing.reset()
    _testing.setStore({
      applyHubCursorTime: (t) => store.applyHubCursorTime(t),
      applyHubSelection: (id) => store.applyHubSelection(id),
      applyHubScope: (p) => store.applyHubScope(p),
      applyDiagnostics: (s, items) => store.applyDiagnostics(s, items),
      applyHubError: (e) => store.applyHubError(e),
      presentSelectionCandidates: (paths) =>
        store.presentSelectionCandidates(paths),
      chooseSelectionCandidate: (path) => store.chooseSelectionCandidate(path),
      dismissSelectionCandidates: () => store.dismissSelectionCandidates(),
    })
  })
  afterEach(() => { _testing.reset() })

  it('chooseSelectionCandidate locks the pick AND broadcasts to the hub', () => {
    vi.useFakeTimers()
    try {
      const sock = new MockSocket('ws://stub/ws')
      _testing.setWsFactory(() => sock)
      initHub({ store })
      sock.open()
      sock.receive(env('welcome', 'response', { server_version: '1.0', registered_clients: ['view'] }))
      // Step past the post-welcome replay settle window — inside it a
      // multi-match applies its default silently and no picker opens
      // (that path has its own tests below).
      vi.advanceTimersByTime(_testing.HUB_REPLAY_SETTLE_MS + 1)

      // Multi-match arrives → picker opens with [0] selected.
      sock.receive(
        env(
          'selection_changed',
          'event',
          { instance_path: ['top.u_a', 'top.u_b'] },
          'wave',
        ),
      )
      expect(store.selection).toBe('top.u_a')
      expect(store.selectionCandidates).toEqual(['top.u_a', 'top.u_b'])

      const sentBefore = sock.sent.length
      const hub = useHub()
      hub.chooseSelectionCandidate('top.u_b')

      // Store locked in on the override and the picker dismissed.
      expect(store.selection).toBe('top.u_b')
      expect(store.selectionCandidates).toBeNull()

      // Wire broadcast: one ``selection_changed`` envelope from origin=view
      // so peers (nvim, wave) lock onto the same path.
      const fresh = sock.sent.slice(sentBefore).map((s) => JSON.parse(s))
      const broadcast = fresh.find((e) => e.type === 'selection_changed')
      expect(broadcast).toBeDefined()
      expect(broadcast.origin).toBe('view')
      expect(broadcast.payload.instance_path).toBe('top.u_b')
    } finally {
      vi.useRealTimers()
    }
  })

  it('a fresh single-match selection_changed dismisses the picker', () => {
    _testing.applyEnvelope(
      env(
        'selection_changed',
        'event',
        { instance_path: ['top.u_a', 'top.u_b'] },
        'wave',
      ),
    )
    expect(store.selectionCandidates).toEqual(['top.u_a', 'top.u_b'])

    _testing.applyEnvelope(
      env('selection_changed', 'event', { instance_path: 'top.other' }, 'wave'),
    )
    expect(store.selection).toBe('top.other')
    expect(store.selectionCandidates).toBeNull()
  })

  it('dismissSelectionCandidates clears the picker without touching selection', () => {
    _testing.applyEnvelope(
      env(
        'selection_changed',
        'event',
        { instance_path: ['top.u_a', 'top.u_b'] },
        'wave',
      ),
    )
    expect(store.selection).toBe('top.u_a')
    expect(store.selectionCandidates).toEqual(['top.u_a', 'top.u_b'])

    const hub = useHub()
    hub.dismissSelectionCandidates()
    expect(store.selectionCandidates).toBeNull()
    expect(store.selection).toBe('top.u_a')
  })

  it('the picker has no timer — it waits to be dismissed (R10)', () => {
    // The auto-dismiss timer is GONE. A popover that vanishes while
    // the user is still reading a handful of near-identical instance
    // paths is a worse failure than one that waits.
    vi.useFakeTimers()
    try {
      _testing.applyEnvelope(
        env(
          'selection_changed',
          'event',
          { instance_path: ['top.u_a', 'top.u_b'] },
          'wave',
        ),
      )
      expect(store.selectionCandidates).toEqual(['top.u_a', 'top.u_b'])
      vi.advanceTimersByTime(60_000)
      expect(store.selectionCandidates).toEqual(['top.u_a', 'top.u_b'])
      expect(store.selection).toBe('top.u_a')
    } finally {
      vi.useRealTimers()
    }
  })

  it('exposes no auto-dismiss constant any more', () => {
    expect(_testing.SELECTION_PICKER_AUTODISMISS_MS).toBeUndefined()
  })
})

describe('useHub graph_focus → module selection', () => {
  // A node clicked in the /graph pane, or a module pill clicked in
  // /cov, arrives as ``graph_focus {node: "module:<name>"}``. The
  // panes think in MODULE TYPES; this view is a tree of INSTANCES, so
  // the SPA resolves 1→N against the currently loaded view.json.
  let store

  function payload(nodes) {
    return {
      schema_version: '1.0',
      top: 'top',
      nodes: nodes.map((n) => ({
        is_blackbox: false,
        parameters: {},
        ports: [],
        overlays: {},
        ...n,
      })),
      edges: [],
      overlays_present: [],
    }
  }

  function load(nodes) {
    store.loadFromText(JSON.stringify(payload(nodes)))
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
    _testing.reset()
    _testing.setStore({
      applyHubSelection: (id) => store.applyHubSelection(id),
      presentSelectionCandidates: (paths) => store.presentSelectionCandidates(paths),
      dismissSelectionCandidates: () => store.dismissSelectionCandidates(),
      // A getter, not a snapshot: the resolution has to see whatever
      // model is loaded at the moment the envelope lands.
      get nodeIdsByModule() {
        return store.nodeIdsByModule
      },
    })
    load([
      { id: 'top', module: 'top' },
      { id: 'top.u_fifo', module: 'fifo' },
      { id: 'top.u_core', module: 'core' },
      { id: 'top.u_core.u_fifo', module: 'fifo' },
    ])
  })
  afterEach(() => { _testing.reset() })

  it('selects the only instance of the named module', () => {
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:core' }, 'graph'))
    expect(store.selection).toBe('top.u_core')
    expect(store.selectionCandidates).toBeNull()
  })

  it('opens the picker for a module instantiated more than once, shallowest first', () => {
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:fifo' }, 'graph'))
    // Same multi-match path a ``selection_changed`` array takes: the
    // least-nested instance is applied immediately, the full list
    // surfaces for the user to override.
    expect(store.selection).toBe('top.u_fifo')
    expect(store.selectionCandidates).toEqual(['top.u_fifo', 'top.u_core.u_fifo'])
  })

  it('the multi-match picker stays until dismissed, then keeps the default', () => {
    vi.useFakeTimers()
    try {
      _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:fifo' }, 'graph'))
      expect(store.selectionCandidates).not.toBeNull()
      // No timer: waiting changes nothing.
      vi.advanceTimersByTime(60_000)
      expect(store.selectionCandidates).not.toBeNull()

      useHub().dismissSelectionCandidates()
      expect(store.selectionCandidates).toBeNull()
      // Dismissing the picker does not undo the applied default.
      expect(store.selection).toBe('top.u_fifo')
    } finally {
      vi.useRealTimers()
    }
  })

  it('soft-ignores a module this model does not contain', () => {
    // Schema semantics for graph_focus: an unresolvable target is
    // silently kept, not an error. The knowledge graph spans every
    // module in the project; this view holds one elaboration.
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:nowhere' }, 'graph'))
    expect(store.selection).toBeNull()
    expect(store.selectionCandidates).toBeNull()
    expect(store.hubError).toBeNull()
  })

  it('ignores node ids outside the module: vocabulary', () => {
    // The knowledge graph's id space is wider than this view's; only
    // ``module:`` names something a hierarchy can be resolved against.
    for (const node of [
      'inst:top/top.u_fifo',
      'test:smoke#fifo_basic',
      'fifo',
      'module:',
      'MODULE:fifo',
      '',
    ]) {
      _testing.applyEnvelope(env('graph_focus', 'event', { node }, 'graph'))
      expect(store.selection).toBeNull()
    }
    // Non-string payloads are equally inert.
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 42 }, 'graph'))
    _testing.applyEnvelope(env('graph_focus', 'event', {}, 'graph'))
    expect(store.selection).toBeNull()
  })

  it('ignores its own origin so a focus cannot loop', () => {
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:core' }, 'view'))
    expect(store.selection).toBeNull()
  })

  it('never echoes a focus back to the hub', () => {
    const sock = new MockSocket('ws://stub/ws')
    _testing.setWsFactory(() => sock)
    initHub({ store: undefined })
    sock.open()
    sock.receive(env('welcome', 'response', { server_version: '1.0', registered_clients: ['view'] }))
    const before = sock.sent.length
    sock.receive(env('graph_focus', 'event', { node: 'module:core' }, 'graph'))
    // Broadcasting the selection we were just handed is how two panes
    // bounce one click between each other forever.
    expect(sock.sent.length).toBe(before)
  })

  it('resolves against the model loaded right now, not the one at connect time', () => {
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:core' }, 'graph'))
    expect(store.selection).toBe('top.u_core')

    // Model switch: same module name, different hierarchy.
    load([
      { id: 'tb', module: 'top' },
      { id: 'tb.dut', module: 'core' },
    ])
    expect(store.selection).toBeNull() // _installGraph wipes selection
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:core' }, 'graph'))
    expect(store.selection).toBe('tb.dut')

    // And a module that only existed in the previous model is now a
    // soft miss rather than a stale hit.
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:fifo' }, 'graph'))
    expect(store.selection).toBe('tb.dut')
  })
})

describe('hub takeover handshake', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    _testing.reset()
  })
  afterEach(() => { _testing.reset() })

  it('retries hello with takeover=true on "already registered" error', () => {
    const sock = new MockSocket('ws://stub/ws')
    _testing.setWsFactory(() => sock)
    initHub({})
    sock.open()
    // First hello — plain, no takeover.
    expect(sock.sent.length).toBe(1)
    const firstHello = JSON.parse(sock.sent[0])
    expect(firstHello.type).toBe('hello')
    expect(firstHello.payload.takeover).toBeUndefined()

    // Hub refuses because another view is registered.
    sock.receive({
      v: 1,
      id: '00000000-0000-4000-8000-000000000001',
      origin: 'cli',
      kind: 'error',
      type: 'error',
      payload: { code: 'not_connected', message: 'view client already registered' },
    })

    // Composable retries with takeover=true.
    expect(sock.sent.length).toBe(2)
    const retryHello = JSON.parse(sock.sent[1])
    expect(retryHello.type).toBe('hello')
    expect(retryHello.payload.takeover).toBe(true)
  })

  it('sets superseded and stops auto-reconnect on superseded error', () => {
    const sock = new MockSocket('ws://stub/ws')
    _testing.setWsFactory(() => sock)
    initHub({})
    sock.open()
    sock.receive(env('welcome', 'response', { server_version: '1.0', registered_clients: ['view'] }))
    const hub = useHub()
    expect(hub.state.value).toBe('ready')
    expect(hub.superseded.value).toBe(false)

    sock.receive({
      v: 1,
      id: '00000000-0000-4000-8000-000000000002',
      origin: 'cli',
      kind: 'error',
      type: 'error',
      payload: { code: 'superseded', message: 'view client replaced by a newer registration' },
    })
    expect(hub.superseded.value).toBe(true)
  })

  it('reconnect({takeover}) clears superseded and primes the next hello', () => {
    const sock = new MockSocket('ws://stub/ws')
    _testing.setWsFactory(() => sock)
    initHub({})
    sock.open()
    sock.receive(env('welcome', 'response', { server_version: '1.0', registered_clients: ['view'] }))
    const hub = useHub()
    sock.receive({
      v: 1,
      id: '00000000-0000-4000-8000-000000000003',
      origin: 'cli',
      kind: 'error',
      type: 'error',
      payload: { code: 'superseded', message: 'kicked' },
    })
    expect(hub.superseded.value).toBe(true)

    // Take-back: open a fresh socket and verify the hello carries
    // takeover=true.
    const sock2 = new MockSocket('ws://stub/ws')
    _testing.setWsFactory(() => sock2)
    hub.reconnect({ takeover: true })
    sock2.open()
    expect(hub.superseded.value).toBe(false)
    const hello = JSON.parse(sock2.sent[0])
    expect(hello.payload.takeover).toBe(true)
  })
})

describe('viewer store hub reducers', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
  })

  it('applyDiagnostics ignores empty source names', () => {
    store.applyDiagnostics('', [{ file: 'x', line: 1, severity: 'error', message: 'm' }])
    expect(store.diagnosticsBySource).toEqual({})
  })

  it('applyHubSelection sets selection only for non-empty strings', () => {
    store.applyHubSelection('')
    expect(store.selection).toBeNull()
    store.applyHubSelection('top.u_x')
    expect(store.selection).toBe('top.u_x')
  })

  it('dismissHubError clears the toast slot', () => {
    store.applyHubError({ code: 'bad_request', message: 'oops' })
    expect(store.hubError?.code).toBe('bad_request')
    store.dismissHubError()
    expect(store.hubError).toBeNull()
  })

  it('diagnosticsFlat flattens across sources', () => {
    store.applyDiagnostics('a', [{ file: '/x', line: 1, severity: 'error', message: 'm1' }])
    store.applyDiagnostics('b', [{ file: '/y', line: 2, severity: 'warning', message: 'm2' }])
    const flat = store.diagnosticsFlat
    expect(flat).toHaveLength(2)
    const sources = flat.map((d) => d.source).sort()
    expect(sources).toEqual(['a', 'b'])
  })
})

describe('useHub view_capture request handler', () => {
  // Fake <svg> shaped the way capture.js inspects it: ``getAttribute``
  // for viewBox/width/height, ``cloneNode(true)`` returning a serialisable
  // copy, and XMLSerializer chewing on it. jsdom provides both, so the
  // SVG branch round-trips cleanly without a real layout pass.
  function makeFakeSvg() {
    // Use the live HTML document's createElementNS — jsdom serialises
    // its detached SVGDocument as <html>, while elements attached
    // through the main document keep their <svg> tag through
    // XMLSerializer.
    const ns = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(ns, 'svg')
    svg.setAttribute('viewBox', '0 0 100 50')
    svg.setAttribute('width', '100')
    svg.setAttribute('height', '50')
    const rect = document.createElementNS(ns, 'rect')
    rect.setAttribute('x', '0')
    rect.setAttribute('y', '0')
    rect.setAttribute('width', '100')
    rect.setAttribute('height', '50')
    rect.setAttribute('fill', '#abcdef')
    svg.appendChild(rect)
    // Park it in the DOM so the cloneNode + XMLSerializer path
    // doesn't fall through to the html serializer fallback.
    document.body.appendChild(svg)
    return svg
  }

  let sock
  let provider
  beforeEach(() => {
    setActivePinia(createPinia())
    _testing.reset()
    sock = new MockSocket('ws://stub/ws')
    _testing.setWsFactory(() => sock)
    initHub({})
    sock.open()
    sock.receive(
      env('welcome', 'response', {
        server_version: '1.0',
        registered_clients: ['view'],
      }),
    )
    provider = () => makeFakeSvg()
    registerSvgProvider(provider)
  })
  afterEach(() => {
    unregisterSvgProvider(provider)
    _testing.reset()
  })

  it('responds with format=svg and base64 bytes when SVG requested', async () => {
    const reqId = '11111111-1111-4111-8111-111111111111'
    const sentBefore = sock.sent.length
    sock.receive({
      v: 1,
      id: reqId,
      origin: 'cli',
      kind: 'request',
      type: 'view_capture',
      payload: { format: 'svg' },
    })
    // The handler is async — let microtasks drain so the response send
    // lands in sock.sent before we inspect it.
    await new Promise((r) => setTimeout(r, 0))
    const out = sock.sent.slice(sentBefore).map((s) => JSON.parse(s))
    const resp = out.find((e) => e.type === 'view_capture' && e.kind === 'response')
    expect(resp).toBeDefined()
    expect(resp.id).toBe(reqId) // correlation: same id back
    expect(resp.origin).toBe('view')
    expect(resp.payload.format).toBe('svg')
    expect(resp.payload.width).toBe(100)
    expect(resp.payload.height).toBe(50)
    expect(typeof resp.payload.bytes_b64).toBe('string')
    expect(resp.payload.bytes_b64.length).toBeGreaterThan(0)
    // The decoded body should be SVG markup containing the rect we added.
    const decoded = atob(resp.payload.bytes_b64)
    expect(decoded).toContain('<svg')
    expect(decoded).toContain('#abcdef')
  })

  it('responds with kind=error when no SVG is registered', async () => {
    unregisterSvgProvider(provider)
    const reqId = '22222222-2222-4222-8222-222222222222'
    const sentBefore = sock.sent.length
    sock.receive({
      v: 1,
      id: reqId,
      origin: 'cli',
      kind: 'request',
      type: 'view_capture',
      payload: { format: 'svg' },
    })
    await new Promise((r) => setTimeout(r, 0))
    const out = sock.sent.slice(sentBefore).map((s) => JSON.parse(s))
    const err = out.find((e) => e.kind === 'error')
    expect(err).toBeDefined()
    expect(err.id).toBe(reqId)
    expect(err.payload.code).toBe('bad_request')
    expect(err.payload.message).toMatch(/no graph rendered/i)
  })

  it('ignores view_capture envelopes whose kind is not request', async () => {
    const sentBefore = sock.sent.length
    sock.receive({
      v: 1,
      id: '33333333-3333-4333-8333-333333333333',
      origin: 'cli',
      kind: 'event',
      type: 'view_capture',
      payload: {},
    })
    await new Promise((r) => setTimeout(r, 0))
    // No new envelope sent — wrong-kind capture is a silent drop, not
    // an error reply (responses to non-requests would loop).
    expect(sock.sent.length).toBe(sentBefore)
  })
})

describe('useHub replay settle window (R9)', () => {
  // The hub replays its cached focus/selection slot to a freshly
  // joining peer right after ``welcome`` (HubState._replay_cached_state).
  // On a fresh page load that used to pop an unexplained multi-match
  // picker before the user had touched anything.
  let store

  function loadTwoFifos() {
    store.loadFromText(
      JSON.stringify({
        schema_version: '1.0',
        top: 'top',
        nodes: [
          { id: 'top', module: 'top' },
          { id: 'top.u_fifo', module: 'fifo' },
          { id: 'top.u_core', module: 'core' },
          { id: 'top.u_core.u_fifo', module: 'fifo' },
        ].map((n) => ({
          is_blackbox: false,
          parameters: {},
          ports: [],
          overlays: {},
          ...n,
        })),
        edges: [],
        overlays_present: [],
      }),
    )
  }

  function welcome() {
    _testing.applyEnvelope(
      env('welcome', 'response', { server_version: '1.0', registered_clients: ['view'] }, 'view'),
    )
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
    _testing.reset()
    _testing.setStore({
      applyHubSelection: (id) => store.applyHubSelection(id),
      presentSelectionCandidates: (paths) => store.presentSelectionCandidates(paths),
      dismissSelectionCandidates: () => store.dismissSelectionCandidates(),
      get nodeIdsByModule() {
        return store.nodeIdsByModule
      },
    })
    loadTwoFifos()
  })
  afterEach(() => {
    _testing.reset()
    vi.useRealTimers()
  })

  it('a replayed multi-match selection_changed applies its default silently', () => {
    vi.useFakeTimers()
    welcome()
    _testing.applyEnvelope(
      env('selection_changed', 'event', { instance_path: ['top.u_fifo', 'top.u_core.u_fifo'] }, 'wave'),
    )
    // Default applied — the canvas still reacts…
    expect(store.selection).toBe('top.u_fifo')
    // …but no picker nobody asked for.
    expect(store.selectionCandidates).toBeNull()
  })

  it('a replayed multi-match graph_focus applies its default silently', () => {
    vi.useFakeTimers()
    welcome()
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:fifo' }, 'graph'))
    expect(store.selection).toBe('top.u_fifo')
    expect(store.selectionCandidates).toBeNull()
  })

  it('the same event AFTER the window opens the picker', () => {
    vi.useFakeTimers()
    welcome()
    vi.advanceTimersByTime(_testing.HUB_REPLAY_SETTLE_MS + 1)
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:fifo' }, 'graph'))
    expect(store.selectionCandidates).toEqual(['top.u_fifo', 'top.u_core.u_fifo'])
  })

  it('is anchored on welcome, not on module load', () => {
    // Never welcomed → nothing to replay → the picker is live from
    // the first event (a standalone SPA driven by a peer that
    // connected before us).
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:fifo' }, 'graph'))
    expect(store.selectionCandidates).toEqual(['top.u_fifo', 'top.u_core.u_fifo'])
  })

  it('re-arms on every welcome, so a reconnect replay is quiet too', () => {
    vi.useFakeTimers()
    welcome()
    vi.advanceTimersByTime(_testing.HUB_REPLAY_SETTLE_MS + 1)
    welcome()
    _testing.applyEnvelope(env('graph_focus', 'event', { node: 'module:fifo' }, 'graph'))
    expect(store.selectionCandidates).toBeNull()
    expect(store.selection).toBe('top.u_fifo')
  })
})
