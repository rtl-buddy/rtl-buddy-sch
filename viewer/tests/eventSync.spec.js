// useEventSync() composable + the matching store reducer.
//
// Mirrors hub.spec.js: no real WebSocket — the singleton exposes a
// ``_testing`` surface so the dispatcher can be driven directly.

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useViewerStore } from '../src/store.js'
import {
  initEventSync,
  useEventSync,
  _testing,
} from '../src/composables/useEventSync.js'

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
  send(text) {
    this.sent.push(text)
  }
  close() {
    this.readyState = 3
    if (this.onclose) this.onclose({})
  }
}

describe('useEventSync', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
    _testing.reset()
  })
  afterEach(() => {
    _testing.reset()
  })

  it('publish sends an envelope tagged source=spa once the socket is open', () => {
    initEventSync({ store, socketFactory: () => new MockSocket('ws://x/x') })
    const sock = _testing.currentSocket()
    sock.open()
    const handle = useEventSync()
    expect(handle.connected.value).toBe(true)
    expect(handle.publish('selection', { bundle: 'axi_xbar' })).toBe(true)
    expect(sock.sent).toHaveLength(1)
    const env = JSON.parse(sock.sent[0])
    expect(env.topic).toBe('selection')
    expect(env.source).toBe('spa')
    expect(env.data.bundle).toBe('axi_xbar')
  })

  it('publish returns false when the socket is not open', () => {
    initEventSync({ store, socketFactory: () => new MockSocket('ws://x/x') })
    const handle = useEventSync()
    // Not opened yet.
    expect(handle.publish('selection', {})).toBe(false)
  })

  it('inbound time-window dispatches to applyAxiPerfTimeWindow', () => {
    initEventSync({ store, socketFactory: () => new MockSocket('ws://x/x') })
    _testing.currentSocket().open()
    _testing.dispatch({
      topic: 'time-window',
      data: { t_start_fs: 1000, t_end_fs: 5000 },
      source: 'notebook',
    })
    expect(store.axiPerfTimeWindow).toEqual({ t_start_fs: 1000, t_end_fs: 5000 })
  })

  it('drops self-echoes (source=spa) before dispatch', () => {
    initEventSync({ store, socketFactory: () => new MockSocket('ws://x/x') })
    _testing.dispatch({
      topic: 'time-window',
      data: { t_start_fs: 1, t_end_fs: 2 },
      source: 'spa',
    })
    expect(store.axiPerfTimeWindow).toBe(null)
  })

  it('on/off subscribers fire for matching topics and ``*``', () => {
    initEventSync({ store, socketFactory: () => new MockSocket('ws://x/x') })
    const handle = useEventSync()
    const selSeen = []
    const allSeen = []
    const selCb = (env) => selSeen.push(env)
    const allCb = (env) => allSeen.push(env)
    handle.on('selection', selCb)
    handle.on('*', allCb)
    _testing.dispatch({
      topic: 'selection',
      data: { bundle: 'a' },
      source: 'notebook',
    })
    _testing.dispatch({
      topic: 'time-window',
      data: { t_start_fs: 1, t_end_fs: 2 },
      source: 'notebook',
    })
    expect(selSeen).toHaveLength(1)
    expect(allSeen).toHaveLength(2)
    handle.off('selection', selCb)
    _testing.dispatch({
      topic: 'selection',
      data: { bundle: 'b' },
      source: 'notebook',
    })
    expect(selSeen).toHaveLength(1) // unchanged
    expect(allSeen).toHaveLength(3)
  })

  it('malformed dispatch input is dropped silently', () => {
    initEventSync({ store, socketFactory: () => new MockSocket('ws://x/x') })
    _testing.dispatch(null)
    _testing.dispatch({})
    _testing.dispatch({ topic: 'time-window' /* no data, no source */ })
    expect(store.axiPerfTimeWindow).toBe(null)
  })
})

describe('applyAxiPerfTimeWindow store reducer', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
  })

  it('accepts a well-formed payload', () => {
    store.applyAxiPerfTimeWindow({ t_start_fs: 100, t_end_fs: 200 })
    expect(store.axiPerfTimeWindow).toEqual({ t_start_fs: 100, t_end_fs: 200 })
  })

  it('clears on null', () => {
    store.applyAxiPerfTimeWindow({ t_start_fs: 100, t_end_fs: 200 })
    store.applyAxiPerfTimeWindow(null)
    expect(store.axiPerfTimeWindow).toBe(null)
  })

  it('rejects payloads without finite numbers', () => {
    store.applyAxiPerfTimeWindow({ t_start_fs: 'oops', t_end_fs: 5 })
    expect(store.axiPerfTimeWindow).toBe(null)
    store.applyAxiPerfTimeWindow({ t_start_fs: NaN, t_end_fs: 5 })
    expect(store.axiPerfTimeWindow).toBe(null)
  })

  it('clearAxiPerfTimeWindow nulls the field', () => {
    store.applyAxiPerfTimeWindow({ t_start_fs: 1, t_end_fs: 2 })
    store.clearAxiPerfTimeWindow()
    expect(store.axiPerfTimeWindow).toBe(null)
  })
})
