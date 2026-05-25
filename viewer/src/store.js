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


// -------------------------------------------------------------------
// Diagnostic → node mapping
// -------------------------------------------------------------------
//
// The v1 ``diagnostics_set`` payload carries each item with at least
// ``{ file, line, col, severity, message }``. Producers MAY also
// attach an ``instance_path`` hint for fast-path matching (the hub
// doesn't strip it from forwarded events). To render badges on the
// schematic we need to map each item to one of the nodes currently
// loaded in the graph.
//
// Strategy, in priority order:
//
//   1. ``item.instance_path`` literal → if it names a known node,
//      use it. No file lookup needed.
//   2. File+line range → walk every node whose ``source.file``
//      matches and whose ``[start_line, end_line]`` range contains
//      ``item.line``, then pick the *deepest* match (the smallest
//      enclosing range). "Deepest" because nested instances at the
//      same source produce overlapping ranges; the most specific
//      one is what the producer almost certainly meant.
//   3. No match → null (item still appears in the DiagnosticsPanel
//      sidebar; it just doesn't anchor to a canvas node).
//
// The line range in ``node.source`` is the *instantiation site* in
// the parent module's file, not the instantiated module's body.
// A diagnostic at e.g. ``rtl/fifo.sv:142`` matches the instance
// whose declaration spans line 142 in ``rtl/fifo.sv`` — which works
// for issues reported against an instance declaration but not for
// issues reported against the module body unless the producer also
// hands in ``instance_path``.

function _resolveDiagnosticItemToNodeId(item, nodesByFile, nodeIdSet) {
  if (typeof item?.instance_path === 'string' && item.instance_path) {
    return nodeIdSet.has(item.instance_path) ? item.instance_path : null
  }
  if (typeof item?.file !== 'string' || !item.file) return null
  const candidates = nodesByFile.get(item.file)
  if (!candidates || candidates.length === 0) return null
  const line = typeof item?.line === 'number' ? item.line : null
  if (line === null) return null
  let bestId = null
  let bestSpan = Infinity
  for (const n of candidates) {
    const s = n.source
    if (!s) continue
    if (typeof s.start_line !== 'number' || typeof s.end_line !== 'number') continue
    if (line < s.start_line || line > s.end_line) continue
    const span = s.end_line - s.start_line
    if (span < bestSpan) {
      bestSpan = span
      bestId = n.id
    }
  }
  return bestId
}

