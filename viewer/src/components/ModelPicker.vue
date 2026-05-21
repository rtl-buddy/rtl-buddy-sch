<template>
  <select
    v-if="store.availableModels.length > 0"
    class="model-picker"
    :value="store.activeModel ?? ''"
    @change="onChange"
    :title="title"
  >
    <option v-if="!store.activeModel" value="" disabled>(select a model)</option>
    <option
      v-for="m in store.availableModels"
      :key="m.models_file + '::' + m.name"
      :value="m.name"
    >
      {{ m.name }}{{ m.has_cdc ? '  ⚡' : '' }}
    </option>
  </select>
</template>

<script setup>
// Header model picker. Visible only when the hub's ``GET /models``
// endpoint responded with at least one entry — that's how we
// detect "running attached to a runtime-switching hub" vs.
// "drag-drop / embed.py mode". Selecting an option calls
// store.switchModel, which re-fetches view.json via the hub's
// ``?model=`` query and updates the URL bar so the link is
// shareable.
//
// The ``⚡`` glyph next to a name flags ``has_cdc=true`` — the
// model has a resolvable ``cdc:`` back-pointer and the clock-domain
// overlay will work once selected. No glyph = overlay unavailable
// for that model.
import { computed } from 'vue'
import { useViewerStore } from '../store.js'

const store = useViewerStore()

const title = computed(() => {
  if (!store.activeModel) return 'Pick a model'
  return `active: ${store.activeModel}`
})

async function onChange(event) {
  const next = event.target.value
  if (!next || next === store.activeModel) return
  try {
    await store.switchModel(next, { updateUrl: true })
  } catch {
    // store.switchModel surfaces errors via store.status='error' +
    // the toast host; the picker just needs to not throw out of
    // the event handler.
  }
}
</script>

<style scoped>
.model-picker {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.75rem;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  border: 1px solid #cbd5e1;
  background: #ffffff;
  color: #1f2937;
  cursor: pointer;
  max-width: 18rem;
}
.model-picker:focus {
  outline: 2px solid #94a3b8;
  outline-offset: 1px;
}
</style>
