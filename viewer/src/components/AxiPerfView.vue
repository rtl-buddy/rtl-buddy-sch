<template>
  <section class="axi-perf-view" v-if="hasData">
    <header class="view-header">
      <h2>AXI Performance</h2>
      <div class="header-stats">
        <span>{{ bundles.length }} bundle{{ bundles.length === 1 ? '' : 's' }}</span>
        <span v-if="interconnects.length">
          · {{ interconnects.length }} interconnect{{ interconnects.length === 1 ? '' : 's' }}
        </span>
      </div>
      <div class="header-actions">
        <button
          type="button"
          class="open-marimo-btn"
          :disabled="store.axiNotebookLaunching"
          @click="onOpenInMarimo"
        >
          <span v-if="store.axiNotebookLaunching">Launching…</span>
          <span v-else>Open in marimo ↗</span>
        </button>
      </div>
      <div
        v-if="store.axiNotebookError"
        class="open-marimo-err"
        role="alert"
        @click="store.axiNotebookError = null"
      >
        {{ store.axiNotebookError }} (click to dismiss)
      </div>
      <div
        v-if="store.axiPerfTimeWindow"
        class="time-window-chip"
        aria-label="notebook time window"
      >
        <span>
          notebook window:
          {{ fmtFs(store.axiPerfTimeWindow.t_start_fs) }} —
          {{ fmtFs(store.axiPerfTimeWindow.t_end_fs) }}
        </span>
        <button
          type="button"
          class="time-window-clear"
          @click="store.clearAxiPerfTimeWindow()"
          title="clear"
        >×</button>
      </div>
    </header>

    <div class="view-body">
      <aside class="bundle-list" aria-label="AXI bundles">
        <h3>Bundles</h3>
        <ul>
          <li
            v-for="b in bundles"
            :key="b.bundleKey"
            :class="{ selected: b.bundleKey === store.selectedAxiBundle }"
            @click="onSelectBundle(b.bundleKey)"
          >
            <div class="bundle-name">{{ b.name }}</div>
            <div class="bundle-meta">
              {{ b.from }} → {{ b.to }}
            </div>
            <div class="bundle-throughput">
              R {{ fmtBps(b.throughput.read_bps) }} ·
              W {{ fmtBps(b.throughput.write_bps) }}
            </div>
            <div class="bundle-bp" :class="bpClass(b.maxBp)">
              max bp {{ b.maxBp.toFixed(1) }}%
            </div>
          </li>
        </ul>
        <h3 v-if="interconnects.length">Interconnects</h3>
        <ul v-if="interconnects.length">
          <li
            v-for="ic in interconnects"
            :key="ic.node_path"
            :class="{ selected: ic.node_path === store.selectedAxiBundle }"
            @click="onSelectBundle(ic.node_path)"
          >
            <div class="bundle-name">{{ ic.node_path }}</div>
            <div class="bundle-meta">
              hottest: {{ ic.hottest_master }} → {{ ic.hottest_slave }}
            </div>
            <div class="bundle-throughput">
              R {{ fmtBps(ic.total_read_bps) }} ·
              W {{ fmtBps(ic.total_write_bps) }}
            </div>
          </li>
        </ul>
      </aside>

      <section class="bundle-detail">
        <div v-if="selectedBundle" class="bundle-detail-inner">
          <h3>{{ selectedBundle.name }}</h3>
          <dl class="meta">
            <dt>Master</dt><dd>{{ selectedBundle.from }}</dd>
            <dt>Slave</dt><dd>{{ selectedBundle.to }}</dd>
            <dt>Protocol</dt><dd>{{ selectedBundle.protocol }} / {{ selectedBundle.data_width }}b data / {{ selectedBundle.id_width }}b id</dd>
            <dt>Throughput</dt>
            <dd>R {{ fmtBps(selectedBundle.throughput.read_bps) }} · W {{ fmtBps(selectedBundle.throughput.write_bps) }}</dd>
            <dt>Theoretical max</dt>
            <dd>
              <template v-if="theoreticalMaxBps">
                {{ fmtBps(theoreticalMaxBps) }}/dir<span v-if="clockMhz" class="muted"> @ {{ clockMhz }} MHz</span>
                <span class="muted"> ({{ utilOfMaxPct }}% used)</span>
              </template>
              <template v-else>—</template>
            </dd>
            <dt>Outstanding</dt>
            <dd>
              R peak {{ selectedBundle.outstanding.read_peak }} avg {{ selectedBundle.outstanding.read_avg.toFixed(1) }} ·
              W peak {{ selectedBundle.outstanding.write_peak }} avg {{ selectedBundle.outstanding.write_avg.toFixed(1) }}
            </dd>
            <dt>Errors</dt>
            <dd :class="{ bad: selectedBundleErrors > 0 }">
              SLVERR {{ selectedBundle.errors.slverr }} · DECERR {{ selectedBundle.errors.decerr }}
            </dd>
          </dl>

          <h4>Channel utilization</h4>
          <div class="chart-wrap"><Bar :data="channelBarData" :options="channelBarOptions" /></div>

          <h4>AR → R first-data latency</h4>
          <p class="lat-summary">
            p50 {{ selectedBundle.latency_cycles.ar_to_r_first.p50 }} ·
            p95 {{ selectedBundle.latency_cycles.ar_to_r_first.p95 }} ·
            p99 {{ selectedBundle.latency_cycles.ar_to_r_first.p99 }} ·
            max {{ selectedBundle.latency_cycles.ar_to_r_first.max }} cycles
          </p>
          <div class="chart-wrap"><Bar :data="latencyHistData" :options="latencyHistOptions" /></div>
        </div>
        <div v-else-if="selectedInterconnect" class="interconnect-detail">
          <h3>Interconnect {{ selectedInterconnect.node_path }}</h3>
          <dl class="meta">
            <dt>Total throughput</dt>
            <dd>R {{ fmtBps(selectedInterconnect.total_read_bps) }} · W {{ fmtBps(selectedInterconnect.total_write_bps) }}</dd>
            <dt>Hottest pair</dt>
            <dd>{{ selectedInterconnect.hottest_master }} → {{ selectedInterconnect.hottest_slave }}</dd>
            <dt>Fairness (Jain)</dt>
            <dd>{{ selectedInterconnect.arbitration.fairness_jain.toFixed(2) }}</dd>
            <dt v-if="selectedInterconnect.arbitration.starved_masters.length">Starved</dt>
            <dd v-if="selectedInterconnect.arbitration.starved_masters.length">
              {{ selectedInterconnect.arbitration.starved_masters.join(', ') }}
            </dd>
          </dl>
        </div>
        <div v-else class="empty">
          <p>Select a bundle or interconnect on the left to see channel utilization, latency, and arbitration details.</p>
        </div>
      </section>
    </div>
  </section>
  <section class="axi-perf-view empty-state" v-else>
    <h2>No AXI performance data</h2>
    <p>
      Load a <code>view.json</code> produced with
      <code>--overlay axi-perf=axi-perf.json</code> to see bundle stats here.
    </p>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { Bar } from 'vue-chartjs'
