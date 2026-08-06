// HubStatus — the SPA's bottom status strip.
//
// The chrome contract (rtl_buddy docs/concepts/hub.md) pins three
// things this component owes every other hub app: ONE status
// vocabulary (connected / connecting… / offline), a peer list covering
// every origin a user can have open, and a message area on the shared
// severity tokens. All three are asserted here; the colours themselves
// are the e2e theme suite's job.

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

  it('lists every origin a user can have open, connected or not', () => {
    hub.state.value = 'ready'
    hub.peers.value = ['wave']
    const w = mount(HubStatus)
    const rows = w.findAll('.peer-strip li')
    // `cli` and `notebook` are excluded on purpose: one is a one-shot
    // (`rb hub send`), the other is a single marimo session.
    expect(rows.map((r) => r.text())).toEqual(['view', 'src', 'wave', 'graph', 'cov'])
    const byOrigin = Object.fromEntries(
      rows.map((r) => [r.text(), r.attributes('data-state')]),
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
