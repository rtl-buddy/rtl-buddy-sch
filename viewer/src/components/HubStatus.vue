<template>
  <div class="hub-status-root">
    <button
      type="button"
      class="hub-status"
      :data-state="hub.state.value"
      :title="hint"
      @click="open = !open"
    >
      <span class="dot" :data-state="hub.state.value" aria-hidden="true"></span>
      hub: {{ hub.state.value }}
    </button>
    <div v-if="open" class="hub-popover" role="dialog" aria-label="Hub details">
      <header>
        <strong>rtl-buddy-hub</strong>
        <button type="button" class="close" @click="open = false" aria-label="Close">×</button>
      </header>
      <dl>
        <dt>state</dt><dd>{{ hub.state.value }}</dd>
        <dt>server</dt><dd>{{ hub.serverVersion.value || '—' }}</dd>
        <dt>peers</dt>
        <dd>
          <ul class="peer-list">
            <li
              v-for="peer in peerRows"
              :key="peer.origin"
              :data-state="peer.connected ? 'connected' : 'disconnected'"
              :title="peer.connected ? 'connected' : 'not connected'"
            >
              <span class="peer-dot" aria-hidden="true"></span>
              <code>{{ peer.origin }}</code>
              <span class="peer-label">{{ peer.label }}</span>
            </li>
          </ul>
        </dd>
        <dt>last error</dt>
        <dd v-if="hub.lastError.value">
          <code>{{ hub.lastError.value.code }}</code> — {{ hub.lastError.value.message }}
        </dd>
        <dd v-else>—</dd>
      </dl>
      <footer v-if="hub.state.value === 'disconnected'">
        <button type="button" @click="reconnect">Reconnect</button>
      </footer>
    </div>
    <div
      v-if="hub.superseded.value"
      class="superseded-banner"
      role="status"
    >
      <strong>Superseded</strong> — another tab took over this hub.
      <button type="button" @click="takeBack">Take back</button>
    </div>
  </div>
</template>

<script setup>
// Connection indicator + expanded panel. Click the pill to toggle
// the popover; the popover shows the protocol/peer details that
// would otherwise live in devtools and the last hub-side error so
// users have a single place to look when things go quiet.
import { computed, ref } from 'vue'
import { useHub } from '../composables/useHub.js'

const hub = useHub()
const open = ref(false)

const peerSummary = computed(() => {
  const list = hub.peers.value || []
  return list.length > 0 ? list.join(', ') : '—'
})

// Render every known peer role, including ones not currently
// connected, so the user can tell at a glance which adapter is
// missing instead of just seeing a shorter list. Roles match the
// `Origin` enum in rtl_buddy/hub/protocol — `cli` is the hub itself
// and isn't a peer in the adapter sense, so it's omitted.
const PEER_ROLES = [
  { origin: 'view', label: '(this SPA)' },
  { origin: 'src', label: '(editor)' },
  { origin: 'wave', label: '(surfer)' },
]
const peerRows = computed(() => {
  const list = hub.peers.value || []
  return PEER_ROLES.map((role) => ({
    ...role,
    connected: list.includes(role.origin),
  }))
})

const hint = computed(() => {
  switch (hub.state.value) {
    case 'ready':
      return `connected — ${peerSummary.value}`
    case 'connecting':
      return 'connecting to /ws…'
    case 'disconnected':
      return 'no hub — running in offline mode'
    default:
      return hub.state.value
  }
})

function reconnect() {
  hub.reconnect()
}
// "Take back": this tab was kicked because another tab opened
// and registered as ``view``. Reconnect with ``takeover=true`` so
// the hub evicts the newer tab and accepts us instead.
function takeBack() {
  hub.reconnect({ takeover: true })
}
</script>

<style scoped>
.hub-status-root {
  position: relative;
}
.hub-status {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.75rem;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  background: #f1f5f9;
  color: #64748b;
  border: 1px solid transparent;
  cursor: pointer;
}
.hub-status[data-state='ready'] {
  background: #dcfce7;
  color: #166534;
}
.hub-status[data-state='connecting'] {
  background: #fef3c7;
  color: #92400e;
}
.hub-status[data-state='error'] {
  background: #fee2e2;
  color: #991b1b;
}
.hub-status .dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: #94a3b8;
}
.hub-status .dot[data-state='ready']    { background: #16a34a; }
.hub-status .dot[data-state='connecting'] { background: #d97706; }
.hub-status .dot[data-state='error']    { background: #dc2626; }
.hub-popover {
  position: absolute;
  right: 0;
  top: calc(100% + 0.25rem);
  z-index: 50;
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
  padding: 0.5rem 0.75rem;
  min-width: 18rem;
  font-size: 0.8rem;
  color: #1f2937;
}
.hub-popover header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.4rem;
}
.hub-popover .close {
  background: transparent;
  border: 0;
  font-size: 1rem;
  cursor: pointer;
  line-height: 1;
  padding: 0 0.25rem;
}
.hub-popover dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.15rem 0.75rem;
  margin: 0;
}
.hub-popover dt { color: #64748b; }
.hub-popover dd { margin: 0; word-break: break-word; }
.hub-popover footer {
  margin-top: 0.5rem;
  display: flex;
  justify-content: flex-end;
}
.hub-popover footer button {
  font-size: 0.75rem;
  padding: 0.2rem 0.6rem;
  border-radius: 4px;
  border: 1px solid #cbd5e1;
  background: #f8fafc;
  cursor: pointer;
}
.peer-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.peer-list li {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.75rem;
}
.peer-list li[data-state='disconnected'] {
  color: #94a3b8;
}
.peer-list code {
  font-family: inherit;
  background: transparent;
  padding: 0;
}
.peer-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: transparent;
  border: 1px solid #cbd5e1;
  flex-shrink: 0;
}
.peer-list li[data-state='connected'] .peer-dot {
  background: #16a34a;
  border-color: #16a34a;
}
.peer-label {
  color: #64748b;
  font-family: -apple-system, BlinkMacSystemFont, sans-serif;
}
.superseded-banner {
  margin-top: 0.4rem;
  padding: 0.4rem 0.6rem;
  background: #fee2e2;
  color: #991b1b;
  border: 1px solid #fecaca;
  border-radius: 4px;
  font-size: 0.8rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.superseded-banner button {
  margin-left: auto;
  border: 1px solid #b91c1c;
  background: #ffffff;
  color: #991b1b;
  padding: 0.1rem 0.5rem;
  cursor: pointer;
  border-radius: 4px;
  font-size: 0.75rem;
}
</style>
