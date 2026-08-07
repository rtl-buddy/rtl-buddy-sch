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

    <!-- middle slot: who else is attached right now, in the panes'
         one-line form (``peers: view, graph`` / ``peers: none``). The
         full roster — every origin a user CAN have open, connected or
         not — is one click away in the popover; putting it inline here
         instead was the SPA disagreeing with the panes about what the
         middle slot of the strip says. -->
    <span
      class="peers-inline"
      aria-label="Hub peers"
      :title="peerRosterHint"
      >peers: {{ peerSummary }}</span
    >

    <!-- Which rtl-buddy is on the other end, in the SAME words the
         /graph and /cov panes use — one label a user can read off any
         hub surface and compare with ``rb --version``. Nothing at all
         before the first welcome: an empty version is a fact about the
         connection, and the dot already states it. The full raw string
         is the title here and a row in the popover. -->
    <span v-if="versionText" class="version-inline" :title="hub.serverVersion.value"
      >rtl-buddy {{ versionText
      }}<span
        v-if="bundleHash"
        class="ui-hash"
        title="SPA bundle — changes when the viewer is rebuilt"
        >{{ ` · ui ${bundleHash}` }}</span
      ></span
    >

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
      <!-- One event, one full rendering. The toast says the sentence;
           this slot says a few words and expires (the composable's
           ``errorNotice`` clears itself, and a welcome clears it
           early). It used to be a red mono duplicate of the toast
           that never went away. -->
      <template v-else-if="noticeCopy">
        <span class="msg-text" :title="noticeCopy.detail">
          {{ noticeCopy.short }}
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
      <!-- Versions. These two were chips in the top bar, where they
           competed for space with the controls a user actually
           touches while answering a question that comes up about once
           a session ("am I on the build I just staged?"). They belong
           with the rest of the connection facts. -->
      <section class="versions">
        <h4>versions</h4>
        <dl>
          <dt>rtl-buddy</dt>
          <dd :title="hub.serverVersion.value || 'no hub connected'">
            <code>{{ shortVersion || '—' }}</code>
          </dd>
          <dt>spa bundle</dt>
          <dd :title="bundleHash ? `index-${bundleHash}.js` : 'dev server or embedded build — no hashed bundle'">
            <code>{{ bundleHash || '—' }}</code>
          </dd>
        </dl>
      </section>
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
// the status word for the popover with the protocol/peer detail that
// would otherwise live in devtools.
import { computed, ref } from 'vue'
import { useHub } from '../composables/useHub.js'
import { humanizeHubError } from '../hubErrors.js'
import { readBundleHash, shortServerVersion, versionLabel } from '../buildInfo.js'

const hub = useHub()
const open = ref(false)

// Build identity. The popover keeps the full detail (R8b); the strip
// carries the one short label every hub app shows, recomputed off the
// ``serverVersion`` ref so a fresh welcome (reconnect, hub restart on a
// new build) rewrites it without a reload.
const shortVersion = computed(() => shortServerVersion(hub.serverVersion.value))
const versionText = computed(() => versionLabel(hub.serverVersion.value))
// The SPA's own build. Unlike the panes — which are HTML the hub
// server itself serves, so the server version covers them — this
// bundle ships from a different artefact and can be older than the hub
// it is talking to, so the strip names it separately. Empty under the
// dev server and the offline embed (no hashed asset): then there is no
// second build to disambiguate, and the suffix stays off.
const bundleHash = readBundleHash()

// Human copy for the expiring strip message. Reads ``errorNotice``,
// NOT ``lastError`` — the latter is the popover's permanent log row.
const noticeCopy = computed(() => humanizeHubError(hub.errorNotice.value))

// The panes' inline form, verbatim: ``peers: view, graph`` when some
// are attached, ``peers: none`` when none are (see ``setPeers`` in
// rtl_buddy hub/graph_page.html). Only CONNECTED peers — the roster of
// everything that could connect is the popover's job.
const peerSummary = computed(() => {
  const list = hub.peers.value || []
  return list.length > 0 ? list.join(', ') : 'none'
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
// Hover text for the inline list: the full roster with each origin's
// state, so "who is missing" is answerable without opening anything.
const peerRosterHint = computed(() =>
  peerRows.value
    .map((r) => `${r.origin} ${r.label} — ${r.connected ? 'connected' : 'not connected'}`)
    .join('\n'),
)

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
  if (hub.errorNotice.value) return 'error'
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
/* Plain text in the strip, not a button-looking pill: the panes'
   footers put ``<dot> connected`` there as bare muted text, and a
   bordered chip in the same slot read as a different kind of control.
   It is still a button (it opens the popover), so it keeps a hover
   affordance — a colour lift, no box that appears out of nowhere. */
.hub-status {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  flex-shrink: 0;
  font-family: inherit;
  font-size: var(--fs-small);
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--fg-muted);
  cursor: pointer;
}
.hub-status:hover {
  color: var(--fg);
}
/* Dot colour is the state; the word is the state spelled out. Both
   from the shared status tokens — connected/connecting…/offline map
   to --ok / --warn / --err, per the chrome contract. .55rem is the
   panes' ``.dot`` size. */
.hub-status .dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  background: var(--err);
  flex-shrink: 0;
}
.hub-status .dot[data-state='ready'] { background: var(--ok); }
.hub-status .dot[data-state='connecting'] { background: var(--warn); }
.hub-status .dot[data-state='error'] { background: var(--err); }

/* -- middle slot: inline peer list + version label -------------------- */
/* The peer list shrinks (and ellipsises) before the version label does:
   a truncated version string is worse than useless — it looks like a
   different build. The flexible gap moved to the message area's
   ``margin-left`` so both sit together on the left of it. */
.peers-inline {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-small);
  color: var(--fg-muted);
}
.version-inline {
  flex: 0 0 auto;
  white-space: nowrap;
  font-size: var(--fs-small);
  color: var(--fg-muted);
}
/* One step fainter: the hub's version is the answer to "what am I
   talking to"; the bundle hash is a footnote to it. */
.version-inline .ui-hash {
  color: var(--fg-faint);
}

/* -- right slot: message area ---------------------------------------- */
.hub-message {
  margin-left: auto;
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
/* Versions block: a hairline-separated sub-section so it reads as
   build identity rather than another connection fact. */
.hub-popover .versions {
  margin-top: 0.5rem;
  padding-top: 0.4rem;
  border-top: 1px solid var(--line);
}
.hub-popover .versions h4 {
  margin: 0 0 0.2rem;
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg-muted);
}
.hub-popover .versions code {
  font-family: var(--font-mono);
  background: var(--panel-2);
  padding: 0.05rem 0.35rem;
  border-radius: var(--radius-1);
}
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
