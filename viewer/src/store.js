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
    // Explicit scope for the block-flow view (the instance whose
    // direct children are rendered). ``null`` means show
    // ``graph.top``. Kept independent of ``selection`` so clicking
    // a block in flow view selects it (populating NodeDetail)
    // without changing scope — matching hier-view's click =
    // focus, button = navigate contract.
    flowScope: null,
    // Top-level tab. ``hierarchy`` = the hier/flow canvas with
    // overlays. ``axi-perf`` = the dedicated AxiPerfView tab. Default
    // ``hierarchy`` so the app renders on first load even when no
    // axi-perf overlay is present.
    activeTab: 'hierarchy',
    // Bundle currently selected in the AxiPerfView detail pane, or
    // ``null`` when none is selected.
    selectedAxiBundle: null,
    // Hub-mirrored state. All written by applyHub*/applyDiagnostics
    // actions; consumers read these directly.
    hubCursorTimeFs: null,
    hubScope: null,
    diagnosticsBySource: {},
    hubError: null,
    // Available models advertised by the hub's ``GET /models``
    // endpoint (issue rtl_buddy#174). Empty when the SPA is running
    // standalone (drag-drop / embed.py / no /models endpoint). The
    // header picker is hidden when this is empty.
    availableModels: [],
    // Whichever model is currently active on the hub. Driven by
    // ``GET /models`` at boot and ``view_changed`` events at runtime.
    // ``null`` when running standalone.
    activeModel: null,
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
    // Scope shown in the block-flow view: the explicit
    // ``flowScope`` state when set, otherwise the design top. The
    // flow renderer reads this to decide *which* instance to expand
    // (showing its direct children + their interconnections).
    flowScopeId(state) {
      if (state.flowScope) return state.flowScope
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
      //   1. ``?model=NAME`` URL query — explicit hub-model selector
      //      (rtl_buddy#174). Calls ``/view.json?model=NAME`` and
      //      promotes that model to active hub-side.
      //   2. ``?view=`` URL query — explicit caller intent.
      //   3. ``window.__RTL_BUDDY_VIEW_URL__`` — hub injection. Set
      //      by rtl-buddy-hub's index.html renderer when it has a
      //      view.json configured (see rtl_buddy hub/viewer_http.py).
      //      Visiting ``http://hub:port/`` with no query string then
      //      auto-loads the design instead of dropping the user on
      //      the empty drag-drop screen.
      //   4. ``window.__RTL_BUDDY_VIEW_DATA__`` — embed.py inject
      //      (single-file standalone HTML build).
      //   5. Stay idle and wait for drag-drop / file picker.
      //
      // In hub mode (priority 1-3), we also fire-and-forget a
      // ``GET /models`` so the picker is populated by the time the
      // first frame renders.
      const params = new URLSearchParams(window.location.search)
      const modelParam = params.get('model')
      const viewUrl = params.get('view')
      if (modelParam) {
        // Best-effort models list — failure here doesn't block the
        // primary load.
        this.loadAvailableModels().catch(() => {})
        await this.switchModel(modelParam, { updateUrl: false })
        return
      }
      if (viewUrl) {
        this.loadAvailableModels().catch(() => {})
        await this.loadFromUrl(viewUrl)
        return
      }
      if (typeof window !== 'undefined' && window.__RTL_BUDDY_VIEW_URL__) {
        this.loadAvailableModels().catch(() => {})
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

    /**
     * Fetch ``GET /models`` and populate the picker. Silently no-ops
     * (clears the list) when the endpoint is missing — that's how we
     * detect "running standalone, no hub" and hide the UI.
     */
    async loadAvailableModels() {
      try {
        const response = await fetch('/models')
        if (!response.ok) {
          this.availableModels = []
          this.activeModel = null
          return
        }
        const payload = await response.json()
        this.availableModels = Array.isArray(payload.models) ? payload.models : []
        this.activeModel =
          typeof payload.active === 'string' ? payload.active : null
      } catch {
        this.availableModels = []
        this.activeModel = null
      }
    },

    /**
     * Switch the hub-side active model. Calls
     * ``GET /view.json?model=NAME``, installs the returned graph, and
     * (by default) updates the URL bar with ``?model=NAME`` so the
     * link is shareable. ``view_changed`` events that echo this
     * switch are deduped by comparing against ``activeModel``.
     */
    async switchModel(name, { updateUrl = true } = {}) {
      if (!name) return
      const url = `/view.json?model=${encodeURIComponent(name)}`
      await this.loadFromUrl(url)
      // Only flip activeModel + URL after the load actually succeeded
      // — a 400/500 leaves us pointed at the previous model so the
      // next view_changed echo doesn't get masked.
      if (this.status === 'ready') {
        this.activeModel = name
        if (updateUrl && typeof window !== 'undefined' && window.history) {
          const params = new URLSearchParams(window.location.search)
          params.set('model', name)
          params.delete('view')
          const newQuery = params.toString()
          const newUrl =
            window.location.pathname +
            (newQuery ? `?${newQuery}` : '') +
            window.location.hash
          try {
            window.history.replaceState({}, '', newUrl)
          } catch {
            /* security-restricted environments — best effort only */
          }
        }
      }
    },

    /**
     * Apply a ``view_changed`` event from the hub. Dedupes against
     * ``activeModel`` so the SPA that initiated a switch via
     * ``switchModel`` doesn't re-fetch from its own broadcast (the
     * hub broadcasts to every WS peer including the initiator —
     * see rtl_buddy#174 close-out comment).
     */
    async applyViewChanged(payload) {
      if (!payload || typeof payload.model !== 'string') return
      if (payload.model === this.activeModel) return
      await this.switchModel(payload.model, { updateUrl: true })
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
      //
      // The "scope" that descends depends on the current view mode:
      // ``hier`` updates ``rootInstancePath`` (the cluster-tree's
      // visible-subtree filter); ``flow`` updates ``flowScope`` (the
      // block-flow's expand-one-level target). Both views share the
      // same selection so the NodeDetail panel stays meaningful.
      if (!id) return
      const prefix = id + '.'
      const hasChildren = this.graph?.nodes.some((n) =>
        n.id.startsWith(prefix),
      )
      if (!hasChildren) return
      if (this.viewMode === 'flow') {
        this.flowScope = id
      } else {
        this.rootInstancePath = id
      }
      this.selection = id
    },
    ascend() {
      // Pop one level. If we're already showing the original top,
      // this is a no-op. View-mode-aware in the same way ``descend``
      // is.
      if (this.viewMode === 'flow') {
        if (!this.flowScope) return
        const parent = this.flowScope.replace(/\.[^.]+$/, '')
        this.flowScope =
          parent === this.flowScope ? null : parent || null
        return
      }
      if (!this.rootInstancePath) return
      const parent = this.rootInstancePath.replace(/\.[^.]+$/, '')
      this.rootInstancePath =
        parent === this.rootInstancePath ? null : parent || null
    },
    goToTop() {
      if (this.viewMode === 'flow') {
        this.flowScope = null
        return
      }
      this.rootInstancePath = null
    },
    setViewMode(mode) {
      if (mode === 'hier' || mode === 'flow') {
        this.viewMode = mode
      }
    },
    setActiveTab(name) {
      // 'hierarchy' or 'axi-perf'. Unknown names are ignored.
      if (name !== 'hierarchy' && name !== 'axi-perf') return
      this.activeTab = name
    },
    selectAxiBundle(name) {
      this.selectedAxiBundle = name || null
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
