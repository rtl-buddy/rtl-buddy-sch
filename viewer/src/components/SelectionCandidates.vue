<template>
  <div
    v-if="store.selectionCandidates && store.selectionCandidates.length > 1"
    class="selection-candidates"
    role="dialog"
    aria-label="Multiple matches — pick one"
  >
    <header>
      <strong>{{ store.selectionCandidates.length }} matches</strong>
      <button
        type="button"
        class="close"
        @click="dismiss"
        aria-label="Dismiss"
        title="Dismiss"
      >×</button>
    </header>
    <p class="hint">
      The source range mapped to more than one instance. The smallest-
      range match is selected by default — click another to lock that
      choice. Auto-dismisses in a few seconds.
    </p>
    <ul class="candidate-list">
      <li
        v-for="(path, i) in store.selectionCandidates"
        :key="path"
        :class="{ 'is-current': path === store.selection }"
      >
        <button
          type="button"
          class="candidate"
          :title="rangeFor(path) || path"
          @click="choose(path)"
        >
          <span class="rank">{{ i === 0 ? 'default' : '#' + (i + 1) }}</span>
          <code class="path">{{ path }}</code>
          <span v-if="rangeFor(path)" class="range">{{ rangeFor(path) }}</span>
        </button>
      </li>
    </ul>
  </div>
</template>

<script setup>
// Disambiguation picker (rtl-buddy-view#55). Surfaces when the hub
// resolves a source_focused event to more than one instance path.
// The composable stashes the candidate list on the store (via
// presentSelectionCandidates) and starts an auto-dismiss timer; this
// component just renders the list and forwards clicks back through
// useHub.chooseSelectionCandidate so the chosen path is locked
// locally AND broadcast to the other hub peers.
//
// Showing source ranges (file:start-end) next to each path is what
// distinguishes siblings like ``u_a`` / ``u_b`` whose instance names
// only differ by a one-char suffix — the path alone is often hard to
// read at a glance.
import { useViewerStore } from '../store.js'
import { useHub } from '../composables/useHub.js'

const store = useViewerStore()
const hub = useHub()

function rangeFor(path) {
  // Pull file + line range from the node table so the user can tell
  // siblings apart by source location. Returns an empty string when
  // the node has no source block (synthetic / blackbox) so the
  // template's ``v-if`` skips the chip.
  const node = store.nodesById.get(path)
  const src = node?.source
  if (!src || typeof src.file !== 'string') return ''
  const basename = src.file.split('/').pop() || src.file
  const start = typeof src.start_line === 'number' ? src.start_line : null
  const end = typeof src.end_line === 'number' ? src.end_line : null
  if (start && end && start !== end) return `${basename}:${start}-${end}`
  if (start) return `${basename}:${start}`
  return basename
}

function choose(path) {
  hub.chooseSelectionCandidate(path)
}

function dismiss() {
  hub.dismissSelectionCandidates()
}
</script>

<style scoped>
.selection-candidates {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  z-index: 30;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.12);
  padding: 0.6rem 0.75rem;
  max-width: 28rem;
  min-width: 18rem;
  font-size: 0.8rem;
  color: #1f2937;
}
.selection-candidates header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.35rem;
}
.selection-candidates .close {
  background: transparent;
  border: 0;
  font-size: 1rem;
  cursor: pointer;
  line-height: 1;
  padding: 0 0.25rem;
  color: #64748b;
}
.selection-candidates .hint {
  margin: 0 0 0.5rem 0;
  color: #475569;
  font-size: 0.72rem;
  line-height: 1.35;
}
.candidate-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.candidate {
  width: 100%;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 0.35rem 0.5rem;
  cursor: pointer;
  display: grid;
  grid-template-columns: max-content 1fr max-content;
  gap: 0.5rem;
  align-items: center;
  text-align: left;
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.72rem;
}
.candidate:hover {
  background: #eff6ff;
  border-color: #93c5fd;
}
.is-current .candidate {
  background: #ecfdf5;
  border-color: #6ee7b7;
}
.candidate .rank {
  color: #64748b;
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.is-current .candidate .rank {
  color: #047857;
  font-weight: 600;
}
.candidate .path {
  color: #1e293b;
  background: transparent;
  padding: 0;
  word-break: break-all;
}
.candidate .range {
  color: #64748b;
  font-size: 0.7rem;
  white-space: nowrap;
}
</style>
