<template>
  <section class="node-detail" v-if="node">
    <h3 :title="node.id">
      <span v-for="(segment, i) in pathSegments" :key="i">
        <span v-if="i > 0" class="sep">.</span>{{ segment }}<wbr />
      </span>
    </h3>
    <div class="nav-actions">
      <button
        type="button"
        @click="store.descend(node.id)"
        :disabled="!store.selectedHasChildren"
        :title="store.selectedHasChildren ? 'Show only this subtree' : 'Leaf node — nothing to descend into'"
      >Descend</button>
      <button
        type="button"
        @click="store.ascend()"
        :disabled="!store.rootInstancePath"
        title="Show parent scope"
      >Up</button>
      <button
        type="button"
        @click="store.goToTop()"
        :disabled="!store.rootInstancePath"
        title="Back to design top"
      >Top</button>
      <button
        v-if="hasOpenable"
        type="button"
        class="open-source"
        @click="openInEditor"
        :title="openTitle"
      >Open in editor</button>
    </div>
    <div v-if="hasOpenable" class="open-target-row" :title="openTitle">
      <code class="open-target">{{ openTargetText }}</code>
      <span class="open-via" :data-mode="openVia">{{ openViaLabel }}</span>
    </div>
    <dl>
      <dt>Module</dt><dd>{{ node.module }}</dd>
      <dt v-if="node.is_blackbox">Status</dt>
      <dd v-if="node.is_blackbox" class="blackbox">blackbox</dd>
      <template v-if="hasParameters">
        <dt>Parameters</dt>
        <dd>
          <ul>
            <li v-for="(value, key) in node.parameters" :key="key">
              <code>.{{ key }}({{ value }})</code>
            </li>
          </ul>
        </dd>
      </template>
      <template v-for="group in portGroups" :key="group.dir">
        <dt>Ports — {{ group.label }} ({{ group.ports.length }})</dt>
        <dd>
          <table class="ports-table">
            <tbody>
              <tr v-for="port in group.ports" :key="port.name">
                <td class="port-name-cell">
                  <code>{{ port.name }}</code>
                  <code
                    v-if="port.port_kind === 'interface' || port.port_kind === 'interface_signal'"
                    class="port-iface-tag"
                    :title="ifaceTagTitle(port)"
                  >{{ ifaceTag(port) }}</code>
                </td>
                <td class="port-expr-cell">
                  <code v-if="port.expr" class="port-expr">← {{ port.expr }}</code>
                </td>
              </tr>
            </tbody>
          </table>
        </dd>
      </template>
      <template v-if="coverage">
        <dt>Coverage</dt>
        <dd class="coverage-block">
          <div v-for="row in coverageRows" :key="row.label" class="cov-row">
            <span class="cov-label">{{ row.label }}</span>
            <span class="cov-bar" :title="`${row.covered}/${row.total}`">
              <span
                class="cov-bar-fill"
                :style="{ width: row.pct + '%', background: row.color }"
              ></span>
            </span>
            <span class="cov-nums">{{ row.covered }}/{{ row.total }} ({{ row.pct }}%)</span>
          </div>
          <a
            v-if="coverage.coverview_link"
            class="coverview-link"
            :href="coverage.coverview_link"
            target="_blank"
            rel="noopener"
            title="Open this module's source file in Coverview"
          >Open in Coverview ↗</a>
        </dd>
      </template>
      <template v-for="(payload, name) in genericOverlays" :key="name">
        <dt>overlay: {{ name }}</dt>
        <dd><pre>{{ JSON.stringify(payload, null, 2) }}</pre></dd>
      </template>
    </dl>
  </section>
  <section class="node-detail empty" v-else>
    <p>Click a node to see its ports, parameters, and overlay values.</p>
  </section>
</template>

<script setup>
// Per-node detail panel. Driven entirely by the store's
// selection; click handling lives in GraphCanvas.
//
// Overlay values are rendered raw as JSON — readable, doesn't
// require this component to know about every overlay's payload
// shape, and stays useful for unknown / future overlays the
// viewer doesn't have a dedicated renderer for.
import { computed } from 'vue'
import { useViewerStore } from '../store.js'
import { useHub } from '../composables/useHub.js'
import { heatColor } from '../overlays/coverage.js'

