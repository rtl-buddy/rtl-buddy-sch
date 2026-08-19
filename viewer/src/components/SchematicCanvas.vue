<template>
  <main class="schematic-canvas">
    <div class="canvas-tabs">
      <button
        type="button"
        class="canvas-tab"
        @click="store.setViewMode('hier')"
        title="Nested-cluster hierarchy from the producer's embedded layout"
      >Hierarchy</button>
      <button
        type="button"
        class="canvas-tab"
        @click="store.setViewMode('flow')"
        title="One-level signal flow"
      >Block Flow</button>
      <button
        type="button"
        class="canvas-tab active"
        title="Pin-level schematic laid out in-browser by elkjs"
      >Schematic</button>
    </div>
    <div class="graph-toolbar" v-if="hasPayload">
      <button type="button" @click="panZoom.zoomIn">+</button>
      <button type="button" @click="panZoom.zoomOut">−</button>
      <button type="button" @click="panZoom.fit">Fit</button>
      <button type="button" @click="panZoom.reset">Reset</button>
    </div>

    <!-- Degraded producer: the payload this canvas lays out simply
         isn't in the file. Same explain-shape the other empty states
         use (AxiPerfView's "No AXI performance data", the no-view
         placeholder): name what's missing, then the command. -->
    <section v-if="!hasPayload" class="sch-empty">
      <h2>No schematic payload in this view</h2>
      <p>
        The schematic canvas lays out <code>layout.elk</code>, which this
        <code>view.json</code> doesn't carry — it came from an
        <strong>older rtl-buddy-view</strong>.
      </p>
      <p class="empty-hint">
        Regenerate it with a current one:<br />
        <code>{{ RENDER_FOR_SCHEMATIC_HINT }}</code><br />
        Under the hub, restarting it against the same model is enough.
        The Hierarchy and Block Flow tabs work either way.
      </p>
    </section>

    <div
      v-else
      class="svg-host"
      ref="svgHostEl"
      @click="onClick"
      @dblclick="onDoubleClick"
      @contextmenu="onContextMenu"
    >
      <svg
        class="sch-svg"
        ref="svgEl"
        xmlns="http://www.w3.org/2000/svg"
        :width="model.width"
        :height="model.height"
      >
        <g ref="rootG">
          <!-- Containment frames (design sheet + compound blocks)
               first, so leaf blocks and wires paint over them. -->
          <g
            v-for="box in frames"
            :key="'f-' + box.id"
            class="sch-frame"
            :class="{ sheet: box.sheet }"
            :data-node-id="box.id"
            :data-rb-selected="box.id === store.selection ? 'true' : null"
          >
            <rect
              :x="box.x"
              :y="box.y"
              :width="box.width"
              :height="box.height"
            />
          </g>

          <!-- Leaf blocks: sharp rectangles, schematic line weight. -->
          <g
            v-for="box in blocks"
            :key="'b-' + box.id"
            class="sch-block"
            :class="{ blackbox: box.blackbox }"
            :data-node-id="box.id"
            :data-rb-selected="box.id === store.selection ? 'true' : null"
          >
            <rect
              :x="box.x"
              :y="box.y"
              :width="box.width"
              :height="box.height"
            />
          </g>

          <!-- Wires. Buses get the heavier stroke plus a /N slash. -->
          <g
            v-for="(wire, i) in model.wires"
            :key="'w-' + i"
            class="sch-wire"
            :class="{ bus: wire.bus }"
            :data-edge-id="wire.id"
            :data-edge-source="wire.sourceId"
            :data-edge-target="wire.targetId"
          >
            <path :d="pathOf(wire)" />
            <polygon :points="arrowOf(wire)" class="sch-arrow" />
            <template v-if="wire.slash">
              <line
                :x1="slashOf(wire).x - 4"
                :y1="slashOf(wire).y + 5"
                :x2="slashOf(wire).x + 4"
                :y2="slashOf(wire).y - 5"
              />
              <text
                class="sch-slash-text"
                :x="slashOf(wire).x + 6"
                :y="slashOf(wire).y - 4"
              >{{ wire.slash }}</text>
            </template>
          </g>

          <!-- Junction dots: ELK's own branch points for merged nets. -->
          <circle
            v-for="(j, i) in model.junctions"
            :key="'j-' + i"
            class="sch-junction"
            :cx="j.x"
            :cy="j.y"
            r="2.7"
          />

          <!-- Pin stubs crossing the border, formal name inside. -->
          <g
            v-for="pin in model.pins"
            :key="'p-' + pin.id"
            class="sch-pin"
            :class="[pin.kind, { unconnected: !pin.connected }]"
            :data-node-id="pin.nodeId"
            :data-port-id="pin.id"
          >
            <line
              :x1="pin.side === 'WEST' ? pin.x - PIN_STUB : pin.x"
              :y1="pin.y"
              :x2="pin.side === 'WEST' ? pin.x : pin.x + PIN_STUB"
              :y2="pin.y"
            />
            <!-- Clock: the standard wedge, drawn inside the border. -->
            <polygon
              v-if="pin.kind === 'clock'"
              class="sch-clock-wedge"
              :points="wedge(pin)"
            />
            <!-- Reset: an inversion bubble when the pin asserts low. -->
            <circle
              v-else-if="pin.activeLow"
              class="sch-bubble"
              :cx="pin.side === 'WEST' ? pin.x - 3 : pin.x + 3"
              :cy="pin.y"
              r="3"
            />
            <text
              :x="pin.side === 'WEST' ? pin.x + PIN_LABEL_GAP : pin.x - PIN_LABEL_GAP"
              :y="pin.y + 3.2"
              :text-anchor="pin.side === 'WEST' ? 'start' : 'end'"
            >{{ pin.name }}</text>
          </g>

          <!-- Off-page connector flags for the design's own ports. -->
          <g
            v-for="flag in flagBoxes"
            :key="'g-' + flag.id"
            class="sch-flag"
            :class="{ out: flag.out, clock: flag.isClock, reset: flag.isReset }"
            :data-node-id="flag.nodeId"
            :data-port-id="flag.id"
          >
            <path :d="flag.d" />
            <text :x="flag.textX" :y="flag.y + 3.4" :text-anchor="flag.anchor">
              {{ flag.name }}
            </text>
          </g>

          <!-- Refdes above, module type + params below, net names on
               their segments. All three come back from ELK with real
               positions, so nothing here guesses at free space. -->
          <g
            v-for="(label, i) in model.labels"
            :key="'l-' + i"
            class="sch-label"
            :class="label.role"
            :data-node-id="label.nodeId"
          >
            <text
              v-for="(line, li) in label.lines"
              :key="li"
              :x="label.role === 'refdes' || label.role === 'net'
                ? label.x
                : label.x + label.width / 2"
              :y="label.y + 9 + li * PARAM_LINE_HEIGHT"
              :text-anchor="label.role === 'refdes' || label.role === 'net'
                ? 'start'
                : 'middle'"
              :class="{ param: label.role === 'type' && li > 0 }"
            >{{ line }}</text>
          </g>
        </g>
      </svg>
    </div>
    <SelectionCandidates />
  </main>
