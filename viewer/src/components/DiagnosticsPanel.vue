<template>
  <section v-if="hasAny" class="diagnostics-panel" data-test="diagnostics-panel">
    <h3>Diagnostics</h3>
    <div
      v-for="(items, source) in store.diagnosticsBySource"
      :key="source"
      class="source-group"
      :data-source="source"
      :data-count="items.length"
    >
      <header>
        <span class="source-name">{{ source }}</span>
        <span class="count">{{ items.length }}</span>
      </header>
      <ul>
        <li
          v-for="(d, i) in items"
          :key="i"
          :data-severity="d.severity"
          @click="onClick(d)"
        >
          <span class="severity" :data-severity="d.severity">{{ d.severity }}</span>
          <span class="loc">{{ d.file }}:{{ d.line }}</span>
          <span v-if="d.code" class="code">{{ d.code }}</span>
          <span class="msg">{{ d.message }}</span>
        </li>
      </ul>
    </div>
  </section>
</template>

<script setup>
// Lightweight diagnostics surface for hub-published findings
// (rtl-buddy-cdc, rtl-buddy-lint, …). Findings are stashed in the
// store by source via `applyDiagnostics`; this panel just walks
// that map and renders an entry per item.
//
// Clicking an item selects the matching node via the same resolver
// the on-canvas badge layer uses (`store.nodeIdForDiagnosticItem`):
//   1. fast path on `item.instance_path` when the producer
//      attaches it,
//   2. else file+line range → deepest enclosing instance.
// `store.select` then triggers the standard pan+zoom+broadcast
// pipeline, so a sidebar click brings the schematic into view at
// the same node the badge marks.
import { computed } from 'vue'
import { useViewerStore } from '../store.js'

const store = useViewerStore()
const hasAny = computed(() => Object.keys(store.diagnosticsBySource).length > 0)

function onClick(item) {
  const nodeId = store.nodeIdForDiagnosticItem(item)
  if (nodeId) store.select(nodeId)
}
</script>

<style scoped>
.diagnostics-panel {
  margin-top: 1rem;
  padding-top: 0.5rem;
  border-top: 1px solid #e5e7eb;
}
.diagnostics-panel h3 {
  font-size: 0.85rem;
  margin: 0 0 0.5rem 0;
  color: #475569;
}
.source-group {
  margin-bottom: 0.6rem;
}
.source-group > header {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  font-family: ui-monospace, Menlo, monospace;
  color: #64748b;
  margin-bottom: 0.2rem;
}
.source-group .count {
  background: #e2e8f0;
  color: #1f2937;
  padding: 0 0.4rem;
  border-radius: 999px;
}
.source-group ul {
  list-style: none;
  padding: 0;
  margin: 0;
}
.source-group li {
  display: grid;
  grid-template-columns: max-content max-content max-content 1fr;
  gap: 0.4rem;
  align-items: baseline;
  padding: 0.2rem 0.3rem;
  font-size: 0.75rem;
  border-radius: 4px;
  cursor: pointer;
}
.source-group li:hover {
  background: #f8fafc;
}
.severity {
  font-family: ui-monospace, Menlo, monospace;
  text-transform: uppercase;
  font-size: 0.65rem;
  padding: 0 0.3rem;
  border-radius: 3px;
  color: #ffffff;
  background: #94a3b8;
}
.severity[data-severity='error']   { background: #dc2626; }
.severity[data-severity='warning'] { background: #d97706; }
.severity[data-severity='info']    { background: #0ea5e9; }
.severity[data-severity='hint']    { background: #64748b; }
.loc, .code {
  font-family: ui-monospace, Menlo, monospace;
  color: #475569;
  white-space: nowrap;
}
.code { color: #1f2937; }
.msg { color: #1f2937; }
</style>
