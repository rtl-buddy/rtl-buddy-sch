<template>
  <div class="toast-host" aria-live="polite">
    <transition name="toast">
      <div
        v-if="copy"
        class="toast"
        :data-code="store.hubError.code"
        :data-known="copy.known ? 'yes' : 'no'"
        role="alert"
      >
        <div class="body">
          <span class="message">{{ copy.headline }}</span>
          <span class="detail" :title="copy.detail">{{ copy.detail }}</span>
        </div>
        <button
          v-if="copy.takeover"
          type="button"
          class="action"
          title="Evict the other tab's registration and connect this one"
          @click="takeOver"
        >Take over</button>
        <button type="button" class="dismiss" @click="dismiss" aria-label="Dismiss">×</button>
      </div>
    </transition>
  </div>
</template>

<script setup>
// Surfaces hub-side `error` envelopes from the closed code catalog
// (unresolvable | not_connected | bad_request | protocol_mismatch).
// One toast at a time; the latest error replaces the previous and
// auto-dismisses after a fixed delay so a stuck producer doesn't
// pin a stale toast.
//
// The toast is THE full rendering of a hub error: it leads with a
// sentence (hubErrors.js maps the closed catalog onto human wording)
// and demotes ``code — message`` to a secondary line for whoever is
// debugging the producer. The status strip gets a few words and an
// expiry; nobody else renders this event.
import { useViewerStore } from '../store.js'
import { useHub } from '../composables/useHub.js'
import { humanizeHubError } from '../hubErrors.js'
import { computed, onBeforeUnmount, watch } from 'vue'

const store = useViewerStore()
const hub = useHub()
const AUTO_DISMISS_MS = 6000

const copy = computed(() => humanizeHubError(store.hubError))

let timer = null

function dismiss() {
  if (timer) { clearTimeout(timer); timer = null }
  store.dismissHubError()
}

// "Another schematic tab is connected": the fix is to evict it. Same
// takeover hello the composable retries with automatically — this is
// the manual handle for when that retry was refused too.
function takeOver() {
  hub.reconnect({ takeover: true })
  dismiss()
}

watch(
  () => store.hubError,
  (next) => {
    if (timer) { clearTimeout(timer); timer = null }
    if (next) {
      timer = setTimeout(() => {
        store.dismissHubError()
        timer = null
      }, AUTO_DISMISS_MS)
    }
  },
)

onBeforeUnmount(() => {
  if (timer) { clearTimeout(timer); timer = null }
})
</script>

<style scoped>
.toast-host {
  position: fixed;
  right: 1rem;
  /* Clear of the bottom status strip — a toast that covers the
     connection state hides the thing the user needs to read to make
     sense of it. */
  bottom: calc(var(--status-h) + 0.75rem);
  z-index: 100;
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  background: var(--fg);
  color: var(--bg);
  padding: 0.6rem 0.9rem;
  border-radius: var(--radius-3);
  box-shadow: var(--shadow-2);
  min-width: 16rem;
  max-width: 28rem;
  font-size: 0.85rem;
}
.toast[data-code='protocol_mismatch'],
.toast[data-code='bad_request'] {
  background: var(--err);
  color: var(--accent-contrast);
}
.toast[data-code='not_connected'] {
  background: var(--warn);
  color: var(--accent-contrast);
}
.toast .body {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  flex: 1;
}
/* Headline first, machine detail second — the reverse of the
   original layout, which led with an uppercase error code. */
.toast .message {
  word-break: break-word;
  font-weight: 600;
}
.toast .detail {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  opacity: 0.75;
  word-break: break-word;
  /* Never let a chatty producer message push the toast past two
     lines — the title carries the rest. */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.toast .action {
  flex-shrink: 0;
  font-size: 0.75rem;
  padding: 0.15rem 0.5rem;
  border: 1px solid currentColor;
  border-radius: var(--radius-2);
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.toast .dismiss {
  background: transparent;
  border: 0;
  color: inherit;
  font-size: 1rem;
  cursor: pointer;
  line-height: 1;
  padding: 0 0.25rem;
}
.toast-enter-active, .toast-leave-active {
  transition: opacity 150ms ease, transform 150ms ease;
}
.toast-enter-from, .toast-leave-to {
  opacity: 0;
  transform: translateY(0.5rem);
}
</style>