</template>

<script setup>
// The elkjs schematic canvas (epic rtl-buddy-sch#163, P2).
//
// The one architectural difference from GraphCanvas: **the SVG is
// Vue's**. viz.js hands back a finished SVG string, so GraphCanvas
// injects it and then recovers identity by scraping Graphviz's
// ``<title>`` elements and reversing a producer-supplied
// ``cluster_lookup``. elkjs hands back *geometry*, which means every
// rect, stub and wire is emitted by a template that already knows
// which instance path it belongs to — ``data-node-id`` is bound, not
// reconstructed. That is what makes the P3 work (expand/collapse,
// net hover) tractable at all.
//
// Everything else deliberately matches GraphCanvas so the two tabs
// feel like one canvas: same store actions on click / dblclick /
// right-click, the same ``data-rb-selected`` highlight vocabulary,
// the same toolbar, the same pan/zoom gestures (now shared via
// usePanZoom).
//
// Seam for P3: the layout runs in ``relayout()`` from the payload
// returned by ``visiblePayload`` — a collapse action becomes a filter
// on that computed plus a re-run, with no change to the draw path.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useViewerStore } from '../store.js'
import { useHub } from '../composables/useHub.js'
import { usePanZoom } from '../composables/usePanZoom.js'
import { registerSvgProvider, unregisterSvgProvider } from '../capture.js'
import { token } from '../theme.js'
import { RENDER_FOR_SCHEMATIC_HINT } from '../cliHints.js'
import {
  FLAG_HEIGHT,
  PARAM_LINE_HEIGHT,
  PIN_LABEL_GAP,
  PIN_STUB,
  buildElkGraph,
  flagPath,
  polylinePath,
  resolveMeasure,
  subtreeOf,
  toSchematic,
} from '../layout/elkSchematic.js'
import SelectionCandidates from './SelectionCandidates.vue'

