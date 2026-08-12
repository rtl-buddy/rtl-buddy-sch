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
      <template v-if="axiPins.length || axiInterconnect">
        <dt>AXI performance</dt>
        <dd>
          <table class="axi-table" v-if="axiPins.length">
            <thead>
              <tr><th>bundle</th><th>role</th><th>throughput</th><th>bp</th></tr>
            </thead>
            <tbody>
              <tr v-for="p in axiPins" :key="p.port">
                <td>
                  <code>{{ p.port }}</code>
                  <span v-if="p.errors" class="axi-err" :title="p.errors + ' AXI error response(s)'">⚠</span>
                </td>
                <td><span class="axi-role" :data-role="p.role">{{ p.role || '—' }}</span></td>
                <td class="axi-tput" :title="'peer: ' + (p.peer || '—')">
                  <span class="rd">R {{ p.rd }}</span> <span class="wr">W {{ p.wr }}</span>
                </td>
                <td class="rb-bp" :data-level="bpLevel(p.bp)">{{ p.bp.toFixed(0) }}%</td>
              </tr>
            </tbody>
          </table>
          <div v-if="axiInterconnect" class="axi-ic">
            interconnect — read {{ fmtBps(axiInterconnect.total_read_bps) }}, write
            {{ fmtBps(axiInterconnect.total_write_bps) }}; hottest master
            <code>{{ axiInterconnect.hottest_master }}</code>
          </div>
        </dd>
      </template>
      <template v-for="(payload, name) in otherOverlays" :key="name">
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
import { bpLevel } from '../palette.js'
import { themeVersion } from '../theme.js'
import { formatBandwidth as fmtBps } from '../format.js'

