// R8 (chrome decluttering), R10 (picker dismissal) and R12
// (actionable empty states) — the parts with a decision in them.

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'

import { readBundleHash, shortServerVersion } from '../src/buildInfo.js'
import {
  AXI_PERF_HINT,
  RENDER_WITH_AXI_PERF_HINT,
  RENDER_WITH_OVERLAYS_HINT,
} from '../src/cliHints.js'
import { useViewerStore } from '../src/store.js'
import { useHub, _testing } from '../src/composables/useHub.js'
import App from '../src/App.vue'
import HubStatus from '../src/components/HubStatus.vue'
import ModelPicker from '../src/components/ModelPicker.vue'
import OverlayPanel from '../src/components/OverlayPanel.vue'
import SelectionCandidates from '../src/components/SelectionCandidates.vue'

function graphPayload(overlaysPresent = []) {
  return {
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
      {
        id: 'top.u_a',
        module: 'a',
        is_blackbox: false,
        parameters: {},
        ports: [],
        overlays: {},
      },
      {
        id: 'top.u_b',
        module: 'a',
        is_blackbox: false,
        parameters: {},
        ports: [],
        overlays: {},
      },
    ],
    edges: [],
    overlays_present: overlaysPresent,
  }
}

describe('build identity (R8b)', () => {
  it('shortens the long rtl-buddy build string to its semver head', () => {
    expect(shortServerVersion('6.24.0.dev12+g1234abc')).toBe('6.24.0.dev12')
    expect(shortServerVersion('6.24.0')).toBe('6.24.0')
    // Anything unparseable comes back whole rather than empty.
    expect(shortServerVersion('main')).toBe('main')
    expect(shortServerVersion(null)).toBe('')
  })

  it('reads the bundle hash off the loaded hashed asset', () => {
    const doc = {
      querySelectorAll: () => [
        { src: 'http://h/assets/vendor.js' },
        { src: 'http://h/assets/index-Ab3-9x.js' },
      ],
    }
    expect(readBundleHash(doc)).toBe('Ab3-9x')
    // Dev server / embedded build: no hashed asset, no row.
    expect(readBundleHash({ querySelectorAll: () => [] })).toBe('')
    expect(readBundleHash(null)).toBe('')
  })

  it('surfaces both in the hub popover, not the top bar', async () => {
    _testing.reset()
    const hub = useHub()
    hub.serverVersion.value = '6.24.0'
    const w = mount(HubStatus)
    await w.get('.hub-status').trigger('click')
    const versions = w.get('.hub-popover .versions')
    expect(versions.text()).toContain('versions')
    expect(versions.text()).toContain('6.24.0')
    _testing.reset()
  })
})

describe('top bar (R8a, R8c, R12)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    _testing.reset()
  })
  afterEach(() => {
    _testing.reset()
  })

  function mountApp(overlaysPresent = []) {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(graphPayload(overlaysPresent)))
    return mount(App, {
      global: {
        stubs: {
          GraphCanvas: true,
          AxiPerfView: true,
          ModelPicker: true,
          DiagnosticsPanel: true,
          OverlayPanel: true,
          NodeDetail: true,
          EdgeDetail: true,
        },
      },
    })
  }

  it('names the top-level tab "Design", not "Hierarchy"', () => {
    // The canvas has its OWN Hierarchy/Flow mode tabs on screen at the
    // same time; two controls a few pixels apart with the same word is
    // the collision this rename removes.
    const labels = mountApp().findAll('.app-tabs button').map((b) => b.text())
    expect(labels).toEqual(['Design', 'AXI Performance'])
  })

  it('keeps the build chips out of the top bar', () => {
    const w = mountApp()
    expect(w.find('.build-chip').exists()).toBe(false)
  })

  it('tells the disabled AXI tab how to get its data', () => {
    const w = mountApp()
    const axi = w.findAll('.app-tabs button')[1]
    expect(axi.attributes('disabled')).toBeDefined()
    expect(axi.attributes('title')).toBe(AXI_PERF_HINT)
    // Docs spelling, verified against docs/axi-perf-overlay.md.
    expect(AXI_PERF_HINT).toContain('--overlay axi-perf=axi-perf.json')
  })

  it('drops the hint once the overlay is present', () => {
    const w = mountApp(['axi-perf'])
    const axi = w.findAll('.app-tabs button')[1]
    expect(axi.attributes('disabled')).toBeUndefined()
    expect(axi.attributes('title')).not.toContain('--overlay')
  })
})

