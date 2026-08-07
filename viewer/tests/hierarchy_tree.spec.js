// The instance-hierarchy panel: the store-side tree derivation (which
// is pure and gets tested as such) and the component's three pieces of
// view state — expansion, filter, keyboard cursor.

import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'

import { useViewerStore } from '../src/store.js'
import { useHub } from '../src/composables/useHub.js'
import HierarchyTree from '../src/components/HierarchyTree.vue'

function graph(nodes, top = 'top') {
  return JSON.stringify({
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
  })
}

// Mirrors the e2e fixture (two_clock_two_reset_design): three levels,
// six instances, two of which answer to "sync".
const DESIGN = [
  { id: 'top', module: 'top' },
  { id: 'top.u_fifo', module: 'fifo' },
  { id: 'top.u_fifo.u_rd_ptr', module: 'ff' },
  { id: 'top.u_fifo.u_wr_ptr', module: 'ff' },
  { id: 'top.u_rstgen', module: 'rstsync' },
  { id: 'top.u_rstgen.u_sync', module: 'ff' },
]

describe('tree derivation getters', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
  })

  it('splits ids into a parent map, a child map and roots', () => {
    store.loadFromText(graph(DESIGN))
    expect(store.treeRoots).toEqual(['top'])
    expect(store.treeParentOf.get('top')).toBeNull()
    expect(store.treeParentOf.get('top.u_fifo')).toBe('top')
    expect(store.treeParentOf.get('top.u_fifo.u_rd_ptr')).toBe('top.u_fifo')
    expect(store.treeChildren.get('top')).toEqual(['top.u_fifo', 'top.u_rstgen'])
    expect(store.treeChildren.get('top.u_fifo')).toEqual([
      'top.u_fifo.u_rd_ptr',
      'top.u_fifo.u_wr_ptr',
    ])
    // Leaves are absent from the child map entirely.
    expect(store.treeChildren.has('top.u_rstgen.u_sync')).toBe(false)
  })

  it('re-attaches an orphan to its nearest existing ancestor', () => {
    // ``top.u_mid`` is missing from the node set: its child must not
    // vanish and must not be promoted to a root — it hangs off ``top``.
    store.loadFromText(
      graph([
        { id: 'top', module: 'top' },
        { id: 'top.u_mid.u_deep', module: 'deep' },
      ]),
    )
    expect(store.treeRoots).toEqual(['top'])
    expect(store.treeParentOf.get('top.u_mid.u_deep')).toBe('top')
    expect(store.treeChildren.get('top')).toEqual(['top.u_mid.u_deep'])
  })

  it('makes an id with no existing ancestor an extra root', () => {
    store.loadFromText(
      graph([
        { id: 'top', module: 'top' },
        { id: 'orphan.u_a', module: 'a' },
      ]),
    )
    expect(store.treeRoots).toEqual(['top', 'orphan.u_a'])
    expect(store.treeParentOf.get('orphan.u_a')).toBeNull()
  })

  it('is empty before a graph is loaded', () => {
    expect(store.treeRoots).toEqual([])
    expect(store.treeChildren.size).toBe(0)
    expect(store.treeMatch('x').matches.size).toBe(0)
  })

  it('matches instance name OR module name, case-insensitively', () => {
    store.loadFromText(graph(DESIGN))
    // ``top.u_rstgen`` matches on its MODULE (rstsync), the leaf on its
    // instance name.
    const { matches, visible } = store.treeMatch('SYNC')
    expect([...matches].sort()).toEqual(['top.u_rstgen', 'top.u_rstgen.u_sync'])
    // Ancestors ride along so the matches are shown as a tree.
    expect([...visible].sort()).toEqual([
      'top',
      'top.u_rstgen',
      'top.u_rstgen.u_sync',
    ])
    expect(store.treeMatch('ff').matches.size).toBe(3)
    // An empty / whitespace query matches nothing (the panel reads that
    // as "not filtering" and shows everything).
    expect(store.treeMatch('   ').matches.size).toBe(0)
  })
})