const store = useViewerStore()
const hub = useHub()
const node = computed(() => store.selectedNode)
const pathSegments = computed(() => (node.value?.id || '').split('.'))
const hasParameters = computed(
  () => node.value && node.value.parameters && Object.keys(node.value.parameters).length > 0,
)
// Coverage gets a dedicated section (progress bars + Coverview deep
// link) instead of the raw-JSON fallback the generic loop renders;
// everything without a dedicated section stays on the generic path
// so unknown / future overlays remain inspectable.
const coverage = computed(
  () => (node.value && node.value.overlays && node.value.overlays.coverage) || null,
)
const COVERAGE_CHANNELS = [
  ['lines', 'lines'],
  ['branches', 'branches'],
  ['toggles', 'toggles'],
]
const coverageRows = computed(() => {
  // ``heatColor`` resolves --cov-l at call time, so the rows have to be
  // recomputed when the theme flips (the ramp moves 82% → 38%).
  themeVersion.value
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

// --- axi-perf: render the overlay human-readably instead of raw JSON.
// (throughput formatting shared via ../format.js — bytes/s, decimal MB/GB)
function axiMaxBp(block) {
  const ch = block && block.channels
  if (!ch) return 0
  let best = 0
  for (const r of ['ar', 'aw', 'r', 'w', 'b']) {
    const c = ch[r]
    if (c && typeof c.bp_pct === 'number' && c.bp_pct > best) best = c.bp_pct
  }
  return best
}
// Initiator (master) first, then target (slave), then by name —
// matching the AXI Performance tab's ordering.
const axiPins = computed(() => {
  const ov = node.value?.overlays?.['axi-perf']
  const pins = ov && Array.isArray(ov.bundle_pins) ? ov.bundle_pins : []
  const rank = (r) => (r === 'master' ? 0 : r === 'slave' ? 1 : 2)
  return pins
    .map((p) => {
      const b = p.bundle || {}
      const t = b.throughput || {}
      const e = b.errors || {}
      return {
        port: p.port,
        role: p.role,
        peer: p.peer,
        rd: fmtBps(t.read_bps),
        wr: fmtBps(t.write_bps),
        bp: axiMaxBp(b),
        errors: (e.slverr || 0) + (e.decerr || 0),
      }
    })
    .sort((a, b) => rank(a.role) - rank(b.role) || a.port.localeCompare(b.port))
})
const axiInterconnect = computed(
  () => node.value?.overlays?.['axi-perf']?.interconnect || null,
)
// Every overlay WITHOUT a dedicated section above (axi-perf,
// coverage) falls back to the raw-JSON renderer.
const otherOverlays = computed(() => {
  const ov = node.value?.overlays || {}
  const out = {}
  for (const k of Object.keys(ov)) {
    if (k !== 'axi-perf' && k !== 'coverage') out[k] = ov[k]
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
/* No border-top / margin-top: the enclosing CollapsiblePanel already
   draws the section separator — both drawing it doubled the rule. */
.node-detail { padding: 0.5rem; }
.node-detail h3 {
  font-family: var(--font-mono);
  font-size: 0.85rem;
  margin: 0 0 0.5rem;
  /* Long instance paths (top.u_a.u_b.…) get soft-wrap points at
     each dot via <wbr/>, so the heading wraps where it makes
     visual sense instead of mid-segment. */
  word-break: normal;
  overflow-wrap: break-word;
  line-height: 1.3;
}
.node-detail h3 .sep { color: var(--fg-faint); }
.node-detail dt {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg-muted);
  margin-top: 0.5rem;
}
.node-detail dd { margin: 0.1rem 0 0; font-size: 0.85rem; }
.node-detail dd ul { margin: 0; padding-left: 1rem; }
.node-detail dd pre {
  background: var(--panel-2);
  padding: 0.25rem 0.5rem;
  border-radius: var(--radius-2);
  font-size: 0.75rem;
}
.empty { color: var(--fg-muted); font-size: 0.85rem; }
.nav-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-bottom: 0.25rem;
}
.nav-actions button {
  border: 1px solid var(--line-strong);
  background: var(--panel);
  color: var(--fg);
  padding: 0.15rem 0.5rem;
  cursor: pointer;
  border-radius: var(--radius-2);
  font-size: 0.75rem;
}
/* Disabled styling is the shared ``button:disabled`` rule in app.css —
   this component used to carry one of four different treatments. */
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
  color: var(--fg-muted);
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
  border-radius: var(--radius-1);
}
.open-via[data-mode='hub'] {
  background: var(--ok-bg);
  color: var(--ok);
}
.open-via[data-mode='os'] {
  background: var(--panel-2);
  color: var(--fg-muted);
}
.blackbox { color: var(--warn); }
.coverage-block { font-size: 0.78rem; }
.cov-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: 0.15rem 0;
}
.cov-label {
  width: 4.5rem;
  color: var(--fg-muted);
}
.cov-bar {
  flex: 1;
  height: 0.55rem;
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-1);
  overflow: hidden;
  display: inline-block;
}
.cov-bar-fill {
  display: block;
  height: 100%;
}
.cov-nums {
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.coverview-link {
  display: inline-block;
  margin-top: 0.25rem;
  font-size: 0.75rem;
  color: var(--accent);
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
  color: var(--fg);
}
.port-expr-cell {
  color: var(--fg-muted);
  word-break: break-all;
}
.port-expr { color: var(--fg-muted); }
.port-iface-tag {
  margin-left: 0.5em;
  color: var(--warn);        /* matches the block-flow interface tint */
  background: var(--warn-bg);
  font-style: italic;
  font-size: 0.85em;
  padding: 0 0.3em;
  border-radius: var(--radius-1);
}
.axi-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
}
.axi-table th {
  text-align: left;
  font-weight: 600;
  color: var(--fg-muted);
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0 0.4em 0.1em 0;
}
.axi-table td {
  padding: 0.1em 0.4em 0.1em 0;
  vertical-align: top;
  white-space: nowrap;
}
.axi-err { color: var(--err); margin-left: 0.25em; }
.axi-role {
  font-size: 0.7rem;
  padding: 0 0.35em;
  border-radius: var(--radius-1);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
/* Initiator reads as the accent, target as a neutral chip. The old
   blue/violet pair spent two hues on a two-value enum and pulled the
   retired indigo in with it. */
.axi-role[data-role='master'] { background: var(--accent); color: var(--accent-contrast); }
.axi-role[data-role='slave'] { background: var(--panel-2); color: var(--fg-muted); }
.axi-tput .rd { color: var(--info); }
.axi-tput .wr { color: var(--warn); margin-left: 0.4em; }
/* Backpressure colours are the shared ``.rb-bp`` ramp in app.css. */
.axi-ic {
  margin-top: 0.3em;
  font-size: 0.75rem;
  color: var(--fg-muted);
}
</style>