describe('DUT/TB segmented control (R8c)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('says what each segment does', () => {
    const store = useViewerStore()
    store.availableTests = [
      { name: 'smoke', model: 'demo', tb: 'tb_demo', tests_file: '/p/tests.yaml' },
    ]
    const titles = mount(ModelPicker)
      .findAll('.view-mode-toggle button')
      .map((b) => b.attributes('title'))
    expect(titles[0]).toMatch(/DUT subtree/i)
    expect(titles[1]).toMatch(/testbench hierarchy/i)
  })
})

describe('overlays empty state (R12)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('names the command that produces overlays', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(graphPayload([])))
    const text = mount(OverlayPanel).get('.empty-hint').text()
    expect(text).toContain('Produce them with')
    expect(text).toContain(RENDER_WITH_OVERLAYS_HINT)
  })

  it('uses the repo docs spelling, not an invented verb', () => {
    // README.md §"Overlays — `--overlay name=path`" + docs/overlays.md.
    for (const hint of [RENDER_WITH_OVERLAYS_HINT, RENDER_WITH_AXI_PERF_HINT]) {
      expect(hint.startsWith('rtl-buddy-view ')).toBe(true)
      expect(hint).not.toContain('rb view render')
    }
    expect(RENDER_WITH_OVERLAYS_HINT).toContain('--overlay clock=')
    expect(RENDER_WITH_OVERLAYS_HINT).toContain('--overlay reset=')
  })

  it('says nothing extra when there are overlays', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(graphPayload(['clock'])))
    expect(mount(OverlayPanel).find('.empty-hint').exists()).toBe(false)
  })
})

describe('picker dismissal (R10)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    _testing.reset()
  })
  afterEach(() => {
    _testing.reset()
  })

  it('a click outside closes it; a click inside does not', async () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(graphPayload()))
    _testing.setStore({
      dismissSelectionCandidates: () => store.dismissSelectionCandidates(),
      chooseSelectionCandidate: (p) => store.chooseSelectionCandidate(p),
    })
    store.presentSelectionCandidates(['top.u_a', 'top.u_b'])

    const w = mount(SelectionCandidates, { attachTo: document.body })
    expect(w.find('.selection-candidates').exists()).toBe(true)

    // Inside — the popover survives (otherwise picking a candidate
    // would race its own dismissal).
    w.get('.candidate').element.dispatchEvent(
      new MouseEvent('mousedown', { bubbles: true }),
    )
    await w.vm.$nextTick()
    expect(store.selectionCandidates).not.toBeNull()

    // Outside.
    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await w.vm.$nextTick()
    expect(store.selectionCandidates).toBeNull()
    // The applied default is untouched.
    expect(store.selection).toBe('top.u_a')
    w.unmount()
  })

  it('choosing a candidate closes it', async () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(graphPayload()))
    _testing.setStore({
      dismissSelectionCandidates: () => store.dismissSelectionCandidates(),
      chooseSelectionCandidate: (p) => store.chooseSelectionCandidate(p),
    })
    store.presentSelectionCandidates(['top.u_a', 'top.u_b'])
    const w = mount(SelectionCandidates)
    await w.findAll('.candidate')[1].trigger('click')
    expect(store.selection).toBe('top.u_b')
    expect(store.selectionCandidates).toBeNull()
  })

  it('no longer advertises an auto-dismiss in its own hint text', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(graphPayload()))
    store.presentSelectionCandidates(['top.u_a', 'top.u_b'])
    const hint = mount(SelectionCandidates).get('.hint').text()
    expect(hint).not.toMatch(/auto-dismiss/i)
    expect(hint).toContain('Esc')
  })
})
