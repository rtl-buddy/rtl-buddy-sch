<template>
  <main class="graph-canvas" ref="canvasEl">
    <div class="canvas-tabs">
      <button
        type="button"
        class="canvas-tab"
        :class="{ active: store.viewMode === 'hier' }"
        @click="store.setViewMode('hier')"
        title="Nested-cluster hierarchy from the producer's embedded layout"
      >Hierarchy</button>
      <button
        type="button"
        class="canvas-tab"
        :class="{ active: store.viewMode === 'flow' }"
        @click="store.setViewMode('flow')"
        :title="flowTabTitle"
      >Block Flow</button>
      <!-- Third mode: the elkjs pin-level schematic (#163 P2). It is a
           separate component (the SVG is Vue's, not a viz.js string),
           so App.vue swaps the whole canvas — this button only has to
           set the mode. -->
      <button
        type="button"
        class="canvas-tab"
        @click="store.setViewMode('sch')"
        :title="schTabTitle"
      >Schematic</button>
      <nav
        v-if="breadcrumb.length > 0"
        class="scope-breadcrumb"
        data-rb-scope-breadcrumb
        :aria-label="store.viewMode === 'flow' ? 'Block-flow scope' : 'Hierarchy scope'"
      >
        <span class="scope-label">scope:</span>
        <template v-for="(crumb, i) in breadcrumb" :key="crumb.path">
          <span v-if="i > 0" class="crumb-sep" aria-hidden="true">›</span>
          <button
            type="button"
            class="crumb"
            :class="{ current: crumb.current }"
            :disabled="crumb.current"
            :title="crumb.current ? `Current scope: ${crumb.path}` : `Set scope to ${crumb.path}`"
            @click="jumpToScope(crumb.path)"
          >{{ crumb.label }}</button>
        </template>
      </nav>
    </div>
    <div class="graph-toolbar">
      <button type="button" @click="zoomIn">+</button>
      <button type="button" @click="zoomOut">−</button>
      <button type="button" @click="fitToWindow">Fit</button>
      <button type="button" @click="resetView">Reset</button>
    </div>
    <div class="svg-host" ref="svgHostEl"></div>
    <!-- Diagnostics badges overlaid on the canvas. Position-only
         pass; the badge component itself owns hover-expand UX. The
         layer is pointer-events: none so it doesn't steal pan
         drags; each badge re-enables pointer events on itself. -->
    <div class="badge-layer" aria-hidden="false">
      <NodeBadge
        v-for="b in nodeBadges"
        :key="b.nodeId"
        :node-id="b.nodeId"
        :items="b.items"
        :style="{ left: b.x + 'px', top: b.y + 'px' }"
        @select="onBadgeSelect(b.nodeId)"
      />
    </div>
    <!-- Disambiguation picker (rtl-buddy-view#55). Self-hides when
         store.selectionCandidates is null / length <= 1, so we mount
         unconditionally inside the canvas where it sits near the
         schematic toolbar without disturbing the canvas layout. -->
    <SelectionCandidates />
  </main>
</template>

<script setup>
// SVG canvas. Owns the viz.js-rendered SVG, the pan/zoom transform,
// and the click handlers that dispatch ``node.link`` URIs.
//
// We deliberately don't try to convert the SVG into Vue's virtual
// DOM — viz.js's SVG is production-quality already and rewrapping
// every node/edge in Vue templates would lose that. Instead we
// stamp ``data-node-id`` / ``data-edge-from`` / ``data-edge-to``
// onto each rendered group so overlays can query them after the
// layout step and so click handlers can recover the model node.
import { computed, onMounted, ref, watch, onBeforeUnmount } from 'vue'
import { useViewerStore } from '../store.js'
import { layoutGraph, layoutDot, clusterIdFor, hasEmbeddedDot } from '../layout/viz.js'
import { buildBlockFlowDot } from '../layout/blockFlow.js'
import { applyOverlays } from '../overlays/index.js'
import { coverageLiveOverlay } from '../overlays/coverage_live.js'
import { useHub } from '../composables/useHub.js'
import { registerSvgProvider, unregisterSvgProvider } from '../capture.js'
import { FIT_SCALE_MAX } from '../layout/constants.js'
import { token, themeVersion } from '../theme.js'
import NodeBadge from './NodeBadge.vue'
import SelectionCandidates from './SelectionCandidates.vue'

const store = useViewerStore()
const hub = useHub()
const svgHostEl = ref(null)
const canvasEl = ref(null)
const transform = ref({ x: 0, y: 0, scale: 1 })
let _svgEl = null
// Bumped after every renderSvg run. ``_svgEl`` is a let-binding,
// not a ref, so reactive consumers (the NodeBadge position
// computed) need a separate signal to know the underlying SVG
// elements have just been replaced wholesale and any previously
// cached bboxes are stale.
const svgVersion = ref(0)

const graph = computed(() => store.displayGraph)
const flowTabTitle = computed(() =>
  store.flowScopeId
    ? `One-level signal flow under ${store.flowScopeId}`
    : 'One-level signal flow (select a node to scope)',
)
const schTabTitle = computed(() =>
  store.graph?.layout?.elk
    ? 'Pin-level schematic laid out in-browser by elkjs'
    : 'Pin-level schematic — this view.json predates the layout.elk payload',
)
// "Up" is only meaningful when the current scope isn't already the
// design top — popping past top would lose context.
const canAscendScope = computed(() => {
  if (!store.graph) return false
  return store.flowScopeId && store.flowScopeId !== store.graph.top
})

// Clickable breadcrumb path for whichever scope the active tab is
// showing. Each segment is a button that jumps the scope to that
// prefix; the trailing (current) segment is rendered disabled.
//
// Both tabs get one, from the same computed, because both tabs have a
// scope and the two are kept in lockstep by ``store.descend`` /
// ``store.ascend``. Block-flow always has one (it falls back to
// ``graph.top``); the hier view only shows it once the user has
// actually descended — at the top of the design the canvas already
// says where you are, and an unconditional strip would just be
// permanent chrome. Before this, descending in hier left NO indication
// of the current scope anywhere on the canvas.
const scopePath = computed(() => {
  if (!store.graph) return null
  return store.viewMode === 'flow' ? store.flowScopeId : store.rootInstancePath
})

const breadcrumb = computed(() => {
  const scope = scopePath.value
  if (!scope) return []
  const segments = scope.split('.')
  return segments.map((label, i) => {
    const path = segments.slice(0, i + 1).join('.')
    return { label, path, current: i === segments.length - 1 }
  })
})

function jumpToScope(path) {
  if (!path || !store.graph) return
  // Top of design → clear the scope entirely. ``goToTop`` resets BOTH
  // scope fields, which is what keeps the two tabs pointing at the same
  // place (the same invariant ``descend`` / ``ascend`` maintain).
  if (path === store.graph.top) {
    store.goToTop()
    return
  }
  // Any other crumb is an ancestor of the current scope, so it has
  // children by construction and ``descend`` will accept it.
  store.descend(path)
}

async function renderSvg() {
  if (!graph.value || !svgHostEl.value) return
  let svgText
  // Which DOT source this render used. Only the producer's embedded
  // DOT gets the stylesheet's re-tint safety net — the in-JS builders
  // bake theme-resolved tokens (CDC red, clock pastels, the
  // unconstrained-source marker) and a blanket re-tint would neutralise
  // every one of them. Block-flow always builds its own DOT.
  const producerDot = store.viewMode !== 'flow' && hasEmbeddedDot(graph.value)
  svgHostEl.value.classList.toggle('producer-dot', producerDot)
  try {
    if (store.viewMode === 'flow') {
      // Block-flow view builds its DOT in the SPA from the graph's
      // own port-expression data — no producer round-trip. Hand the
      // resulting DOT to viz.js via the shared layoutDot path so
      // the engine is loaded once and the SVG-string convention
      // matches the hier view.
      const dot = buildBlockFlowDot(graph.value, store.flowScopeId)
      svgText = await layoutDot(dot)
    } else {
      svgText = await layoutGraph(graph.value)
    }
  } catch (e) {
    store.$patch({ status: 'error', error: `viz.js layout failed: ${e.message}` })
    return
  }
  svgHostEl.value.innerHTML = svgText
  _svgEl = svgHostEl.value.querySelector('svg')
  if (!_svgEl) return
  // viz.js emits a Graphviz-default SVG. Tag each rendered group
  // with its model id so overlays + click handlers can join back
  // to the graph.
  for (const group of _svgEl.querySelectorAll('g.node')) {
    const titleEl = group.querySelector('title')
    if (titleEl) group.setAttribute('data-node-id', titleEl.textContent)
  }
  // Cluster groups carry Graphviz's sanitized cluster identifier as
  // their <title>; recover the original instance path from the
  // producer-supplied lookup map so clicks on the cluster border or
  // label select the wrapper node (rtl-buddy-view#... follow-up to
  // the cluster-tree layout switch).
  const clusterLookup =
    (graph.value && graph.value.layout && graph.value.layout.cluster_lookup) || null
  const graphTop = graph.value?.top
  // For the graphToDot path (no embedded layout), the cluster name
  // is ``clusterIdFor(instance_path)``; build a reverse map over the
  // current graph's nodes so any g.cluster whose title matches a
  // known node gets ``data-node-id`` stamped.
  let synthClusterLookup = null
  if (!clusterLookup && graph.value?.nodes) {
    synthClusterLookup = new Map()
    for (const n of graph.value.nodes) {
      synthClusterLookup.set(clusterIdFor(n.id), n.id)
    }
  }
  for (const group of _svgEl.querySelectorAll('g.cluster')) {
    const titleEl = group.querySelector('title')
    if (!titleEl) continue
    const clusterId = titleEl.textContent
    if (clusterLookup && clusterLookup[clusterId]) {
      group.setAttribute('data-node-id', clusterLookup[clusterId])
    } else if (clusterId === 'cluster_top' && graphTop) {
      // ``graphToDot`` wraps the scope root in ``cluster_top``
      // regardless of its sanitized id — map that to graph.top.
      group.setAttribute('data-node-id', graphTop)
    } else if (synthClusterLookup && synthClusterLookup.has(clusterId)) {
      // Nested clusters from graphToDot: cluster name encodes the
      // original instance path via ``clusterIdFor``.
      group.setAttribute('data-node-id', synthClusterLookup.get(clusterId))
    }
  }
  for (const group of _svgEl.querySelectorAll('g.edge')) {
    const titleEl = group.querySelector('title')
    if (!titleEl) continue
    // Graphviz edge title format: "from->to".
    const [from, to] = titleEl.textContent.split('->')
    if (from) group.setAttribute('data-edge-from', from.trim())
    if (to) group.setAttribute('data-edge-to', to.trim())
  }
  // Block-flow cells: viz.js wraps each HTML table cell that has
  // an ``HREF`` in an ``<a xlink:href="...">`` element. Stamp the
  // href on the anchor as ``data-bf-id`` (no namespace gymnastics
  // for selectors) so click handlers can match cells via
  // ``[data-bf-id]`` and lookups via ``[data-bf-id="bf-out:…"]``.
  for (const anchor of _svgEl.querySelectorAll('a')) {
    const href =
      anchor.getAttribute('xlink:href') ||
      anchor.getAttribute('href') ||
      anchor.getAttributeNS('http://www.w3.org/1999/xlink', 'href')
    if (href && href.startsWith('bf-')) {
      anchor.setAttribute('data-bf-id', href)
    }
  }
  applyDutBoundary(_svgEl, graph.value)
  repaintOverlays()
  applySelectionHighlight(store.selection)
  // Signal to the badge-position computed that the underlying SVG
  // has just been re-rendered — previously cached bboxes are stale.
  svgVersion.value += 1
  // Defer to next frame so flex layout has settled and the host
  // rect is its final size before we compute the fit scale.
  requestAnimationFrame(fitToWindow)
}

// Draw a dashed boundary + "DUT" label around every SVG group whose
// model matches the active view's ``dut_top`` (view.json v1.1 / #99).
//
// Implementation: rather than restyle Graphviz's cluster polygon
// (which sits flush against the children — visually cramped),
// inject a dedicated ``<rect data-rb-dut-frame>`` sized to the
// polygon's bbox PLUS a few pixels of padding, and a sibling
// ``<text data-rb-dut-label>`` anchored at the rect's top-left
// corner. Both elements carry ``pointer-events: none`` so they
// stay out of the hit-test path — clicks still land on the
// underlying cluster / leaf as before.
//
// Idempotent: any previously injected frame/label is removed before
// the new pass, so a model/scope switch can't leave stale
// decorations stacking up.
const DUT_FRAME_PADDING = 10
function applyDutBoundary(svgRoot, graph) {
  if (!svgRoot) return
  for (const el of svgRoot.querySelectorAll('[data-rb-dut-anchor]')) {
    el.removeAttribute('data-rb-dut-anchor')
  }
  for (const el of svgRoot.querySelectorAll(
    '[data-rb-dut-frame], [data-rb-dut-label]',
  )) {
    el.remove()
  }
  const dutTop = graph?.dut_top
  if (typeof dutTop !== 'string' || !dutTop) return
  const ns = 'http://www.w3.org/2000/svg'
  for (const node of graph.nodes) {
    if (!node || node.module !== dutTop) continue
    // Match every SVG element tagged with this instance path —
    // both ``g.node`` (leaf DUT) and ``g.cluster`` (container DUT)
    // get decorated so the visual stays consistent across the
    // hier-view's flat / cluster / bridge branches.
    const groups = svgRoot.querySelectorAll(
      `[data-node-id="${cssEscape(node.id)}"]`,
    )
    for (const group of groups) {
      group.setAttribute('data-rb-dut-anchor', 'true')
      const shape = group.querySelector('polygon, ellipse, rect, path')
      if (!shape || typeof shape.getBBox !== 'function') continue
      let bb
      try {
        bb = shape.getBBox()
      } catch {
        continue
      }
      if (!bb || !bb.width || !bb.height) continue

      const pad = DUT_FRAME_PADDING
      const frame = document.createElementNS(ns, 'rect')
      frame.setAttribute('data-rb-dut-frame', 'true')
      frame.setAttribute('x', String(bb.x - pad))
      frame.setAttribute('y', String(bb.y - pad))
      frame.setAttribute('width', String(bb.width + 2 * pad))
      frame.setAttribute('height', String(bb.height + 2 * pad))
      frame.setAttribute('rx', '6')
      frame.setAttribute('ry', '6')
      frame.setAttribute('fill', 'none')
      frame.setAttribute('stroke', token('--fg-muted'))
      frame.setAttribute('stroke-width', '2')
      frame.setAttribute('stroke-dasharray', '6,4')
      frame.setAttribute('pointer-events', 'none')
      // Insert as the FIRST child of the group so the dashed rect
      // sits behind Graphviz's polygon + the children — the user
      // sees the original cluster frame intact, with the dashed
      // outer rect floating around it as a separator.
      group.insertBefore(frame, group.firstChild)

      const label = document.createElementNS(ns, 'text')
      label.setAttribute('data-rb-dut-label', 'true')
      // Anchor at the padded rect's top-left, slightly inset so the
      // letters don't kiss the dashed stroke.
      label.setAttribute('x', String(bb.x - pad + 6))
      label.setAttribute('y', String(bb.y - pad - 4))
      label.setAttribute('fill', token('--fg-muted'))
      label.setAttribute('font-size', '10')
      label.setAttribute('font-family', token('--font-mono'))
      label.setAttribute('font-weight', '700')
      label.setAttribute('letter-spacing', '0.05em')
      label.setAttribute('pointer-events', 'none')
      label.textContent = 'DUT'
      group.appendChild(label)
    }
  }
}

// CSS.escape is widely available but not on every JSDOM build; fall
// back to a conservative escaper for selector use. Mirrors the same
// helper in overlays/clock.js — kept local so this file stands on
// its own for the boundary-renderer unit test.
function cssEscape(s) {
  if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(s)
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`)
}

// Mark the SVG element whose ``data-node-id`` matches the current
// selection so CSS can give it a stroke/fill accent. Walks the
// node + cluster sets so both hier-view clusters and flow-view
// HTML-table cells light up correctly. Clears the previous mark
// before painting a new one.
function applySelectionHighlight(selectedId) {
  if (!_svgEl) return
  for (const el of _svgEl.querySelectorAll('[data-rb-selected]')) {
    el.removeAttribute('data-rb-selected')
  }
  if (!selectedId) return
  let first = null
  for (const el of _svgEl.querySelectorAll(`[data-node-id="${selectedId}"]`)) {
    el.setAttribute('data-rb-selected', 'true')
    if (first === null) first = el
  }
  // Bring the selection into view WITHOUT changing zoom, and only when
  // it's actually off-screen. Selecting a node must not rescale the
  // canvas — clicking a large block (e.g. the DUT) used to zoom out to
  // "fit" it, which reads as a disorienting jump. A node already in
  // view (the common case for a local click) leaves the viewport
  // untouched; an off-screen selection (e.g. a hub-driven peer) is
  // recentred at the current zoom.
  if (first !== null) bringSelectionIntoView(first)
}

// Block-flow edges carry a stable ``id="bf-edge:<src>:<srcPort>:
// <dst>:<dstPort>"`` attribute emitted by blockFlow.js — Graphviz
// strips port names from the edge ``<title>`` so the title is
// useless for endpoint lookup, but ``id`` propagates verbatim.
//
// To find edges incident on a clicked port, we walk every
// ``g.edge[id^="bf-edge:"]``, parse the id back into its four
// components, and match against ``<nodeId>:<portName>`` on either
// side. Match → stamp ``data-rb-edge-highlighted`` for CSS to
// paint amber.
function clearEdgeHighlight() {
  if (!_svgEl) return
  for (const el of _svgEl.querySelectorAll('[data-rb-edge-highlighted]')) {
    el.removeAttribute('data-rb-edge-highlighted')
  }
}

function highlightEdgesForPort(nodeId, portName) {
  if (!_svgEl || !nodeId || !portName) return
  const want = `${nodeId}:${portName}`
  for (const edge of _svgEl.querySelectorAll('g.edge[id^="bf-edge:"]')) {
    // ``bf-edge:<src>:<srcPort>:<dst>:<dstPort>`` — the leading
    // ``bf-edge:`` is fixed; the rest is `src:srcPort:dst:dstPort`
    // where src/dst are instance paths (contain ``.``, never ``:``).
    const rest = edge.id.slice('bf-edge:'.length)
    const parts = rest.split(':')
    if (parts.length < 4) continue
    const src = `${parts[0]}:${parts[1]}`
    const dst = `${parts[2]}:${parts[3]}`
    if (src === want || dst === want) {
      edge.setAttribute('data-rb-edge-highlighted', 'true')
    }
  }
}

watch(graph, renderSvg)
// Graphviz bakes colours into the SVG it emits, so a theme flip needs
// a fresh layout — there is no ``var(--…)`` for the sheet to
// re-resolve after the fact. Cheap in practice: the theme changes when
// a person changes it, not per frame.
watch(themeVersion, renderSvg)
// Tab switch + scope drill-down trigger a full re-layout. Both
// produce a different DOT, so the SVG must be rebuilt.
watch(() => store.viewMode, renderSvg)
watch(() => store.flowScopeId, () => {
  if (store.viewMode === 'flow') renderSvg()
})
watch(() => store.selection, (id) => applySelectionHighlight(id))
watch(
  () => store.enabledOverlays,
  () => {
    if (_svgEl && graph.value) {
      repaintOverlays()
    }
  },
)
// Live wave-overlay re-tint: when the hub pushes a new value batch
// (or the user selects a different surfer signal), re-run the
// overlay layer without re-running viz.js layout. The wave overlay
// reuses badge nodes in place — toggling neighbouring overlays is
// unaffected.
function overlayContext() {
  return {
    waveValuesByKey: store.waveValuesByKey,
    selectedSignal: store.hubSignalSelected,
    // Live coverage from the hub's /cov.json, joined by module name.
    // An empty Map when there is no hub or no coverage data.
    covByModule: store.covByModule,
  }
}

// One repaint of every overlay layer, in the order they must run.
//
// ``applyOverlays`` first: it is the payload-driven pass, and it owns
// restoring the Graphviz fill floor for everything it doesn't paint.
// The LIVE coverage overlay second, on top: it isn't in
// ``overlays_present`` (the hub, not the producer, is its source), it
// wins the fill on plain leaf boxes when enabled, and its clear
// branch assumes the pass before it has already run. Every trigger —
// first render, overlay toggle, wave values, coverage arriving or
// being unticked — goes through here so that order is never in doubt.
function repaintOverlays() {
  if (!_svgEl || !graph.value) return
  const context = overlayContext()
  applyOverlays(_svgEl, graph.value, store.enabledOverlays, context)
  coverageLiveOverlay.apply(_svgEl, graph.value, store.covEnabled, context)
}
watch(
  () => [store.waveValuesByKey, store.hubSignalSelected],
  () => {
    if (_svgEl && graph.value) {
      repaintOverlays()
    }
  },
)
// Live coverage re-tint: the payload lands asynchronously (one fetch
// per session, kicked off when the graph installs), so the first
// paint usually happens before it arrives. Re-run the overlay layer
// on arrival and on every toggle — no viz.js re-layout, just fills.
watch(
  () => [store.covEnabled, store.covByModule],
  () => {
    if (_svgEl && graph.value) {
      repaintOverlays()
    }
  },
)

// Recentre the selected element WITHOUT changing zoom, and only when
// its centre is currently off-screen. Selecting a node must never
// rescale the canvas: clicking a large block (e.g. the DUT) used to
// zoom out to "fit" it, which read as a disorienting jump. An
// already-visible selection (the common local-click case) is left
// untouched; an off-screen one (e.g. a hub-driven ``selection_changed``
// peer) is panned into view at the current zoom. Port-cell clicks use
// ``panToElement`` directly (also zoom-preserving).
function bringSelectionIntoView(el) {
  if (!el || !_svgEl || !svgHostEl.value) return
  let bb
  try {
    bb = el.getBBox()
  } catch {
    return
  }
  if (!bb || !bb.width || !bb.height) return
  const rect = svgHostEl.value.getBoundingClientRect()
  const { scale, x, y } = transform.value
  const cx = (bb.x + bb.width / 2) * scale + x
  const cy = (bb.y + bb.height / 2) * scale + y
  // Centre already within the viewport → leave the view as-is.
  if (cx >= 0 && cx <= rect.width && cy >= 0 && cy <= rect.height) return
  // Off-screen → recentre at the current zoom (no rescale).
  panToElement(el)
}

// ---------------------------------------------------------------
// Diagnostics badge layer
// ---------------------------------------------------------------
//
// One badge per node that has at least one diagnostic mapped to
// it by the store (file+line → node, with ``instance_path`` fast
// path). Position is computed in screen-space against the current
// canvas transform; depends on:
//
//   - store.diagnosticsByNode  (what to show)
//   - transform.value          (pan + zoom)
//   - svgVersion               (force recompute after re-render)
//
// Skips badges whose anchor falls outside the visible host rect —
// off-screen DOM elements aren't free, and a 6×6 dot at (-50, 200)
// just spends layout budget for nothing the user can see.

function onBadgeSelect(nodeId) {
  store.select(nodeId)
}

const nodeBadges = computed(() => {
  // Touching svgVersion here ties the computed to renderSvg's
  // bump so a fresh layout re-queries the bboxes.
  // eslint-disable-next-line no-unused-expressions
  svgVersion.value
  if (!_svgEl || !svgHostEl.value) return []
  const byNode = store.diagnosticsByNode
  const out = []
  const rect = svgHostEl.value.getBoundingClientRect()
  const { x: tx, y: ty, scale } = transform.value
  for (const [nodeId, items] of Object.entries(byNode)) {
    if (!items || items.length === 0) continue
    const el = _svgEl.querySelector(`[data-node-id="${CSS.escape(nodeId)}"]`)
    if (!el) continue
    let bb
    try {
      bb = el.getBBox()
    } catch {
      continue
    }
    if (!bb || !bb.width || !bb.height) continue
    // Anchor at the top-right corner of the bbox in screen-space.
    const x = (bb.x + bb.width) * scale + tx
    const y = bb.y * scale + ty
    // Cull when the anchor lands fully outside the visible host.
    // Allow a 24px margin so a badge anchored just at the edge
    // still pops with its translate(-50%,-50%) pivot intact.
    if (x < -24 || y < -24 || x > rect.width + 24 || y > rect.height + 24) {
      continue
    }
    out.push({ nodeId, items, x, y })
  }
  return out
})

function nodeFromEvent(e) {
  // ``g.cluster`` is the cluster-tree wrapper; ``g.node`` the leaf
  // box. Both get ``data-node-id`` stamped in renderSvg, so the
  // closest()-walk picks up whichever the user actually clicked —
  // including the cluster's border / label / blank interior.
  const group = e.target.closest('g.node, g.cluster, [data-node-id]')
  if (!group) return null
  const id = group.getAttribute('data-node-id')
  if (!id) return null
  const node = store.nodesById.get(id)
  return node ? { id, node } : null
}

function edgeFromEvent(e) {
  const group = e.target.closest('g.edge, [data-edge-from]')
  if (!group) return null
  const from = group.getAttribute('data-edge-from')
  const to = group.getAttribute('data-edge-to')
  if (!from || !to) return null
  // Synthetic port-anchor edges (``_in_<port>`` / ``_out_<port>``)
  // don't appear in graph.edges; treat them as non-clickable
  // structural decoration so a click falls through.
  const match = store.graph
    ? store.graph.edges.find((edge) => edge.from === from && edge.to === to)
    : null
  return match ? { from, to, edge: match } : null
}

// Block-flow HTML-table cells carry ``HREF`` attributes that
// viz.js turns into ``<a xlink:href="...">`` wrappers. We stamp
// the href on the anchor as ``data-bf-id`` in renderSvg, so a
// ``closest('[data-bf-id]')`` walk finds the clicked cell.
//   - ``bf-in:<nodeId>:<portName>`` — input-port cell
//   - ``bf-out:<nodeId>:<portName>`` — output-port cell
//   - ``bf-ctr:<nodeId>`` — instance / module centre cell
function blockFlowHitFromEvent(e) {
  if (store.viewMode !== 'flow') return null
  const anchor = e.target.closest('[data-bf-id]')
  if (!anchor) return null
  const id = anchor.getAttribute('data-bf-id')
  return parseBlockFlowId(id)
}

function parseBlockFlowId(id) {
  if (id.startsWith('bf-in:')) {
    const rest = id.slice('bf-in:'.length)
    const idx = rest.lastIndexOf(':')
    if (idx < 0) return null
    return { kind: 'port_in', nodeId: rest.slice(0, idx), portName: rest.slice(idx + 1) }
  }
  if (id.startsWith('bf-out:')) {
    const rest = id.slice('bf-out:'.length)
    const idx = rest.lastIndexOf(':')
    if (idx < 0) return null
    return { kind: 'port_out', nodeId: rest.slice(0, idx), portName: rest.slice(idx + 1) }
  }
  if (id.startsWith('bf-ctr:')) {
    return { kind: 'block_center', nodeId: id.slice('bf-ctr:'.length) }
  }
  return null
}

// Children of the current flow scope (excluding ``selfId``). Used
// to find the sibling whose port is wired to the same net.
function flowSiblings(selfId) {
  if (!store.graph || !store.flowScopeId) return []
  const prefix = store.flowScopeId + '.'
  return store.graph.nodes.filter((n) => {
    if (n.id === selfId) return false
    if (!n.id.startsWith(prefix)) return false
    const rest = n.id.slice(prefix.length)
    return rest.length > 0 && !rest.includes('.')
  })
}

// Given a click on an input/output port cell, locate the SVG
// element of the *peer* of that port:
//   - input port click → driver (sibling output OR scope input
//     anchor ``_in_<net>``)
//   - output port click → sink (sibling input OR scope output
//     anchor ``_out_<net>``)
function findFlowPeerSvgEl(hit) {
  if (!_svgEl || !store.graph) return null
  const node = store.nodesById.get(hit.nodeId)
  if (!node) return null
  const port = (node.ports || []).find((p) => p.name === hit.portName)
  if (!port || typeof port.expr !== 'string') return null
  const net = port.expr.trim()
  if (!net) return null
  const wantOutput = hit.kind === 'port_in'
  const siblings = flowSiblings(hit.nodeId)
  for (const sib of siblings) {
    for (const sp of sib.ports || []) {
      if (typeof sp.expr !== 'string' || sp.expr.trim() !== net) continue
      const matchesDir = wantOutput
        ? sp.dir === 'output' || sp.dir === 'inout'
        : sp.dir === 'input'
      if (!matchesDir) continue
      const peerId = wantOutput
        ? `bf-out:${sib.id}:${sp.name}`
        : `bf-in:${sib.id}:${sp.name}`
      const el = _svgEl.querySelector(`[data-bf-id="${peerId}"]`)
      if (el) return el
    }
  }
  // Fall back to the scope-boundary anchor.
  const scopeNode = store.nodesById.get(store.flowScopeId)
  if (scopeNode) {
    const anchorDir = wantOutput ? 'input' : 'output'
    const matchInout = !wantOutput // outputs/inout both drive scope_out
    if (
      (scopeNode.ports || []).some(
        (p) =>
          p.name === net &&
          (p.dir === anchorDir || (matchInout && p.dir === 'inout')),
      )
    ) {
      const anchorId = wantOutput ? `_in_${net}` : `_out_${net}`
      return _svgEl.querySelector(`[data-node-id="${anchorId}"]`)
    }
  }
  return null
}

// Smooth-ish pan that centres ``el``'s bbox in the host. Reuses
// the existing transform pipeline (no extra animation lib) and
// keeps the current zoom level — only translates.
function panToElement(el) {
  if (!el || !_svgEl || !svgHostEl.value) return
  let bb
  try {
    bb = el.getBBox()
  } catch {
    return
  }
  if (!bb || !bb.width || !bb.height) return
  const rect = svgHostEl.value.getBoundingClientRect()
  const scale = transform.value.scale
  transform.value = {
    scale,
    x: rect.width / 2 - (bb.x + bb.width / 2) * scale,
    y: rect.height / 2 - (bb.y + bb.height / 2) * scale,
  }
  applyTransform()
}

function onClick(e) {
  // Block-flow mode click contract — matches hier-view: click =
  // focus, button = navigate. Descending into a block is owned by
  // NodeDetail's "Descend" button (which routes through
  // store.descend(id), view-mode-aware).
  //   - port cell  → pan view to the peer + highlight the
  //                  connecting edge(s) for that port
  //   - centre     → select the block (populates NodeDetail)
  // ``HREF`` on the HTML-table cell makes viz.js wrap the cell in
  // an ``<a>`` with a relative href — without preventDefault the
  // browser would try to navigate to ``bf-in:...``.
  // AXI aggregate badge → jump to the AXI Performance tab. The badge
  // (painted by the axi-perf overlay) carries ``data-axi-open=<nodeId>``
  // and re-enables pointer events, so it intercepts the click before
  // node selection. Also select the node so context carries over.
  const axiBadge = e.target.closest('[data-axi-open]')
  if (axiBadge) {
    e.preventDefault()
    const nodeId = axiBadge.getAttribute('data-axi-open')
    if (nodeId) store.select(nodeId)
    store.setActiveTab('axi-perf')
    return
  }
  const bfHit = blockFlowHitFromEvent(e)
  // Reset any previous port-edge highlight on every click so it
  // doesn't accumulate as the user clicks around.
  clearEdgeHighlight()
  if (bfHit) {
    e.preventDefault()
    if (bfHit.kind === 'block_center') {
      store.select(bfHit.nodeId)
      const node = store.nodesById.get(bfHit.nodeId)
      if (node) hub.notifyClick(node)
      return
    }
    highlightEdgesForPort(bfHit.nodeId, bfHit.portName)
    // Pan to the centre of the connecting edge — keeps both
    // endpoints visible in the same shot, which is more useful
    // than centring on just the peer port. ``getBBox`` on the
    // edge group covers the path + arrowhead. Fall back to the
    // peer-port pan when no edge is incident (e.g. an
    // unconnected port within the current scope, or a port whose
    // only connection is via the scope-boundary anchor).
    const edgeEl = _svgEl?.querySelector('g.edge[data-rb-edge-highlighted]')
    if (edgeEl) {
      panToElement(edgeEl)
    } else {
      const peerEl = findFlowPeerSvgEl(bfHit)
      if (peerEl) panToElement(peerEl)
    }
    return
  }
  // Hier-view contract: node selects, edge selects.
  const nodeHit = nodeFromEvent(e)
  if (nodeHit) {
    store.select(nodeHit.id)
    hub.notifyClick(nodeHit.node)
    return
  }
  const edgeHit = edgeFromEvent(e)
  if (edgeHit) {
    store.selectEdge(edgeHit.from, edgeHit.to)
    return
  }
  // Background click — clear any selection so NodeDetail / EdgeDetail
  // collapse. Matches conventional graph-tool behaviour (clicking
  // empty canvas deselects).
  store.clearSelection()
}

function onContextMenu(e) {
  // Right-click asks the editor to open the source location. When
  // the hub is up, we send an ``open_source`` request through the
  // wire — nvim's RtlBuddyOpen handler picks it up and jumps in
  // place, no OS round-trip. With the hub offline,
  // ``requestOpenSource`` falls back to dispatching ``node.link``
  // (rtlbuddy://) through the OS so the action is never a no-op.
  const bfHit = blockFlowHitFromEvent(e)
  if (bfHit) {
    e.preventDefault()
    const node = store.nodesById.get(bfHit.nodeId)
    if (!node) return
    if (bfHit.kind === 'block_center') {
      store.select(bfHit.nodeId)
      hub.requestOpenSource(node)
      return
    }
    // port_in / port_out: prefer the port's own anchor (line/col)
    // over the module's source location so the editor jumps to
    // the port declaration, not the module header.
    const port = (node.ports || []).find((p) => p.name === bfHit.portName)
    if (port && port.anchor && node.source) {
      hub.requestOpenSource({
        source: {
          file: node.source.file,
          start_line: typeof port.anchor.line === 'number' ? port.anchor.line : 1,
          start_column: typeof port.anchor.col === 'number' ? port.anchor.col : 1,
        },
        link: node.link,
      })
    } else {
      hub.requestOpenSource(node)
    }
    return
  }
  const hit = nodeFromEvent(e)
  if (!hit) return
  e.preventDefault()
  store.select(hit.id)
  hub.requestOpenSource(hit.node)
}

// --- pan / zoom -----------------------------------------------------------
//
// Hand-rolled because the dependency budget for the standalone
// HTML is tight; viz.js + vue + pinia already dominate. Wheel
// zooms about the cursor; click-drag pans.

let dragStart = null

function onWheel(e) {
  e.preventDefault()
  const rect = svgHostEl.value.getBoundingClientRect()
  const cx = e.clientX - rect.left
  const cy = e.clientY - rect.top
  const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1
  const next = transform.value.scale * factor
  const clamped = Math.max(0.1, Math.min(10, next))
  const ratio = clamped / transform.value.scale
  transform.value = {
    scale: clamped,
    x: cx - (cx - transform.value.x) * ratio,
    y: cy - (cy - transform.value.y) * ratio,
  }
  applyTransform()
}

function onMouseDown(e) {
  if (e.button !== 0) return
  dragStart = {
    x: e.clientX,
    y: e.clientY,
    tx: transform.value.x,
    ty: transform.value.y,
  }
}

function onMouseMove(e) {
  if (!dragStart) return
  transform.value = {
    ...transform.value,
    x: dragStart.tx + (e.clientX - dragStart.x),
    y: dragStart.ty + (e.clientY - dragStart.y),
  }
  applyTransform()
}

function onMouseUp() {
  dragStart = null
}

function applyTransform() {
  if (!_svgEl) return
  const root = _svgEl.querySelector('g')
  if (!root) return
  const { x, y, scale } = transform.value
  root.setAttribute(
    'transform',
    `translate(${x},${y}) scale(${scale})`,
  )
}

function zoomIn() {
  transform.value = { ...transform.value, scale: transform.value.scale * 1.2 }
  applyTransform()
}

function zoomOut() {
  transform.value = {
    ...transform.value,
    scale: Math.max(0.1, transform.value.scale / 1.2),
  }
  applyTransform()
}

function resetView() {
  transform.value = { x: 0, y: 0, scale: 1 }
  applyTransform()
}

// Fit the rendered graph tightly into the visible canvas. viz.js's
// default viewBox already does an aspect-fit, but it leaves a lot of
// dead space when the graph's aspect ratio differs from the canvas's
// (common for tall hierarchies in wide windows, or vice versa).
// This drops viewBox so user-space units map 1:1 to host pixels, then
// uses the existing g-transform pipeline to scale and centre the
// content bbox into the host rect.
function fitToWindow() {
  if (!_svgEl || !svgHostEl.value) return
  const root = _svgEl.querySelector('g')
  if (!root) return
  _svgEl.removeAttribute('viewBox')
  _svgEl.removeAttribute('width')
  _svgEl.removeAttribute('height')
  // Vue's scoped CSS (`.svg-host > svg[data-v-…]`) doesn't match
  // because the SVG is injected via innerHTML and never gets the
  // data-v attribute; we relied on viz.js's intrinsic width/height
  // attrs for sizing. With those stripped we must pin the size
  // ourselves or the SVG collapses to the 300×150 default.
  _svgEl.style.width = '100%'
  _svgEl.style.height = '100%'
  // Strip any prior transform so getBBox returns the content's
  // bbox in its own coord system; the new transform we apply below
  // is what positions it.
  root.removeAttribute('transform')
  const bb = root.getBBox()
  const rect = svgHostEl.value.getBoundingClientRect()
  if (!bb.width || !bb.height || !rect.width || !rect.height) {
    applyTransform()
    return
  }
  // Capped: see FIT_SCALE_MAX. An uncapped aspect-fit turns a two-node
  // scope into a billboard the moment the user descends.
  const scale = Math.min(
    rect.width / bb.width,
    rect.height / bb.height,
    FIT_SCALE_MAX,
  )
  transform.value = {
    scale,
    x: (rect.width - bb.width * scale) / 2 - bb.x * scale,
    y: (rect.height - bb.height * scale) / 2 - bb.y * scale,
  }
  applyTransform()
}

function onDoubleClick(e) {
  // Double-click = descend into the clicked node. Skips edges /
  // block-flow port cells (those have their own single-click
  // behaviour we don't want to override). When the node is a leaf
  // ``store.descend`` is a no-op so the action degrades safely.
  const nodeHit = nodeFromEvent(e)
  if (!nodeHit) return
  e.preventDefault()
  store.select(nodeHit.id)
  store.descend(nodeHit.id)
}

// Capture module reads the live ``_svgEl`` through this getter — viz.js
// rewrites the host's innerHTML on every renderSvg(), so the SVG node
// identity moves; a function dodges that staleness.
const _captureSvgProvider = () => _svgEl

onMounted(() => {
  if (svgHostEl.value) {
    svgHostEl.value.addEventListener('click', onClick)
    svgHostEl.value.addEventListener('dblclick', onDoubleClick)
    svgHostEl.value.addEventListener('contextmenu', onContextMenu)
    svgHostEl.value.addEventListener('wheel', onWheel, { passive: false })
    svgHostEl.value.addEventListener('mousedown', onMouseDown)
  }
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
  registerSvgProvider(_captureSvgProvider)
  renderSvg()
})
onBeforeUnmount(() => {
  if (svgHostEl.value) {
    svgHostEl.value.removeEventListener('click', onClick)
    svgHostEl.value.removeEventListener('dblclick', onDoubleClick)
    svgHostEl.value.removeEventListener('contextmenu', onContextMenu)
    svgHostEl.value.removeEventListener('wheel', onWheel)
    svgHostEl.value.removeEventListener('mousedown', onMouseDown)
  }
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
  unregisterSvgProvider(_captureSvgProvider)
})
</script>

<style scoped>
.graph-canvas {
  flex: 1;
  position: relative;
  overflow: hidden;
  background: var(--bg);
}
.canvas-tabs {
  position: absolute;
  top: 0.5rem;
  left: 0.5rem;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  z-index: 10;
  font-size: 0.85rem;
}
.canvas-tab {
  border: 1px solid var(--line-strong);
  background: var(--panel);
  color: var(--fg);
  padding: 0.25rem 0.6rem;
  cursor: pointer;
  border-radius: var(--radius-2);
  font-family: inherit;
}
.canvas-tab.active {
  background: var(--accent);
  color: var(--accent-contrast);
  border-color: var(--accent);
}
.scope-breadcrumb {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.15rem;
  font-size: 0.75rem;
  margin-left: 0.5rem;
  color: var(--fg-muted);
}
.scope-label {
  margin-right: 0.15rem;
}
.crumb-sep {
  color: var(--fg-faint);
  padding: 0 0.05rem;
}
.crumb {
  font: inherit;
  background: var(--panel-2);
  border: 1px solid transparent;
  color: var(--fg);
  padding: 0.05rem 0.35rem;
  border-radius: var(--radius-1);
  cursor: pointer;
  font-family: var(--font-mono);
}
.crumb:hover:not(:disabled) {
  background: var(--line);
  border-color: var(--line-strong);
}
/* ``.current`` is a disabled button, so app.css's shared disabled
   treatment paints it; the accent border is what says "you are here". */
.crumb.current {
  border-color: var(--accent);
  cursor: default;
}
.graph-toolbar {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  display: flex;
  gap: 0.25rem;
  z-index: 10;
}
.graph-toolbar button {
  border: 1px solid var(--line-strong);
  background: var(--panel);
  color: var(--fg);
  padding: 0.25rem 0.5rem;
  cursor: pointer;
  border-radius: var(--radius-2);
}
.svg-host {
  /* Start below the floating tab / toolbar row: pan-zoom's fit
     measures this element, so reserving the strip here keeps the
     sheet's top edge from ever landing under the buttons. */
  width: 100%;
  height: calc(100% - 3rem);
  margin-top: 3rem;
  cursor: grab;
}
.svg-host:active { cursor: grabbing; }

/* Diagnostics badge overlay. Positioned at the same origin as the
   .svg-host (both fill .graph-canvas, which is the positioning
   parent). The layer itself is transparent to mouse events so it
   doesn't intercept pan drags — each <NodeBadge> child re-enables
   pointer events on itself. */
.badge-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  /* Keep the layer above the SVG but below the floating toolbars
     (.canvas-tabs / .graph-toolbar both run z-index 10). */
  z-index: 5;
}
.badge-layer > * {
  pointer-events: auto;
}
.svg-host > svg {
  width: 100%;
  height: 100%;
}
/* Clickability cue. ``g.node`` / ``g.edge`` are the Graphviz-
   emitted groups we stamp ``data-node-id`` / ``data-edge-from``
   onto in renderSvg — hover them and the cursor goes from
   "grab" (pan) to "pointer" (clickable). :deep() reaches inside
   the injected SVG which isn't scoped to this component. */
.svg-host :deep(g.node),
.svg-host :deep(g.edge),
.svg-host :deep(g.cluster),
.svg-host :deep([data-bf-id]) {
  cursor: pointer;
}
/* Cluster borders are ``fill:none`` on every DOT path (producer and
   graphToDot both emit ``style="rounded"``), and SVG skips pointer
   events on unpainted fill — so a container was only clickable on
   its hairline border, making "select the wrapper, then Descend"
   (and dblclick-descend) effectively impossible. ``pointer-events:
   all`` opts the full geometry in regardless of paint. Children are
   emitted after their cluster's backdrop, so they still win
   hit-testing wherever they overlap it. */
.svg-host :deep(g.cluster > path),
.svg-host :deep(g.cluster > polygon) {
  pointer-events: all;
}

/* Port-edge highlight. Painted by ``highlightEdgesForPort`` when
   the user clicks a port cell in block-flow view — marks every
   edge incident on the clicked port (typically one, occasionally
   two for inout / fanout) with ``data-rb-edge-highlighted``.
   Amber so it doesn't collide with the blue selected-node accent
   or the existing edge palette. */
.svg-host :deep([data-rb-edge-highlighted]) path {
  stroke: var(--warn) !important;
  stroke-width: 2.5 !important;
}
.svg-host :deep([data-rb-edge-highlighted]) polygon {
  fill: var(--warn) !important;
  stroke: var(--warn) !important;
}

/* Selected-node accent. Painted by ``applySelectionHighlight``,
   which stamps ``data-rb-selected`` on the SVG group whose
   ``data-node-id`` matches the store's selection. Covers hier-view
   ``g.node`` polygons, ``g.cluster`` backdrops (a container is
   selectable in its own right) and flow-view HTML-table outer tables.

   A single thin outline was easy to miss against the range of fills
   the overlays paint, so the treatment is two-part: a 2.5px accent
   stroke, plus a two-stop accent halo (tight + soft) that reads as a
   glow rather than as a second border. Both stops are ``color-mix``ed
   off ``--accent`` so the highlight follows a theme flip. Static —
   nothing here animates. */
.svg-host :deep([data-rb-selected]) > polygon,
.svg-host :deep([data-rb-selected]) > path {
  stroke: var(--accent);
  stroke-width: 2.5 !important;
  filter:
    drop-shadow(0 0 3px color-mix(in srgb, var(--accent) 70%, transparent))
    drop-shadow(0 0 9px color-mix(in srgb, var(--accent) 35%, transparent));
}
/* For HTML-table labels (block-flow boxes, CDC bridge grids), the
   outer table is a nested polygon — accent that one too. */
.svg-host :deep([data-rb-selected] polygon:first-of-type) {
  stroke: var(--accent);
  stroke-width: 2.5 !important;
}
/* A selected cluster's own label is the only text that names it, so
   promote it to the accent as well — the backdrop stroke alone is far
   from the label on a large container. */
.svg-host :deep(g.cluster[data-rb-selected] > text) {
  fill: var(--accent);
  font-weight: 700;
}

/* Theme safety net for the *producer-supplied* layout DOT — and ONLY
   for it. ``pickDot`` prefers ``graph.layout.dot`` when the producer
   embedded one, and that string was generated by the Python renderer
   with the light palette baked in; this repo cannot rebuild it, so
   these rules re-tint the parts that would otherwise be unreadable in
   dark. Graphviz emits them as presentation attributes, which a
   stylesheet outranks.

   The ``.producer-dot`` gate is load-bearing, not decoration.
   ``renderSvg`` sets that class only when it fed viz.js the producer's
   DOT. Ungated, the same rules apply to the in-JS builders' output,
   where "a stylesheet outranks a presentation attribute" is exactly
   the problem: ``graphToDot`` / ``buildBlockFlowDot`` resolve tokens at
   build time and re-run on a theme flip, so their CDC-red edge labels
   and arrowheads and the ``?`` unconstrained-source marker are already
   correct for the active theme — and a blanket ``fill: var(--fg)``
   silently repaints them body-text grey.
   Overlay paint is inline style, so it still wins over all of this. */
.svg-host.producer-dot :deep(text) {
  fill: var(--fg);
}
/* ``--line-strong`` is a surface-divider tier; as a 1px polyline on the
   open canvas it was the faintest thing in the render. Edges carry the
   connectivity, so they get the readable ``--fg-muted`` text tier —
   matching what ``graphToDot`` / ``buildBlockFlowDot`` now bake in, so
   the producer and in-JS renders agree on edge weight. Arrowheads are
   filled polygons and need both properties. */
.svg-host.producer-dot :deep(g.edge path) {
  stroke: var(--fg-muted);
}
.svg-host.producer-dot :deep(g.edge polygon) {
  fill: var(--fg-muted);
  stroke: var(--fg-muted);
}
.svg-host.producer-dot :deep(g.cluster > polygon) {
  fill: none;
  stroke: var(--fg-faint);
}
</style>
