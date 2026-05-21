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
      <span v-if="store.viewMode === 'flow'" class="flow-scope">
        scope: <code>{{ store.flowScopeId || '(none)' }}</code>
        <button
          v-if="canAscendScope"
          type="button"
          class="flow-up"
          @click="ascendScope"
          title="Pop one level up — make this scope's parent the new flow scope"
        >↑</button>
      </span>
    </div>
    <div class="graph-toolbar">
      <button type="button" @click="zoomIn">+</button>
      <button type="button" @click="zoomOut">−</button>
      <button type="button" @click="fitToWindow">Fit</button>
      <button type="button" @click="resetView">Reset</button>
    </div>
    <div class="svg-host" ref="svgHostEl"></div>
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
import { layoutGraph, layoutDot } from '../layout/viz.js'
import { buildBlockFlowDot } from '../layout/blockFlow.js'
import { applyOverlays } from '../overlays/index.js'
import { useHub } from '../composables/useHub.js'

const store = useViewerStore()
const hub = useHub()
const svgHostEl = ref(null)
const canvasEl = ref(null)
const transform = ref({ x: 0, y: 0, scale: 1 })
let _svgEl = null

const graph = computed(() => store.displayGraph)
const flowTabTitle = computed(() =>
  store.flowScopeId
    ? `One-level signal flow under ${store.flowScopeId}`
    : 'One-level signal flow (select a node to scope)',
)
// "Up" is only meaningful when the current scope isn't already the
// design top — popping past top would lose context.
const canAscendScope = computed(() => {
  if (!store.graph) return false
  return store.flowScopeId && store.flowScopeId !== store.graph.top
})
function ascendScope() {
  if (!canAscendScope.value) return
  const cur = store.flowScopeId
  // Strip the last dot-segment to get the parent path.
  const lastDot = cur.lastIndexOf('.')
  if (lastDot < 0) {
    // Already a top-level identifier — clear selection so the
    // scope falls back to graph.top via flowScopeId getter.
    store.clearSelection()
  } else {
    store.select(cur.slice(0, lastDot))
  }
}

async function renderSvg() {
  if (!graph.value || !svgHostEl.value) return
  let svgText
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
  for (const group of _svgEl.querySelectorAll('g.cluster')) {
    const titleEl = group.querySelector('title')
    if (!titleEl) continue
    const clusterId = titleEl.textContent
    if (clusterLookup && clusterLookup[clusterId]) {
      group.setAttribute('data-node-id', clusterLookup[clusterId])
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
  applyOverlays(_svgEl, graph.value, store.enabledOverlays)
  // Defer to next frame so flex layout has settled and the host
  // rect is its final size before we compute the fit scale.
  requestAnimationFrame(fitToWindow)
}

watch(graph, renderSvg)
// Tab switch + scope drill-down trigger a full re-layout. Both
// produce a different DOT, so the SVG must be rebuilt.
watch(() => store.viewMode, renderSvg)
watch(() => store.flowScopeId, () => {
  if (store.viewMode === 'flow') renderSvg()
})
watch(
  () => store.enabledOverlays,
  () => {
    if (_svgEl && graph.value) {
      applyOverlays(_svgEl, graph.value, store.enabledOverlays)
    }
  },
)

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

function onClick(e) {
  // Left-click: nodes take precedence over edges (target.closest
  // walks up; a node's polygon and an edge's path are siblings,
  // not nested, so this is mostly belt-and-suspenders). Edge clicks
  // populate ``selectedEdge`` so EdgeDetail renders; the hub stays
  // out of the loop for edges since the v1 protocol has no
  // edge-selection envelope.
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
}

function onContextMenu(e) {
  // Right-click asks the editor to open the source location. When
  // the hub is up, we send an ``open_source`` request through the
  // wire — nvim's RtlBuddyOpen handler picks it up and jumps in
  // place, no OS round-trip. With the hub offline,
  // ``requestOpenSource`` falls back to dispatching ``node.link``
  // (rtlbuddy://) through the OS so the action is never a no-op.
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
  const scale = Math.min(rect.width / bb.width, rect.height / bb.height)
  transform.value = {
    scale,
    x: (rect.width - bb.width * scale) / 2 - bb.x * scale,
    y: (rect.height - bb.height * scale) / 2 - bb.y * scale,
  }
  applyTransform()
}

onMounted(() => {
  if (svgHostEl.value) {
    svgHostEl.value.addEventListener('click', onClick)
    svgHostEl.value.addEventListener('contextmenu', onContextMenu)
    svgHostEl.value.addEventListener('wheel', onWheel, { passive: false })
    svgHostEl.value.addEventListener('mousedown', onMouseDown)
  }
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
  renderSvg()
})
onBeforeUnmount(() => {
  if (svgHostEl.value) {
    svgHostEl.value.removeEventListener('click', onClick)
    svgHostEl.value.removeEventListener('contextmenu', onContextMenu)
    svgHostEl.value.removeEventListener('wheel', onWheel)
    svgHostEl.value.removeEventListener('mousedown', onMouseDown)
  }
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
})
</script>

<style scoped>
.graph-canvas {
  flex: 1;
  position: relative;
  overflow: hidden;
  background: #f8fafc;
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
  border: 1px solid #cbd5e1;
  background: #ffffff;
  padding: 0.25rem 0.6rem;
  cursor: pointer;
  border-radius: 4px;
  font-family: inherit;
}
.canvas-tab.active {
  background: #1e293b;
  color: #ffffff;
  border-color: #1e293b;
}
.flow-scope {
  font-size: 0.75rem;
  color: #64748b;
  margin-left: 0.5rem;
}
.flow-scope code {
  background: #f1f5f9;
  padding: 0 0.3rem;
  border-radius: 3px;
}
.flow-up {
  border: 1px solid #cbd5e1;
  background: #ffffff;
  padding: 0 0.4rem;
  cursor: pointer;
  border-radius: 4px;
  font-size: 0.75rem;
  margin-left: 0.25rem;
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
  border: 1px solid #cbd5e1;
  background: #ffffff;
  padding: 0.25rem 0.5rem;
  cursor: pointer;
  border-radius: 4px;
}
.svg-host {
  width: 100%;
  height: 100%;
  cursor: grab;
}
.svg-host:active { cursor: grabbing; }
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
.svg-host :deep(g.cluster) {
  cursor: pointer;
}
</style>
