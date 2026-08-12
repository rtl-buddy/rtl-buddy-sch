// The family display map, and the fence that keeps it off the wire.
//
// The rebrand (`view` → `sch`, `graph` → `gph`) is a DISPLAY-stage
// change. Two things are asserted here:
//
//   1. the map itself — the two renames, and passthrough for every
//      other origin, including ones this build has never heard of;
//   2. that nothing it produces reaches the protocol. The hello still
//      says `client: "view"` and the envelope still says
//      `origin: "view"`; a rebrand that leaks into the wire is a
//      silent incompatibility with every hub already deployed.
//
// The wire origin becomes `sch` no earlier than protocol v2, at which
// point (2) is what has to be deliberately rewritten.

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import {
  APP_DISPLAY_NAME,
  ORIGIN_DISPLAY,
  displayOrigin,
  displayOrigins,
} from '../src/displayNames.js'
import { hubApps } from '../src/hubApps.js'
import { initHub, _testing } from '../src/composables/useHub.js'
import { useViewerStore } from '../src/store.js'

describe('display names', () => {
  it('renames exactly the two origins whose label differs from the wire', () => {
    expect(displayOrigin('view')).toBe('sch')
    expect(displayOrigin('graph')).toBe('gph')
    // The knob: everything else is passthrough, so the table stays the
    // short list of DIFFERENCES rather than a second copy of the enum.
    expect(Object.keys(ORIGIN_DISPLAY).sort()).toEqual(['graph', 'view'])
  })

  it('passes through every origin it has no opinion about', () => {
    for (const origin of ['src', 'wave', 'cov', 'cli', 'notebook']) {
      expect(displayOrigin(origin)).toBe(origin)
    }
    // A hub that grows a peer we predate must still print something
    // true rather than dropping the row.
    expect(displayOrigin('telepath')).toBe('telepath')
  })

  it('is empty — not "undefined" — for a non-origin', () => {
    expect(displayOrigin('')).toBe('')
    expect(displayOrigin(null)).toBe('')
    expect(displayOrigin(undefined)).toBe('')
  })

  it('labels a peer list in order', () => {
    expect(displayOrigins(['view', 'src', 'graph', 'cov'])).toEqual([
      'sch', 'src', 'gph', 'cov',
    ])
    expect(displayOrigins([])).toEqual([])
    expect(displayOrigins(null)).toEqual([])
  })

  it('names this app in the wordmark and the switcher labels', () => {
    expect(APP_DISPLAY_NAME).toBe('rtl-buddy-sch')
    const apps = hubApps({
      __RTL_BUDDY_HUB__: '127.0.0.1:8123',
      __RTL_BUDDY_GRAPH_URL__: '/graph.json',
      __RTL_BUDDY_COV_URL__: '/cov.json',
    })
    // Labels are short names; the routes they open are unchanged.
    expect(apps.map((a) => [a.label, a.href])).toEqual([
      ['⌂ hub', '/'],
      ['gph ↗', '/graph'],
      ['cov ↗', '/cov'],
    ])
  })
})

class MockSocket {
  constructor() {
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
  send(text) { this.sent.push(text) }
  close() { this.readyState = 3 }
}

describe('display rebrand does not reach the wire', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    _testing.reset()
    store = useViewerStore()
  })
  afterEach(() => {
    _testing.reset()
  })

  it('still registers as client/origin "view" — never "sch"', () => {
    const sock = new MockSocket()
    _testing.setWsFactory(() => sock)
    initHub({ store })
    sock.open()
    const hello = JSON.parse(sock.sent[0])
    expect(hello.type).toBe('hello')
    expect(hello.payload.client).toBe('view')
    expect(hello.origin).toBe('view')
  })

  it('puts no display label anywhere in an emitted envelope', () => {
    const sock = new MockSocket()
    _testing.setWsFactory(() => sock)
    initHub({ store })
    sock.open()
    // Everything this tab has said so far, as raw wire text.
    for (const text of sock.sent) {
      expect(text).not.toMatch(/\bsch\b/)
      expect(text).not.toMatch(/\bgph\b/)
    }
  })
})
