// OverlayPanel component tests.
//
// Most of the panel's behaviour (legend entries, swatch styling) is
// already covered by overlays.spec.js at the registry layer. This
// suite covers the component-level rendering that depends on store
// state — specifically the Phase 6d (#99) TB-scope footnote that
// appears under the clock + reset legends when the active view is
// TB-rooted.

import { describe, expect, it, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import OverlayPanel from '../src/components/OverlayPanel.vue'
import { useViewerStore } from '../src/store.js'

function clockPayload(extra = {}) {
  // Minimal payload with a clock overlay populated so the legend
  // renders at least one entry (the TB-scope footnote sits *after*
  // the legend list).
  return {
    schema_version: '1.1',
    top: 'tb_top',
    tb_top: 'tb_top',
    dut_top: 'dut',
    nodes: [
      {
        id: 'tb_top',
        module: 'tb_top',
        is_blackbox: false,
        parameters: {},
        ports: [],
        overlays: {},
      },
      {
        id: 'tb_top.u_dut',
        module: 'dut',
        is_blackbox: false,
        parameters: {},
        ports: [],
        overlays: { clock: { clock: 'clk_a' } },
      },
    ],
    edges: [],
    overlays_present: ['clock'],
    ...extra,
  }
}

function dutPayload(extra = {}) {
  return {
    schema_version: '1.1',
    top: 'dut',
    dut_top: 'dut',
    tb_top: null,
    nodes: [
      {
        id: 'dut',
        module: 'dut',
        is_blackbox: false,
        parameters: {},
        ports: [],
        overlays: { clock: { clock: 'clk_a' } },
      },
    ],
    edges: [],
    overlays_present: ['clock'],
    ...extra,
  }
}

describe('OverlayPanel TB-scope legend note (#99 / 6d)', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('shows "(no clock map above DUT)" under the clock legend in TB view', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(clockPayload()))
    const wrapper = mount(OverlayPanel)
    const text = wrapper.text()
    expect(text).toContain('clock')
    expect(text).toContain('(no clock map above DUT)')
    // The note is rendered as a .legend-note <li> so the swatch
    // styling does not apply to it (italic + indented copy only).
    expect(wrapper.find('.legend-note').exists()).toBe(true)
  })

  it('shows "(no reset map above DUT)" under the reset legend in TB view', () => {
    const store = useViewerStore()
    // Synthesise a reset-only payload by reshaping the clock fixture —
    // the legend gating is per-overlay-name, so we just need the
    // reset overlay's legend to be non-empty.
    const p = clockPayload({
      overlays_present: ['reset'],
      nodes: [
        {
          id: 'tb_top',
          module: 'tb_top',
          is_blackbox: false,
          parameters: {},
          ports: [],
          overlays: {},
        },
        {
          id: 'tb_top.u_dut',
          module: 'dut',
          is_blackbox: false,
          parameters: {},
          ports: [],
          overlays: { reset: { reset: 'rst_n', polarity: 'low' } },
        },
      ],
    })
    store.loadFromText(JSON.stringify(p))
    const wrapper = mount(OverlayPanel)
    expect(wrapper.text()).toContain('(no reset map above DUT)')
  })

  it('hides the TB-scope note in DUT view (today\'s default behaviour stays clean)', () => {
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(dutPayload()))
    const wrapper = mount(OverlayPanel)
    expect(wrapper.find('.legend-note').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('(no clock map above DUT)')
  })

  it('does not attach the note to non-clock/reset overlays', () => {
    // Even in TB view, only the DUT-side overlays get the footnote
    // — an axi-perf / unknown overlay's legend stays unchanged.
    const store = useViewerStore()
    store.loadFromText(
      JSON.stringify(clockPayload({ overlays_present: ['clock', 'axi-perf'] })),
    )
    const wrapper = mount(OverlayPanel)
    // The clock legend gets the note; axi-perf doesn't. Only one
    // legend-note element should be present.
    expect(wrapper.findAll('.legend-note').length).toBe(1)
  })

  it('renders nothing extra when the legend itself is empty', () => {
    // No clock data on any node → the clock overlay's legend
    // returns []. The note must still not render — otherwise users
    // would see a footnote with no swatches above it, which reads
    // as "broken legend" rather than "note about TB scope".
    const store = useViewerStore()
    const p = clockPayload()
    // Strip the only clock-carrying overlay block so legend() returns [].
    p.nodes[1].overlays = {}
    store.loadFromText(JSON.stringify(p))
    const wrapper = mount(OverlayPanel)
    expect(wrapper.find('.legend-note').exists()).toBe(false)
  })

  it('hides the clock footnote in TB view when a tb_clock_map tinted TB scope (#99 / 6e)', () => {
    // The renderer-side merge synthesises FlopDomain entries for TB
    // scopes outside the DUT subtree, which surface here as
    // ``overlays.clock`` on non-DUT nodes. When that happens the
    // "(no clock map above DUT)" footnote is misleading — the user
    // can see the tints exist.
    const p = clockPayload()
    // Drop the in-DUT clock entry, add a TB-scope one (above the DUT).
    p.nodes[1].overlays = {}
    p.nodes.push({
      id: 'tb_top.u_clkgen',
      module: 'clkgen',
      is_blackbox: false,
      parameters: {},
      ports: [],
      overlays: { clock: { clock: 'main_clk' } },
    })
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(p))
    const wrapper = mount(OverlayPanel)
    expect(wrapper.text()).not.toContain('(no clock map above DUT)')
  })

  it('hides the reset footnote in TB view when a tb_clock_map tinted TB scope', () => {
    const p = clockPayload()
    p.overlays_present = ['reset']
    p.nodes[1].overlays = {}
    p.nodes.push({
      id: 'tb_top.u_rstgen',
      module: 'rstgen',
      is_blackbox: false,
      parameters: {},
      ports: [],
      overlays: { reset: { reset: 'por_rst_n', polarity: 'low' } },
    })
    const store = useViewerStore()
    store.loadFromText(JSON.stringify(p))
    const wrapper = mount(OverlayPanel)
    expect(wrapper.text()).not.toContain('(no reset map above DUT)')
  })
})