import {
  Chart as ChartJS,
  BarElement,
  CategoryScale,
  LinearScale,
  Title,
  Tooltip,
  Legend,
} from 'chart.js'

import { useViewerStore } from '../store.js'
import { useEventSync } from '../composables/useEventSync.js'
import { formatBandwidth as fmtBps } from '../format.js'

ChartJS.register(BarElement, CategoryScale, LinearScale, Title, Tooltip, Legend)

const store = useViewerStore()
const eventSync = useEventSync()

function onSelectBundle(name) {
  store.selectAxiBundle(name)
  // Best-effort publish — broker may not be connected (no hub
  // running, or marimo not yet spawned). Auto-detected notebook
  // metadata mirrors the same source as the "Open in marimo" path
  // so the notebook can scope the filter to the right artefact.
  const auto = autoDetectedNotebookParams()
  eventSync.publish('selection', {
    bundle: name,
    test: auto?.test ?? null,
    suite_dir: auto?.suiteDir ?? null,
  })
}

function fmtFs(v) {
  if (!Number.isFinite(v)) return '?'
  // fs → human: ns above 1e6, µs above 1e9, ms above 1e12.
  if (Math.abs(v) >= 1e12) return (v / 1e12).toFixed(2) + ' ms'
  if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(2) + ' µs'
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(2) + ' ns'
  return v + ' fs'
}

