<template>
  <div class="app">
    <header class="app-header">
      <h1>rtl-buddy-view</h1>
      <nav class="app-tabs" v-if="store.status === 'ready'">
        <button
          type="button"
          :class="{ active: store.activeTab === 'hierarchy' }"
          @click="store.setActiveTab('hierarchy')"
        >Hierarchy</button>
        <button
          type="button"
          :class="{ active: store.activeTab === 'axi-perf' }"
          :disabled="!hasAxiPerf"
          :title="hasAxiPerf ? '' : 'Load a view.json with --overlay axi-perf=...'"
          @click="store.setActiveTab('axi-perf')"
        >AXI Performance</button>
      </nav>
      <div class="header-status">
        <span class="design-name" v-if="store.graph">{{ store.graph.top }}</span>
        <ModelPicker />
        <span
          v-if="hub.serverVersion.value"
          class="server-version"
          :title="`rtl-buddy ${hub.serverVersion.value}`"
        >rtl-buddy <code>{{ shortVersion }}</code></span>
        <span
          v-if="bundleHash"
          class="server-version bundle-hash"
          :title="`SPA bundle index-${bundleHash}.js`"
        >spa <code>{{ bundleHash }}</code></span>
        <HubStatus />
      </div>
    </header>
    <!-- Keep the body mounted across status transitions so a model
         switch (status='ready' → 'loading' → 'ready') doesn't tear
         down the canvas + sidebar mid-flight. The loading overlay
         renders on top instead, giving the user clear "something is
         happening" feedback while preserving the previous view as a
         visual anchor. -->
    <div
      class="app-body"
      v-if="store.graph && (store.status !== 'ready' || store.activeTab === 'hierarchy')"
    >
      <aside class="sidebar" :style="{ width: sidebarWidth + 'px' }">
        <CollapsiblePanel title="Overlays" persist-key="overlays">
          <OverlayPanel />
        </CollapsiblePanel>
        <CollapsiblePanel
          v-if="!store.selectedEdgeObj"
          title="Node detail"
          persist-key="node-detail"
          :badge="store.selection ? '●' : null"
        >
          <NodeDetail />
        </CollapsiblePanel>
        <CollapsiblePanel
          v-else
          title="Edge detail"
          persist-key="edge-detail"
          :badge="'●'"
        >
          <EdgeDetail />
        </CollapsiblePanel>
        <!-- Diagnostics default-expanded so dynamically-arriving
             items (hub-pushed after page load) are immediately
             visible — auto-collapse based on initial count would
             swallow late-arriving updates and broke the e2e
             ``diagnostics_set`` tests. -->
        <CollapsiblePanel
          title="Diagnostics"
          persist-key="diagnostics"
          :badge="diagnosticsCount || null"
        >
          <DiagnosticsPanel />
        </CollapsiblePanel>
      </aside>
      <div
        class="sidebar-resizer"
        role="separator"
        aria-orientation="vertical"
        title="Drag to resize"
        @mousedown.prevent="startResize"
      ></div>
      <div class="canvas-wrap">
        <GraphCanvas />
        <div v-if="store.status === 'loading'" class="loading-overlay">
          <div class="spinner" aria-hidden="true"></div>
          <span>Loading…</span>
        </div>
      </div>
    </div>
    <div
      class="app-body axi-tab"
      v-else-if="store.status === 'ready' && store.activeTab === 'axi-perf'"
    >
      <AxiPerfView />
    </div>
    <div class="empty-state" v-else-if="store.status === 'idle'">
      <h2>Load a view.json</h2>
      <p>
        Drop a <code>view.json</code> file here, or pass
        <code>?view=path/to/view.json</code> in the URL.
      </p>
      <p class="empty-hint">
        Or start the hub:
        <code>rb hub start --serve-viewer --model &lt;model_name&gt;</code><br />
        then open <code>http://127.0.0.1:&lt;http_port&gt;/</code>.
        The model picker in the header lets you switch between models
        without restarting.
      </p>
      <input type="file" accept=".json,application/json" @change="onPickFile" />
    </div>
    <div class="loading" v-else-if="store.status === 'loading'">
      <div class="spinner" aria-hidden="true"></div>
      <span>Loading…</span>
    </div>
    <div class="error" v-else-if="store.status === 'error'">
      <h2>Could not load this view</h2>
      <p class="error-status" v-if="store.errorMeta && store.errorMeta.status">
        The hub responded HTTP {{ store.errorMeta.status }}.
      </p>
      <pre>{{ store.error }}</pre>
      <div class="error-reasons">
        <p class="error-reasons-title">Possible reasons:</p>
        <ul>
          <li>
            The test or model name isn't unique across suites — pick the exact
            row from the header selector (it disambiguates by testbench + DUT).
          </li>
          <li>
            The name doesn't exist in any <code>tests.yaml</code> /
            <code>models.yaml</code> the hub discovered.
          </li>
          <li>
            The view build failed (filelist or parse error). Check the hub log
            at <code>artefacts/hier/&lt;model&gt;/…/hier.log</code>.
          </li>
          <li>The hub isn't reachable — not started, restarted, or wrong port.</li>
        </ul>
        <p class="error-hint">
          Pick a different view from the header selector — no reload needed.
        </p>
      </div>
    </div>
    <ToastHost />
  </div>
