// useHub() + store reducer tests.
//
// These tests do not stand up a real WebSocket; they exercise the
// envelope dispatch directly via the _testing surface. The Playwright
// suite at viewer/e2e/hub.spec.js is the end-to-end coverage that
// actually drives a mock /ws server.

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useViewerStore } from '../src/store.js'
import { useHub, initHub, _testing } from '../src/composables/useHub.js'

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

  it('cursor_time_changed from wave updates store, but echo from view is ignored', () => {
    _testing.applyEnvelope(env('cursor_time_changed', 'event', { t_fs: '1234567890' }, 'wave'))
    expect(store.hubCursorTimeFs).toBe('1234567890')
    _testing.applyEnvelope(env('cursor_time_changed', 'event', { t_fs: '99' }, 'view'))
    expect(store.hubCursorTimeFs).toBe('1234567890')
  })

  it('selection_changed picks the first element of an array instance_path', () => {
    _testing.applyEnvelope(
      env('selection_changed', 'event', { instance_path: ['top.u_fifo', 'top.u_dut'] }, 'wave'),
    )
    expect(store.selection).toBe('top.u_fifo')
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

  it('falls back to opening node.link when not connected', () => {
    const opened = []
    const realOpen = window.open
    window.open = (url) => { opened.push(url) }
    try {
      const hub = useHub()
      // state is 'disconnected' by default after reset.
      hub.notifyClick({ id: 'top.u_x', link: 'rtlbuddy:///x.sv?line=1' })
      expect(opened).toEqual(['rtlbuddy:///x.sv?line=1'])
    } finally {
      window.open = realOpen
    }
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
