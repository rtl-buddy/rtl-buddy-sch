<template>
  <section class="overlay-panel">
    <h3>Overlays</h3>
    <ul v-if="summary.length">
      <li v-for="entry in summary" :key="entry.name">
        <label class="overlay-row">
          <input
            type="checkbox"
            :checked="store.enabledOverlays.has(entry.name)"
            :disabled="!entry.known"
            @change="store.toggleOverlay(entry.name)"
          />
          <span class="overlay-name">{{ entry.name }}</span>
          <span v-if="!entry.known" class="tag-unknown">unknown</span>
        </label>
        <ul v-if="entry.known && legendFor(entry.name).length" class="legend">
          <li v-for="item in legendFor(entry.name)" :key="item.label">
            <span class="swatch" :style="{ background: item.swatch }"></span>
            {{ item.label }}
          </li>
        </ul>
      </li>
    </ul>
    <p v-else class="empty">No overlays in this view.</p>
  </section>
</template>

<script setup>
// Per-overlay enable/disable + per-overlay legend slot.
//
// The list is driven by ``graph.overlays_present`` so the panel
// stays correct when a future producer ships an overlay this
// viewer doesn't know about — the entry appears tagged
// ``unknown`` and the checkbox is disabled (toggling it would do
// nothing visible).
import { computed } from 'vue'
import { useViewerStore } from '../store.js'
import { getOverlay, overlaySummary } from '../overlays/index.js'

const store = useViewerStore()
const summary = computed(() =>
  store.graph ? overlaySummary(store.graph) : [],
)
function legendFor(name) {
  const overlay = getOverlay(name)
  if (!overlay || typeof overlay.legend !== 'function') return []
  return overlay.legend(store.graph) || []
}
</script>

<style scoped>
.overlay-panel { padding: 0.5rem; }
.overlay-panel h3 {
  margin: 0 0 0.5rem;
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
}
.overlay-panel ul { list-style: none; padding: 0; margin: 0; }
.overlay-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.9rem;
  cursor: pointer;
}
.tag-unknown {
  font-size: 0.7rem;
  background: #f1f5f9;
  color: #64748b;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
}
.legend {
  margin-left: 1.5rem;
  font-size: 0.75rem;
  color: #475569;
}
.legend li { display: flex; align-items: center; gap: 0.5rem; padding: 0.1rem 0; }
.swatch {
  width: 12px;
  height: 12px;
  border-radius: 2px;
  border: 1px solid #cbd5e1;
  display: inline-block;
}
.empty {
  font-size: 0.85rem;
  color: #64748b;
  margin: 0;
}
</style>
