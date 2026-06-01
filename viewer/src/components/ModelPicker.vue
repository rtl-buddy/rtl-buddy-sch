<template>
  <div class="picker-group" v-if="showAny">
    <!-- DUT / TB segmented control. Visible only when the hub has
         advertised at least one test via ``GET /tests`` (#99 / 6b);
         standalone / older-hub deployments keep the original
         model-only flow with no extra chrome. -->
    <div
      v-if="store.availableTests.length > 0"
      class="view-mode-toggle"
      role="tablist"
      aria-label="Hierarchy view mode"
    >
      <button
        type="button"
        role="tab"
        :aria-selected="!store.viewModeTb"
        :class="{ active: !store.viewModeTb }"
        title="Show the DUT hierarchy (the today's-default --top view)"
        @click="onSelectMode('dut')"
      >DUT</button>
      <button
        type="button"
        role="tab"
        :aria-selected="store.viewModeTb"
        :class="{ active: store.viewModeTb }"
        title="Show the testbench hierarchy with the DUT called out as a subtree"
        @click="onSelectMode('tb')"
      >TB</button>
    </div>
    <!-- Picker. In DUT mode this lists models; in TB mode it lists
         tests (each test resolves to a (model, tb) pair on the hub
         side, so picking one selects both). -->
    <select
      v-if="store.viewModeTb"
      class="model-picker"
      :value="activeTestKey"
      @change="onChangeTest"
      :title="title"
    >
      <option v-if="!store.activeTest" value="" disabled>(select a test)</option>
      <option
        v-for="t in sortedTests"
        :key="testKey(t)"
        :value="testKey(t)"
      >
        {{ testLabel(t) }}
      </option>
    </select>
    <select
      v-else-if="store.availableModels.length > 0"
      class="model-picker"
      :value="store.activeModel ?? ''"
      @change="onChangeModel"
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
  </div>
</template>

<script setup>
// Header model picker. Visible only when the hub advertised at
// least one model via ``GET /models`` (that's how we detect
// "running attached to a runtime-switching hub" vs. "drag-drop /
// embed.py mode"). Selecting an option calls store.switchModel,
// which re-fetches view.json via the hub's ``?model=`` query and
// updates the URL bar so the link is shareable.
//
// The ``⚡`` glyph next to a name flags ``has_cdc=true`` — the
// model has a resolvable ``cdc:`` back-pointer and the clock-domain
// overlay will work once selected. No glyph = overlay unavailable
// for that model.
//
// #99 / 6c: when the hub advertises tests via ``GET /tests``, a
// DUT|TB segmented control appears next to the picker and TB mode
// swaps the picker contents to the test list. Each option reads
// ``tb — model — test``: the testbench top first (so same-tb tests
// cluster), then the model (DUT) — a tb name like ``tb_top`` is reused
// across many DUTs, so the model is what tells them apart — then the
// test name. Switching to a test also implicitly switches the model.
// The toggle is hidden when no tests are available.
import { computed } from 'vue'
import { useViewerStore } from '../store.js'

const store = useViewerStore()

const showAny = computed(
  () => store.availableModels.length > 0 || store.availableTests.length > 0,
)

// A test name alone isn't unique (``smoke`` lives in several suites), so
// the option value is the (tests_file, name) pair. ``switchTest`` sends
// the file back to the hub to disambiguate.
const testKey = (t) => `${t.tests_file || ''}::${t.name}`

// Option label: ``tb — model — test`` (see header comment), with
// graceful fallbacks for entries missing a field.
const testLabel = (t) =>
  [t.tb, t.model, t.name].filter((s) => s != null && s !== '').join('  —  ')

// TB-mode picker order matches the label: tb, then model (DUT), then
// test name — so tests cluster by testbench and then by DUT instead of
// scattering by generic test names. Sort a copy; never mutate the store.
const sortedTests = computed(() =>
  [...store.availableTests].sort(
    (a, b) =>
      (a.tb || '').localeCompare(b.tb || '') ||
      (a.model || '').localeCompare(b.model || '') ||
      (a.name || '').localeCompare(b.name || ''),
  ),
)

// The selected <option>'s value is its composite key; bind the select to
// the active test's key so the right row shows as chosen (and so the
// placeholder shows after a failed switch, which clears activeTest).
const activeTestKey = computed(() =>
  store.activeTest
    ? `${store.activeTestFile || ''}::${store.activeTest}`
    : '',
)

const title = computed(() => {
  if (store.viewModeTb) {
    return store.activeTest ? `active test: ${store.activeTest}` : 'Pick a test'
  }
  return store.activeModel ? `active model: ${store.activeModel}` : 'Pick a model'
})

async function onChangeModel(event) {
  const next = event.target.value
  if (!next || next === store.activeModel) return
  try {
    await store.switchModel(next, { updateUrl: true })
  } catch {
    // store surfaces errors via status='error' + toast; the picker
    // just needs to not throw out of the event handler.
  }
}

async function onChangeTest(event) {
  const next = event.target.value // composite (tests_file::name) key
  if (!next || next === activeTestKey.value) return
  const entry = store.availableTests.find((t) => t && testKey(t) === next)
  if (!entry) return
  try {
    await store.switchTest(entry.name, {
      updateUrl: true,
      testsFile: entry.tests_file || null,
    })
  } catch {
    /* see onChangeModel */
  }
}

async function onSelectMode(mode) {
  // Clicking the inactive segment switches the rendered view.
  // ``dut`` re-selects the current model (re-fetch is cheap thanks
  // to the hub's per-model cache); ``tb`` selects the test that
  // backs the currently-active model when there's an obvious choice,
  // and otherwise flips the picker open so the user picks one.
  if (mode === 'dut' && store.viewModeTb) {
    const fallbackModel = store.activeModel
    if (fallbackModel) {
      try {
        await store.switchModel(fallbackModel, { updateUrl: true })
      } catch {
        /* see onChangeModel */
      }
    } else {
      // Nothing to load — just flip the flag so the picker becomes
      // the model list. The user picks next.
      store.viewModeTb = false
    }
    return
  }
  if (mode === 'tb' && !store.viewModeTb) {
    // Prefer the test whose model matches the currently-active one;
    // that's the "least surprise" path when a user clicks TB while
    // looking at a DUT-rooted view.
    const match = store.availableTests.find(
      (t) => t && (!store.activeModel || t.model === store.activeModel),
    )
    if (match) {
      try {
        await store.switchTest(match.name, {
          updateUrl: true,
          testsFile: match.tests_file || null,
        })
      } catch {
        /* see onChangeModel */
      }
    } else {
      // No obvious choice — flip the flag so the user picks one
      // from the now-visible test list.
      store.viewModeTb = true
    }
  }
}
</script>

<style scoped>
.picker-group {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}
.view-mode-toggle {
  display: inline-flex;
  border: 1px solid #cbd5e1;
  border-radius: 4px;
  overflow: hidden;
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.72rem;
}
.view-mode-toggle button {
  background: #ffffff;
  border: 0;
  padding: 0.2rem 0.55rem;
  color: #475569;
  cursor: pointer;
  font: inherit;
}
.view-mode-toggle button + button {
  border-left: 1px solid #e2e8f0;
}
.view-mode-toggle button.active {
  background: #1e293b;
  color: #ffffff;
  cursor: default;
}
.view-mode-toggle button:not(.active):hover {
  background: #f1f5f9;
}
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