// Collect every AXI bundle into a flat list keyed by bundle NAME, then
// layer in the interconnect roll-ups separately. The primary source is
// per-node ``overlays['axi-perf'].bundle_pins`` — the complete,
// name-keyed set that covers bundles sharing the same (master, slave)
// endpoints (e.g. two slave-side bundles on one DUT) which the legacy
// edge index collapses. Edge overlays are folded in as a back-compat
// fallback for edge-model designs. Dedup is first-wins by bundle name.
const bundles = computed(() => {
  const g = store.graph
  if (!g) return []
  const byKey = new Map()
  const add = (key, name, from, to, role, block) => {
    if (!block || byKey.has(key)) return
    byKey.set(key, { bundleKey: key, name, from, to, role: role || null, maxBp: maxBp(block), ...block })
  }
  // 1. Per-node bundle pins (interface + manifest-synthesized).
  if (Array.isArray(g.nodes)) {
    for (const n of g.nodes) {
      const ov = n.overlays && n.overlays['axi-perf']
      const pins = ov && Array.isArray(ov.bundle_pins) ? ov.bundle_pins : []
      for (const pin of pins) {
        const block = pin.bundle
        if (!block) continue
        const key = block.name || pin.port || n.id
        // The node owns one endpoint; ``peer`` is the other side.
        const from = pin.role === 'master' ? n.id : pin.peer || '?'
        const to = pin.role === 'master' ? pin.peer || '?' : n.id
        add(key, block.name || pin.port || '(unnamed bundle)', from, to, pin.role, block)
      }
    }
  }
  // 2. Edge overlays (back-compat for the (master, slave) edge model).
  if (Array.isArray(g.edges)) {
    for (const e of g.edges) {
      const block = e.overlays && e.overlays['axi-perf']
      if (!block) continue
      add(block.name || `${e.from}->${e.to}`, block.name || '(unnamed bundle)', e.from, e.to, null, block)
    }
  }
  const out = [...byKey.values()]
  // Sort by endpoint TYPE — initiator (master) ports first, then
  // target (slave) ports (edge-model bundles with no node role last) —
  // and alphabetically by name within each type.
  const roleRank = (r) => (r === 'master' ? 0 : r === 'slave' ? 1 : 2)
  out.sort((a, b) => roleRank(a.role) - roleRank(b.role) || a.name.localeCompare(b.name))
  return out
})

const interconnects = computed(() => {
  const g = store.graph
  if (!g || !Array.isArray(g.nodes)) return []
  const out = []
  for (const n of g.nodes) {
    const ov = n.overlays && n.overlays['axi-perf']
    if (ov && ov.interconnect) {
      out.push({ node_path: n.id, ...ov.interconnect })
    }
  }
  out.sort((a, b) =>
    (b.total_read_bps + b.total_write_bps)
    - (a.total_read_bps + a.total_write_bps),
  )
  return out
})

const hasData = computed(
  () => bundles.value.length > 0 || interconnects.value.length > 0,
)

const selectedBundle = computed(
  () =>
    bundles.value.find((b) => b.bundleKey === store.selectedAxiBundle)
    || null,
)
// Clock period (ns) carried on the view.json's top-level axi_perf
// block — needed for the per-bundle theoretical max throughput.
const clockPeriodNs = computed(() => {
  const v = store.graph?.axi_perf?.clock_period_ns
  return typeof v === 'number' && v > 0 ? v : null
})
const clockMhz = computed(() =>
  clockPeriodNs.value ? Math.round(1000 / clockPeriodNs.value) : null,
)
// AXI moves one ``data_width``-bit beat per clock per direction, so the
// theoretical ceiling per direction is data_width × clock frequency.
const theoreticalMaxBps = computed(() => {
  const b = selectedBundle.value
  if (!b || !clockPeriodNs.value || !b.data_width) return null
  return (b.data_width * 1e9) / clockPeriodNs.value
})
// What fraction of that ceiling the busier direction actually used.
const utilOfMaxPct = computed(() => {
  const b = selectedBundle.value
  const max = theoreticalMaxBps.value
  if (!b || !max) return 0
  const busiest = Math.max(b.throughput.read_bps || 0, b.throughput.write_bps || 0)
  return Math.round((busiest / max) * 100)
})
const selectedInterconnect = computed(
  () =>
    interconnects.value.find((ic) => ic.node_path === store.selectedAxiBundle)
    || null,
)

