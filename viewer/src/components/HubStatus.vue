<template>
  <div class="hub-status-root">
    <!-- left slot: connection dot + the status word. One vocabulary,
         shared with every other hub app: connected / connecting… /
         offline. Anything that doesn't fit it (the server version, why
         the socket dropped) goes in the title, not the word. -->
    <button
      type="button"
      class="hub-status"
      :data-state="hub.state.value"
      :title="hint"
      :aria-expanded="open ? 'true' : 'false'"
      @click="open = !open"
    >
      <span class="dot" :data-state="hub.state.value" aria-hidden="true"></span>
      {{ displayState }}
    </button>

    <!-- middle slot: who else is attached right now. -->
    <ul class="peer-strip" aria-label="Hub peers">
      <li
        v-for="peer in peerRows"
        :key="peer.origin"
        :data-state="peer.connected ? 'connected' : 'disconnected'"
        :title="`${peer.origin} ${peer.label} — ${peer.connected ? 'connected' : 'not connected'}`"
      >
        <span class="peer-dot" aria-hidden="true"></span>
        <code>{{ peer.origin }}</code>
      </li>
    </ul>

    <!-- right slot: message area, on the shared severity tokens
         (--err errors, --warn warnings, --fg-muted notes). The two
         banners live HERE rather than as separate floating bars —
         a strip with a dedicated message slot is the place a message
         belongs, and one home means they can't disagree. -->
    <div class="hub-message" :data-severity="messageSeverity" role="status" aria-live="polite">
      <template v-if="hub.superseded.value">
        <span class="superseded-banner">
          <strong>superseded</strong>
          <span class="msg-text" title="Another tab registered as origin=view and the hub evicted this one.">
            another tab took over this hub
          </span>
          <button type="button" class="msg-action" @click="takeBack">Take back</button>
        </span>
      </template>
      <template v-else-if="isReconnecting">
        <span
          class="reconnecting-banner"
          title="Wave-value badges are frozen at their last-known values until the hub welcomes us back."
        >
          <span class="banner-dot" aria-hidden="true"></span>
          <span class="msg-text">reconnecting — wave badges frozen</span>
        </span>
      </template>
      <template v-else-if="hub.lastError.value">
        <code class="msg-code">{{ hub.lastError.value.code }}</code>
        <span class="msg-text" :title="hub.lastError.value.message">
          {{ hub.lastError.value.message }}
        </span>
      </template>
    </div>

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

  </div>
</template>

<script setup>
// The SPA's half of the hub chrome contract's bottom status strip
// (rtl_buddy docs/concepts/hub.md): connection dot + status word on
// the left, peer list in the middle, message area on the right. Click
// the status pill for the popover with the protocol/peer detail that
// would otherwise live in devtools.
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
// `Origin` enum in rtl_buddy/hub/protocol, minus the two that are not
// apps a user keeps open: `cli` (that's `rb hub send`, a one-shot) and
// `notebook` (one marimo session, not an adapter). Same list `rb hub
// status` prints.
//
// `cov` is DISPLAY-ONLY for now: the coverage pane lands with
// rtl-buddy/rtl_buddy#400 and the origin arrives in this repo's
// protocol schema first (rtl-buddy/rtl-buddy-view#133). Until a hub
// speaks it the row simply reads "not connected", which is the honest
// answer either way.
const PEER_ROLES = [
  { origin: 'view', label: '(this SPA)' },
  { origin: 'src', label: '(editor)' },
  { origin: 'wave', label: '(surfer)' },
  { origin: 'graph', label: '(graph pane)' },
  { origin: 'cov', label: '(coverage pane)' },
]
const peerRows = computed(() => {
  const list = hub.peers.value || []
  return PEER_ROLES.map((role) => ({
    ...role,
    connected: list.includes(role.origin),
  }))
})

// User-friendly labels for the protocol-level state names. Keep
// the raw value in ``data-state`` for CSS theming, but the chip
// itself reads ``connected`` / ``connecting`` / ``offline``.
const STATE_LABEL = {
  ready: 'connected',
  connecting: 'connecting…',
  disconnected: 'offline',
  error: 'error',
}
const displayState = computed(
  () => STATE_LABEL[hub.state.value] || hub.state.value,
)

// Reconnecting banner predicate. The hub composable latches
// ``wasEverReady`` the first time it sees a welcome — distinguishing
// "first connect in progress" (no banner; we haven't been online
// yet) from "lost the hub mid-session" (show banner so the user
// knows the wave-value badges they're looking at are frozen at the
// last-known sample). Suppressed when ``superseded`` is set so the
// two banners don't fight for the same slot.
const isReconnecting = computed(
  () =>
    hub.wasEverReady.value &&
    !hub.superseded.value &&
    (hub.state.value === 'disconnected' || hub.state.value === 'connecting'),
)