const store = useViewerStore()
const hub = useHub()
const svgHostEl = ref(null)
const svgEl = ref(null)
const rootG = ref(null)

const EMPTY_MODEL = Object.freeze({
  width: 0,
  height: 0,
  boxes: [],
  pins: [],
  flags: [],
  wires: [],
  junctions: [],
  labels: [],
})
const model = ref(EMPTY_MODEL)

// P3 seam: the payload actually laid out. Today it is the producer's
// tree, scoped by the descend/ascend path; a collapse action prunes
// ``children`` here and calls ``relayout()`` — the draw path below
// never learns about it.
//
// Note this reads ``store.graph``, not ``store.displayGraph``: the
// latter drops ``layout`` wholesale when a scope is set (the embedded
// DOT was laid out for the full design and would redraw the wrong
// picture). The ELK payload has no such problem — it is a tree, and
// the scope is a node in it, so descending is a subtree lookup.
const visiblePayload = computed(() => {
  const elk = store.graph && store.graph.layout && store.graph.layout.elk
  if (!elk || typeof elk !== 'object' || !elk.id) return null
  return subtreeOf(elk, store.rootInstancePath) || elk
})
const hasPayload = computed(() => visiblePayload.value !== null)

const frames = computed(() => model.value.boxes.filter((b) => b.compound))
const blocks = computed(() => model.value.boxes.filter((b) => !b.compound))

// Flag geometry is derived, not laid out: ELK gives us the port's
// point on the sheet border and the pentagon hangs off it.
const flagBoxes = computed(() =>
  model.value.flags.map((f) => {
    const measure = measurer()
    const width = Math.max(46, measure(f.name, 10) + 26)
    const x = f.out ? f.x : f.x - width
    const y = f.y - FLAG_HEIGHT / 2
    return {
      ...f,
      d: flagPath(x, y, width, FLAG_HEIGHT, f.out),
      textX: f.out ? x + 8 : x + width - 8,
      anchor: f.out ? 'start' : 'end',
    }
  }),
)

let _measure = null
function measurer() {
  if (_measure === null) _measure = resolveMeasure(token('--font-mono'))
  return _measure
}

function pathOf(wire) {
  return polylinePath(wire.points)
}

// Arrowhead at the sink end, oriented along the last segment.
function arrowOf(wire) {
  const pts = wire.points
  const a = pts[pts.length - 2]
  const b = pts[pts.length - 1]
  const ang = Math.atan2(b.y - a.y, b.x - a.x)
  const s = 5
  return [
    `${b.x},${b.y}`,
    `${b.x - s * Math.cos(ang - 0.45)},${b.y - s * Math.sin(ang - 0.45)}`,
    `${b.x - s * Math.cos(ang + 0.45)},${b.y - s * Math.sin(ang + 0.45)}`,
  ].join(' ')
}

// Bus slash sits a third of the way along the first segment — near
// the driver, which is where a schematic reader looks for the width.
function slashOf(wire) {
  const p0 = wire.points[0]
  const p1 = wire.points[1]
  return { x: p0.x + (p1.x - p0.x) * 0.35, y: p0.y + (p1.y - p0.y) * 0.35 }
}

// Clock wedge: an isoceles triangle standing on the border, pointing
// into the block. The convention every datasheet uses for a clocked
// input, and the reason a clock pin can stay visible while its net
// stays unrouted (elk.json §4).
function wedge(pin) {
  const inward = pin.side === 'WEST' ? 1 : -1
  const h = 4.5
  const d = 7
  return [
    `${pin.x},${pin.y - h}`,
    `${pin.x + inward * d},${pin.y}`,
    `${pin.x},${pin.y + h}`,
  ].join(' ')
}

let _elk = null
async function elkEngine() {
  if (_elk === null) {
    const { default: ELK } = await import('elkjs/lib/elk.bundled.js')
    _elk = new ELK()
  }
  return _elk
}

