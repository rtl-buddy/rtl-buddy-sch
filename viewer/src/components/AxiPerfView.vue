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
    </header>

    <div class="view-body">
      <aside class="bundle-list" aria-label="AXI bundles">
        <h3>Bundles</h3>
        <ul>
          <li
            v-for="b in bundles"
            :key="b.bundleKey"
            :class="{ selected: b.bundleKey === store.selectedAxiBundle }"
            @click="store.selectAxiBundle(b.bundleKey)"
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
            @click="store.selectAxiBundle(ic.node_path)"
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

ChartJS.register(BarElement, CategoryScale, LinearScale, Title, Tooltip, Legend)

const store = useViewerStore()

// Collect every edge with an axi-perf overlay into a flat list,
// then layer in the interconnect roll-ups separately. Each entry
// gets a stable `bundleKey` (the bundle name; falls back to
// from→to when absent) so the list-detail wiring is one source.
const bundles = computed(() => {
  const g = store.graph
  if (!g || !Array.isArray(g.edges)) return []
  const out = []
  for (const e of g.edges) {
    const block = e.overlays && e.overlays['axi-perf']
    if (!block) continue
    out.push({
      bundleKey: block.name || `${e.from}->${e.to}`,
      name: block.name || '(unnamed bundle)',
      from: e.from,
      to: e.to,
      maxBp: maxBp(block),
      ...block,
    })
  }
  // Highest backpressure first; ties broken by name so the order
  // stays stable across re-renders.
  out.sort((a, b) => b.maxBp - a.maxBp || a.name.localeCompare(b.name))
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

function fmtBps(bps) {
  if (!bps || bps <= 0) return '0'
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s', 'TB/s']
  let i = 0
  let v = bps / 8
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(2)} ${units[i]}`
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
