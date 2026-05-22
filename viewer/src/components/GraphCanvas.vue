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
      <nav v-if="store.viewMode === 'flow' && breadcrumb.length > 0" class="flow-breadcrumb" aria-label="Block-flow scope">
        <span class="flow-scope-label">scope:</span>
        <template v-for="(crumb, i) in breadcrumb" :key="crumb.path">
          <span v-if="i > 0" class="crumb-sep" aria-hidden="true">/</span>
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

// Clickable breadcrumb path for the flow-view scope. Each segment
// is a button that jumps the scope to that prefix; the trailing
// (current) segment is rendered disabled. With one segment (the
// design top) only the top crumb is shown.
const breadcrumb = computed(() => {
  if (store.viewMode !== 'flow' || !store.graph || !store.flowScopeId) return []
  const segments = store.flowScopeId.split('.')
  return segments.map((label, i) => {
    const path = segments.slice(0, i + 1).join('.')
    return { label, path, current: i === segments.length - 1 }
  })
})

function jumpToScope(path) {
  if (!path || !store.graph) return
  // Top of design → clear the explicit scope so it falls back to
  // ``graph.top`` via the getter.
  if (path === store.graph.top) {
    store.flowScope = null
  } else {
    store.flowScope = path
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
  applyOverlays(_svgEl, graph.value, store.enabledOverlays)
  applySelectionHighlight(store.selection)
  // Defer to next frame so flex layout has settled and the host
  // rect is its final size before we compute the fit scale.
  requestAnimationFrame(fitToWindow)
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
  for (const el of _svgEl.querySelectorAll(`[data-node-id="${selectedId}"]`)) {
    el.setAttribute('data-rb-selected', 'true')
  }
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
.flow-breadcrumb {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.15rem;
  font-size: 0.75rem;
  margin-left: 0.5rem;
  color: #64748b;
}
.flow-scope-label {
  margin-right: 0.15rem;
}
.crumb-sep {
  color: #94a3b8;
  padding: 0 0.05rem;
}
.crumb {
  font: inherit;
  background: #f1f5f9;
  border: 1px solid transparent;
  color: #1e293b;
  padding: 0.05rem 0.35rem;
  border-radius: 3px;
  cursor: pointer;
  font-family: ui-monospace, Menlo, monospace;
}
.crumb:hover:not(:disabled) {
  background: #e2e8f0;
  border-color: #cbd5e1;
}
.crumb.current {
  background: #1e293b;
  color: #ffffff;
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
.svg-host :deep(g.cluster),
.svg-host :deep([data-bf-id]) {
  cursor: pointer;
}

/* Port-edge highlight. Painted by ``highlightEdgesForPort`` when
   the user clicks a port cell in block-flow view — marks every
   edge incident on the clicked port (typically one, occasionally
   two for inout / fanout) with ``data-rb-edge-highlighted``.
   Amber so it doesn't collide with the blue selected-node accent
   or the existing edge palette. */
.svg-host :deep([data-rb-edge-highlighted]) path {
  stroke: #f59e0b !important;
  stroke-width: 2.5 !important;
}
.svg-host :deep([data-rb-edge-highlighted]) polygon {
  fill: #f59e0b !important;
  stroke: #f59e0b !important;
}

/* Selected-node accent. Painted by ``applySelectionHighlight``,
   which stamps ``data-rb-selected`` on the SVG group whose
   ``data-node-id`` matches the store's selection. Covers both
   hier-view ``g.node`` polygons + flow-view HTML-table outer
   tables. The thicker stroke is more visible across the wide
   range of node fill colours overlays may apply. */
.svg-host :deep([data-rb-selected]) > polygon,
.svg-host :deep([data-rb-selected]) > path {
  stroke: #2563eb;
  stroke-width: 3 !important;
}
/* For HTML-table labels (block-flow boxes), the outer table is a
   nested polygon — accent that one too. */
.svg-host :deep([data-rb-selected] polygon:first-of-type) {
  stroke: #2563eb;
  stroke-width: 3 !important;
}
</style>