const store = useViewerStore()
const hub = useHub()
const node = computed(() => store.selectedNode)
const pathSegments = computed(() => (node.value?.id || '').split('.'))
const hasParameters = computed(
  () => node.value && node.value.parameters && Object.keys(node.value.parameters).length > 0,
)
// Coverage gets a dedicated section (progress bars + Coverview deep
// link) instead of the raw-JSON fallback the generic loop renders;
// everything else stays on the generic path so unknown / future
// overlays remain inspectable.
const coverage = computed(
  () => (node.value && node.value.overlays && node.value.overlays.coverage) || null,
)
const COVERAGE_CHANNELS = [
  ['lines', 'lines'],
  ['branches', 'branches'],
  ['toggles', 'toggles'],
]
const coverageRows = computed(() => {
  if (!coverage.value) return []
  const rows = []
  for (const [key, label] of COVERAGE_CHANNELS) {
    const ch = coverage.value[key]
    if (!ch || typeof ch.pct !== 'number') continue
    rows.push({
      label,
      covered: ch.covered,
      total: ch.total,
      pct: ch.pct,
      color: heatColor(ch.pct),
    })
  }
  return rows
})
const genericOverlays = computed(() => {
  if (!node.value || !node.value.overlays) return {}
  const out = {}
  for (const [name, payload] of Object.entries(node.value.overlays)) {
    if (name === 'coverage') continue
    out[name] = payload
  }
  return out
})
// Group ports by direction so the panel reads like a port list in
// an SV declaration — inputs first, then outputs, then inout /
// other. Interface bundles (``port_kind === 'interface'``) have no
// direction and would otherwise pile up under "unknown"; we route
// them to a dedicated "interfaces" group instead. Flattened
// interface signals (``port_kind === 'interface_signal'``) have a
// real ``dir`` pinned from the modport, so they land naturally in
// inputs / outputs. (#102, #105.)
const PORT_DIR_ORDER = ['input', 'output', 'inout', 'interface']
const PORT_DIR_LABEL = {
  input: 'inputs',
  output: 'outputs',
  inout: 'inout',
  interface: 'interfaces',
}
const portGroups = computed(() => {
  if (!node.value || !Array.isArray(node.value.ports) || node.value.ports.length === 0) {
    return []
  }
  const groups = new Map()
  for (const port of node.value.ports) {
    const dir =
      port.port_kind === 'interface' ? 'interface' : port.dir || 'unknown'
    if (!groups.has(dir)) groups.set(dir, [])
    groups.get(dir).push(port)
  }
  const out = []
  for (const dir of PORT_DIR_ORDER) {
    if (groups.has(dir)) {
      out.push({ dir, label: PORT_DIR_LABEL[dir], ports: groups.get(dir) })
      groups.delete(dir)
    }
  }
  // Any remaining (unknown direction) at the end.
  for (const [dir, ports] of groups) {
    out.push({ dir, label: dir, ports })
  }
  return out
})

function ifaceDescriptor(port) {
  // ``test_mem_if.sub`` when both are present; just the type when no
  // modport; just the modport when no type — should never happen but
  // we degrade gracefully.
  if (port.interface_type && port.modport) return `${port.interface_type}.${port.modport}`
  return port.interface_type || port.modport || 'interface'
}
function ifaceTag(port) {
  return port.port_kind === 'interface_signal'
    ? `(from ${ifaceDescriptor(port)})`
    : `(${ifaceDescriptor(port)})`
}
function ifaceTagTitle(port) {
  return port.port_kind === 'interface_signal'
    ? `Signal flattened from ${ifaceDescriptor(port)}`
    : `Unresolved interface port: ${ifaceDescriptor(port)}`
}
// Openable when the node has either a structured ``source`` block
// (preferred — hub path uses (file, line, col)) or just a raw
// ``link`` URI (offline fallback through the OS).
const hasOpenable = computed(
  () => node.value && (node.value.source || node.value.link),
)
const openTargetText = computed(() => {
  const src = node.value && node.value.source
  if (src && typeof src.file === 'string') {
    const line = typeof src.start_line === 'number' ? src.start_line : 1
    return `${src.file}:${line}`
  }
  return (node.value && node.value.link) || ''
})
const openVia = computed(() => (hub.state.value === 'ready' ? 'hub' : 'os'))
const openViaLabel = computed(() =>
  openVia.value === 'hub' ? '(via hub)' : '(via OS)',
)
const openTitle = computed(() =>
  hub.state.value === 'ready'
    ? 'Request hub to open this in your editor'
    : 'Hub offline — falls back to the rtlbuddy:// URI via the OS',
)
function openInEditor() {
  if (node.value) hub.requestOpenSource(node.value)
}
</script>

