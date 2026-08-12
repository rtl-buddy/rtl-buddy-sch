// R11 — project-relative source paths.
//
// The panel used to render a four-line wrapped absolute path whose
// leading two thirds were identical for every node in the design.

import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'

import { commonSourceRoot, relativeSourcePath } from '../src/sourcePaths.js'
import { useViewerStore } from '../src/store.js'
import { _testing } from '../src/composables/useHub.js'
import NodeDetail from '../src/components/NodeDetail.vue'

describe('commonSourceRoot', () => {
  it('is the longest shared DIRECTORY prefix', () => {
    expect(
      commonSourceRoot([
        '/home/u/work/proj/rtl/core/alu.sv',
        '/home/u/work/proj/rtl/mem/fifo.sv',
      ]),
    ).toBe('/home/u/work/proj/rtl')
  })

  it('never cuts a path mid-segment', () => {
    // '/a/rtl_x/f.sv' and '/a/rtl_y/g.sv' share the characters
    // '/a/rtl_' — a character-wise prefix would have produced garbage.
    expect(commonSourceRoot(['/a/rtl_x/f.sv', '/a/rtl_y/g.sv'])).toBe('/a')
  })

  it('falls back to the top node file dir when files share only /', () => {
    // A design assembled from two unrelated trees (vendored IP under
    // /opt) has no useful common prefix; the design's own top is the
    // most project-like anchor available.
    expect(
      commonSourceRoot(
        ['/opt/vendor/ip/pll.v', '/home/u/proj/rtl/top.sv'],
        '/home/u/proj/rtl/top.sv',
      ),
    ).toBe('/home/u/proj/rtl')
  })

  it('uses the only file it has when there is just one', () => {
    expect(commonSourceRoot(['/home/u/proj/rtl/top.sv'])).toBe('/home/u/proj/rtl')
  })

  it('is empty when there is nothing to anchor on', () => {
    expect(commonSourceRoot([])).toBe('')
    expect(commonSourceRoot(['top.sv'])).toBe('')
    expect(commonSourceRoot([null, undefined, ''])).toBe('')
  })
})

describe('relativeSourcePath', () => {
  it('strips the root', () => {
    expect(relativeSourcePath('/p/rtl/core/alu.sv', '/p/rtl')).toBe('core/alu.sv')
  })

  it('falls back to the basename for a file outside the root', () => {
    // No ``../../..`` ladders — they are longer than the absolute path
    // they replace and read as an error.
    expect(relativeSourcePath('/opt/ip/pll.v', '/p/rtl')).toBe('pll.v')
    expect(relativeSourcePath('/opt/ip/pll.v', '')).toBe('pll.v')
  })

  it('shrugs off junk', () => {
    expect(relativeSourcePath('', '/p')).toBe('')
    expect(relativeSourcePath(null, '/p')).toBe('')
  })
})

describe('store.sourceRoot', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function load(store, nodes, top = 'top') {
    store.loadFromText(
      JSON.stringify({
        schema_version: '1.0',
        top,
        nodes: nodes.map((n) => ({
          is_blackbox: false,
          parameters: {},
          ports: [],
          overlays: {},
          ...n,
        })),
        edges: [],
        overlays_present: [],
      }),
    )
  }

  it('is derived per-graph, so a model switch re-anchors', () => {
    const store = useViewerStore()
    load(store, [
      { id: 'top', module: 'top', source: { file: '/w/a/rtl/top.sv', start_line: 1 } },
      { id: 'top.u_x', module: 'x', source: { file: '/w/a/rtl/sub/x.sv', start_line: 4 } },
    ])
    expect(store.sourceRoot).toBe('/w/a/rtl')

    load(store, [
      { id: 'top', module: 'top', source: { file: '/other/proj/hdl/top.sv', start_line: 1 } },
      { id: 'top.u_y', module: 'y', source: { file: '/other/proj/hdl/y.sv', start_line: 2 } },
    ])
    expect(store.sourceRoot).toBe('/other/proj/hdl')
  })

  it('is empty for a graph with no source blocks', () => {
    const store = useViewerStore()
    load(store, [{ id: 'top', module: 'top' }])
    expect(store.sourceRoot).toBe('')
  })
})

describe('NodeDetail source row', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    _testing.reset()
  })

  function mountWith(nodes, selection) {
    const store = useViewerStore()
    store.loadFromText(
      JSON.stringify({
        schema_version: '1.0',
        top: 'top',
        nodes: nodes.map((n) => ({
          is_blackbox: false,
          parameters: {},
          ports: [],
          overlays: {},
          ...n,
        })),
        edges: [],
        overlays_present: [],
      }),
    )
    store.select(selection)
    return mount(NodeDetail)
  }

  it('renders the relative path and keeps the absolute one in the title', () => {
    const w = mountWith(
      [
        { id: 'top', module: 'top', source: { file: '/w/proj/rtl/top.sv', start_line: 1 } },
        {
          id: 'top.u_afifo',
          module: 'afifo',
          source: { file: '/w/proj/rtl/fifo/afifo.sv', start_line: 42 },
        },
      ],
      'top.u_afifo',
    )
    const target = w.get('.open-target')
    expect(target.text()).toBe('fifo/afifo.sv:42')
    expect(target.attributes('title')).toBe('/w/proj/rtl/fifo/afifo.sv:42')
  })

  it('degrades to basename:line when there is no usable root', () => {
    const w = mountWith(
      [{ id: 'top', module: 'top', source: { file: 'top.sv', start_line: 7 } }],
      'top',
    )
    expect(w.get('.open-target').text()).toBe('top.sv:7')
  })

  it('offers two copy affordances: instance path and absolute file:line', async () => {
    const copied = []
    // happy-dom exposes ``navigator.clipboard`` as a getter-only
    // property, so it has to be redefined rather than assigned.
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      value: { writeText: async (t) => { copied.push(t) } },
      configurable: true,
    })
    const w = mountWith(
      [
        { id: 'top', module: 'top', source: { file: '/w/proj/rtl/top.sv', start_line: 1 } },
        {
          id: 'top.u_afifo',
          module: 'afifo',
          source: { file: '/w/proj/rtl/fifo/afifo.sv', start_line: 42 },
        },
      ],
      'top.u_afifo',
    )
    const buttons = w.findAll('.copy-btn')
    expect(buttons.length).toBe(2)
    await buttons[0].trigger('click')
    await buttons[1].trigger('click')
    expect(copied).toEqual(['top.u_afifo', '/w/proj/rtl/fifo/afifo.sv:42'])
    // Brief "copied" flash on the affordance that was used last.
    await w.vm.$nextTick()
    expect(w.findAll('.copy-btn')[1].text()).toBe('✓')
  })
})
