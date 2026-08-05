<template>
  <div
    v-if="visible"
    class="hub-gone"
    role="alert"
    aria-live="assertive"
    data-placeholder="hub_gone"
  >
    <div class="hub-gone-card">
      <h2>The hub is gone</h2>
      <p>
        This tab was connected to a hub on this origin and the connection
        dropped; {{ attemptsWord }} reconnect attempts have failed. The
        hub process is no longer answering — nothing else took over this
        tab.
      </p>
      <p class="hub-gone-hint">
        Restart it:<br />
        <code>{{ HUB_START_HINT }}</code><br />
        then reload this page.
      </p>
      <div class="hub-gone-actions">
        <button type="button" class="hub-gone-retry" @click="retry">
          Try reconnecting
        </button>
        <button type="button" class="hub-gone-reload" @click="reload">
          Reload
        </button>
      </div>
      <p class="hub-gone-note">
        The schematic already on screen is frozen at its last-known
        state.
      </p>
    </div>
  </div>
</template>

<script setup>
// Full-pane "hub is gone" placeholder (rtl-buddy-view#130 state 4).
//
// Fires when the composable latches ``hubGone``: we reached a
// ``welcome`` at least once, the socket dropped, and several
// reconnects in a row failed. That's a dead hub process, not a blip —
// the transient case is already covered by HubStatus's
// "reconnecting…" banner, which stays the right UI for the first few
// seconds.
//
// Deliberately worded so it can't be mistaken for the superseded case,
// which HubStatus's strip banner ("another tab took over this hub" +
// Take back) now owns: nothing took this tab's slot, the server itself
// is down. RANK: superseded outranks hub-gone. A superseded tab is
// talking to a LIVE hub that evicted it, so it must never be told the
// hub died — when ``superseded`` is set this overlay stays hidden and
// the strip speaks alone. Two contradictory explanations on one screen
// is worse than either.
import { computed } from 'vue'
import { useHub } from '../composables/useHub.js'
import { HUB_START_HINT } from '../cliHints.js'

const hub = useHub()

const visible = computed(
  () => hub.hubGone.value && !hub.superseded.value,
)

// "several" rather than a live counter: the exact number is an
// implementation detail of the backoff and reads as noise.
const attemptsWord = 'several'

function retry() {
  hub.reconnect()
}

function reload() {
  if (typeof window !== 'undefined' && window.location) {
    window.location.reload()
  }
}
</script>

<style scoped>
/* Tokens only (AGENTS.md § design tokens): a full-pane scrim built
   from literals is exactly the thing that survives a theme flip as a
   white rectangle. ``--bg`` is the page surface in both themes, so the
   scrim reads as "the page, dimmed" either way.

   The inset stops at the status strip rather than covering it: the
   strip carries the connection dot and its own ``hub gone`` banner,
   and it is the one piece of chrome that must stay reachable. */
.hub-gone {
  position: fixed;
  inset: var(--header-h) 0 var(--status-h) 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--bg) 94%, transparent);
  backdrop-filter: blur(2px);
  padding: 2rem;
}
.hub-gone-card {
  max-width: 52ch;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.7rem;
  background: var(--panel);
  border: 1px solid var(--err);
  border-radius: var(--radius-3);
  padding: 1.5rem 1.75rem;
  box-shadow: var(--shadow-2);
}
.hub-gone-card h2 {
  margin: 0;
  font-size: 1.05rem;
  color: var(--err);
}
.hub-gone-card p {
  margin: 0;
  font-size: 0.85rem;
  color: var(--fg-muted);
  line-height: 1.5;
}
.hub-gone-hint {
  background: var(--panel-2);
  border-radius: var(--radius-3);
  padding: 0.6rem 0.9rem;
}
.hub-gone-card code {
  font-family: var(--font-mono);
  background: var(--line);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
  font-size: 0.85em;
}
.hub-gone-actions {
  display: flex;
  gap: 0.5rem;
}
.hub-gone-actions button {
  font-size: 0.8rem;
  padding: 0.3rem 0.9rem;
  border-radius: var(--radius-2);
  border: 1px solid var(--line-strong);
  background: var(--panel-2);
  color: var(--fg);
  cursor: pointer;
}
.hub-gone-actions button:hover {
  background: var(--line);
}
.hub-gone-note {
  font-size: 0.75rem !important;
  color: var(--fg-faint) !important;
}
</style>
