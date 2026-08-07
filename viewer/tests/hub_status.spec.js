// HubStatus — the SPA's bottom status strip.
//
// The chrome contract (rtl_buddy docs/concepts/hub.md) pins three
// things this component owes every other hub app: ONE status
// vocabulary (connected / connecting… / offline), the inline
// ``peers: …`` line the panes print (with the full roster of origins a
// user can have open one click away in the popover), and a message
// area on the shared severity tokens. All three are asserted here; the
// colours themselves are the e2e theme suite's job.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import HubStatus from '../src/components/HubStatus.vue'
import { versionLabel } from '../src/buildInfo.js'
import { useHub, _testing } from '../src/composables/useHub.js'

// Stand in for the hashed asset the browser actually loaded. Putting a
// real ``<script src>`` in the test document instead makes happy-dom
// try to FETCH it, which floods the run with request errors; the
// reading of the tag is unit-tested against a fake document in
// chrome_ux.spec.js.
const { BUNDLE_HASH } = vi.hoisted(() => ({ BUNDLE_HASH: 'Ab3-9x' }))
vi.mock('../src/buildInfo.js', async (importOriginal) => ({
  ...(await importOriginal()),
  readBundleHash: () => BUNDLE_HASH,
}))

// The one label every hub app shows. The pane implementations in
// rtl_buddy carry the same cases — if this table changes, theirs does.
describe('versionLabel', () => {
  it('names the base and the commit sha of a setuptools-scm dev build', () => {
    // The ``.dYYYYMMDD`` dirty-date suffix is noise: it says when the
    // tree was built, which nobody is asking the strip.
    expect(versionLabel('6.26.2.dev13+g3f5b890e3.d20260806')).toBe('6.26.2.dev13 @ 3f5b890e3')
  })

  it('leaves a plain release alone', () => {
    expect(versionLabel('6.26.2')).toBe('6.26.2')
  })

  it('drops a local segment that carries no sha', () => {
    expect(versionLabel('1.0+local')).toBe('1.0')
  })

  it('caps the sha at nine characters', () => {
    expect(versionLabel('1.0+g0123456789abcdef')).toBe('1.0 @ 012345678')
  })

  it('is null — not an empty label — when there is no version', () => {
    expect(versionLabel('')).toBe(null)
    expect(versionLabel(undefined)).toBe(null)
    expect(versionLabel(null)).toBe(null)
  })
})

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

  it('shows the hub version in the strip, only once a welcome has named it', async () => {
    hub.state.value = 'connecting'
    hub.serverVersion.value = null
    const w = mount(HubStatus)
    // Before the welcome there is no version to be honest about, and a
    // placeholder in the strip would read as a build called "—".
    expect(w.find('.version-inline').exists()).toBe(false)

    // A welcome arrives (and so does every later one — reconnect to a
    // hub that has been restarted on a new build must relabel).
    hub.state.value = 'ready'
    hub.serverVersion.value = '6.26.2.dev13+g3f5b890e3.d20260806'
    await w.vm.$nextTick()
    const label = w.get('.version-inline')
    expect(label.text()).toContain('rtl-buddy 6.26.2.dev13 @ 3f5b890e3')
    // The strip is the short form; the whole string is one hover away
    // (and a row in the popover).
    expect(label.attributes('title')).toBe('6.26.2.dev13+g3f5b890e3.d20260806')
  })

  it('names the SPA bundle separately — it ships apart from the hub', () => {
    // The panes are HTML the hub serves, so the server version covers
    // them; this bundle can be a rebuild behind the hub it talks to,
    // which is exactly the confusion the second half of the label ends.
    hub.state.value = 'ready'
    hub.serverVersion.value = '6.26.2'
    const w = mount(HubStatus)
    expect(w.get('.version-inline .ui-hash').text()).toBe(`· ui ${BUNDLE_HASH}`)
    expect(w.get('.version-inline').text()).toContain('rtl-buddy 6.26.2')
  })

  it('the message area is a note when there is nothing to say', () => {
    hub.state.value = 'ready'
    const w = mount(HubStatus)
    expect(w.get('.hub-message').attributes('data-severity')).toBe('note')
    expect(w.get('.hub-message').text()).toBe('')
  })

  it('a hub error is an error-severity message in SHORT human words', () => {
    // R7c: one event, one full rendering. The toast says the sentence;
    // the strip says a few words. The raw ``code — message`` is the
    // hover title, not a second copy of the toast in red mono.
    hub.state.value = 'ready'
    hub.errorNotice.value = { code: 'not_connected', message: 'no wave peer', at: 0 }
    const w = mount(HubStatus)
    const msg = w.get('.hub-message')
    expect(msg.attributes('data-severity')).toBe('error')
    expect(msg.text()).toBe('peer not connected')
    expect(msg.text()).not.toContain('not_connected')
    expect(w.get('.hub-message .msg-text').attributes('title')).toBe(
      'not_connected — no wave peer',
    )
  })

  it('the strip ignores lastError once its notice has expired', () => {
    // ``lastError`` is the popover's permanent log row; the strip
    // reads ``errorNotice``, which expires. Before this the red mono
    // line stayed put for the rest of the session.
    hub.state.value = 'ready'
    hub.lastError.value = { code: 'bad_request', message: 'boom', at: 0 }
    hub.errorNotice.value = null
    const w = mount(HubStatus)
    expect(w.get('.hub-message').attributes('data-severity')).toBe('note')
    expect(w.get('.hub-message').text()).toBe('')
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
