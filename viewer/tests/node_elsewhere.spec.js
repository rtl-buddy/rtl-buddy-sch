// NodeDetail's "elsewhere" row: send the selected node to another hub
// app, or open that app on it.
//
// The row is sends-only: opening an app fresh is the top bar's job
// (the switcher links), so there are no open-↗ variants — and the
// editor action lives here too, as "send → editor".

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'

import NodeDetail from '../src/components/NodeDetail.vue'
import { useViewerStore } from '../src/store.js'
import { initHub, _testing } from '../src/composables/useHub.js'

class MockSocket {
  constructor() {
    this.readyState = 0
    this.sent = []
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
  }
  open() {
    this.readyState = 1
    if (this.onopen) this.onopen({})
  }
  receive(env) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(env) })
  }
  send(text) { this.sent.push(text) }
  close() { this.readyState = 3 }
}

const NODES = [
  { id: 'top', module: 'top' },
  {
    id: 'top.u_afifo',
    module: 'afifo',
    source: { file: '/proj/design/afifo.sv', start_line: 12, start_column: 1 },
  },
]

function loadAndSelect(store, selection = 'top.u_afifo') {
  store.loadFromText(
    JSON.stringify({
      schema_version: '1.0',
      top: 'top',
      nodes: NODES.map((n) => ({
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
}

/** Inject the hub-served globals the switcher and this row gate on. */
function serve({ hub = '127.0.0.1:8399', graph = '/graph.json', cov = '/cov.json' } = {}) {
  if (hub === null) delete window.__RTL_BUDDY_HUB__
  else window.__RTL_BUDDY_HUB__ = hub
  if (graph === null) delete window.__RTL_BUDDY_GRAPH_URL__
  else window.__RTL_BUDDY_GRAPH_URL__ = graph
  if (cov === null) delete window.__RTL_BUDDY_COV_URL__
  else window.__RTL_BUDDY_COV_URL__ = cov
}

/** Open socket + welcome, so emits land and ``peers`` is populated. */
function connect(store, peers = ['view', 'graph', 'cov']) {
  const sock = new MockSocket()
  _testing.setWsFactory(() => sock)
  initHub({ store })
  sock.open()
  sock.receive({
    v: 1,
    id: 'w',
    origin: 'cli',
    kind: 'response',
    type: 'welcome',
    payload: { server_version: '1.0', registered_clients: peers },
  })
  sock.sent.length = 0 // drop the hello
  return sock
}

describe('NodeDetail "elsewhere" row', () => {
  let store
  let realOpen
  let opened

  beforeEach(() => {
    setActivePinia(createPinia())
    _testing.reset()
    store = useViewerStore()
    _testing.setStore({
      applyHubSelection: (id) => store.applyHubSelection(id),
      applyHubError: (e) => store.applyHubError(e),
    })
    opened = []
    realOpen = window.open
    window.open = (...args) => { opened.push(args) }
  })

  afterEach(() => {
    window.open = realOpen
    serve({ hub: null, graph: null, cov: null })
    _testing.reset()
  })

  it('is hidden when no hub is serving the bundle', () => {
    serve({ hub: null })
    loadAndSelect(store)
    const w = mount(NodeDetail)
    expect(w.find('[data-testid="node-elsewhere"]').exists()).toBe(false)
  })

  it('is hidden for a node with no module — every control needs one', () => {
    serve()
    store.loadFromText(
      JSON.stringify({
        schema_version: '1.0',
        top: 'top',
        nodes: [{ id: 'top', module: '', is_blackbox: false, parameters: {}, ports: [], overlays: {} }],
        edges: [],
        overlays_present: [],
      }),
    )
    store.select('top')
    const w = mount(NodeDetail)
    expect(w.find('[data-testid="node-elsewhere"]').exists()).toBe(false)
  })

  it('offers send + open for both sibling apps when hub-served', () => {
    serve()
    loadAndSelect(store)
    const w = mount(NodeDetail)
    const labels = w
      .findAll('[data-testid="node-elsewhere"] button')
      .map((b) => b.text())
    expect(labels).toEqual([
      'send → graph',
      'send → coverage',
      'send → editor',
    ])
  })

  it('disables "send" for an app that is not a connected peer', () => {
    serve()
    loadAndSelect(store)
    connect(store, ['view', 'graph'])
    const w = mount(NodeDetail)
    const sendGraph = w.get('.send-graph')
    const sendCov = w.get('.send-cov')
    expect(sendGraph.attributes('disabled')).toBeUndefined()
    expect(sendCov.attributes('disabled')).toBeDefined()
    expect(sendCov.attributes('title')).toBe(
      'coverage is not connected — open it from the top bar',
    )
    // …and says focus is a broadcast, because it is.
    expect(sendGraph.attributes('title')).toContain('module:afifo')
    expect(sendGraph.attributes('title')).toContain('broadcast')
  })

  it('disables both "send" buttons while the hub is offline', () => {
    serve()
    loadAndSelect(store)
    const w = mount(NodeDetail)
    expect(w.get('.send-graph').attributes('disabled')).toBeDefined()
    expect(w.get('.send-cov').attributes('disabled')).toBeDefined()
  })

  it('"send → graph" emits graph_focus for the selected node\'s module', async () => {
    serve()
    loadAndSelect(store)
    const sock = connect(store)
    const w = mount(NodeDetail)
    await w.get('.send-graph').trigger('click')
    expect(sock.sent.length).toBe(1)
    const sent = JSON.parse(sock.sent[0])
    expect(sent.type).toBe('graph_focus')
    expect(sent.origin).toBe('view')
    expect(sent.payload).toStrictEqual({ node: 'module:afifo' })
    // No navigation: "send" is for a tab that is already open.
    expect(opened).toEqual([])
  })

  it('"send → coverage" emits cov_focus for the same module', async () => {
    serve()
    loadAndSelect(store)
    const sock = connect(store)
    const w = mount(NodeDetail)
    await w.get('.send-cov').trigger('click')
    const sent = JSON.parse(sock.sent[0])
    expect(sent.type).toBe('cov_focus')
    expect(sent.payload).toStrictEqual({ target: 'module:afifo' })
    expect(opened).toEqual([])
  })

  it('"send → editor" appears only for nodes with a source anchor', () => {
    serve()
    store.loadFromText(
      JSON.stringify({
        schema_version: '1.0',
        top: 'top',
        nodes: [
          { id: 'top', module: 'top', is_blackbox: false, parameters: {}, ports: [], overlays: {} },
        ],
        edges: [],
        overlays_present: [],
      }),
    )
    store.select('top')
    const w = mount(NodeDetail)
    expect(w.find('.send-editor').exists()).toBe(false)
    // …and for a node WITH one, it is there (main fixture).
    loadAndSelect(store)
    const w2 = mount(NodeDetail)
    expect(w2.find('.send-editor').exists()).toBe(true)
  })

  it('"send → editor" requests open_source — no tab, no navigation', async () => {
    serve()
    loadAndSelect(store)
    const sock = connect(store)
    const w = mount(NodeDetail)
    await w.get('.send-editor').trigger('click')
    const sent = JSON.parse(sock.sent[0])
    expect(sent.type).toBe('open_source')
    expect(sent.kind).toBe('request')
    expect(sent.payload.file).toBe('/proj/design/afifo.sv')
    expect(opened).toEqual([])
  })

  it('module-less nodes with a source still show the row (editor only)', () => {
    serve()
    store.loadFromText(
      JSON.stringify({
        schema_version: '1.0',
        top: 'top',
        nodes: [
          {
            id: 'top', module: '', is_blackbox: false, parameters: {}, ports: [], overlays: {},
            source: { file: '/proj/top.sv', start_line: 1, start_column: 1 },
          },
        ],
        edges: [],
        overlays_present: [],
      }),
    )
    store.select('top')
    const w = mount(NodeDetail)
    const labels = w
      .findAll('[data-testid="node-elsewhere"] button')
      .map((b) => b.text())
    expect(labels).toEqual(['send → editor'])
  })
})