</template>

<script setup>
// Top-level App shell. The state machine is owned by the Pinia
// store; this component is a presentational switch over
// store.status (``idle`` / ``loading`` / ``ready`` / ``error``).
//
// Drag-and-drop is wired at the document level so users can drop
// onto any part of the viewer without targeting a specific zone.

import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useViewerStore } from './store.js'
import GraphCanvas from './components/GraphCanvas.vue'
import OverlayPanel from './components/OverlayPanel.vue'
import NodeDetail from './components/NodeDetail.vue'
import EdgeDetail from './components/EdgeDetail.vue'
import AxiPerfView from './components/AxiPerfView.vue'
import HubStatus from './components/HubStatus.vue'
import ModelPicker from './components/ModelPicker.vue'
import ToastHost from './components/ToastHost.vue'
import DiagnosticsPanel from './components/DiagnosticsPanel.vue'
import CollapsiblePanel from './components/CollapsiblePanel.vue'
import { initHub, useHub } from './composables/useHub.js'
import { initEventSync } from './composables/useEventSync.js'

const store = useViewerStore()
const hub = useHub()

// --- Resizable sidebar (drag the divider between sidebar and canvas).
const SIDEBAR_MIN = 200
const SIDEBAR_MAX = 640
function loadSidebarWidth() {
  try {
    const v = Number(localStorage.getItem('rb-sidebar-width'))
    if (Number.isFinite(v) && v >= SIDEBAR_MIN && v <= SIDEBAR_MAX) return v
  } catch {
    /* localStorage unavailable — fall through to the default */
  }
  return 280
}
const sidebarWidth = ref(loadSidebarWidth())
function startResize(e) {
  const startX = e.clientX
  const startW = sidebarWidth.value
  const onMove = (ev) => {
    const w = startW + (ev.clientX - startX)
    sidebarWidth.value = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, w))
  }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    try {
      localStorage.setItem('rb-sidebar-width', String(sidebarWidth.value))
    } catch {
      /* best-effort persistence */
    }
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

const hasAxiPerf = computed(
  () =>
    Array.isArray(store.graph?.overlays_present) &&
    store.graph.overlays_present.includes('axi-perf'),
)

const diagnosticsCount = computed(() => {
  const by = store.diagnosticsBySource || {}
  let n = 0
  for (const items of Object.values(by)) {
    if (Array.isArray(items)) n += items.length
  }
  return n
})

// Truncate the long ``rtl-buddy`` build string to its leading
// semver portion for the header chip; full version lives in the
// tooltip.
const shortVersion = computed(() => {
  const raw = hub.serverVersion.value || ''
  const m = raw.match(/^[0-9]+\.[0-9]+\.[0-9]+(?:\.[a-z0-9]+)?/i)
  return m ? m[0] : raw
})

// Extract the SPA bundle hash from the loaded ``index-<hash>.js``
// filename so the user can confirm which build they're on (handy
// when an editable-install hub re-stages the bundle but the
// browser tab might still be on the old one). Empty under the
// Vite dev server (no hashed asset) or the offline embed flow —
// both surface as "no chip".
function readBundleHash() {
  if (typeof document === 'undefined') return ''
  for (const s of document.querySelectorAll('script[src]')) {
    const m = s.src.match(/index-([\w-]+)\.js/)
    if (m) return m[1]
  }
  return ''
}
const bundleHash = readBundleHash()

function onPickFile(event) {
  const file = event.target.files && event.target.files[0]
  if (file) store.loadFromFile(file)
}

function onDragOver(e) {
  e.preventDefault()
}

function onDrop(e) {
  e.preventDefault()
  const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]
  if (file) store.loadFromFile(file)
}

onMounted(() => {
  document.addEventListener('dragover', onDragOver)
  document.addEventListener('drop', onDrop)
  // Phase 10d: kick the hub composable. Same-origin /ws — the hub
  // injects window.__RTL_BUDDY_HUB__ when it serves the bundle.
  initHub({ store })
  // Phase 3 (axi-profiler#16): bidirectional state sync with the
  // marimo notebook over /api/events/sync.
  initEventSync({ store })
})
onBeforeUnmount(() => {
  document.removeEventListener('dragover', onDragOver)
  document.removeEventListener('drop', onDrop)
})
</script>

