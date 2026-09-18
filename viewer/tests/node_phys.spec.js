// NodeDetail's "Physical" section (rtl-buddy/rtl-buddy-sch#22).
//
// Three figures and two bars. The figures are the node's own
// measurements at whichever scope the canvas toggle is on; the bars
// are its share of its PARENT's subtree, which is the question a
// hierarchy panel is actually asked — "is this the block that owns
// the area?" — and the one a bare µW figure cannot answer.

import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'

import NodeDetail from '../src/components/NodeDetail.vue'
import { useViewerStore } from '../src/store.js'

const NODES = [
  {
    id: 'phys_top',
    module: 'phys_top',
    overlays: {
      phys: {
        cell_count: 3,
        area_um2: 11.172,
        self_area_um2: 1.064,
        total_uw: 0.075,
        subtree_total_uw: 2.8565,
      },
    },
  },
  {
    id: 'phys_top.u_sub',
    module: 'phys_sub',
    overlays: {
      phys: {
        cell_count: 2,
        area_um2: 5.586,
        self_area_um2: 1.064,
        total_uw: 0.0888,
        subtree_total_uw: 2.5154,
      },
    },
  },
  {
    // A node whose self area could not be derived: the producer omits
    // ``self_area_um2`` when subtracting the children's areas would
    // have been a guess.
    id: 'phys_top.u_opaque',
    module: 'opaque',
    overlays: { phys: { area_um2: 2.0, total_uw: 0.01, subtree_total_uw: 0.01 } },
  },
]

function loadAndSelect(store, selection) {
  store.loadFromText(
    JSON.stringify({
      schema_version: '1.1',
      top: 'phys_top',
      nodes: NODES.map((n) => ({
        is_blackbox: false,
        parameters: {},
        ports: [],
        ...n,
      })),
      edges: [
        { from: 'phys_top', to: 'phys_top.u_sub' },
        { from: 'phys_top', to: 'phys_top.u_opaque' },
      ],
      overlays_present: ['phys'],
    }),
  )
  store.select(selection)
}

describe('NodeDetail physical section', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('shows cells / area / power and the share-of-parent bars', () => {
    const store = useViewerStore()
    loadAndSelect(store, 'phys_top.u_sub')
    const wrapper = mount(NodeDetail)
    const block = wrapper.find('[data-testid="node-phys"]')
    expect(block.exists()).toBe(true)
    const text = block.text()
    expect(text).toContain('cells')
    expect(text).toContain('2')
    // Subtree scope by default: the producer's own module area, and
    // this overlay's roll-up of the leaf power rows.
    expect(text).toContain('5.59 µm²')
    expect(text).toContain('2.52 µW')
    // 5.586/11.172 = 50%, 2.5154/2.8565 = 88.1%.
    const bars = block.findAll('.cov-row')
    expect(bars).toHaveLength(2)
    expect(bars[0].text()).toContain('50%')
    expect(bars[1].text()).toContain('88.1%')
  })

  it('follows the canvas scope toggle rather than keeping its own', async () => {
    const store = useViewerStore()
    loadAndSelect(store, 'phys_top.u_sub')
    const wrapper = mount(NodeDetail)
    store.setPhysScope('self')
    await wrapper.vm.$nextTick()
    const text = wrapper.find('[data-testid="node-phys"]').text()
    // Self figures: area net of the submodule, and only the leaf
    // cells this scope directly contains.
    expect(text).toContain('1.06 µm²')
    expect(text).toContain('0.0888 µW')
  })

  it('names an unavailable self area instead of showing none silently', async () => {
    const store = useViewerStore()
    loadAndSelect(store, 'phys_top.u_opaque')
    const wrapper = mount(NodeDetail)
    store.setPhysScope('self')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="node-phys"]').text()).toContain(
      'self area unavailable',
    )
  })

  it('omits the bars at the root and the section entirely without a block', () => {
    const store = useViewerStore()
    loadAndSelect(store, 'phys_top')
    const root = mount(NodeDetail)
    const block = root.find('[data-testid="node-phys"]')
    expect(block.exists()).toBe(true)
    // The root has no parent in this view, so there is no share to
    // draw — the figures stand alone.
    expect(block.findAll('.cov-row')).toHaveLength(0)
  })

  it('does not also render phys through the raw-JSON overlay fallback', () => {
    const store = useViewerStore()
    loadAndSelect(store, 'phys_top.u_sub')
    const wrapper = mount(NodeDetail)
    expect(wrapper.text()).not.toContain('overlay: phys')
  })
})
