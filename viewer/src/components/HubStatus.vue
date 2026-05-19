<template>
  <span class="hub-status" :data-state="hub.state.value">
    hub: {{ hub.state.value }}
  </span>
</template>

<script setup>
// Phase 5 stub. Phase 10d swaps the underlying composable for the
// real WebSocket connection; this component then lights up green
// when connected without any UI change.
import { useHub } from '../composables/useHub.js'
const hub = useHub()
</script>

<style scoped>
.hub-status {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.75rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: #f1f5f9;
  color: #64748b;
}
.hub-status[data-state='connected'] {
  background: #dcfce7;
  color: #166534;
}
</style>
