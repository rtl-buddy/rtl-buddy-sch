<template>
  <div class="app">
    <!-- Top bar, per the hub chrome contract (rtl_buddy
         docs/concepts/hub.md): identity left, app controls centre,
         app switcher right. Every hub app wears the same two strips
         so moving between them costs nothing. -->
    <header class="app-header">
      <div class="app-identity">
        <img class="app-logo" :src="LOGO_URL" alt="" width="18" height="18" />
        <h1>rtl-buddy-view</h1>
        <span class="design-name" v-if="store.graph">{{ store.graph.top }}</span>
        <!-- The rtl-buddy / spa build chips used to sit here. They
             answer a once-a-session question and now live in the hub
             popover's "versions" section; the top bar keeps only
             wordmark, model name, tabs, DUT/TB, picker, switcher. -->
      </div>
      <div class="app-controls">
        <!-- "Design", not "Hierarchy": the canvas has its own
             Hierarchy/Flow mode tabs visible at the same time, and two
             controls with the same word on them a few pixels apart is
             the collision this rename removes. The store's tab id
             stays ``hierarchy`` (it is on URLs and in tests). -->
        <nav class="app-tabs" v-if="store.status === 'ready'">
          <button
            type="button"
            :class="{ active: store.activeTab === 'hierarchy' }"
            title="The design hierarchy canvas with its overlays"
            @click="store.setActiveTab('hierarchy')"
          >Design</button>
          <button
            type="button"
            :class="{ active: store.activeTab === 'axi-perf' }"
            :disabled="!hasAxiPerf"
            :title="hasAxiPerf ? 'AXI throughput and backpressure per bundle' : AXI_PERF_HINT"
            @click="store.setActiveTab('axi-perf')"
          >AXI Performance</button>
        </nav>
        <ModelPicker />
      </div>
      <nav class="app-switcher" aria-label="Hub apps">
        <a
          v-for="app in switcherApps"
          :key="app.key"
          class="switch-link"
          v-bind="switcherLinkAttrs(app)"
          :title="app.title"
        >{{ app.label }}</a>
        <button
          type="button"
          class="theme-toggle"
          :title="themeToggleTitle"
          :aria-label="themeToggleTitle"
          @click="cycleTheme"
        >{{ themeToggleGlyph }}</button>
      </nav>
    </header>
    <!-- Keep the body mounted across status transitions so a model
         switch (status='ready' → 'loading' → 'ready') doesn't tear
         down the canvas + sidebar mid-flight. The loading overlay
         renders on top instead, giving the user clear "something is
         happening" feedback while preserving the previous view as a
         visual anchor. -->
    <div
      class="app-body"
      v-if="store.graph && (store.status !== 'ready' || store.activeTab === 'hierarchy')"
    >
      <aside class="sidebar" :style="{ width: sidebarWidth + 'px' }">
        <CollapsiblePanel title="Overlays" persist-key="overlays">
          <OverlayPanel />
        </CollapsiblePanel>
        <CollapsiblePanel
          v-if="!store.selectedEdgeObj"
          title="Node detail"
          persist-key="node-detail"
          :badge="store.selection ? '●' : null"
        >
          <NodeDetail />
        </CollapsiblePanel>
        <CollapsiblePanel
          v-else
          title="Edge detail"
          persist-key="edge-detail"
          :badge="'●'"
        >
          <EdgeDetail />
        </CollapsiblePanel>
        <!-- Diagnostics default-expanded so dynamically-arriving
             items (hub-pushed after page load) are immediately
             visible — auto-collapse based on initial count would
             swallow late-arriving updates and broke the e2e
             ``diagnostics_set`` tests. -->
        <CollapsiblePanel
          title="Diagnostics"
          persist-key="diagnostics"
          :badge="diagnosticsCount || null"
        >
          <DiagnosticsPanel />
        </CollapsiblePanel>
      </aside>
      <div
        class="sidebar-resizer"
        role="separator"
        aria-orientation="vertical"
        title="Drag to resize"
        @mousedown.prevent="startResize"
      ></div>
      <div class="canvas-wrap">
        <GraphCanvas />
        <div v-if="store.status === 'loading'" class="loading-overlay">
          <div class="spinner" aria-hidden="true"></div>
          <span>Loading…</span>
        </div>
      </div>
    </div>
    <div
      class="app-body axi-tab"
      v-else-if="store.status === 'ready' && store.activeTab === 'axi-perf'"
    >
      <AxiPerfView />
    </div>
    <div class="empty-state" v-else-if="store.status === 'idle'">
      <h2>Load a view.json</h2>
      <p>
        Drop a <code>view.json</code> file here, or pass
        <code>?view=path/to/view.json</code> in the URL.
      </p>
      <p class="empty-hint">
        Or start the hub:
        <code>rb hub start --serve-viewer --model &lt;model_name&gt;</code><br />
        then open <code>http://127.0.0.1:&lt;http_port&gt;/</code>.
        The model picker in the header lets you switch between models
        without restarting.
      </p>
      <input type="file" accept=".json,application/json" @change="onPickFile" />
    </div>
    <div class="loading" v-else-if="store.status === 'loading'">
      <div class="spinner" aria-hidden="true"></div>
      <span>Loading…</span>
    </div>
    <div class="error" v-else-if="store.status === 'error'">
      <h2>Could not load this view</h2>
      <p class="error-status" v-if="store.errorMeta && store.errorMeta.status">
        The hub responded HTTP {{ store.errorMeta.status }}.
      </p>
      <pre>{{ store.error }}</pre>
      <div class="error-reasons">
        <p class="error-reasons-title">Possible reasons:</p>
        <ul>
          <li>
            The test or model name isn't unique across suites — pick the exact
            row from the header selector (it disambiguates by testbench + DUT).
          </li>
          <li>
            The name doesn't exist in any <code>tests.yaml</code> /
            <code>models.yaml</code> the hub discovered.
          </li>
          <li>
            The view build failed (filelist or parse error). Check the hub log
            at <code>artefacts/hier/&lt;model&gt;/…/hier.log</code>.
          </li>
          <li>The hub isn't reachable — not started, restarted, or wrong port.</li>
        </ul>
        <p class="error-hint">
          Pick a different view from the header selector — no reload needed.
        </p>
      </div>
    </div>
    <!-- Bottom status strip, per the hub chrome contract: connection
         dot + one vocabulary (connected / connecting… / offline) on
         the left, peer list in the middle, message area on the right.
         HubStatus owns all three. -->
    <footer class="app-status-strip">
      <HubStatus />
    </footer>
    <ToastHost />
  </div>
