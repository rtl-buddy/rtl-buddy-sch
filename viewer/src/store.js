// Pinia store — single source of truth for the viewer.
//
// State shape:
//   - graph: the parsed view.json v1 payload (or null while loading)
//   - status: 'idle' | 'loading' | 'ready' | 'error'
//   - error: human-readable message when status === 'error'
//   - selection: the currently-selected node id (or null)
//   - enabledOverlays: Set<string> of overlay names the user has
//     toggled on. Defaults to all overlays in `graph.overlays_present`.
//   - hubCursorTimeFs: latest cursor_time_changed payload from the hub.
//   - hubScope: latest scope_changed payload from the hub.
//   - diagnosticsBySource: { [source]: items[] } keyed by producer.
//     Latest-writer-wins per source; empty items clears the source.
//   - hubError / hubErrorDismissedAt: surfaces hub `error` envelopes.
//
// Actions:
//   - bootstrap(): kick off the initial load (URL query, inlined
//     data, drag-drop, or file picker — the loader handles
//     precedence).
//   - loadFromUrl(url) / loadFromFile(file) / loadFromText(text):
//     three explicit entry points for the same parse+validate path.
//   - select(id) / clearSelection()
//   - toggleOverlay(name)
//   - applyHubCursorTime / applyHubSelection / applyHubScope /
//     applyDiagnostics / applyHubError — invoked by useHub on inbound
//     events; centralising them keeps the composable thin and the
//     store the only writer.
//
import { defineStore } from 'pinia'
import { parseViewJson } from './parse.js'

