<template>
  <main class="graph-canvas" ref="canvasEl">
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
import { layoutGraph } from '../layout/viz.js'
import { applyOverlays } from '../overlays/index.js'
import { useHub } from '../composables/useHub.js'

const store = useViewerStore()
const hub = useHub()
const svgHostEl = ref(null)
const canvasEl = ref(null)
const transform = ref({ x: 0, y: 0, scale: 1 })
let _svgEl = null

const graph = computed(() => store.displayGraph)

async function renderSvg() {
  if (!graph.value || !svgHostEl.value) return
  let svgText
  try {
    svgText = await layoutGraph(graph.value)
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
watch(
  () => store.enabledOverlays,
  () => {
    if (_svgEl && graph.value) {
      applyOverlays(_svgEl, graph.value, store.enabledOverlays)
    }
  },
)

function nodeFromEvent(e) {
  const group = e.target.closest('g.node, [data-node-id]')
  if (!group) return null
  const id = group.getAttribute('data-node-id')
  if (!id) return null
  const node = store.nodesById.get(id)
  return node ? { id, node } : null
}

function onClick(e) {
  // Left-click: select only. Source-editor dispatch is on right-click.
  const hit = nodeFromEvent(e)
  if (!hit) return
  store.select(hit.id)
  hub.notifyClick(hit.node)
}

function onContextMenu(e) {
  // Right-click: select + dispatch ``node.link`` to the OS so the
  // registered handler (rtlbuddy:// or vscode://) opens the source.
  // Phase 10d's hub will intercept this before the URI dispatch.
  const hit = nodeFromEvent(e)
  if (!hit) return
  e.preventDefault()
  store.select(hit.id)
  if (hit.node.link) {
    window.open(hit.node.link, '_blank')
  }
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
</style>