// Message-area severity, on the shared tokens: --err for errors,
// --warn for warnings, --fg-muted for notes.
const messageSeverity = computed(() => {
  if (hub.superseded.value) return 'error'
  if (isReconnecting.value) return 'warning'
  if (hub.lastError.value) return 'error'
  return 'note'
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
/* The component fills App.vue's ``.app-status-strip``: three slots
   left to right, with the popover and the two banners anchored above
   it (the strip is only --status-h tall, and both carry a sentence). */
.hub-status-root {
  position: relative;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  min-width: 0;
}
.hub-status {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: var(--fs-small);
  padding: 0 0.5rem;
  border-radius: 999px;
  background: transparent;
  color: var(--fg-muted);
  border: 1px solid transparent;
  cursor: pointer;
}
.hub-status:hover {
  border-color: var(--line-strong);
}
/* Dot colour is the state; the word is the state spelled out. Both
   from the shared status tokens — connected/connecting…/offline map
   to --ok / --warn / --err, per the chrome contract. */
.hub-status .dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: var(--err);
  flex-shrink: 0;
}
.hub-status .dot[data-state='ready'] { background: var(--ok); }
.hub-status .dot[data-state='connecting'] { background: var(--warn); }
.hub-status .dot[data-state='error'] { background: var(--err); }
.hub-status[data-state='ready'] { color: var(--fg); }

/* -- middle slot: peer list ------------------------------------------ */
.peer-strip {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
}
.peer-strip li {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-family: var(--font-mono);
  font-size: var(--fs-small);
  color: var(--fg);
}
.peer-strip li[data-state='disconnected'] {
  color: var(--fg-faint);
}
.peer-strip code {
  font-family: inherit;
  background: transparent;
  padding: 0;
}

/* -- right slot: message area ---------------------------------------- */
.hub-message {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex: 0 1 auto;
  min-width: 0;
  font-size: var(--fs-small);
  color: var(--fg-muted);
}
.hub-message[data-severity='error'] { color: var(--err); }
.hub-message[data-severity='warning'] { color: var(--warn); }
.hub-message .msg-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hub-message .msg-code {
  font-family: var(--font-mono);
}
.hub-message .msg-action {
  font-size: var(--fs-small);
  padding: 0 0.4rem;
  border: 1px solid currentColor;
  border-radius: var(--radius-2);
  background: transparent;
  color: inherit;
  cursor: pointer;
}

/* -- popover + banners, anchored above the strip --------------------- */
.hub-popover {
  position: absolute;
  left: 0;
  bottom: calc(100% + 0.35rem);
  z-index: 50;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-3);
  box-shadow: var(--shadow-1);
  padding: 0.5rem 0.75rem;
  min-width: 18rem;
  font-size: 0.8rem;
  color: var(--fg);
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
  color: var(--fg-muted);
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
.hub-popover dt { color: var(--fg-muted); }
.hub-popover dd { margin: 0; word-break: break-word; }
.hub-popover footer {
  margin-top: 0.5rem;
  display: flex;
  justify-content: flex-end;
}
.hub-popover footer button {
  font-size: 0.75rem;
  padding: 0.2rem 0.6rem;
  border-radius: var(--radius-2);
  border: 1px solid var(--line-strong);
  background: var(--panel-2);
  color: var(--fg);
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
  font-family: var(--font-mono);
  font-size: 0.75rem;
}
.peer-list li[data-state='disconnected'] {
  color: var(--fg-faint);
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
  border: 1px solid var(--line-strong);
  flex-shrink: 0;
}
.peer-strip li[data-state='connected'] .peer-dot,
.peer-list li[data-state='connected'] .peer-dot {
  background: var(--ok);
  border-color: var(--ok);
}
.peer-label {
  color: var(--fg-muted);
  font-family: var(--font-sans);
}
/* Both banners are message-area content, so they inherit the slot's
   severity colour and only add the tint that makes them read as a
   state rather than a note. */
.superseded-banner,
.reconnecting-banner {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  min-width: 0;
  padding: 0 0.4rem;
  border-radius: var(--radius-2);
  color: inherit;
}
.superseded-banner { background: var(--err-bg); }
.reconnecting-banner { background: var(--warn-bg); }
.banner-dot {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
  background: currentColor;
  animation: rb-banner-pulse 1.2s ease-in-out infinite;
  flex-shrink: 0;
}
@keyframes rb-banner-pulse {
  0%, 100% { opacity: 0.35; }
  50%      { opacity: 1;    }
}
</style>