</template>

<script setup>
// Top-level App shell. The state machine is owned by the Pinia
// store; this component is a presentational switch over
// store.status (``idle`` / ``loading`` / ``ready`` / ``error``).
//
// Drag-and-drop is wired at the document level so users can drop
// onto any part of the viewer without targeting a specific zone.

import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useViewerStore } from './store.js'
import GraphCanvas from './components/GraphCanvas.vue'
import OverlayPanel from './components/OverlayPanel.vue'
import NodeDetail from './components/NodeDetail.vue'
import EdgeDetail from './components/EdgeDetail.vue'
import AxiPerfView from './components/AxiPerfView.vue'
import HubStatus from './components/HubStatus.vue'
import ModelPicker from './components/ModelPicker.vue'
import ToastHost from './components/ToastHost.vue'
import DiagnosticsPanel from './components/DiagnosticsPanel.vue'
import CollapsiblePanel from './components/CollapsiblePanel.vue'
import { initHub, useHub } from './composables/useHub.js'
import { initEventSync } from './composables/useEventSync.js'
import {
  SIDEBAR_DEFAULT_WIDTH_PX,
  SIDEBAR_MIN_WIDTH_PX,
  SIDEBAR_MAX_WIDTH_PX,
} from './layout/constants.js'
import { hubApps, switcherLinkAttrs } from './hubApps.js'
import { LOGO_URL } from './identity.js'
import { initTheme, themePreference, setThemePreference } from './theme.js'
import { AXI_PERF_HINT } from './cliHints.js'
import { makeGlobalKeydownHandler } from './keyboard.js'

const store = useViewerStore()
const hub = useHub()

// --- Resizable sidebar (drag the divider between sidebar and canvas).
// The clamp and the default live in layout/constants.js because the
// stylesheet needs the same numbers; tests/tokens.spec.js keeps the two
// sides honest.
const SIDEBAR_MIN = SIDEBAR_MIN_WIDTH_PX
const SIDEBAR_MAX = SIDEBAR_MAX_WIDTH_PX
function loadSidebarWidth() {
  try {
    const v = Number(localStorage.getItem('rb-sidebar-width'))
    if (Number.isFinite(v) && v >= SIDEBAR_MIN && v <= SIDEBAR_MAX) return v
  } catch {
    /* localStorage unavailable — fall through to the default */
  }
  return SIDEBAR_DEFAULT_WIDTH_PX
}
const sidebarWidth = ref(loadSidebarWidth())
function startResize(e) {
  const startX = e.clientX
  const startW = sidebarWidth.value
  const onMove = (ev) => {
    const w = startW + (ev.clientX - startX)
    sidebarWidth.value = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, w))
  }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    try {
      localStorage.setItem('rb-sidebar-width', String(sidebarWidth.value))
    } catch {
      /* best-effort persistence */
    }
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

