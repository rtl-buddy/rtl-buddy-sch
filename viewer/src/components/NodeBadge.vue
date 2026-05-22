<template>
  <div
    class="node-badge"
    :data-severity="worstSeverity"
    :data-test="`node-badge-${nodeId}`"
    role="button"
    tabindex="0"
    @click="$emit('select')"
    @keydown.enter="$emit('select')"
    @keydown.space.prevent="$emit('select')"
  >
    <span class="dot" :title="badgeTitle"></span>
    <span v-if="items.length > 1" class="count">{{ items.length }}</span>
    <div class="popup" role="tooltip">
      <ul>
        <li
          v-for="(d, i) in items"
          :key="i"
          :data-severity="d.severity || 'info'"
        >
          <span class="sev" :data-severity="d.severity || 'info'">
            {{ (d.severity || 'info').toUpperCase() }}
          </span>
          <span v-if="d.code" class="code">{{ d.code }}</span>
          <span class="msg">{{ d.message }}</span>
          <span class="src">{{ d.source }} · {{ d.file }}:{{ d.line }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
// One badge per node that carries any diagnostics_set items mapped
// to it by the store's resolver. Collapsed by default (severity-
// coloured dot, item count if >1); hover or focus expands the popup
// listing each item.
//
// Position is set externally via inline ``style``; the parent
// (GraphCanvas) re-computes coords whenever the canvas transform or
// the diagnostics map changes.

import { computed } from 'vue'

const props = defineProps({
  nodeId: { type: String, required: true },
  items: { type: Array, required: true },
})
defineEmits(['select'])

// Severity rank: error > warning > info > hint. Pick the worst the
// items contain so the collapsed dot conveys the highest-priority
// finding at a glance.
const _SEV_RANK = { error: 3, warning: 2, info: 1, hint: 0 }
const worstSeverity = computed(() => {
  let bestKey = 'info'
  let bestRank = -1
  for (const d of props.items) {
    const k = d?.severity || 'info'
    const r = _SEV_RANK[k] ?? 0
    if (r > bestRank) {
      bestRank = r
      bestKey = k
    }
  }
  return bestKey
})

const badgeTitle = computed(() => {
  if (props.items.length === 1) {
    const d = props.items[0]
    return `${(d.severity || 'info').toUpperCase()}${d.code ? ' ' + d.code : ''}: ${d.message || ''}`
  }
  return `${props.items.length} diagnostics — hover for details`
})
</script>

<style scoped>
.node-badge {
  /* Sizing tuned so the dot is unmistakable but doesn't crowd
     adjacent nodes at the default 0.4× zoom-out. */
  position: absolute;
  display: inline-flex;
  align-items: center;
  gap: 0.15rem;
  /* Pivot at the dot's centre so the screen-coord we get from the
     parent is the natural "top-right corner of the node" anchor. */
  transform: translate(-50%, -50%);
  cursor: pointer;
  z-index: 5;
  font-family: ui-monospace, Menlo, monospace;
}
.dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #94a3b8;
  border: 2px solid #ffffff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.35);
}
.node-badge[data-severity='error']   .dot { background: #dc2626; }
.node-badge[data-severity='warning'] .dot { background: #d97706; }
.node-badge[data-severity='info']    .dot { background: #0ea5e9; }
.node-badge[data-severity='hint']    .dot { background: #64748b; }
.count {
  font-size: 0.65rem;
  background: #1f2937;
  color: #ffffff;
  padding: 0 0.3rem;
  border-radius: 999px;
  line-height: 1.3;
  font-weight: 600;
  /* Sit visually adjacent to the dot, no extra offset since the
     flex gap already separates them. */
}
.popup {
  position: absolute;
  /* Default placement: just below + right of the dot. Hidden until
     hover/focus. Smart-flipping near viewport edges is a polish
     follow-up; the dot itself is small enough that the popup almost
     always finds room downstream. */
  top: 1rem;
  left: 0.5rem;
  min-width: 18rem;
  max-width: 28rem;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.18);
  padding: 0.4rem 0;
  font-size: 0.75rem;
  color: #1f2937;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.12s ease-out;
}
.node-badge:hover .popup,
.node-badge:focus-within .popup,
.node-badge:focus .popup {
  opacity: 1;
  pointer-events: auto;
}
.popup ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.popup li {
  display: grid;
  grid-template-columns: max-content max-content 1fr;
  gap: 0.4rem;
  align-items: baseline;
  padding: 0.3rem 0.6rem;
  border-bottom: 1px solid #f1f5f9;
}
.popup li:last-child { border-bottom: none; }
.popup .sev {
  font-size: 0.6rem;
  padding: 0.05rem 0.3rem;
  border-radius: 3px;
  color: #ffffff;
  background: #94a3b8;
  letter-spacing: 0.02em;
}
.popup .sev[data-severity='error']   { background: #dc2626; }
.popup .sev[data-severity='warning'] { background: #d97706; }
.popup .sev[data-severity='info']    { background: #0ea5e9; }
.popup .sev[data-severity='hint']    { background: #64748b; }
.popup .code {
  font-size: 0.7rem;
  color: #1f2937;
  font-weight: 600;
}
.popup .msg {
  font-family: inherit;
  font-size: 0.78rem;
  line-height: 1.35;
  /* Wrap long messages so they don't make the popup grow horizontally. */
  white-space: normal;
  word-break: break-word;
}
.popup .src {
  grid-column: 1 / -1;
  margin-top: 0.1rem;
  font-size: 0.65rem;
  color: #64748b;
}
</style>
