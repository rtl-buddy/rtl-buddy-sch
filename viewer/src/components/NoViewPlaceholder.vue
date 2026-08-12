<template>
  <div class="no-view" :class="rootClass" :data-placeholder="placeholder">
    <!-- ============================================================
         State 3 — the hub tried to build the schematic and the
         build failed. The most actionable of the lot: we have the hub's
         message, the tail of hier.log, and a short list of causes
         that account for most real failures.
         ============================================================ -->
    <template v-if="placeholder === 'view_generation_failed'">
      <h2>Could not build the schematic{{ modelSuffix }}</h2>
      <p class="error-status" v-if="httpStatus">
        The hub responded HTTP {{ httpStatus }}.
      </p>
      <pre class="failure-message">{{ store.error }}</pre>
      <div class="log-tail" v-if="logTail.length > 0">
        <p class="log-tail-title">
          Last {{ logTail.length }} lines of
          <code>{{ logPath || 'hier.log' }}</code
          >:
        </p>
        <pre class="log-tail-pre">{{ logTailText }}</pre>
      </div>
      <p class="log-path" v-if="logPath">
        Full log: <code>{{ logPath }}</code>
      </p>
      <div class="error-reasons">
        <p class="error-reasons-title">Known causes:</p>
        <ul>
          <li>interface-port tops need <code>frontend: slang</code></li>
          <li>
            vendor submodules not initialized
            (<code>git submodule update --init</code>)
          </li>
          <li><code>-v</code> library entries in filelists are unsupported</li>
        </ul>
        <p class="error-hint">
          Fix the cause, then hit Retry — no reload needed. Or pick a
          different model from the header selector.
        </p>
      </div>
      <div class="placeholder-actions">
        <button
          type="button"
          class="retry-button"
          :disabled="retrying"
          @click="retry"
        >
          {{ retrying ? 'Retrying…' : 'Retry' }}
        </button>
      </div>
    </template>

    <!-- ============================================================
         State 2 — hub is up, nothing selected. Not an error the user
         caused; it's the "you're one click away" state.
         ============================================================ -->
    <template v-else-if="placeholder === 'no_active_model'">
      <h2>No model is active</h2>
      <p>
        The hub is connected — it just doesn't have a model selected
        yet.
      </p>
      <p class="empty-hint">
        Pick one from the <strong>model selector</strong> in the header,
        or start the hub already pointed at a model:<br />
        <code>{{ HUB_START_WITH_MODEL_HINT }}</code>
      </p>
      <p class="placeholder-detail" v-if="store.error">{{ store.error }}</p>
    </template>

    <!-- ============================================================
         Unknown model — a stale ``?model=`` deep link, or a model
         that disappeared from models.yaml between sessions.
         ============================================================ -->
    <template v-else-if="placeholder === 'unknown_model'">
      <h2>No such model{{ modelSuffix }}</h2>
      <p class="placeholder-detail">{{ store.error }}</p>
      <p class="empty-hint">
        The hub doesn't know this name — the link may be stale, or the
        entry was renamed in <code>models.yaml</code>. Pick a model from
        the header selector; the list is what this hub actually has.
      </p>
    </template>

    <!-- ============================================================
         State 1 — no schematic requested. Two flavours: genuinely
         standalone (drag-drop a file) versus served BY a hub, where
         the file-drop advice is misleading on its own.
         ============================================================ -->
    <template v-else-if="placeholder === 'idle'">
      <h2>No schematic loaded</h2>
      <template v-if="hubReachable">
        <p>
          The hub on this origin is reachable — nothing has asked it for
          a schematic yet.
        </p>
        <p class="empty-hint">
          Pick a model from the <strong>selector</strong> in the header,
          or restart the hub pointed at one:<br />
          <code>{{ HUB_START_WITH_MODEL_HINT }}</code><br />
          You can also drop a <code>view.json</code> file here to inspect
          it offline.
        </p>
      </template>
      <template v-else>
        <p>
          Drop a <code>view.json</code> file here, or pass
          <code>?view=path/to/view.json</code> in the URL.
        </p>
        <p class="empty-hint">
          Or start the hub:
          <code>{{ HUB_START_WITH_MODEL_HINT }}</code><br />
          then open <code>http://127.0.0.1:&lt;http_port&gt;/</code>.
          The model picker in the header lets you switch between models
          without restarting.
        </p>
      </template>
      <input type="file" accept=".json,application/json" @change="onPickFile" />
    </template>

    <!-- ============================================================
         Generic failure — anything we can't classify, which is also
         where an older hub's bare text/plain 500 lands. We quote the
         body verbatim and keep the long-standing reasons list.
         ============================================================ -->
    <template v-else>
      <h2>Could not load this schematic</h2>
      <p class="error-status" v-if="httpStatus">
        The hub responded HTTP {{ httpStatus }}.
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
            The schematic build failed (filelist or parse error). Check
            the hub log at
            <code>artefacts/hier/&lt;model&gt;/…/hier.log</code>.
          </li>
          <li>The hub isn't reachable — not started, restarted, or wrong port.</li>
        </ul>
        <p class="error-hint">
          Pick a different model from the header selector — no reload
          needed.
        </p>
      </div>
      <div class="placeholder-actions" v-if="canRetry">
        <button
          type="button"
          class="retry-button"
          :disabled="retrying"
          @click="retry"
        >
          {{ retrying ? 'Retrying…' : 'Retry' }}
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
// "No view available" placeholder (rtl-buddy-view#130).
//
// One component, five renderings, because the user question is
// always the same ("why is there nothing here?") and the answer
// differs only in cause and next step. The state is *derived*, never
// stored separately — ``store.status`` plus the decoded hub error
// envelope in ``store.errorMeta.kind`` is enough:
//
//   idle + hub detected      → "the hub is up; you haven't asked for
//                               a view" (state 1, hub flavour)
//   idle + no hub            → today's drop-a-file screen (state 1)
//   error, no_active_model   → "connected, nothing selected" (state 2)
//   error, view_generation_failed
//                            → message + hier.log tail + causes +
//                              Retry (state 3)
//   error, unknown_model     → stale deep link
//   error, anything else     → the generic card, which is also where
//                              a pre-#130 hub's text/plain 500 lands
//
// The "hub is gone" case (state 4) is NOT here — it's a connection
// state, not a view state, so it belongs to ``HubGoneOverlay`` and
// can cover a perfectly-rendered graph.
//
// VOCABULARY: the pane is "the schematic" in prose (displayNames.js —
// the app's display name is ``rtl-buddy-sch``). ``view.json`` and
// ``?view=`` stay verbatim: they are the artefact and the query
// parameter, which the rebrand deliberately does not touch.
import { computed, ref } from 'vue'
import { useViewerStore } from '../store.js'
import { useHub } from '../composables/useHub.js'
import { HUB_START_WITH_MODEL_HINT } from '../cliHints.js'