const hasAxiPerf = computed(
  () =>
    Array.isArray(store.graph?.overlays_present) &&
    store.graph.overlays_present.includes('axi-perf'),
)

const diagnosticsCount = computed(() => {
  const by = store.diagnosticsBySource || {}
  let n = 0
  for (const items of Object.values(by)) {
    if (Array.isArray(items)) n += items.length
  }
  return n
})

// App switcher (top-bar right). Empty unless the hub is serving us —
// off the hub, ``/`` and ``/graph`` are not ours to link to.
const switcherApps = hubApps()

// Theme control. Three states so a user can go back to following the
// OS after pinning; ``system`` is the default and writes no attribute.
const THEME_CYCLE = { system: 'light', light: 'dark', dark: 'system' }
const THEME_GLYPH = { system: '◐', light: '☀', dark: '☾' }
const themeToggleGlyph = computed(() => THEME_GLYPH[themePreference.value] || '◐')
const themeToggleTitle = computed(
  () => `Theme: ${themePreference.value} — click for ${THEME_CYCLE[themePreference.value]}`,
)
function cycleTheme() {
  setThemePreference(THEME_CYCLE[themePreference.value] || 'light')
}

function onPickFile(event) {
  const file = event.target.files && event.target.files[0]
  if (file) store.loadFromFile(file)
}

function onDragOver(e) {
  e.preventDefault()
}

function onDrop(e) {
  e.preventDefault()
  const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]
  if (file) store.loadFromFile(file)
}

// Global keyboard shortcuts (Esc / u). The behaviour lives in
// keyboard.js so it can be unit-tested against a bare KeyboardEvent;
// App.vue owns nothing but the listener's lifetime.
const onGlobalKeydown = makeGlobalKeydownHandler({ store, hub })

onMounted(() => {
  document.addEventListener('dragover', onDragOver)
  document.addEventListener('drop', onDrop)
  document.addEventListener('keydown', onGlobalKeydown)
  // Watch for OS colour-scheme flips and ``data-theme`` pins so the
  // canvas — whose colours are baked, not inherited — redraws.
  initTheme()
  // Phase 10d: kick the hub composable. Same-origin /ws — the hub
  // injects window.__RTL_BUDDY_HUB__ when it serves the bundle.
  initHub({ store })
  // Phase 3 (axi-profiler#16): bidirectional state sync with the
  // marimo notebook over /api/events/sync.
  initEventSync({ store })
})
onBeforeUnmount(() => {
  document.removeEventListener('dragover', onDragOver)
  document.removeEventListener('drop', onDrop)
  document.removeEventListener('keydown', onGlobalKeydown)
})
</script>

<style>
/* App shell. Global (unscoped) because these class names are the
   chrome contract every hub app implements, and because the layout
   numbers (--header-h, --status-h, --sidebar-w) have to reach the
   children. Colours, radii and type come from tokens.css + the
   vendored hub sheet; the document base lives in app.css. */

/* -- top bar: identity left / controls centre / switcher right ------- */
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  height: var(--header-h);
  box-sizing: border-box;
  padding: 0 1rem;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
}
.app-identity,
.app-controls,
.app-switcher {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  min-width: 0;
}
.app-identity { flex: 0 1 auto; }
.app-controls { flex: 1 1 auto; justify-content: center; }
.app-switcher { flex: 0 0 auto; }
/* The one piece of brand art in the SPA chrome: an 18px chip beside
   the wordmark. Minimal by decree — no hero, no watermark. */
.app-logo {
  display: block;
  flex-shrink: 0;
  image-rendering: -webkit-optimize-contrast;
}
.app-header h1 {
  font-size: var(--fs-head);
  margin: 0;
  font-weight: 600;
  white-space: nowrap;
}
.design-name {
  font-family: var(--font-mono);
  color: var(--fg-muted);
  font-size: var(--fs-small);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.app-tabs { display: flex; gap: 0; }
.app-tabs button {
  background: transparent;
  border: 1px solid transparent;
  border-bottom: 2px solid transparent;
  padding: 0.35rem 0.75rem;
  font-size: 0.85rem;
  color: var(--fg-muted);
  cursor: pointer;
  white-space: nowrap;
}
.app-tabs button:hover:not(:disabled) {
  color: var(--fg);
}
/* One accent family: the tab underline was the SPA's indigo. */
.app-tabs button.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 600;
}
/* app.css owns ``button:disabled``; the tab only drops the border it
   would otherwise keep from the shared rule. */
