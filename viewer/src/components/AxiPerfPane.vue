<template>
  <section class="axi-perf-pane" v-if="axiPerf">
    <h3>AXI Performance</h3>
    <div class="bundle-name">{{ axiPerf.name }}</div>
    <dl class="meta">
      <dt>Protocol</dt><dd>{{ axiPerf.protocol }} / {{ axiPerf.data_width }}b data / {{ axiPerf.id_width }}b id</dd>
      <dt>Throughput</dt><dd>R {{ fmtBps(axiPerf.throughput.read_bps) }} / W {{ fmtBps(axiPerf.throughput.write_bps) }}</dd>
      <dt>Outstanding</dt><dd>R peak {{ axiPerf.outstanding.read_peak }} avg {{ axiPerf.outstanding.read_avg.toFixed(1) }} · W peak {{ axiPerf.outstanding.write_peak }} avg {{ axiPerf.outstanding.write_avg.toFixed(1) }}</dd>
      <dt>Errors</dt><dd :class="{ bad: errorTotal > 0 }">SLVERR {{ axiPerf.errors.slverr }} · DECERR {{ axiPerf.errors.decerr }}</dd>
    </dl>

    <h4>Channel utilization</h4>
    <Bar :data="channelBarData" :options="channelBarOptions" />

    <h4>AR → R first-data latency</h4>
    <p class="lat-summary">
      p50 {{ axiPerf.latency_cycles.ar_to_r_first.p50 }} ·
      p95 {{ axiPerf.latency_cycles.ar_to_r_first.p95 }} ·
      p99 {{ axiPerf.latency_cycles.ar_to_r_first.p99 }} ·
      max {{ axiPerf.latency_cycles.ar_to_r_first.max }} cycles
    </p>
    <Bar :data="latencyHistData" :options="latencyHistOptions" />
  </section>
  <section class="axi-perf-pane interconnect" v-else-if="interconnect">
    <h3>AXI Interconnect roll-up</h3>
    <dl class="meta">
      <dt>Total throughput</dt>
      <dd>R {{ fmtBps(interconnect.total_read_bps) }} / W {{ fmtBps(interconnect.total_write_bps) }}</dd>
      <dt>Hottest pair</dt>
      <dd>{{ interconnect.hottest_master }} → {{ interconnect.hottest_slave }}</dd>
      <dt>Fairness (Jain)</dt>
      <dd>{{ interconnect.arbitration.fairness_jain.toFixed(2) }}</dd>
      <dt v-if="interconnect.arbitration.starved_masters.length">Starved</dt>
      <dd v-if="interconnect.arbitration.starved_masters.length">
        {{ interconnect.arbitration.starved_masters.join(', ') }}
      </dd>
    </dl>
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
import {
  selectedEdgeAxiPerf,
  nodeAxiPerfInterconnect,
} from '../overlays/axi_perf.js'

ChartJS.register(BarElement, CategoryScale, LinearScale, Title, Tooltip, Legend)

const store = useViewerStore()

// Show edge-level bundle stats when an edge is selected, otherwise
// fall back to the interconnect roll-up for the selected node.
const axiPerf = computed(() =>
  selectedEdgeAxiPerf(store.graph, store.selectedEdgeObj),
)
const interconnect = computed(() => {
  if (axiPerf.value) return null
  return nodeAxiPerfInterconnect(store.graph, store.selectedNode)
})

const errorTotal = computed(() =>
  axiPerf.value
    ? (axiPerf.value.errors.slverr || 0) + (axiPerf.value.errors.decerr || 0)
    : 0,
)

const channelBarData = computed(() => {
  const ch = axiPerf.value && axiPerf.value.channels
  if (!ch) return { labels: [], datasets: [] }
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
  const hist = axiPerf.value?.latency_cycles?.ar_to_r_first?.hist_log2 || []
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

function fmtBps(bps) {
  if (!bps || bps <= 0) return '0'
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s', 'TB/s']
  let i = 0
  let v = bps / 8  // bps → Bps
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(2)} ${units[i]}`
}
</script>

<style scoped>
.axi-perf-pane {
  padding: 0.5rem;
  border-top: 1px solid #e5e7eb;
  margin-top: 0.5rem;
}
.axi-perf-pane h3 {
  margin: 0 0 0.25rem;
  font-size: 0.9rem;
}
.axi-perf-pane h4 {
  margin: 0.75rem 0 0.25rem;
  font-size: 0.8rem;
  color: #475569;
}
.bundle-name {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.75rem;
  color: #1e293b;
  margin-bottom: 0.5rem;
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
.meta .bad {
  color: #dc2626;
  font-weight: 600;
}
.lat-summary {
  margin: 0 0 0.25rem;
  font-size: 0.75rem;
  color: #475569;
}
</style>