export const useViewerStore = defineStore('viewer', {
  state: () => ({
    graph: null,
    status: 'idle',
    error: null,
    selection: null,
    // Currently-selected edge as ``{from, to}`` or null. Mutually
    // exclusive with ``selection`` so the sidebar shows one detail
    // panel at a time — selecting an edge clears the node, and
    // vice versa.
    selectedEdge: null,
    enabledOverlays: new Set(),
    // When set, the renderer shows only the subtree rooted at this
    // instance path. ``null`` means show the full hierarchy from
    // ``graph.top``. Descend / ascend actions mutate this.
    rootInstancePath: null,
    // Canvas view mode. ``hier`` = nested-cluster tree from the
    // producer's embedded layout. ``flow`` = SPA-derived one-level
    // block-diagram view with sibling-to-sibling connectivity
    // inferred from per-node port expressions.
    viewMode: 'hier',
    // Hub-mirrored state. All written by applyHub*/applyDiagnostics
    // actions; consumers read these directly.
    hubCursorTimeFs: null,
    hubScope: null,
    diagnosticsBySource: {},
    hubError: null,
  }),
  getters: {
    nodesById: (state) => {
      if (!state.graph) return new Map()
      const m = new Map()
      for (const n of state.graph.nodes) m.set(n.id, n)
      return m
    },
    // The graph the canvas actually renders. When rootInstancePath
    // is null, this is the full graph; otherwise it's the subtree
    // rooted at that node.
    displayGraph(state) {
      if (!state.graph) return null
      if (!state.rootInstancePath) return state.graph
      const rootId = state.rootInstancePath
      const prefix = rootId + '.'
      const nodes = state.graph.nodes.filter(
        (n) => n.id === rootId || n.id.startsWith(prefix),
      )
      const ids = new Set(nodes.map((n) => n.id))
      const edges = state.graph.edges.filter(
        (e) => ids.has(e.from) && ids.has(e.to),
      )
      return { ...state.graph, top: rootId, nodes, edges }
    },
    selectedNode(state) {
      if (!state.selection) return null
      return this.nodesById.get(state.selection) || null
    },
    selectedEdgeObj(state) {
      if (!state.selectedEdge || !state.graph) return null
      const { from, to } = state.selectedEdge
      return (
        state.graph.edges.find((e) => e.from === from && e.to === to) || null
      )
    },
    // Scope shown in the block-flow view: the currently-selected
    // instance when there is one, otherwise the design top. The
    // flow renderer reads this to decide *which* instance to expand
    // (showing its direct children + their interconnections).
    flowScopeId(state) {
      if (state.selection) return state.selection
      return state.graph ? state.graph.top : null
    },
    // True when the selected node has at least one child in the
    // current full graph — i.e. descending into it would show
    // something more than the node itself.
    selectedHasChildren(state) {
      if (!state.selection || !state.graph) return false
      const prefix = state.selection + '.'
      for (const n of state.graph.nodes) {
        if (n.id.startsWith(prefix)) return true
      }
      return false
    },
    overlaysPresent: (state) =>
      state.graph ? state.graph.overlays_present : [],
    diagnosticsForNode: (state) => (nodeId) => {
      // Flatten diagnostics across all sources that reference this
      // node. The wire uses absolute paths (file:line); for the
      // viewer we key on the rtl-buddy-cdc-style optional
      // `instance_path` field embedded in the item if present. The
      // hub doesn't enforce that yet, so this getter returns an
      // empty list when the producer is path-only.
      if (!nodeId) return []
      const out = []
      for (const [source, items] of Object.entries(state.diagnosticsBySource)) {
        for (const item of items) {
          if (item.instance_path === nodeId) {
            out.push({ source, ...item })
          }
        }
      }
      return out
    },
    diagnosticsFlat: (state) => {
      const out = []
      for (const [source, items] of Object.entries(state.diagnosticsBySource)) {
        for (const item of items) out.push({ source, ...item })
      }
      return out
    },
  },
  actions: {
    async bootstrap() {
      // Priority order:
      //   1. ``?view=`` URL query — explicit caller intent.
      //   2. ``window.__RTL_BUDDY_VIEW_URL__`` — hub injection. Set
      //      by rtl-buddy-hub's index.html renderer when it has a
      //      view.json configured (see rtl_buddy hub/viewer_http.py).
      //      Visiting ``http://hub:port/`` with no query string then
      //      auto-loads the design instead of dropping the user on
      //      the empty drag-drop screen.
      //   3. ``window.__RTL_BUDDY_VIEW_DATA__`` — embed.py inject
      //      (single-file standalone HTML build).
      //   4. Stay idle and wait for drag-drop / file picker.
      const params = new URLSearchParams(window.location.search)
      const viewUrl = params.get('view')
      if (viewUrl) {
        await this.loadFromUrl(viewUrl)
        return
      }
      if (typeof window !== 'undefined' && window.__RTL_BUDDY_VIEW_URL__) {
        await this.loadFromUrl(window.__RTL_BUDDY_VIEW_URL__)
        return
      }
      if (window.__RTL_BUDDY_VIEW_DATA__) {
        try {
          this._installGraph(window.__RTL_BUDDY_VIEW_DATA__)
        } catch (e) {
          this._fail(`Embedded payload: ${e.message}`)
        }
      }
    },
    async loadFromUrl(url) {
      this.status = 'loading'
      this.error = null
      try {
        const response = await fetch(url)
        if (!response.ok) {
          throw new Error(`fetch failed: ${response.status} ${response.statusText}`)
        }
        const text = await response.text()
        this.loadFromText(text, url)
      } catch (e) {
        this._fail(`Could not load ${url}: ${e.message}`)
      }
    },
    async loadFromFile(file) {
      this.status = 'loading'
      this.error = null
      try {
        const text = await file.text()
        this.loadFromText(text, file.name)
      } catch (e) {
        this._fail(`Could not read ${file.name}: ${e.message}`)
      }
    },
    loadFromText(text, source = '<input>') {
      try {
        const payload = JSON.parse(text)
        this._installGraph(payload)
      } catch (e) {
        // JSON.parse and parseViewJson both throw — surface either
        // with the source label so the toast points at the file.
        this._fail(`${source}: ${e.message}`)
      }
    },
    _installGraph(rawPayload) {
      const graph = parseViewJson(rawPayload)
      this.graph = graph
      this.status = 'ready'
      this.error = null
      this.selection = null
      this.selectedEdge = null
      // Default: every overlay the producer emitted is enabled, so
      // the user sees the full overlay decoration on first open.
      this.enabledOverlays = new Set(graph.overlays_present)
    },
    _fail(message) {
      this.status = 'error'
      this.error = message
      this.graph = null
    },
    select(id) {
      this.selection = id
      this.selectedEdge = null
    },
    clearSelection() {
      this.selection = null
      this.selectedEdge = null
    },
    selectEdge(from, to) {
      // Only accept an edge that exists in the current graph;
      // refuses the synthetic port-anchor edges ``"_in_<port>" ->
      // "<child>"`` the producer DOT injects for top-port signal
      // flow (those have no corresponding ``edges[]`` entry).
      if (typeof from !== 'string' || typeof to !== 'string') return
      if (!this.graph) return
      const match = this.graph.edges.find(
        (e) => e.from === from && e.to === to,
      )
      if (!match) return
      this.selectedEdge = { from, to }
      this.selection = null
    },
    descend(id) {
      // Drill the canvas into the subtree rooted at ``id``. No-op
      // when the node has no children (the canvas would render a
      // single floating node).
      if (!id) return
      const prefix = id + '.'
      const hasChildren = this.graph?.nodes.some((n) =>
        n.id.startsWith(prefix),
      )
      if (!hasChildren) return
      this.rootInstancePath = id
      this.selection = id
    },
    ascend() {
      // Pop one level. If we're already showing the original top,
      // this is a no-op.
      if (!this.rootInstancePath) return
      const parent = this.rootInstancePath.replace(/\.[^.]+$/, '')
      this.rootInstancePath =
        parent === this.rootInstancePath ? null : parent || null
    },
    goToTop() {
      this.rootInstancePath = null
    },
    setViewMode(mode) {
      if (mode === 'hier' || mode === 'flow') {
        this.viewMode = mode
      }
    },
    toggleOverlay(name) {
      if (this.enabledOverlays.has(name)) {
        this.enabledOverlays.delete(name)
      } else {
        this.enabledOverlays.add(name)
      }
      // Pinia tracks deep object reactivity but Set mutations don't
      // trigger reactivity reliably across all consumers; reassign
      // to force an update.
      this.enabledOverlays = new Set(this.enabledOverlays)
    },

    // --- hub-event reducers --------------------------------------------------
    //
    // Each apply* action is invoked by useHub's dispatcher on an
    // inbound envelope. Keeping them on the store (and not in the
    // composable) means tests, devtools, and SSR snapshotting all
    // see the same state — and the composable stays a thin
    // transport.

    applyHubCursorTime(tFs) {
      // Decimal string per protocol §3. Store as-is; UI formats.
      this.hubCursorTimeFs = typeof tFs === 'string' ? tFs : null
    },

    applyHubSelection(id) {
      // Cross-origin selection: update store.selection so existing
      // selection-rendering (NodeDetail, future canvas highlight)
      // reacts. We don't dispatch back to the hub — that would loop
      // (the hub already suppresses by origin class, but echoing
      // would still flood the wire).
      if (typeof id !== 'string' || id.length === 0) return
      this.selection = id
      this.selectedEdge = null
    },

    applyHubScope(payload) {
      this.hubScope = payload && typeof payload === 'object' ? { ...payload } : null
    },

    applyDiagnostics(source, items) {
      // Latest-writer-wins per source. Empty `items` clears the
      // source so a producer can withdraw findings (e.g. a re-run
      // of `rb cdc` returns clean).
      if (typeof source !== 'string' || source.length === 0) return
      const next = { ...this.diagnosticsBySource }
      if (Array.isArray(items) && items.length > 0) {
        next[source] = items.slice()
      } else {
        delete next[source]
      }
      this.diagnosticsBySource = next
    },

    applyHubError(err) {
      this.hubError = err && typeof err === 'object'
        ? { code: err.code, message: err.message, at: err.at || Date.now() }
        : null
    },

    dismissHubError() {
      this.hubError = null
    },
  },
})