.app-tabs button:disabled {
  background: transparent;
  border-color: transparent;
}
/* App switcher: ⌂ hub back to the landing, then the sibling apps the
   hub says it has data for. Rendered only under the hub.

   Byte-for-byte the panes' ``nav.switcher a`` rule (rtl_buddy
   hub/graph_page.html, hub/cov_page.html): --panel-2 fill, --fg text,
   a --line-strong hairline, --radius-2, .25rem/.55rem padding, accent
   on hover. The three apps sit next to each other in a tab strip, so
   a switcher that looks different in one of them reads as a different
   control. The theme toggle rides along — it is the same size of
   button in the same group. */
.switch-link,
.theme-toggle {
  font-family: var(--font-sans);
  font-size: var(--fs-small);
  padding: 0.25rem 0.55rem;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-2);
  background: var(--panel-2);
  color: var(--fg);
  text-decoration: none;
  cursor: pointer;
  white-space: nowrap;
}
.switch-link:hover,
.theme-toggle:hover {
  color: var(--accent);
  border-color: var(--accent);
}

/* -- body ------------------------------------------------------------ */
/* Header and status strip are both fixed-height, so the body is what
   is left. Both numbers are tokens (tests/tokens.spec.js keeps them in
   step with layout/constants.js) — they used to be a magic 48. */
.app-body { display: flex; height: calc(100vh - var(--header-h) - var(--status-h)); }
.app-body.axi-tab { display: block; overflow: auto; background: var(--panel); }
.sidebar {
  width: var(--sidebar-w);  /* fallback; overridden by the inline :style width */
  flex-shrink: 0;      /* honour the width in the flex row (don't squeeze) */
  border-right: 1px solid var(--line);
  padding: 0;
  overflow: auto;
  background: var(--panel);
}
/* Draggable divider between the sidebar and the canvas. */
.sidebar-resizer {
  flex: 0 0 5px;
  cursor: col-resize;
  background: transparent;
  margin-left: -1px;   /* sit over the sidebar's right border */
  z-index: 2;
}
.sidebar-resizer:hover,
.sidebar-resizer:active {
  background: var(--accent);
}
.canvas-wrap {
  flex: 1;
  position: relative;
  display: flex;
}
.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  /* Scrim over the previous view. ``--bg`` at 78% so it dims in dark
     as well; a baked rgba(248,250,252,…) was a white veil there. */
  background: color-mix(in srgb, var(--bg) 78%, transparent);
  backdrop-filter: blur(2px);
  font-size: 0.85rem;
  color: var(--fg-muted);
  z-index: 20;
  pointer-events: all;
}
.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid var(--line-strong);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: rb-spin 0.8s linear infinite;
}
@keyframes rb-spin {
  to { transform: rotate(360deg); }
}
.empty-state, .loading, .error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  height: calc(100vh - var(--header-h) - var(--status-h));
  text-align: center;
  padding: 2rem;
  /* content-box would add the 2rem padding on TOP of the calc and push
     the status strip below the fold — the strip is the one piece of
     chrome that has to stay reachable. */
  box-sizing: border-box;
}
.empty-state .empty-hint {
  font-size: 0.85rem;
  color: var(--fg-muted);
  max-width: 60ch;
  line-height: 1.5;
  background: var(--panel-2);
  padding: 0.75rem 1rem;
  border-radius: var(--radius-3);
}
.empty-state code, .empty-hint code {
  background: var(--line);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
  font-size: 0.85em;
}
.error pre {
  max-width: 60ch;
  white-space: pre-wrap;
  background: var(--err-bg);
  border: 1px solid var(--err);
  padding: 0.75rem;
  border-radius: var(--radius-2);
  color: var(--err);
}
.error-status {
  color: var(--err);
  font-weight: 600;
  margin: 0;
}
.error-reasons {
  max-width: 64ch;
  text-align: left;
  font-size: 0.85rem;
  color: var(--fg-muted);
  background: var(--panel-2);
  padding: 0.75rem 1rem;
  border-radius: var(--radius-3);
  line-height: 1.5;
}
.error-reasons-title {
  margin: 0 0 0.4rem;
  font-weight: 600;
}
.error-reasons ul {
  margin: 0;
  padding-left: 1.2rem;
}
.error-reasons li {
  margin: 0.2rem 0;
}
.error-reasons .error-hint {
  margin: 0.6rem 0 0;
  font-style: italic;
}
.error code {
  background: var(--line);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
  font-size: 0.85em;
}

/* -- bottom status strip --------------------------------------------- */
/* The second half of the chrome contract. Fixed height so the body's
   calc() is exact; HubStatus lays out the three slots inside it. */
.app-status-strip {
  height: var(--status-h);
  box-sizing: border-box;
  display: flex;
  align-items: center;
  padding: 0 0.75rem;
  background: var(--panel);
  border-top: 1px solid var(--line);
  font-size: var(--fs-small);
}
</style>