const selectedBundleErrors = computed(() => {
  const b = selectedBundle.value
  if (!b) return 0
  return (b.errors.slverr || 0) + (b.errors.decerr || 0)
})

const channelBarData = computed(() => {
  const b = selectedBundle.value
  if (!b) return { labels: [], datasets: [] }
  const ch = b.channels
  return {
    labels: ['AR', 'AW', 'R', 'W', 'B'],
    datasets: [
      {
        label: 'util %',
        data: ['ar', 'aw', 'r', 'w', 'b'].map((k) => ch[k]?.util_pct ?? 0),
        backgroundColor: '#3b82f6',
      },
      {
        label: 'bp %',
        data: ['ar', 'aw', 'r', 'w', 'b'].map((k) => ch[k]?.bp_pct ?? 0),
        backgroundColor: '#f59e0b',
      },
    ],
  }
})

const channelBarOptions = {
  responsive: true,
  maintainAspectRatio: false,
  scales: { y: { max: 100, ticks: { callback: (v) => v + '%' } } },
}

const latencyHistData = computed(() => {
  const hist = selectedBundle.value?.latency_cycles?.ar_to_r_first?.hist_log2 || []
  return {
    labels: hist.map((_, i) => `2^${i}`),
    datasets: [
      {
        label: 'count',
        data: hist,
        backgroundColor: '#6366f1',
      },
    ],
  }
})

const latencyHistOptions = {
  responsive: true,
  maintainAspectRatio: false,
  scales: { y: { beginAtZero: true } },
}

function maxBp(block) {
  let m = 0
  for (const role of ['ar', 'aw', 'r', 'w', 'b']) {
    const c = block.channels && block.channels[role]
    if (c && typeof c.bp_pct === 'number' && c.bp_pct > m) m = c.bp_pct
  }
  return m
}

function bpClass(bp) {
  if (bp > 15) return 'bp-bad'
  if (bp > 5) return 'bp-warn'
  return 'bp-ok'
}

// "Open in marimo" — auto-fills test + suite_dir from view.json's
// top-level ``axi_perf`` block when the producer recorded them
// (rtl-buddy-view since Phase 2.5; emits the block whenever
// ``--overlay axi-perf=PATH`` is supplied). Falls back to prompting
// when the block is missing or the producer couldn't derive the
// canonical artefact-layout fields — previous answers persist to
// localStorage in that fallback path.
const LS_TEST = 'rtl-buddy.axi-notebook.test'
const LS_SUITE = 'rtl-buddy.axi-notebook.suite_dir'

function autoDetectedNotebookParams() {
  const g = store.graph
  const block = g && g.axi_perf
  if (block && typeof block.test === 'string' && typeof block.suite_dir === 'string') {
    return { test: block.test, suiteDir: block.suite_dir }
  }
  return null
}

async function onOpenInMarimo() {
  const auto = autoDetectedNotebookParams()
  let test = auto ? auto.test : null
  let suiteDir = auto ? auto.suiteDir : null

  if (!auto) {
    let defaultTest = ''
    let defaultSuite = ''
    try {
      defaultTest = window.localStorage.getItem(LS_TEST) || ''
      defaultSuite = window.localStorage.getItem(LS_SUITE) || ''
    } catch {
      /* localStorage unavailable (private mode) — fall back to empty */
    }

    test = window.prompt('Test name (from tests.yaml):', defaultTest)
    if (!test) return
    suiteDir = window.prompt(
      'Suite dir (relative to project root, e.g. verif/demo_axi_2x2):',
      defaultSuite,
    )
    if (!suiteDir) return

    try {
      window.localStorage.setItem(LS_TEST, test)
      window.localStorage.setItem(LS_SUITE, suiteDir)
    } catch {
      /* best-effort persist */
    }
  }

  try {
    await store.openAxiNotebook({ test, suiteDir })
  } catch {
    // Error is already surfaced via store.axiNotebookError + the
    // template's role="alert" div; nothing to do here. Swallow so
    // the unhandled-rejection warning doesn't spam the console.
  }
}
</script>

