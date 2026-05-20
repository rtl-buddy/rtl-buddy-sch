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
  bottom: 1rem;
  z-index: 100;
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  background: #1f2937;
  color: #f9fafb;
  padding: 0.6rem 0.9rem;
  border-radius: 6px;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.25);
  min-width: 16rem;
  max-width: 28rem;
  font-size: 0.85rem;
}
.toast[data-code='protocol_mismatch'],
.toast[data-code='bad_request'] {
  background: #7f1d1d;
}
.toast[data-code='not_connected'] {
  background: #78350f;
}
.toast .body {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  flex: 1;
}
.toast .code {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #fcd34d;
}
.toast[data-code='unresolvable'] .code { color: #fdba74; }
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
