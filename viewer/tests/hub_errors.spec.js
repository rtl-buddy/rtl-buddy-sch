// R7 — one error surface, human wording, expiring strip message.
//
// The defect: a hub error produced BOTH a toast headlined with the raw
// code AND a red mono duplicate in the status strip that never
// cleared. These tests pin the three halves of the fix — the wording
// map, the expiry, and "one event, one full rendering".

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'

import { humanizeHubError } from '../src/hubErrors.js'
import { useViewerStore } from '../src/store.js'
import { useHub, _testing } from '../src/composables/useHub.js'
import ToastHost from '../src/components/ToastHost.vue'

function errEnvelope(code, message) {
  return {
    v: 1,
    id: '00000000-0000-4000-8000-00000000000e',
    origin: 'wave',
    kind: 'error',
    type: 'error',
    payload: { code, message },
  }
}

describe('humanizeHubError', () => {
  it('turns the registration clash into a sentence with a take-over', () => {
    const copy = humanizeHubError({
      code: 'not_connected',
      message: 'view client already registered',
    })
    expect(copy.headline).toBe('Another schematic tab is connected to the hub.')
    expect(copy.takeover).toBe(true)
    expect(copy.known).toBe(true)
    // The machine facts survive as SECONDARY detail, not the headline.
    expect(copy.detail).toBe('not_connected — view client already registered')
  })

  it('separates the other not_connected meaning', () => {
    const copy = humanizeHubError({ code: 'not_connected', message: 'no wave peer' })
    expect(copy.headline).toContain('peer that request needed')
    expect(copy.takeover).toBe(false)
  })

  it('has wording for every code in the closed catalog', () => {
    for (const code of [
      'unresolvable',
      'not_connected',
      'bad_request',
      'protocol_mismatch',
      'superseded',
    ]) {
      const copy = humanizeHubError({ code, message: 'x' })
      expect(copy.known, code).toBe(true)
      // A sentence, not a token.
      expect(copy.headline).toMatch(/\.$/)
      expect(copy.short.length).toBeGreaterThan(0)
    }
  })

  it('keeps an unknown code as DETAIL and leads with the message', () => {
    const copy = humanizeHubError({ code: 'wormhole', message: 'the wire melted' })
    expect(copy.headline).toBe('the wire melted')
    expect(copy.detail).toBe('wormhole')
    expect(copy.known).toBe(false)
    // Nothing human to shorten — point at the surface that has it all.
    expect(copy.short).toBe('hub error — see toast')
  })

  it('survives a payload with neither field', () => {
    const copy = humanizeHubError({})
    expect(copy.headline).toContain('unknown')
    expect(humanizeHubError(null)).toBeNull()
  })
})

describe('hub error surfaces', () => {
  let store
  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
    _testing.reset()
    _testing.setStore({
      applyHubError: (e) => store.applyHubError(e),
    })
  })
  afterEach(() => {
    _testing.reset()
  })

  it('the strip notice expires; the popover log does not', () => {
    vi.useFakeTimers()
    try {
      const hub = useHub()
      _testing.applyEnvelope(errEnvelope('bad_request', 'malformed envelope'))
      expect(hub.errorNotice.value).not.toBeNull()
      expect(hub.lastError.value).not.toBeNull()

      vi.advanceTimersByTime(_testing.HUB_ERROR_STRIP_TTL_MS + 1)
      expect(hub.errorNotice.value).toBeNull()
      // The popover's "last error" row is a log, not a notification.
      expect(hub.lastError.value.code).toBe('bad_request')
    } finally {
      vi.useRealTimers()
    }
  })

  it('a welcome clears the strip notice early', () => {
    const hub = useHub()
    _testing.applyEnvelope(errEnvelope('unresolvable', 'no such node'))
    expect(hub.errorNotice.value).not.toBeNull()
    _testing.applyEnvelope({
      v: 1,
      id: '00000000-0000-4000-8000-00000000000f',
      origin: 'view',
      kind: 'response',
      type: 'welcome',
      payload: { server_version: '1.0.0', registered_clients: ['view'] },
    })
    expect(hub.errorNotice.value).toBeNull()
  })

  it('an explicit reconnect clears the strip notice', () => {
    const hub = useHub()
    _testing.applyEnvelope(errEnvelope('not_connected', 'no wave peer'))
    expect(hub.errorNotice.value).not.toBeNull()
    // No window/WebSocket in this env — connect() bails harmlessly.
    hub.reconnect()
    expect(hub.errorNotice.value).toBeNull()
  })

  it('superseded is the banner ONLY — no toast, no strip notice', () => {
    const hub = useHub()
    _testing.applyEnvelope(errEnvelope('superseded', 'another view tab connected'))
    expect(hub.superseded.value).toBe(true)
    // The banner (driven by ``superseded``) is the single surface.
    expect(store.hubError).toBeNull()
    expect(hub.errorNotice.value).toBeNull()
    // Still recorded for the popover's log row.
    expect(hub.lastError.value.code).toBe('superseded')
  })
})

describe('ToastHost', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    _testing.reset()
  })
  afterEach(() => {
    _testing.reset()
  })

  it('leads with the sentence and demotes the code to detail', () => {
    const store = useViewerStore()
    store.applyHubError({
      code: 'not_connected',
      message: 'view client already registered',
      at: 0,
    })
    const w = mount(ToastHost)
    expect(w.get('.toast .message').text()).toBe(
      'Another schematic tab is connected to the hub.',
    )
    expect(w.get('.toast .detail').text()).toBe(
      'not_connected — view client already registered',
    )
    // The take-over affordance rides along for this one case.
    expect(w.get('.toast .action').text()).toBe('Take over')
  })

  it('offers no take-over for errors a take-over cannot fix', () => {
    const store = useViewerStore()
    store.applyHubError({ code: 'bad_request', message: 'nope', at: 0 })
    const w = mount(ToastHost)
    expect(w.find('.toast .action').exists()).toBe(false)
  })
})