// Build the (file → nodes) index once per ``diagnosticsByNode`` read.
// The graph is mutated wholesale on load so we don't cache across
// reads — the work is O(nodes) and the call cadence is gated by
// Vue's reactivity (only fires when ``diagnosticsBySource`` or
// ``graph`` actually changes).
function _diagnosticsByNode(state) {
  const out = {}
  if (!state.graph || !state.graph.nodes) return out
  const nodesByFile = new Map()
  const nodeIdSet = new Set()
  for (const n of state.graph.nodes) {
    nodeIdSet.add(n.id)
    const f = n?.source?.file
    if (!f) continue
    let arr = nodesByFile.get(f)
    if (!arr) {
      arr = []
      nodesByFile.set(f, arr)
    }
    arr.push(n)
  }
  for (const [source, items] of Object.entries(state.diagnosticsBySource)) {
    for (const item of items) {
      const nodeId = _resolveDiagnosticItemToNodeId(item, nodesByFile, nodeIdSet)
      if (nodeId === null) continue
      if (!out[nodeId]) out[nodeId] = []
      out[nodeId].push({ source, ...item })
    }
  }
  return out
}

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
    // Candidate instance paths when a hub ``selection_changed`` event
    // resolves to more than one match (rtl-buddy-view#55). ``null``
    // means no picker is showing. The first entry is also written to
    // ``selection`` so the canvas pans/zooms to the smallest-range
    // match — the picker just lets the user override that default.
    selectionCandidates: null,
    // "Open in marimo" launch state. Drives the AxiPerfView button's
    // spinner / disabled state. ``axiNotebookError`` is a string when
    // the last launch attempt failed (hub returned 4xx/5xx, or the
    // network rejected); the UI shows it as an inline toast and the
    // user can retry.
    axiNotebookLaunching: false,
    axiNotebookError: null,
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
      // Drop the producer-supplied embedded DOT (state.graph.layout)
      // when descending into a subtree. That DOT was generated for
      // the FULL graph; pickDot would otherwise hand it back to
      // viz.js verbatim and the canvas would re-render the original
      // hier — overlays would still recolour by the filtered node
      // list, producing "only the subtree node has a clock fill" /
      // "the rest of the hier visually unchanged" (see fix in
      // PR #xx). graphToDot rebuilds DOT from nodes+edges, which is
      // correct for the subtree.
      const { layout: _drop, ...rest } = state.graph
      return { ...rest, top: rootId, nodes, edges }
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
      // node. Producers may attach an optional ``instance_path``
      // field to each item for fast-path matching; otherwise we
      // resolve via the file+line range carried by every item per
      // the v1 protocol's ``diagnostics_set`` shape (§3).
      if (!nodeId) return []
      const byNode = _diagnosticsByNode(state)
      return byNode[nodeId] ? byNode[nodeId].slice() : []
    },
    diagnosticsByNode: (state) => _diagnosticsByNode(state),
    nodeIdForDiagnosticItem: (state) => (item) => {
      // Used by surfaces that present a clickable diagnostic (the
      // DiagnosticsPanel sidebar list, future inline hover popovers,
      // etc.). Mirrors the resolver `diagnosticsByNode` uses — same
      // priority order, same deepest-range tiebreak — so clicking a
      // sidebar item lands on the same node its on-canvas badge
      // anchors to.
      if (!state.graph || !item) return null
      const nodesByFile = new Map()
      const nodeIdSet = new Set()
      for (const n of state.graph.nodes) {
        nodeIdSet.add(n.id)
        const f = n?.source?.file
        if (!f) continue
        let arr = nodesByFile.get(f)
        if (!arr) {
          arr = []
          nodesByFile.set(f, arr)
        }
        arr.push(n)
      }
      return _resolveDiagnosticItemToNodeId(item, nodesByFile, nodeIdSet)
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
    /**
     * Ask the hub to spawn a marimo notebook for ``{test, suiteDir}``
     * and open the returned URL in a new browser tab. Mirrors
     * ``rtl_buddy``'s ``/api/axi-profile/notebook`` endpoint (Phase 2
     * of the marimo umbrella, axi-profiler#16).
     *
     * Used by the "Open in marimo" button in AxiPerfView. The hub
     * spawns marimo with ``--headless --no-token`` (#190) so the URL
     * we get back is the bare ``http://localhost:NNNN`` — no token
     * juggling on our side.
     */
    async openAxiNotebook({ test, suiteDir }) {
      this.axiNotebookLaunching = true
      this.axiNotebookError = null
      try {
        const params = new URLSearchParams({ test, suite_dir: suiteDir })
        const response = await fetch(
          `/api/axi-profile/notebook?${params.toString()}`,
        )
        if (!response.ok) {
          let detail = `${response.status} ${response.statusText}`
          try {
            const body = await response.json()
            if (body && typeof body.error === 'string') detail = body.error
          } catch {
            /* non-JSON body — fall through with the status line */
          }
          throw new Error(detail)
        }
        const body = await response.json()
        if (!body || typeof body.url !== 'string') {
          throw new Error('hub response missing url')
        }
        // Open in a new tab. Some browsers block window.open() outside
        // a user-gesture stack; the click handler that called this
        // action satisfies that requirement.
        if (typeof window !== 'undefined') {
          window.open(body.url, '_blank', 'noopener')
        }
        return body
      } catch (err) {
        this.axiNotebookError =
          err && err.message ? String(err.message) : String(err)
        throw err
      } finally {
        this.axiNotebookLaunching = false
      }
    },

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
      // Scope state is per-graph. Carrying ``rootInstancePath`` /
      // ``flowScope`` across a model switch (or any view.json
      // reload) leaves the canvas filtering for a path that doesn't
      // exist in the new graph — the hier view drops the breadcrumb
      // and renders empty, and the flow view shows
      // ``scope X not in graph``. Reset both so the new model
      // boots at its own top.
      this.rootInstancePath = null
      this.flowScope = null
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
      // Each view mode has its own scope field — ``rootInstancePath``
      // for hier (cluster-tree subtree filter), ``flowScope`` for
      // block-flow (the expand-one-level target). We update both so
      // switching tabs lands on the same logical scope; otherwise
      // descending in hier and flipping to flow would dump the user
      // back at the design top.
      if (!id) return
      const prefix = id + '.'
      const hasChildren = this.graph?.nodes.some((n) =>
        n.id.startsWith(prefix),
      )
      if (!hasChildren) return
      this.rootInstancePath = id
      this.flowScope = id
      this.selection = id
    },
    ascend() {
      // Pop one level on both scope fields so the two view modes stay
      // in sync (see descend's note), and follow the selection up
      // too — without the selection bump the NodeDetail panel keeps
      // showing the deeper path (e.g. ``ip_dma.i_cor.i_wlg``) even
      // though the canvas is now at ``ip_dma.i_cor``.
      if (!this.rootInstancePath && !this.flowScope) return
      const cur = this.rootInstancePath || this.flowScope
      const parent = cur.replace(/\.[^.]+$/, '')
      const next = parent === cur ? null : parent || null
      this.rootInstancePath = next
      this.flowScope = next
      this.selection = next
    },
    goToTop() {
      this.rootInstancePath = null
      this.flowScope = null
      this.selection = null
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
      // Any non-ambiguous selection invalidates a pending picker; a
      // single match means the hub already disambiguated and we
      // shouldn't keep an old candidate list floating.
      this.selectionCandidates = null
    },

    presentSelectionCandidates(paths) {
      // Multi-match hub ``selection_changed`` — apply the smallest-
      // range default ([0]) immediately so the canvas reacts in the
      // common case, and stash the full list so the SelectionCandidates
      // popover can offer alternatives. The composable owns the
      // auto-dismiss timer.
      if (!Array.isArray(paths) || paths.length === 0) return
      const filtered = paths.filter(
        (p) => typeof p === 'string' && p.length > 0,
      )
      if (filtered.length === 0) return
      this.selection = filtered[0]
      this.selectedEdge = null
      // Single match → no picker. Acts as the array-collapsing case
      // for callers that don't pre-check (useHub does pre-check, but
      // tests / future callers may not).
      this.selectionCandidates = filtered.length > 1 ? filtered.slice() : null
    },

    chooseSelectionCandidate(path) {
      // User picked one of the multi-match candidates from the popover.
      // Lock the selection and dismiss the picker. The hub broadcast
      // is the composable's job (useHub.chooseSelectionCandidate sends
      // ``selection_changed`` from origin=view); the store just owns
      // the local-side state transition.
      if (typeof path !== 'string' || path.length === 0) return
      this.selection = path
      this.selectedEdge = null
      this.selectionCandidates = null
    },

    dismissSelectionCandidates() {
      this.selectionCandidates = null
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