<style scoped>
.node-detail { padding: 0.5rem; border-top: 1px solid #e5e7eb; margin-top: 0.5rem; }
.node-detail h3 {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.85rem;
  margin: 0 0 0.5rem;
  /* Long instance paths (top.u_a.u_b.…) get soft-wrap points at
     each dot via <wbr/>, so the heading wraps where it makes
     visual sense instead of mid-segment. */
  word-break: normal;
  overflow-wrap: break-word;
  line-height: 1.3;
}
.node-detail h3 .sep { color: #94a3b8; }
.node-detail dt {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
  margin-top: 0.5rem;
}
.node-detail dd { margin: 0.1rem 0 0; font-size: 0.85rem; }
.node-detail dd ul { margin: 0; padding-left: 1rem; }
.node-detail dd pre {
  background: #f1f5f9;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  font-size: 0.75rem;
}
.empty { color: #64748b; font-size: 0.85rem; }
.nav-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-bottom: 0.25rem;
}
.nav-actions button {
  border: 1px solid #cbd5e1;
  background: #ffffff;
  padding: 0.15rem 0.5rem;
  cursor: pointer;
  border-radius: 4px;
  font-size: 0.75rem;
}
.nav-actions button:disabled {
  color: #94a3b8;
  background: #f1f5f9;
  cursor: not-allowed;
}
/* Open-in-editor sits next to the navigation buttons (always at
   the top of the panel) so it doesn't migrate as the per-node
   data section grows/shrinks. The file:line hint lives on its
   own row immediately under so long paths can wrap without
   pushing the buttons around. */
.nav-actions .open-source {
  margin-left: auto;
}
.open-target-row {
  margin-bottom: 0.5rem;
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.open-target {
  font-size: 0.7rem;
  color: #64748b;
  word-break: break-all;
}
/* "(via hub)" / "(via OS)" tag tells the user how the next click
   will be routed — green when the hub is connected and will
   handle the request inline, grey when we'll fall back to a
   ``rtlbuddy://`` URI dispatched through the OS. */
.open-via {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0.05rem 0.3rem;
  border-radius: 3px;
}
.open-via[data-mode='hub'] {
  background: #dcfce7;
  color: #166534;
}
.open-via[data-mode='os'] {
  background: #f1f5f9;
  color: #64748b;
}
.blackbox { color: #b45309; }
.coverage-block { font-size: 0.78rem; }
.cov-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: 0.15rem 0;
}
.cov-label {
  width: 4.5rem;
  color: #475569;
}
.cov-bar {
  flex: 1;
  height: 0.55rem;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 3px;
  overflow: hidden;
  display: inline-block;
}
.cov-bar-fill {
  display: block;
  height: 100%;
}
.cov-nums {
  color: #64748b;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.coverview-link {
  display: inline-block;
  margin-top: 0.25rem;
  font-size: 0.75rem;
  color: #2563eb;
}
.ports-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}
.ports-table td {
  vertical-align: top;
  padding: 0.05rem 0.25rem 0.05rem 0;
}
.port-name-cell {
  width: 1%;
  white-space: nowrap;
  color: #1e293b;
}
.port-expr-cell {
  color: #475569;
  word-break: break-all;
}
.port-expr { color: #475569; }
.port-iface-tag {
  margin-left: 0.5em;
  color: #92400e;       /* amber-800 — matches the block-flow tint */
  background: #fef3c7;  /* amber-100 */
  font-style: italic;
  font-size: 0.85em;
  padding: 0 0.3em;
  border-radius: 3px;
}
</style>