let _relayoutToken = 0
async function relayout() {
  const payload = visiblePayload.value
  if (!payload) {
    model.value = EMPTY_MODEL
    return
  }
  const mine = ++_relayoutToken
  const graph = buildElkGraph(payload, { measure: measurer() })
  let laid
  try {
    const elk = await elkEngine()
    laid = await elk.layout(graph)
  } catch (e) {
    store.$patch({ status: 'error', error: `elkjs layout failed: ${e.message}` })
    return
  }
  // A newer relayout finished first (model switch mid-flight) — drop
  // this result rather than painting a stale picture over it.
  if (mine !== _relayoutToken) return
  model.value = toSchematic(laid)
  requestAnimationFrame(() => {
    panZoom.fit()
    bringSelectionIntoView(store.selection)
  })
}

const panZoom = usePanZoom({
  hostEl: svgHostEl,
  getRoot: () => rootG.value,
  getContentBox: () => ({
    x: 0,
    y: 0,
    width: model.value.width,
    height: model.value.height,
  }),
})

function boxFor(nodeId) {
  return model.value.boxes.find((b) => b.id === nodeId) || null
}

function bringSelectionIntoView(id) {
  if (!id) return
  const box = boxFor(id)
  if (box) panZoom.bringIntoView(box)
}

// --- interactions (parity with GraphCanvas) --------------------------------

function nodeFromEvent(e) {
  const group = e.target.closest('[data-node-id]')
  if (!group) return null
  const id = group.getAttribute('data-node-id')
  if (!id) return null
  const node = store.nodesById.get(id)
  return node ? { id, node } : null
}

function onClick(e) {
  const hit = nodeFromEvent(e)
  if (hit) {
    store.select(hit.id)
    hub.notifyClick(hit.node)
    return
  }
  // Background click clears, exactly as in the hier/flow canvas.
  store.clearSelection()
}

function onDoubleClick(e) {
  const hit = nodeFromEvent(e)
  if (!hit) return
  e.preventDefault()
  store.select(hit.id)
  store.descend(hit.id)
}

function onContextMenu(e) {
  const hit = nodeFromEvent(e)
  if (!hit) return
  e.preventDefault()
  store.select(hit.id)
  // A pin click knows more than the block does: jump to the port
  // declaration rather than the module header when we have its
  // anchor. Same refinement block-flow's port cells make.
  const portId = e.target.closest('[data-port-id]')?.getAttribute('data-port-id')
  const portName = portId ? portId.slice(portId.lastIndexOf(':') + 1) : null
  const port = portName
    ? (hit.node.ports || []).find((p) => p.name === portName)
    : null
  if (port && port.anchor && hit.node.source) {
    hub.requestOpenSource({
      source: {
        file: hit.node.source.file,
        start_line: typeof port.anchor.line === 'number' ? port.anchor.line : 1,
        start_column: typeof port.anchor.col === 'number' ? port.anchor.col : 1,
      },
      link: hit.node.link,
    })
    return
  }
  hub.requestOpenSource(hit.node)
}

watch(visiblePayload, async (payload, before) => {
  // The host div is behind a ``v-if``: mounting straight into the
  // empty state leaves usePanZoom with nothing to listen on. Wire it
  // up the moment a payload turns the canvas on.
  if (payload && !before) {
    await nextTick()
    panZoom.attach()
  }
  relayout()
})
watch(
  () => store.selection,
  (id) => bringSelectionIntoView(id),
)

// The capture module reads the live SVG through a getter (see
// GraphCanvas): the element identity moves when the canvas
// re-renders, and a function dodges that staleness.
const _captureSvgProvider = () => svgEl.value

onMounted(() => {
  registerSvgProvider(_captureSvgProvider)
  relayout()
})
onBeforeUnmount(() => {
  unregisterSvgProvider(_captureSvgProvider)
})
</script>

<style scoped>
/* No hex literals: every colour below resolves through the vendored
   hub sheet / tokens.css (docs/design-tokens.md). The schematic reads
   as black-on-white in light and inverts cleanly in dark, because the
   SVG is ours — unlike the producer's DOT, there is nothing baked to
   re-tint. */
