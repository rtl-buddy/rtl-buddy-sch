<template>
  <section class="node-detail" v-if="node">
    <h3>{{ node.id }}</h3>
    <div class="nav-actions">
      <button
        type="button"
        @click="store.descend(node.id)"
        :disabled="!store.selectedHasChildren"
        :title="store.selectedHasChildren ? 'Show only this subtree' : 'Leaf node — nothing to descend into'"
      >Descend</button>
      <button
        type="button"
        @click="store.ascend()"
        :disabled="!store.rootInstancePath"
        title="Show parent scope"
      >Up</button>
      <button
        type="button"
        @click="store.goToTop()"
        :disabled="!store.rootInstancePath"
        title="Back to design top"
      >Top</button>
    </div>
    <dl>
      <dt>Module</dt><dd>{{ node.module }}</dd>
      <dt v-if="node.is_blackbox">Status</dt>
      <dd v-if="node.is_blackbox" class="blackbox">blackbox</dd>
      <template v-if="hasParameters">
        <dt>Parameters</dt>
        <dd>
          <ul>
            <li v-for="(value, key) in node.parameters" :key="key">
              <code>.{{ key }}({{ value }})</code>
            </li>
          </ul>
        </dd>
      </template>
      <template v-if="node.ports && node.ports.length">
        <dt>Ports</dt>
        <dd>
          <ul>
            <li v-for="port in node.ports" :key="port.name">
              <code>
                <span class="port-name">{{ port.name }}</span>
                <span v-if="port.dir" class="port-dir">({{ port.dir }})</span>
                <span v-if="port.expr"> ← {{ port.expr }}</span>
              </code>
            </li>
          </ul>
        </dd>
      </template>
      <template v-for="(payload, name) in node.overlays" :key="name">
        <dt>overlay: {{ name }}</dt>
        <dd><pre>{{ JSON.stringify(payload, null, 2) }}</pre></dd>
      </template>
      <template v-if="hasOpenable">
        <dt>Open</dt>
        <dd>
          <button
            type="button"
            class="open-source"
            @click="openInEditor"
            :title="openTitle"
          >Open in editor</button>
          <code class="open-target">{{ openTargetText }}</code>
        </dd>
      </template>
    </dl>
  </section>
  <section class="node-detail empty" v-else>
    <p>Click a node to see its ports, parameters, and overlay values.</p>
  </section>
</template>

<script setup>
// Per-node detail panel. Driven entirely by the store's
// selection; click handling lives in GraphCanvas.
//
// Overlay values are rendered raw as JSON — readable, doesn't
// require this component to know about every overlay's payload
// shape, and stays useful for unknown / future overlays the
// viewer doesn't have a dedicated renderer for.
import { computed } from 'vue'
import { useViewerStore } from '../store.js'
import { useHub } from '../composables/useHub.js'

const store = useViewerStore()
const hub = useHub()
const node = computed(() => store.selectedNode)
const hasParameters = computed(
  () => node.value && node.value.parameters && Object.keys(node.value.parameters).length > 0,
)
// Openable when the node has either a structured ``source`` block
// (preferred — hub path uses (file, line, col)) or just a raw
// ``link`` URI (offline fallback through the OS).
const hasOpenable = computed(
  () => node.value && (node.value.source || node.value.link),
)
const openTargetText = computed(() => {
  const src = node.value && node.value.source
  if (src && typeof src.file === 'string') {
    const line = typeof src.start_line === 'number' ? src.start_line : 1
    return `${src.file}:${line}`
  }
  return (node.value && node.value.link) || ''
})
const openTitle = computed(() =>
  hub.state.value === 'ready'
    ? 'Request hub to open this in your editor'
    : 'Hub offline — falls back to the rtlbuddy:// URI via the OS',
)
function openInEditor() {
  if (node.value) hub.requestOpenSource(node.value)
}
</script>

<style scoped>
.node-detail { padding: 0.5rem; border-top: 1px solid #e5e7eb; margin-top: 0.5rem; }
.node-detail h3 {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.85rem;
  margin: 0 0 0.5rem;
  word-break: break-all;
}
.node-detail dt {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
  margin-top: 0.5rem;
}
.node-detail dd { margin: 0.1rem 0 0; font-size: 0.85rem; }
.node-detail dd ul { margin: 0; padding-left: 1rem; }
.node-detail dd pre {
  background: #f1f5f9;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
}
.empty { color: #64748b; font-size: 0.85rem; }
.nav-actions { display: flex; gap: 0.25rem; margin-bottom: 0.5rem; }
.nav-actions button {
  border: 1px solid #cbd5e1;
  background: #ffffff;
  padding: 0.15rem 0.5rem;
  cursor: pointer;
  border-radius: 4px;
  font-size: 0.75rem;
}
.nav-actions button:disabled {
  color: #94a3b8;
  background: #f1f5f9;
  cursor: not-allowed;
}
.blackbox { color: #b45309; }
.port-name { color: #1e293b; }
.port-dir { color: #64748b; font-size: 0.75rem; margin-left: 0.25rem; }
.open-source {
  border: 1px solid #cbd5e1;
  background: #ffffff;
  padding: 0.15rem 0.5rem;
  cursor: pointer;
  border-radius: 4px;
  font-size: 0.75rem;
  margin-right: 0.4rem;
}
.open-target { font-size: 0.75rem; color: #64748b; word-break: break-all; }
</style>
