// HubStatus — the SPA's bottom status strip.
//
// The chrome contract (rtl_buddy docs/concepts/hub.md) pins three
// things this component owes every other hub app: ONE status
// vocabulary (connected / connecting… / offline), the inline
// ``peers: …`` line the panes print (with the full roster of origins a
// user can have open one click away in the popover), and a message
// area on the shared severity tokens. All three are asserted here; the
// colours themselves are the e2e theme suite's job.

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import HubStatus from '../src/components/HubStatus.vue'
import { useHub, _testing } from '../src/composables/useHub.js'

describe('HubStatus strip', () => {
  let hub
  beforeEach(() => {
    _testing.reset()
    hub = useHub()
  })
  afterEach(() => {
    _testing.reset()
  })

  const states = [
    ['ready', 'connected'],
    ['connecting', 'connecting…'],
    ['disconnected', 'offline'],
  ]
  for (const [state, word] of states) {
    it(`says "${word}" for protocol state ${state}`, () => {
      hub.state.value = state
      const w = mount(HubStatus)
      const pill = w.get('.hub-status')
      expect(pill.text()).toBe(word)
      // The raw protocol name stays in the attribute — CSS and the
      // Playwright suite both key off it.
      expect(pill.attributes('data-state')).toBe(state)
    })
  }

  it('names the connected peers inline, in the panes\' exact form', () => {
    hub.state.value = 'ready'
    hub.peers.value = ['view', 'graph']
    const w = mount(HubStatus)
    // ``setPeers`` in rtl_buddy hub/graph_page.html writes exactly
    // this string; the SPA is the third app in the same strip.
    expect(w.get('.peers-inline').text()).toBe('peers: view, graph')
  })

  it('says "peers: none" rather than going blank when nobody is attached', () => {
    hub.state.value = 'ready'
    hub.peers.value = []
    expect(mount(HubStatus).get('.peers-inline').text()).toBe('peers: none')
  })

  it('lists every origin a user can have open, connected or not', async () => {
    hub.state.value = 'ready'
    hub.peers.value = ['wave']
    const w = mount(HubStatus)
    // The full roster is the popover's — the strip itself carries the
    // panes' one-line "peers: …" and nothing else.
    await w.get('.hub-status').trigger('click')
    const rows = w.findAll('.peer-list li')
    // `cli` and `notebook` are excluded on purpose: one is a one-shot
    // (`rb hub send`), the other is a single marimo session.
    expect(rows.map((r) => r.find('code').text())).toEqual([
      'view', 'src', 'wave', 'graph', 'cov',
    ])
    const byOrigin = Object.fromEntries(
      rows.map((r) => [r.find('code').text(), r.attributes('data-state')]),
    )
    expect(byOrigin.wave).toBe('connected')
    expect(byOrigin.view).toBe('disconnected')
    // `cov` is display-only until the pane lands (rtl_buddy#400) — it
    // must not claim to be connected on the strength of being listed.
    expect(byOrigin.cov).toBe('disconnected')
    expect(byOrigin.graph).toBe('disconnected')
  })

  it('the message area is a note when there is nothing to say', () => {
    hub.state.value = 'ready'
    const w = mount(HubStatus)
    expect(w.get('.hub-message').attributes('data-severity')).toBe('note')
    expect(w.get('.hub-message').text()).toBe('')
  })

  it('a hub error is an error-severity message carrying the code', () => {
    hub.state.value = 'ready'
    hub.lastError.value = { code: 'not_connected', message: 'no wave peer', at: 0 }
    const w = mount(HubStatus)
    expect(w.get('.hub-message').attributes('data-severity')).toBe('error')
    expect(w.get('.hub-message').text()).toContain('not_connected')
    expect(w.get('.hub-message').text()).toContain('no wave peer')
  })

  it('a mid-session drop is a warning, and only after we were once ready', () => {
    hub.state.value = 'disconnected'
    // A tab that has never connected is not "reconnecting" — it is
    // connecting for the first time, and saying otherwise cries wolf.
    expect(mount(HubStatus).find('.reconnecting-banner').exists()).toBe(false)

    hub.wasEverReady.value = true
    const w = mount(HubStatus)
    expect(w.find('.reconnecting-banner').exists()).toBe(true)
    expect(w.get('.hub-message').attributes('data-severity')).toBe('warning')
  })

  it('superseded outranks the reconnecting message and offers take-back', () => {
    hub.state.value = 'disconnected'
    hub.wasEverReady.value = true
    hub.superseded.value = true
    const w = mount(HubStatus)
    expect(w.find('.superseded-banner').exists()).toBe(true)
    expect(w.find('.reconnecting-banner').exists()).toBe(false)
    expect(w.get('.hub-message').attributes('data-severity')).toBe('error')
    expect(w.get('.superseded-banner button').text()).toBe('Take back')
  })
})