.schematic-canvas {
  flex: 1;
  position: relative;
  overflow: hidden;
  background: var(--bg);
}
.canvas-tabs {
  position: absolute;
  top: 0.5rem;
  left: 0.5rem;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  z-index: 10;
  font-size: 0.85rem;
}
.canvas-tab {
  border: 1px solid var(--line-strong);
  background: var(--panel);
  color: var(--fg);
  padding: 0.25rem 0.6rem;
  cursor: pointer;
  border-radius: var(--radius-2);
  font-family: inherit;
}
.canvas-tab.active {
  background: var(--accent);
  color: var(--accent-contrast);
  border-color: var(--accent);
}
.graph-toolbar {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  display: flex;
  gap: 0.25rem;
  z-index: 10;
}
.graph-toolbar button {
  border: 1px solid var(--line-strong);
  background: var(--panel);
  color: var(--fg);
  padding: 0.25rem 0.5rem;
  cursor: pointer;
  border-radius: var(--radius-2);
}
.svg-host {
  width: 100%;
  height: 100%;
  cursor: grab;
}
.svg-host:active { cursor: grabbing; }
.sch-svg {
  width: 100%;
  height: 100%;
  font-family: var(--font-mono);
}

/* -- schematic dress ------------------------------------------------ */
/* Sharp corners, 1.5px block border, 1px wires, 2.5px buses. Rounded
   corners read as "diagram"; this is meant to read as a schematic. */
.sch-block rect {
  fill: var(--panel);
  stroke: var(--fg);
  stroke-width: 1.5;
}
.sch-block.blackbox rect {
  stroke-dasharray: 5 3;
}
.sch-frame rect {
  fill: none;
  stroke: var(--fg-faint);
  stroke-width: 1.2;
  pointer-events: all;
}
.sch-frame.sheet rect {
  stroke: var(--fg-muted);
  stroke-width: 1.8;
}
.sch-block,
.sch-frame,
.sch-pin,
.sch-flag {
  cursor: pointer;
}

.sch-wire path {
  fill: none;
  stroke: var(--fg);
  stroke-width: 1.1;
}
.sch-wire.bus path {
  stroke-width: 2.5;
}
.sch-wire line {
  stroke: var(--fg);
  stroke-width: 1.2;
}
.sch-arrow {
  fill: var(--fg);
}
.sch-junction {
  fill: var(--fg);
}
.sch-slash-text {
  fill: var(--fg);
  font-size: 8.5px;
}

.sch-pin line {
  stroke: var(--fg);
  stroke-width: 1.4;
}
.sch-pin text {
  fill: var(--fg-muted);
  font-size: 9px;
}
.sch-pin.unconnected line {
  stroke: var(--fg-faint);
  stroke-dasharray: 3 2;
}
.sch-clock-wedge {
  fill: none;
  stroke: var(--fg);
  stroke-width: 1.2;
}
.sch-bubble {
  fill: var(--panel);
  stroke: var(--fg);
  stroke-width: 1.2;
}

.sch-flag path {
  fill: var(--panel-2);
  stroke: var(--fg);
  stroke-width: 1.3;
}
.sch-flag text {
  fill: var(--fg);
  font-size: 10px;
}

.sch-label text {
  fill: var(--fg);
  font-size: 10.5px;
}
.sch-label.refdes text {
  font-size: 11px;
  font-weight: 600;
}
.sch-label.net text {
  fill: var(--fg-muted);
  font-size: 8.5px;
}
.sch-label text.param {
  fill: var(--fg-muted);
  font-size: 9px;
}

/* Selected-node accent — the same vocabulary GraphCanvas paints:
   ``data-rb-selected`` on the group, a 2.5px accent stroke plus a
   two-stop accent halo so the mark survives whatever fill an overlay
   put underneath it. */
[data-rb-selected] > rect {
  stroke: var(--accent);
  stroke-width: 2.5;
  filter:
    drop-shadow(0 0 3px color-mix(in srgb, var(--accent) 70%, transparent))
    drop-shadow(0 0 9px color-mix(in srgb, var(--accent) 35%, transparent));
}

/* -- empty state ---------------------------------------------------- */
.sch-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  height: 100%;
  text-align: center;
  padding: 2rem;
  box-sizing: border-box;
}
.sch-empty h2 {
  margin: 0;
  font-size: 1rem;
}
.sch-empty .empty-hint {
  font-size: 0.85rem;
  color: var(--fg-muted);
  max-width: 60ch;
  line-height: 1.6;
  background: var(--panel-2);
  padding: 0.75rem 1rem;
  border-radius: var(--radius-3);
}
.sch-empty code {
  background: var(--line);
  padding: 0.05rem 0.3rem;
  border-radius: var(--radius-1);
  font-size: 0.85em;
  font-family: var(--font-mono);
}
</style>
