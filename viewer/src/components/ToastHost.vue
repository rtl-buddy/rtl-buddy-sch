<template>
  <div class="toast-host" aria-live="polite">
    <transition name="toast">
      <div
        v-if="store.hubError"
        class="toast"
        :data-code="store.hubError.code"
        role="alert"
      >
        <div class="body">
          <span class="code">{{ store.hubError.code }}</span>
          <span class="message">{{ store.hubError.message }}</span>
        </div>
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
import { useViewerStore } from '../store.js'
import { onBeforeUnmount, watch } from 'vue'

const store = useViewerStore()
const AUTO_DISMISS_MS = 6000

let timer = null

function dismiss() {
  if (timer) { clearTimeout(timer); timer = null }
  store.dismissHubError()
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
.toast .code {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  opacity: 0.75;
}
.toast .message {
  word-break: break-word;
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
