// Component coverage for the "no view available" placeholder and the
// "hub is gone" overlay (rtl-buddy-view#130).
//
// Copy follows the family rebrand: the pane is "the schematic" in
// prose (displayNames.js — this app displays as ``rtl-buddy-sch``),
// while ``view.json``, ``?view=`` and the ``view`` wire origin are
// untouched. Assertions below are deliberately on both halves of that
// split so a re-rebrand can't quietly move the fence.
//
// The acceptance criterion in the issue is about *what the user
// reads*, so these assertions are on rendered copy: the failure card
// must quote the hub's message and the hier.log tail and name the
// three recurring causes; the no-model card must point at the picker;
// the hub-gone card must not read as "another tab took over".

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import NoViewPlaceholder from '../src/components/NoViewPlaceholder.vue'
import HubGoneOverlay from '../src/components/HubGoneOverlay.vue'
import { useViewerStore } from '../src/store.js'
import { _testing } from '../src/composables/useHub.js'

const LOG_TAIL = [
  "error: cannot resolve interface port 'apb' on module apb_intf",
  'rtl-buddy-view exited with code 1',
]

const norm = (s) => s.replace(/\s+/g, ' ').trim()

function failWith(store, meta) {
  store._fail(meta.message || 'failed', meta)
}

describe('NoViewPlaceholder', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    _testing.reset()
  })
  afterEach(() => {
    _testing.reset()
  })

  it('state 1: idle with no hub keeps the drop-a-file screen', () => {
    const wrapper = mount(NoViewPlaceholder)
    expect(wrapper.attributes('data-placeholder')).toBe('idle')
    expect(wrapper.classes()).toContain('empty-state')
    const text = norm(wrapper.text())
    expect(text).toContain('Drop a view.json file here')
    expect(text).toContain('?view=path/to/view.json')
    expect(wrapper.find('input[type="file"]').exists()).toBe(true)
    // No hub seen — must not claim one is reachable.
    expect(text).not.toContain('reachable')
    // The rebrand fence: prose says "schematic", the artefact and the
    // query parameter keep their wire spelling.
    expect(text).toContain('No schematic loaded')
    expect(text).toContain('view.json')
  })

  it('state 1: idle on a hub host says the hub is up, nothing requested', () => {
    const store = useViewerStore()
    store.hubDetected = true
    const wrapper = mount(NoViewPlaceholder)
    expect(wrapper.attributes('data-placeholder')).toBe('idle')
    const text = norm(wrapper.text())
    expect(text).toContain('reachable')
    expect(text).toContain('nothing has asked it for a schematic yet')
    // The offline escape hatch is still offered, just demoted.
    expect(wrapper.find('input[type="file"]').exists()).toBe(true)
  })

  it('state 2: no_active_model points at the picker and rb hub start --model', () => {
    const store = useViewerStore()
    failWith(store, {
      status: 409,
      url: '/view.json',
      kind: 'no_active_model',
      message: 'no model is active on this hub',
      modelsUrl: '/models',
    })
    const wrapper = mount(NoViewPlaceholder)
    expect(wrapper.attributes('data-placeholder')).toBe('no_active_model')
    const text = norm(wrapper.text())
    expect(text).toContain('No model is active')
    expect(text).toContain('The hub is connected')
    expect(text).toContain('model selector')
    expect(text).toContain('rb hub start --serve-viewer --model <model_name>')
  })

  it('state 3: view_generation_failed shows message, log tail, path and causes', () => {
    const store = useViewerStore()
    failWith(store, {
      status: 500,
      url: '/view.json?model=apb_intf',
      kind: 'view_generation_failed',
      model: 'apb_intf',
      message: 'rtl-buddy-view exited with code 1',
      logPath: '/w/artefacts/hier/apb_intf/hier.log',
      logTail: LOG_TAIL,
    })
    const wrapper = mount(NoViewPlaceholder)
    expect(wrapper.attributes('data-placeholder')).toBe('view_generation_failed')
    const text = norm(wrapper.text())
    expect(text).toContain('Could not build the schematic for apb_intf')
    expect(text).toContain('The hub responded HTTP 500')
    expect(text).toContain('rtl-buddy-view exited with code 1')
    expect(text).toContain('/w/artefacts/hier/apb_intf/hier.log')
    // The tail renders verbatim in its own scrollable <pre>.
    const tailPre = wrapper.find('pre.log-tail-pre')
    expect(tailPre.exists()).toBe(true)
    expect(tailPre.text()).toBe(LOG_TAIL.join('\n'))
    // The three recurring causes, verbatim per the issue.
    expect(text).toContain('interface-port tops need frontend: slang')
    expect(text).toContain(
      'vendor submodules not initialized (git submodule update --init)',
    )
    expect(text).toContain('-v library entries in filelists are unsupported')
    expect(wrapper.find('button.retry-button').exists()).toBe(true)
  })

  it('state 3: the retry button re-issues the failed request', async () => {
    const store = useViewerStore()
    failWith(store, {
      status: 500,
      url: '/view.json?model=apb_intf',
      kind: 'view_generation_failed',
      message: 'boom',
      logTail: [],
    })
    const spy = vi.spyOn(store, 'retryLastLoad').mockResolvedValue(false)
    const wrapper = mount(NoViewPlaceholder)
    await wrapper.find('button.retry-button').trigger('click')
    expect(spy).toHaveBeenCalledTimes(1)
    spy.mockRestore()
  })

  it('degradation: a plain-text 500 renders the generic card verbatim', () => {
    const store = useViewerStore()
    const body =
      'rb hub --model apb_intf: rtl-buddy-view exited with code 1; ' +
      'see .../artefacts/hier/apb_intf/hier.log for details.'
    // No ``kind`` — this is what an older hub produces.
    failWith(store, { status: 500, url: '/view.json?model=apb_intf', message: body })
    const wrapper = mount(NoViewPlaceholder)
    expect(wrapper.attributes('data-placeholder')).toBe('generic_error')
    const text = norm(wrapper.text())
    expect(text).toContain('Could not load this schematic')
    expect(text).toContain(body)
    expect(text).toContain('Possible reasons')
    // Retry is still offered — we know which URL failed.
    expect(wrapper.find('button.retry-button').exists()).toBe(true)
  })

  it('unknown_model reads as a stale link, not a build failure', () => {
    const store = useViewerStore()
    failWith(store, {
      status: 404,
      url: '/view.json?model=ghost',
      kind: 'unknown_model',
      model: 'ghost',
      message: "no model named 'ghost'",
    })
    const wrapper = mount(NoViewPlaceholder)
    expect(wrapper.attributes('data-placeholder')).toBe('unknown_model')
    const text = norm(wrapper.text())
    expect(text).toContain('No such model for ghost')
    expect(text).toContain("no model named 'ghost'")
    expect(text).not.toContain('Known causes')
  })
})

