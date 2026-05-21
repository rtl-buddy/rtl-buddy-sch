<template>
  <section class="edge-detail" v-if="edge">
    <h3>{{ edge.from }} → {{ edge.to }}</h3>
    <div class="nav-actions">
      <button
        type="button"
        @click="store.select(edge.from)"
        title="Select the source instance"
      >Select source</button>
      <button
        type="button"
        @click="store.select(edge.to)"
        title="Select the destination instance"
      >Select destination</button>
      <button
        type="button"
        @click="store.clearSelection()"
        title="Clear edge selection"
      >Clear</button>
    </div>
    <dl>
      <template v-if="hasPortPairs">
        <dt>Port pairs</dt>
        <dd>
          <ul>
            <li v-for="(pair, idx) in edge.port_pairs" :key="idx">
              <code>
                <span class="port-name">.{{ pair[1] }}</span>
                <span class="port-arrow">({{ pair[0] }})</span>
              </code>
            </li>
          </ul>
        </dd>
      </template>
      <template v-for="(payload, name) in edge.overlays" :key="name">
        <dt>overlay: {{ name }}</dt>
        <dd><pre>{{ JSON.stringify(payload, null, 2) }}</pre></dd>
      </template>
      <template v-if="!hasPortPairs && !hasOverlays">
        <dt>Edge</dt>
        <dd class="empty">Structural parent → child edge. No port pairs or overlay payloads recorded.</dd>
      </template>
    </dl>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { useViewerStore } from '../store.js'

const store = useViewerStore()
const edge = computed(() => store.selectedEdgeObj)
const hasPortPairs = computed(
  () => edge.value && Array.isArray(edge.value.port_pairs) && edge.value.port_pairs.length > 0,
)
const hasOverlays = computed(
  () => edge.value && edge.value.overlays && Object.keys(edge.value.overlays).length > 0,
)
</script>

<style scoped>
.edge-detail { padding: 0.5rem; border-top: 1px solid #e5e7eb; margin-top: 0.5rem; }
.edge-detail h3 {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.85rem;
  margin: 0 0 0.5rem;
  word-break: break-all;
}
.edge-detail dt {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
  margin-top: 0.5rem;
}
.edge-detail dd { margin: 0.1rem 0 0; font-size: 0.85rem; }
.edge-detail dd ul { margin: 0; padding-left: 1rem; }
.edge-detail dd pre {
  background: #f1f5f9;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
}
.nav-actions { display: flex; gap: 0.25rem; margin-bottom: 0.5rem; flex-wrap: wrap; }
.nav-actions button {
  border: 1px solid #cbd5e1;
  background: #ffffff;
  padding: 0.15rem 0.5rem;
  cursor: pointer;
  border-radius: 4px;
  font-size: 0.75rem;
}
.empty { color: #64748b; }
.port-name { color: #1e293b; }
.port-arrow { color: #64748b; font-size: 0.75rem; margin-left: 0.25rem; }
</style>
