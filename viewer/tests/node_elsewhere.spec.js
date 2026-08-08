// NodeDetail's "elsewhere" row: send the selected node to another hub
// app, or open that app on it.
//
// The contract worth pinning is the ORDER. "open X ↗" works because
// the hub caches the latest focus and replays it to a peer that
// registers later, so the envelope has to be on the wire BEFORE the
// tab exists. If the emit fails there is nothing to replay, and a tab
// opened anyway would come up on whatever the pane defaults to while
// looking like the click worked — so it must not open at all.

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
  { id: 'top.u_afifo', module: 'afifo' },
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
      'open graph ↗',
      'open coverage ↗',
    ])
  })

  it('omits an "open ↗" for an app the hub has no data for', () => {
    // Same data-presence gate the switcher uses — opening ``/cov`` on a
    // hub with no coverage is a 404, so the button is not offered.
    serve({ cov: null })
    loadAndSelect(store)
    const w = mount(NodeDetail)
    const labels = w
      .findAll('[data-testid="node-elsewhere"] button')
      .map((b) => b.text())
    expect(labels).toEqual(['send → graph', 'send → coverage', 'open graph ↗'])
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
    expect(sendCov.attributes('title')).toBe('coverage is not connected — use open ↗')
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

  it('"open graph ↗" emits the focus BEFORE opening the tab', async () => {
    serve()
    loadAndSelect(store)
    const sock = connect(store, ['view'])
    let sentWhenOpened = -1
    window.open = (...args) => {
      sentWhenOpened = sock.sent.length
      opened.push(args)
    }
    const w = mount(NodeDetail)
    await w.get('.open-graph').trigger('click')
    expect(JSON.parse(sock.sent[0]).type).toBe('graph_focus')
    expect(sentWhenOpened).toBe(1)
    expect(opened).toEqual([['/graph', '_blank', 'noopener']])
  })

  it('"open coverage ↗" does the same for the coverage pane', async () => {
    serve()
    loadAndSelect(store)
    const sock = connect(store, ['view'])
    const w = mount(NodeDetail)
    await w.get('.open-cov').trigger('click')
    expect(JSON.parse(sock.sent[0]).type).toBe('cov_focus')
    expect(opened).toEqual([['/cov', '_blank', 'noopener']])
  })

  it('opens no tab when the focus could not be sent, and says why', async () => {
    serve()
    loadAndSelect(store)
    // No connect(): the hub is not there, so the emit fails and the
    // tab would land unfocused.
    const w = mount(NodeDetail)
    await w.get('.open-graph').trigger('click')
    expect(opened).toEqual([])
    expect(store.hubError).toMatchObject({ code: 'not_connected' })
    expect(store.hubError.message).toContain('graph')
  })

  it('warns that opening supersedes an app that is already connected', () => {
    serve()
    loadAndSelect(store)
    connect(store, ['view', 'graph'])
    const w = mount(NodeDetail)
    expect(w.get('.open-graph').attributes('title')).toContain(
      "replaces the currently open graph tab's hub connection",
    )
    // Nothing connected on the coverage side — no such warning.
    expect(w.get('.open-cov').attributes('title')).not.toContain('replaces')
  })
})