const store = useViewerStore()
const hub = useHub()

const retrying = ref(false)

const errorKind = computed(() =>
  store.errorMeta && typeof store.errorMeta.kind === 'string'
    ? store.errorMeta.kind
    : null,
)

const placeholder = computed(() => {
  if (store.status !== 'error') return 'idle'
  return errorKind.value || 'generic_error'
})

const rootClass = computed(() =>
  placeholder.value === 'idle' || placeholder.value === 'no_active_model'
    ? 'empty-state'
    : 'error',
)

// A hub is "reachable" if anything hub-shaped has answered: /models,
// a #130 error envelope, or the /ws handshake. The last one races
// with bootstrap, hence checking all three rather than picking one.
const hubReachable = computed(
  () =>
    store.hubDetected ||
    hub.state.value === 'ready' ||
    hub.wasEverReady.value,
)

const httpStatus = computed(() =>
  store.errorMeta && store.errorMeta.status ? store.errorMeta.status : null,
)

const failedModel = computed(() =>
  store.errorMeta && store.errorMeta.model ? store.errorMeta.model : null,
)
const modelSuffix = computed(() =>
  failedModel.value ? ` for ${failedModel.value}` : '',
)

const logPath = computed(() =>
  store.errorMeta && store.errorMeta.logPath ? store.errorMeta.logPath : null,
)
const logTail = computed(() =>
  store.errorMeta && Array.isArray(store.errorMeta.logTail)
    ? store.errorMeta.logTail
    : [],
)
const logTailText = computed(() => logTail.value.join('\n'))

const canRetry = computed(() =>
  Boolean(store.errorMeta && store.errorMeta.url),
)

async function retry() {
  if (retrying.value) return
  retrying.value = true
  try {
    await store.retryLastLoad()
  } catch {
    // The store already flips back to status='error' with the new
    // body; the button just must not throw out of the handler.
  } finally {
    retrying.value = false
  }
}

function onPickFile(event) {
  const file = event.target.files && event.target.files[0]
  if (file) store.loadFromFile(file)
}
</script>

<style scoped>
/* The shared ``.empty-state`` / ``.error`` layout + code/pre styling
   lives in App.vue's global block; only the #130-specific bits are
   scoped here. No hex literals — the palette is theme.css/tokens.css
   (AGENTS.md § design tokens), so these surfaces follow the dark
   theme #135 added instead of punching a light hole in it. */
.placeholder-detail {
  max-width: 60ch;
  font-size: 0.85rem;
  color: var(--fg-muted);
  margin: 0;
}
.failure-message {
  max-width: 72ch;
  white-space: pre-wrap;
  background: var(--err-bg);
  border: 1px solid var(--err);
  padding: 0.75rem;
  border-radius: var(--radius-2);
  color: var(--err);
  margin: 0;
  text-align: left;
}
.log-tail {
  width: min(72ch, 100%);
  text-align: left;
}
.log-tail-title {
  margin: 0 0 0.3rem;
  font-size: 0.8rem;
  color: var(--fg-muted);
}
.log-tail-pre {
  max-height: 16rem;
  overflow: auto;
  margin: 0;
  padding: 0.6rem 0.75rem;
  background: var(--panel-2);
  border: 1px solid var(--line);
  color: var(--fg);
  border-radius: var(--radius-2);
  font-family: var(--font-mono);
  font-size: 0.75rem;
  line-height: 1.45;
  white-space: pre;
  text-align: left;
}
.log-path {
  margin: 0;
  font-size: 0.78rem;
  color: var(--fg-faint);
  max-width: 72ch;
  word-break: break-all;
}
.placeholder-actions {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.retry-button {
  font-size: 0.8rem;
  padding: 0.3rem 0.9rem;
  border-radius: var(--radius-2);
  border: 1px solid var(--line-strong);
  background: var(--panel);
  color: var(--fg);
  cursor: pointer;
}
.retry-button:hover:not(:disabled) {
  background: var(--panel-2);
}
.retry-button:disabled {
  color: var(--fg-faint);
  cursor: progress;
}
</style>