<style scoped>
.axi-perf-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  background: #f9fafb;
}
.axi-perf-view.empty-state {
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 2rem;
}
.view-header {
  padding: 0.5rem 1rem;
  border-bottom: 1px solid #e5e7eb;
  background: #ffffff;
  display: flex;
  align-items: baseline;
  gap: 1rem;
}
.view-header h2 {
  margin: 0;
  font-size: 1rem;
}
.header-stats {
  color: #475569;
  font-size: 0.75rem;
}
.header-actions {
  margin-left: auto;
}
.open-marimo-btn {
  font-size: 0.75rem;
  padding: 0.25rem 0.65rem;
  border: 1px solid #4f46e5;
  background: #4f46e5;
  color: #ffffff;
  border-radius: 4px;
  cursor: pointer;
}
.open-marimo-btn:hover:not(:disabled) {
  background: #4338ca;
  border-color: #4338ca;
}
.time-window-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.7rem;
  padding: 0.15rem 0.45rem;
  background: #eef2ff;
  border: 1px solid #c7d2fe;
  color: #3730a3;
  border-radius: 999px;
}
.time-window-clear {
  border: 0;
  background: transparent;
  color: #3730a3;
  font-size: 0.9rem;
  line-height: 1;
  cursor: pointer;
  padding: 0;
}
.time-window-clear:hover {
  color: #4338ca;
}
.open-marimo-btn:disabled {
  opacity: 0.5;
  cursor: progress;
}
.open-marimo-err {
  flex-basis: 100%;
  font-size: 0.75rem;
  color: #b91c1c;
  background: #fee2e2;
  padding: 0.4rem 0.65rem;
  border-radius: 4px;
  cursor: pointer;
}
.view-body {
  flex: 1;
  display: grid;
  grid-template-columns: 280px 1fr;
  min-height: 0;
}
.bundle-list {
  border-right: 1px solid #e5e7eb;
  overflow-y: auto;
  background: #ffffff;
}
.bundle-list h3 {
  margin: 0;
  padding: 0.5rem 0.75rem;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
  background: #f1f5f9;
  border-bottom: 1px solid #e5e7eb;
}
.bundle-list ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
.bundle-list li {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #f1f5f9;
  cursor: pointer;
}
.bundle-list li:hover { background: #f1f5f9; }
.bundle-list li.selected {
  background: #e0e7ff;
  border-left: 3px solid #4f46e5;
  padding-left: calc(0.75rem - 3px);
}
.bundle-name {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.8rem;
  color: #1e293b;
}
.bundle-meta {
  font-size: 0.7rem;
  color: #64748b;
  word-break: break-all;
}
.bundle-throughput { font-size: 0.7rem; color: #475569; }
.bundle-bp { font-size: 0.7rem; }
.bp-ok { color: #16a34a; }
.bp-warn { color: #d97706; }
.bp-bad { color: #dc2626; font-weight: 600; }
.bundle-detail {
  overflow-y: auto;
  padding: 1rem;
}
.bundle-detail-inner h3,
.interconnect-detail h3 {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.9rem;
  margin: 0 0 0.5rem;
  word-break: break-all;
}
.bundle-detail h4 {
  font-size: 0.8rem;
  color: #475569;
  margin: 1rem 0 0.25rem;
}
.meta {
  display: grid;
  grid-template-columns: max-content 1fr;
  column-gap: 0.5rem;
  row-gap: 0.15rem;
  font-size: 0.8rem;
  margin-bottom: 0.5rem;
}
.meta dt {
  color: #64748b;
  text-transform: uppercase;
  font-size: 0.65rem;
  letter-spacing: 0.05em;
}
.meta dd {
  margin: 0;
  word-break: break-all;
}
.meta .bad { color: #dc2626; font-weight: 600; }
.meta .muted { color: #94a3b8; }
.lat-summary { margin: 0 0 0.25rem; font-size: 0.75rem; color: #475569; }
.chart-wrap {
  height: 220px;
  background: #ffffff;
  border-radius: 4px;
  padding: 0.25rem;
}
.empty {
  color: #94a3b8;
  font-size: 0.85rem;
}
</style>
