<template>
  <div class="app">
    <header class="app-header">
      <h1>rtl-buddy-view</h1>
      <div class="header-status">
        <span class="design-name" v-if="store.graph">{{ store.graph.top }}</span>
        <HubStatus />
      </div>
    </header>
    <div class="app-body" v-if="store.status === 'ready'">
      <aside class="sidebar">
        <OverlayPanel />
        <NodeDetail v-if="!store.selectedEdgeObj" />
        <EdgeDetail v-if="store.selectedEdgeObj" />
        <AxiPerfPane />
        <DiagnosticsPanel />
      </aside>
      <GraphCanvas />
    </div>
    <div class="empty-state" v-else-if="store.status === 'idle'">
      <h2>Load a view.json</h2>
      <p>
        Drop a <code>view.json</code> file here, or pass
        <code>?view=path/to/view.json</code> in the URL.
      </p>
      <input type="file" accept=".json,application/json" @change="onPickFile" />
    </div>
    <div class="loading" v-else-if="store.status === 'loading'">Loading…</div>
    <div class="error" v-else-if="store.status === 'error'">
      <h2>Could not load this view</h2>
      <pre>{{ store.error }}</pre>
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

import { onMounted, onBeforeUnmount } from 'vue'
import { useViewerStore } from './store.js'
import GraphCanvas from './components/GraphCanvas.vue'
import OverlayPanel from './components/OverlayPanel.vue'
import NodeDetail from './components/NodeDetail.vue'
import EdgeDetail from './components/EdgeDetail.vue'
import AxiPerfPane from './components/AxiPerfPane.vue'
import HubStatus from './components/HubStatus.vue'
import ToastHost from './components/ToastHost.vue'
import DiagnosticsPanel from './components/DiagnosticsPanel.vue'
import { initHub } from './composables/useHub.js'

const store = useViewerStore()

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
.header-status { display: flex; gap: 1rem; align-items: center; }
.design-name { font-family: ui-monospace, Menlo, monospace; color: #475569; }
.app-body { display: flex; height: calc(100vh - 48px); }
.sidebar {
  width: 280px;
  border-right: 1px solid #e5e7eb;
  padding: 0.5rem;
  overflow: auto;
  background: #ffffff;
}
.empty-state, .loading, .error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: calc(100vh - 48px);
  text-align: center;
  padding: 2rem;
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
</style>