describe('HierarchyTree', () => {
  let store
  let wrapper

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
    store.loadFromText(graph(DESIGN))
    wrapper = mount(HierarchyTree)
  })

  const rowIds = () =>
    wrapper.findAll('.tree-row').map((r) => r.attributes('data-inst'))
  const row = (id) => wrapper.find(`.tree-row[data-inst="${id}"]`)
  const tree = () => wrapper.find('[role="tree"]')

  it('opens the top one level and expands on the caret', async () => {
    expect(rowIds()).toEqual(['top', 'top.u_fifo', 'top.u_rstgen'])
    await row('top.u_fifo').find('.tree-caret').trigger('click')
    expect(rowIds()).toEqual([
      'top',
      'top.u_fifo',
      'top.u_fifo.u_rd_ptr',
      'top.u_fifo.u_wr_ptr',
      'top.u_rstgen',
    ])
    // …and collapses again.
    await row('top.u_fifo').find('.tree-caret').trigger('click')
    expect(rowIds()).toEqual(['top', 'top.u_fifo', 'top.u_rstgen'])
  })

  it('renders instance name, module name and the scope marker', () => {
    expect(row('top.u_fifo').find('.tree-inst').text()).toBe('u_fifo')
    expect(row('top.u_fifo').find('.tree-module').text()).toBe('fifo')
    expect(wrapper.find('.tree-scope-mark').exists()).toBe(false)
    store.descend('top.u_fifo')
    return wrapper.vm.$nextTick().then(() => {
      expect(row('top.u_fifo').classes()).toContain('is-scope')
      expect(row('top.u_fifo').find('.tree-scope-mark').exists()).toBe(true)
    })
  })

  it('a row click selects through the canvas path (store + hub)', async () => {
    const hub = useHub()
    await row('top.u_rstgen').trigger('click')
    expect(store.selection).toBe('top.u_rstgen')
    // ``notifyClick`` is what emits ``selection_changed``; offline it
    // still records the click, which is how we see the path was taken.
    expect(hub.lastClick.value?.id).toBe('top.u_rstgen')
    expect(row('top.u_rstgen').classes()).toContain('is-selected')
  })

  it('a row double-click descends, and is inert on a leaf', async () => {
    await row('top.u_fifo').trigger('dblclick')
    expect(store.rootInstancePath).toBe('top.u_fifo')
    await row('top.u_fifo.u_rd_ptr').trigger('dblclick')
    expect(store.rootInstancePath).toBe('top.u_fifo')
  })

  it('follows a selection from any source: expands ancestors and parks the cursor', async () => {
    // Nothing has expanded ``top.u_fifo`` — a hub-driven selection of a
    // node underneath it has to open the way down to it.
    store.applyHubSelection('top.u_fifo.u_wr_ptr')
    await wrapper.vm.$nextTick()
    expect(rowIds()).toContain('top.u_fifo.u_wr_ptr')
    expect(row('top.u_fifo.u_wr_ptr').classes()).toContain('is-cursor')
  })

  it('resets expansion and filter on a model switch', async () => {
    await row('top.u_fifo').find('.tree-caret').trigger('click')
    await wrapper.find('.tree-filter-input').setValue('sync')
    store.loadFromText(graph(DESIGN))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.tree-filter-input').element.value).toBe('')
    expect(rowIds()).toEqual(['top', 'top.u_fifo', 'top.u_rstgen'])
  })

  describe('filter', () => {
    it('shows matches plus dimmed ancestors, auto-expanded, with a count', async () => {
      await wrapper.find('.tree-filter-input').setValue('sync')
      expect(rowIds()).toEqual(['top', 'top.u_rstgen', 'top.u_rstgen.u_sync'])
      expect(row('top').classes()).toContain('is-dimmed')
      expect(row('top.u_rstgen').classes()).not.toContain('is-dimmed')
      expect(wrapper.find('.tree-count').text().replace(/\s+/g, ' ')).toBe('2 / 6')
    })

    it('says so when nothing matches', async () => {
      await wrapper.find('.tree-filter-input').setValue('zzz')
      expect(rowIds()).toEqual([])
      expect(wrapper.find('.tree-empty').exists()).toBe(true)
    })

    it('Enter selects the first match, not the first row', async () => {
      const input = wrapper.find('.tree-filter-input')
      await input.setValue('sync')
      await input.trigger('keydown', { key: 'Enter' })
      // ``top`` is row 0 but only an ancestor — the first MATCH wins.
      expect(store.selection).toBe('top.u_rstgen')
    })

    it('Esc clears the filter before it falls through anywhere else', async () => {
      const input = wrapper.find('.tree-filter-input')
      await input.setValue('sync')
      store.select('top.u_fifo')
      await input.trigger('keydown', { key: 'Escape' })
      expect(wrapper.find('.tree-filter-input').element.value).toBe('')
      // The selection is untouched: the key stopped at the filter.
      expect(store.selection).toBe('top.u_fifo')
      expect(rowIds()).toEqual(['top', 'top.u_fifo', 'top.u_rstgen'])
    })
  })

  describe('keyboard cursor', () => {
    const cursor = () =>
      wrapper.find('.tree-row.is-cursor').attributes('data-inst')

    it('ArrowDown/ArrowUp walk the VISIBLE rows', async () => {
      await tree().trigger('keydown', { key: 'ArrowDown' })
      expect(cursor()).toBe('top')
      await tree().trigger('keydown', { key: 'ArrowDown' })
      expect(cursor()).toBe('top.u_fifo')
      // ``top.u_fifo``'s children are collapsed, so Down skips them.
      await tree().trigger('keydown', { key: 'ArrowDown' })
      expect(cursor()).toBe('top.u_rstgen')
      // …and stops at the end rather than wrapping.
      await tree().trigger('keydown', { key: 'ArrowDown' })
      expect(cursor()).toBe('top.u_rstgen')
      await tree().trigger('keydown', { key: 'ArrowUp' })
      expect(cursor()).toBe('top.u_fifo')
    })

    it('ArrowRight expands then steps in; ArrowLeft collapses then steps out', async () => {
      await tree().trigger('keydown', { key: 'ArrowDown' }) // top
      await tree().trigger('keydown', { key: 'ArrowDown' }) // top.u_fifo
      await tree().trigger('keydown', { key: 'ArrowRight' })
      expect(cursor()).toBe('top.u_fifo')
      expect(rowIds()).toContain('top.u_fifo.u_rd_ptr')
      await tree().trigger('keydown', { key: 'ArrowRight' })
      expect(cursor()).toBe('top.u_fifo.u_rd_ptr')
      // On a leaf, Left goes to the parent…
      await tree().trigger('keydown', { key: 'ArrowLeft' })
      expect(cursor()).toBe('top.u_fifo')
      // …and on an open node it closes it.
      await tree().trigger('keydown', { key: 'ArrowLeft' })
      expect(rowIds()).not.toContain('top.u_fifo.u_rd_ptr')
      expect(cursor()).toBe('top.u_fifo')
    })

    it('Enter selects the row under the cursor', async () => {
      await tree().trigger('keydown', { key: 'ArrowDown' })
      await tree().trigger('keydown', { key: 'ArrowDown' })
      await tree().trigger('keydown', { key: 'Enter' })
      expect(store.selection).toBe('top.u_fifo')
      expect(tree().attributes('aria-activedescendant')).toBe('rb-tree-top_u_fifo')
    })

    it('exposes tree/treeitem roles with levels', () => {
      expect(tree().attributes('role')).toBe('tree')
      expect(row('top').attributes('aria-level')).toBe('1')
      expect(row('top.u_fifo').attributes('aria-level')).toBe('2')
      expect(row('top.u_fifo').attributes('aria-expanded')).toBe('false')
      expect(row('top').attributes('aria-expanded')).toBe('true')
    })
  })
})