describe('HubGoneOverlay (state 4)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    _testing.reset()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    _testing.reset()
  })

  function goLiveThenDie(attempts = _testing.HUB_GONE_ATTEMPTS) {
    _testing.setWsFactory(() => ({ close() {} }))
    _testing.applyEnvelope({
      v: 1,
      id: 'x',
      origin: 'cli',
      kind: 'response',
      type: 'welcome',
      payload: { server_version: '1.0.0-test', registered_clients: ['view'] },
    })
    for (let i = 0; i < attempts; i++) _testing.scheduleReconnect()
  }

  it('stays hidden while the hub has never been up', () => {
    const wrapper = mount(HubGoneOverlay)
    expect(wrapper.find('[data-placeholder="hub_gone"]').exists()).toBe(false)
    // Even a long run of failed first-connect attempts is "no hub
    // here", not "the hub died" — that's the idle placeholder's job.
    for (let i = 0; i < 10; i++) _testing.scheduleReconnect()
    expect(wrapper.find('[data-placeholder="hub_gone"]').exists()).toBe(false)
  })

  it('stays hidden for a single blip after having been ready', async () => {
    goLiveThenDie(1)
    const wrapper = mount(HubGoneOverlay)
    expect(wrapper.find('[data-placeholder="hub_gone"]').exists()).toBe(false)
  })

  it('appears after repeated failed reconnects, with restart guidance', async () => {
    goLiveThenDie()
    const wrapper = mount(HubGoneOverlay)
    const pane = wrapper.find('[data-placeholder="hub_gone"]')
    expect(pane.exists()).toBe(true)
    const text = norm(pane.text())
    expect(text).toContain('The hub is gone')
    expect(text).toContain('rb hub start --serve-viewer')
    expect(text).toContain('reload')
    // Unambiguously NOT the superseded case (rtl-buddy-view#129).
    expect(text).toContain('nothing else took over this tab')
    expect(text.toLowerCase()).not.toContain('another tab')
  })

  it('yields to the superseded banner when a tab really did take over', async () => {
    goLiveThenDie()
    _testing.applyEnvelope({
      v: 1,
      id: 'y',
      origin: 'cli',
      kind: 'error',
      type: 'error',
      payload: { code: 'superseded', message: 'another view registered' },
    })
    const wrapper = mount(HubGoneOverlay)
    expect(wrapper.find('[data-placeholder="hub_gone"]').exists()).toBe(false)
  })

  it('a fresh welcome clears the overlay', async () => {
    goLiveThenDie()
    const wrapper = mount(HubGoneOverlay)
    expect(wrapper.find('[data-placeholder="hub_gone"]').exists()).toBe(true)
    _testing.applyEnvelope({
      v: 1,
      id: 'z',
      origin: 'cli',
      kind: 'response',
      type: 'welcome',
      payload: { server_version: '1.0.0-test', registered_clients: ['view'] },
    })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-placeholder="hub_gone"]').exists()).toBe(false)
  })
})
