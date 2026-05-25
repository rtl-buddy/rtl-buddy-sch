<template>
  <section class="overlay-panel">
    <h3>Overlays</h3>
    <ul v-if="summary.length">
      <li v-for="entry in summary" :key="entry.name">
        <label class="overlay-row">
          <input
            type="checkbox"
            :checked="store.enabledOverlays.has(entry.name)"
            :disabled="!entry.known"
            @change="store.toggleOverlay(entry.name)"
          />
          <span class="overlay-name">{{ entry.name }}</span>
          <span v-if="!entry.known" class="tag-unknown">unknown</span>
        </label>
        <ul v-if="entry.known && legendFor(entry.name).length" class="legend">
          <li v-for="item in legendFor(entry.name)" :key="item.label">
            <span
              class="swatch"
              :class="`swatch-${item.kind || 'fill'}`"
              :style="swatchStyle(item)"
            ></span>
            {{ item.label }}
          </li>
          <li
            v-if="tbScopeNoteFor(entry.name)"
            class="legend-note"
          >
            {{ tbScopeNoteFor(entry.name) }}
          </li>
        </ul>
      </li>
    </ul>
    <p v-else class="empty">No overlays in this view.</p>
  </section>
  <section v-if="layoutLegend.length" class="overlay-panel">
    <h3>Layout</h3>
    <ul class="legend layout-legend">
      <li v-for="item in layoutLegend" :key="item.label">
        <span
          class="swatch"
          :class="`swatch-${item.kind || 'fill'}`"
          :style="swatchStyle(item)"
        ></span>
        {{ item.label }}
      </li>
    </ul>
  </section>
</template>

<script setup>
// Per-overlay enable/disable + per-overlay legend slot.
//
// The list is driven by ``graph.overlays_present`` so the panel
// stays correct when a future producer ships an overlay this
// viewer doesn't know about — the entry appears tagged
// ``unknown`` and the checkbox is disabled (toggling it would do
// nothing visible).
import { computed } from 'vue'
import { useViewerStore } from '../store.js'
import { getOverlay, overlaySummary } from '../overlays/index.js'

const store = useViewerStore()
const summary = computed(() =>
  store.graph ? overlaySummary(store.graph) : [],
)
function legendFor(name) {
  const overlay = getOverlay(name)
  if (!overlay || typeof overlay.legend !== 'function') return []
  return overlay.legend(store.graph) || []
}

// Phase 6d (#99): in TB view, the DUT-side clock/reset domain maps
// produced by rtl-buddy-cdc don't speak the testbench's scope —
// SDC is the design's timing contract, not the simulation harness's
// stimulus. So the DUT subtree keeps its full overlay tint while
// every TB scope above renders unannotated. Surface that as a small
// footnote on each affected overlay's legend rather than leaving a
// silent gap; users see a legend entry that explains why the TB
// blocks aren't coloured. Returns an empty string (falsy) for
// overlays that aren't affected or when the view is DUT-rooted.
function tbScopeNoteFor(name) {
  if (store.renderedViewMode !== 'tb') return ''
  if (name === 'clock') return '(no clock map above DUT)'
  if (name === 'reset') return '(no reset map above DUT)'
  return ''
}

// Structural-style legend — entries that aren't overlay-toggleable
// but are drawn into the embedded layout DOT by the producer.
// Detected by scanning the dot source for the producer's signature
// markers. Auto-filters per current graph and refreshes via Vue
// reactivity when ``store.graph`` rebinds.
const layoutLegend = computed(() => {
  const dot = store.graph && store.graph.layout && store.graph.layout.dot
  if (typeof dot !== 'string' || dot.length === 0) return []
  const entries = []
  // Port→child signal flow edges. ``_emit_port_signal_edges`` emits
  // ``"_in_<port>" -> "<child>"`` / ``"<child>" -> "_out_<port>"``
  // pairs with ``color="#cbd5e1"`` when no clock typing is known.
  if (dot.includes('"_in_') || dot.includes('"_out_')) {
    entries.push({
      label: 'port → instance signal',
      swatch: '#cbd5e1',
      kind: 'solid-line',
    })
  }
  return entries
})

// Per-kind swatch styling. ``fill`` is a solid filled box (clock
// palette pastels, default for entries without a ``kind`` field).
// ``stroke`` is an outlined-only box (reset border markers).
// ``dashed-line`` is a horizontal dashed segment (CDC / RDC edge
// styles). The kind is a hint from each overlay's legend() — see
// overlays/clock.js + overlays/reset.js.
function swatchStyle(item) {
  const kind = item.kind || 'fill'
  if (kind === 'stroke') {
    return { borderColor: item.swatch, borderWidth: '2px', background: 'transparent' }
  }
  if (kind === 'dashed-line' || kind === 'solid-line') {
    return { borderColor: item.swatch }
  }
  return { background: item.swatch }
}
</script>

<style scoped>
.overlay-panel { padding: 0.5rem; }
.overlay-panel h3 {
  margin: 0 0 0.5rem;
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
}
.overlay-panel ul { list-style: none; padding: 0; margin: 0; }
.overlay-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.9rem;
  cursor: pointer;
}
.tag-unknown {
  font-size: 0.7rem;
  background: #f1f5f9;
  color: #64748b;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
}
.legend {
  margin-left: 1.5rem;
  font-size: 0.75rem;
  color: #475569;
}
.legend li { display: flex; align-items: center; gap: 0.5rem; padding: 0.1rem 0; }
/* Phase 6d (#99): TB-scope explanatory footnote on the clock/reset
   legends. Italic, smaller, indented so it reads as a side comment
   rather than a swatched legend entry. */
.legend-note {
  font-style: italic;
  font-size: 0.7rem;
  color: #94a3b8;
  margin-top: 0.15rem;
  padding-left: 1.25rem;
}
.swatch {
  width: 16px;
  height: 12px;
  border-radius: 2px;
  border: 1px solid #cbd5e1;
  display: inline-block;
  box-sizing: border-box;
}
/* Outlined-only swatch — reset border markers. The border colour
   is injected inline; the fill stays transparent so the kind reads
   as "stroke" rather than "fill". */
.swatch-stroke { border-radius: 2px; }
/* Horizontal dashed line — CDC / RDC edge styles. Render via a
   thick top border on a short, otherwise-borderless box so the
   dashes are visible at 16x12. */
.swatch-dashed-line {
  border: none;
  border-top-style: dashed;
  border-top-width: 3px;
  height: 0;
  width: 18px;
  align-self: center;
  margin-top: 4px;
  border-radius: 0;
}
/* Horizontal solid line — non-toggleable structural styles (e.g.
   port → instance signal flow). Same shape as the dashed
   variant; only the stroke style differs. */
.swatch-solid-line {
  border: none;
  border-top-style: solid;
  border-top-width: 3px;
  height: 0;
  width: 18px;
  align-self: center;
  margin-top: 4px;
  border-radius: 0;
}
.layout-legend { margin-left: 0; }
.empty {
  font-size: 0.85rem;
  color: #64748b;
  margin: 0;
}
</style>
