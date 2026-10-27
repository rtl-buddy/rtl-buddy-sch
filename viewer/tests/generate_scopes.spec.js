// Generate-scope instance paths (rtl-buddy-sch#209).
//
// An instance inside ``for (genvar t …) begin : g`` is named the way
// slang and simulators name it: ``instance_name`` ``g[0].u_s``, id
// ``top.g[0].u_s``. The generate scope is not a node, so a dot in an
// id no longer always separates two hierarchy levels. These pin that
// such an instance is still a direct child of ``top`` everywhere the
// viewer derives structure from ids, and that it is labelled with its
// full scoped name rather than the bare ``u_s`` both iterations share.

import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'

import { buildBlockFlowDot } from '../src/layout/blockFlow.js'
import { leafName, parentPath } from '../src/instancePath.js'
import { useViewerStore } from '../src/store.js'
import HierarchyTree from '../src/components/HierarchyTree.vue'

function node(id, module, instanceName, ports = []) {
  return {
    id,
    module,
    instance_name: instanceName,
    is_blackbox: false,
    parameters: {},
    ports,
    overlays: {},
  }
}

// The issue's repro, as ``rtl-buddy-view --format json`` emits it.
const REPRO = {
  schema_version: '1.0',
  top: 'top7',
  overlays_present: [],
  nodes: [
    node('top7', 'top7', null, [{ name: 's_clk', dir: 'input', expr: null, anchor: null }]),
    node('top7.g[0].u_s', 'src_m', 'g[0].u_s', [
      { name: 'q', dir: 'output', expr: 'bq[0].v', anchor: null },
    ]),
    node('top7.g[0].u_k', 'sink_m', 'g[0].u_k', [
      { name: 'd', dir: 'input', expr: 'bq[0].v', anchor: null },
    ]),
    node('top7.g[1].u_s', 'src_m', 'g[1].u_s', [
      { name: 'q', dir: 'output', expr: 'bq[1].v', anchor: null },
    ]),
    node('top7.g[1].u_k', 'sink_m', 'g[1].u_k', [
      { name: 'd', dir: 'input', expr: 'bq[1].v', anchor: null },
    ]),
  ],
  edges: [
    { from: 'top7', to: 'top7.g[0].u_s', port_pairs: [], overlays: {} },
    { from: 'top7', to: 'top7.g[0].u_k', port_pairs: [], overlays: {} },
    { from: 'top7', to: 'top7.g[1].u_s', port_pairs: [], overlays: {} },
    { from: 'top7', to: 'top7.g[1].u_k', port_pairs: [], overlays: {} },
  ],
}

describe('instance path helpers', () => {
  it('splits a generate-scoped id at its instance name, not its last dot', () => {
    const n = REPRO.nodes[1]
    expect(parentPath(n)).toBe('top7')
    expect(leafName(n, n.id)).toBe('g[0].u_s')
  })

  it('falls back to the last dot without an instance name', () => {
    expect(parentPath({ id: 'top.u_a.u_b' })).toBe('top.u_a')
    expect(leafName({ id: 'top.u_a.u_b' }, 'top.u_a.u_b')).toBe('u_b')
    expect(parentPath({ id: 'top' })).toBe(null)
    expect(leafName(undefined, 'top')).toBe('top')
  })
})

describe('block flow over generate-scoped children', () => {
  it('draws every iteration as a block of the enclosing scope', () => {
    const dot = buildBlockFlowDot(REPRO, 'top7')
    for (const id of ['top7.g[0].u_s', 'top7.g[0].u_k', 'top7.g[1].u_s', 'top7.g[1].u_k']) {
      expect(dot).toContain(`"${id}"`)
    }
  })

  it('wires the generate children like any other sibling', () => {
    // Block flow only follows plain-identifier nets (an interface
    // member select like ``bq[0].v`` is the ELK schematic's job), so
    // give each iteration its own plain net.
    const graph = structuredClone(REPRO)
    for (const n of graph.nodes) {
      for (const p of n.ports) {
        if (p.expr === 'bq[0].v') p.expr = 'w0'
        if (p.expr === 'bq[1].v') p.expr = 'w1'
      }
    }
    const dot = buildBlockFlowDot(graph, 'top7')
    expect(dot).toMatch(/"top7\.g\[0\]\.u_s":q:e\s*->\s*"top7\.g\[0\]\.u_k":d:w/)
    expect(dot).toMatch(/"top7\.g\[1\]\.u_s":q:e\s*->\s*"top7\.g\[1\]\.u_k":d:w/)
    expect(dot).not.toMatch(/"top7\.g\[0\]\.u_s":q:e\s*->\s*"top7\.g\[1\]/)
  })
})

describe('hierarchy panel over generate-scoped children', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
    store.loadFromText(JSON.stringify(REPRO))
  })

  it('labels a row with its scoped instance name', () => {
    const wrapper = mount(HierarchyTree)
    const row = wrapper.find('.tree-row[data-inst="top7.g[1].u_k"]')
    expect(row.exists()).toBe(true)
    expect(row.find('.tree-inst').text()).toBe('g[1].u_k')
  })

  it('filters on the scoped name, so one iteration can be found', () => {
    const { matches } = store.treeMatch('g[1]')
    expect([...matches].sort()).toEqual(['top7.g[1].u_k', 'top7.g[1].u_s'])
  })
})