<style>
:root {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  color: #1f2937;
  background: #f8fafc;
}
body, html, .app { margin: 0; height: 100%; }
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.5rem 1rem;
  background: #ffffff;
  border-bottom: 1px solid #e5e7eb;
}
.app-header h1 { font-size: 1rem; margin: 0; font-weight: 600; }
.app-tabs { display: flex; gap: 0; }
.app-tabs button {
  background: transparent;
  border: 1px solid transparent;
  border-bottom: 2px solid transparent;
  padding: 0.35rem 0.75rem;
  font-size: 0.85rem;
  color: #475569;
  cursor: pointer;
}
.app-tabs button:hover:not(:disabled) {
  color: #1e293b;
}
.app-tabs button.active {
  color: #4f46e5;
  border-bottom-color: #4f46e5;
  font-weight: 600;
}
.app-tabs button:disabled {
  color: #cbd5e1;
  cursor: not-allowed;
}
.header-status { display: flex; gap: 1rem; align-items: center; }
.design-name { font-family: ui-monospace, Menlo, monospace; color: #475569; }
.server-version {
  font-size: 0.72rem;
  color: #64748b;
  white-space: nowrap;
}
.server-version code {
  font-family: ui-monospace, Menlo, monospace;
  background: #f1f5f9;
  padding: 0.05rem 0.35rem;
  border-radius: 3px;
  margin-left: 0.25rem;
}
.app-body { display: flex; height: calc(100vh - 48px); }
.app-body.axi-tab { display: block; overflow: auto; background: #ffffff; }
.sidebar {
  width: 280px;        /* fallback; overridden by the inline :style width */
  flex-shrink: 0;      /* honour the width in the flex row (don't squeeze) */
  border-right: 1px solid #e5e7eb;
  padding: 0;
  overflow: auto;
  background: #ffffff;
}
/* Draggable divider between the sidebar and the canvas. */
.sidebar-resizer {
  flex: 0 0 5px;
  cursor: col-resize;
  background: transparent;
  margin-left: -1px;   /* sit over the sidebar's right border */
  z-index: 2;
}
.sidebar-resizer:hover,
.sidebar-resizer:active {
  background: #93c5fd;  /* blue-300 highlight while grabbing */
}
.canvas-wrap {
  flex: 1;
  position: relative;
  display: flex;
}
.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  background: rgba(248, 250, 252, 0.78);
  backdrop-filter: blur(2px);
  font-size: 0.85rem;
  color: #475569;
  z-index: 20;
  pointer-events: all;
}
.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #cbd5e1;
  border-top-color: #1e293b;
  border-radius: 50%;
  animation: rb-spin 0.8s linear infinite;
}
@keyframes rb-spin {
  to { transform: rotate(360deg); }
}
.empty-state, .loading, .error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  height: calc(100vh - 48px);
  text-align: center;
  padding: 2rem;
}
.empty-state .empty-hint {
  font-size: 0.85rem;
  color: #475569;
  max-width: 60ch;
  line-height: 1.5;
  background: #f1f5f9;
  padding: 0.75rem 1rem;
  border-radius: 6px;
}
.empty-state code, .empty-hint code {
  font-family: ui-monospace, Menlo, monospace;
  background: #e2e8f0;
  padding: 0.05rem 0.3rem;
  border-radius: 3px;
  font-size: 0.85em;
}
.error pre {
  max-width: 60ch;
  white-space: pre-wrap;
  background: #fef2f2;
  border: 1px solid #fecaca;
  padding: 0.75rem;
  border-radius: 4px;
  color: #991b1b;
}
.error-status {
  color: #991b1b;
  font-weight: 600;
  margin: 0;
}
.error-reasons {
  max-width: 64ch;
  text-align: left;
  font-size: 0.85rem;
  color: #475569;
  background: #f1f5f9;
  padding: 0.75rem 1rem;
  border-radius: 6px;
  line-height: 1.5;
}
.error-reasons-title {
  margin: 0 0 0.4rem;
  font-weight: 600;
}
.error-reasons ul {
  margin: 0;
  padding-left: 1.2rem;
}
.error-reasons li {
  margin: 0.2rem 0;
}
.error-reasons .error-hint {
  margin: 0.6rem 0 0;
  font-style: italic;
}
.error code {
  font-family: ui-monospace, Menlo, monospace;
  background: #e2e8f0;
  padding: 0.05rem 0.3rem;
  border-radius: 3px;
  font-size: 0.85em;
}
</style>
