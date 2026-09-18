// Tests for the overlay registry's per-name dispatch +
// graceful-unknown behavior.
//
// The actual apply() methods are exercised against real SVG in
// the Playwright snapshot test; here we cover the static
// surface — getOverlay miss-returns-null, summary surfaces
// unknown overlays as ``known: false``, legend() is callable for
// each built-in.

import { describe, expect, it } from 'vitest'
import { getOverlay, overlaySummary, applyOverlays } from '../src/overlays/index.js'
import { heatColor, tintMetric } from '../src/overlays/coverage.js'
import { resolvePortValues } from '../src/overlays/wave.js'
import {
  areaOf,
  bivariateLegend,
  fractionOf,
  physFillNote,
  physMax,
  physScope,
  powerOf,
} from '../src/overlays/phys.js'
import { heatNoneColor, heatRampColor, saturateBy } from '../src/palette.js'

describe('overlay registry', () => {
  it('returns null for an unknown name (never throws)', () => {
    expect(getOverlay('cov')).toBeNull()
    expect(getOverlay('')).toBeNull()
  })

  it('returns the clock + reset built-ins by name', () => {
    expect(getOverlay('clock')).not.toBeNull()
    expect(getOverlay('clock').name).toBe('clock')
    expect(getOverlay('reset').name).toBe('reset')
  })

  it('overlaySummary tags unknown overlays with known:false', () => {
    const graph = {
      overlays_present: ['clock', 'unknown_overlay'],
    }
    const summary = overlaySummary(graph)
    expect(summary).toEqual([
      { name: 'clock', known: true },
      { name: 'unknown_overlay', known: false },
    ])
  })

  it('applyOverlays is a no-op for unknown overlays', () => {
    // Soft-miss semantics: unknown overlays don't crash the
    // viewer; they contribute nothing to the render.
    const graph = {
      overlays_present: ['unknown_overlay'],
      nodes: [],
      edges: [],
    }
    expect(() => applyOverlays(null, graph, new Set(['unknown_overlay']))).not.toThrow()
  })
})

describe('built-in overlays', () => {
  it('clock overlay produces a legend with one entry per distinct clock', () => {
    const overlay = getOverlay('clock')
    const graph = {
      nodes: [
        { id: 'a', overlays: { clock: { clock: 'clk_a' } } },
        { id: 'b', overlays: { clock: { clock: 'clk_b' } } },
        { id: 'c', overlays: { clock: { clock: 'clk_a' } } },
      ],
    }
    const legend = overlay.legend(graph)
    const labels = legend.map((e) => e.label)
    expect(labels).toEqual(['clk_a', 'clk_b'])
    for (const entry of legend) expect(entry.swatch).toMatch(/^#[0-9a-f]{6}$/i)
  })

  it('reset overlay legend filters by what is present in the graph', () => {
    const overlay = getOverlay('reset')
    // Empty graph yields an empty legend — keeps the panel from
    // showing roles that aren't visible in the current view.
    expect(overlay.legend({ nodes: [], edges: [] })).toEqual([])
    // Only synchronisers → only the synchroniser swatch shows.
    expect(
      overlay
        .legend({
          nodes: [{ id: 'a', overlays: { reset: { is_synchronizer: true } } }],
          edges: [],
        })
        .map((e) => e.label),
    ).toEqual(['reset-synchroniser'])
    // Full mix: binding + sync + RDC crossing → all three entries.
    const full = overlay.legend({
      nodes: [
        { id: 'a', overlays: { reset: { is_synchronizer: true } } },
        { id: 'b', overlays: { reset: { reset: 'rst_n', is_synchronizer: false } } },
      ],
      edges: [
        { from: 'p', to: 'b', overlays: { reset: { crossing: true } } },
      ],
    })
    expect(full.map((e) => e.label)).toEqual([
      'reset-binding',
      'reset-synchroniser',
      'RDC crossing',
    ])
    // CDC crossing edge ⇒ dashed-line kind.
    expect(full.find((e) => e.label === 'RDC crossing').kind).toBe('dashed-line')
    // Stroke entries flagged for the panel's outlined-swatch renderer.
    expect(full.find((e) => e.label === 'reset-binding').kind).toBe('stroke')
  })

  it('clock overlay legend appends CDC entry only when the graph has crossings', () => {
    const overlay = getOverlay('clock')
    // No crossings — only clock-domain entries.
    const noCdc = overlay.legend({
      nodes: [{ id: 'a', overlays: { clock: { clock: 'clk_a' } } }],
      edges: [{ from: 'x', to: 'a' }],
    })
    expect(noCdc.find((e) => e.label === 'CDC crossing')).toBeUndefined()
    // With a crossing edge — CDC dashed-line entry appears.
    const withCdc = overlay.legend({
      nodes: [{ id: 'a', overlays: { clock: { clock: 'clk_a' } } }],
      edges: [
        {
          from: 'x',
          to: 'a',
          overlays: { clock: { crossing: true } },
        },
      ],
    })
    const cdc = withCdc.find((e) => e.label === 'CDC crossing')
    expect(cdc).toBeDefined()
    expect(cdc.kind).toBe('dashed-line')
    expect(cdc.swatch).toBe('#dc2626')
  })

  it('clock overlay legend and apply agree on the colour for each clock', () => {
    // Reproduces the rtl-buddy-view live demo bug (2026-05-20):
    // ``apply`` painted by first-seen palette index, ``legend``
    // painted by sorted-alphabetical, and they disagreed. Both must
    // use the same sorted-alphabetical assignment so the user can
    // read the legend off the schematic.
    const overlay = getOverlay('clock')
    const graph = {
      // Deliberately *not* alphabetical traversal order — if apply
      // walked first-seen this would assign clk_z=PALETTE[0],
      // clk_a=PALETTE[1], legend would do the reverse.
      nodes: [
        { id: 'a', overlays: { clock: { clock: 'clk_z' } } },
        { id: 'b', overlays: { clock: { clock: 'clk_a' } } },
        { id: 'c', overlays: { clock: { clock: 'clk_m' } } },
      ],
      edges: [],
    }
    const legend = overlay.legend(graph)
    const legendByLabel = Object.fromEntries(legend.map((e) => [e.label, e.swatch]))
    expect(legend.map((e) => e.label)).toEqual(['clk_a', 'clk_m', 'clk_z'])

    // Mock a minimal DOM: each node has a polygon child the overlay
    // resolves via ``data-node-id``. Verify the fill applied matches
    // the legend swatch for that clock.
    const polys = {}
    const svgRoot = {
      querySelector(sel) {
        const m = sel.match(/data-node-id="([^"]+)"/)
        if (!m) return null
        const id = m[1]
        if (!polys[id]) {
          let fillAttr = ''
          const shape = {
            style: { fill: '' },
            setAttribute(name, val) { if (name === 'fill') fillAttr = val },
            removeAttribute(name) { if (name === 'fill') fillAttr = '' },
            getFill: () => fillAttr,
          }
          polys[id] = {
            shape,
            setAttribute() {},
            removeAttribute() {},
            querySelector() { return shape },
          }
        }
        return polys[id]
      },
    }
    overlay.apply(svgRoot, graph, true)
    expect(polys.a.shape.style.fill).toBe(legendByLabel.clk_z)
    expect(polys.b.shape.style.fill).toBe(legendByLabel.clk_a)
    expect(polys.c.shape.style.fill).toBe(legendByLabel.clk_m)
  })

  it('clock overlay apply(enabled=false) clears inline style but preserves the producer-default fill attribute', () => {
    // SVG defaults ``fill`` to *black* when the attribute is
    // absent. Graphviz sets each polygon's ``fill=`` attribute
    // from the DOT global ``node [fillcolor=...]`` default
    // (neutral grey ``#f5f5f5``), and clearing inline style on
    // toggle-off restores that neutral floor. An earlier
    // defensive ``removeAttribute('fill')`` made modules turn
    // black on toggle-off (rtl-buddy-view live demo,
    // 2026-05-21) — this test pins the corrected behaviour.
    const overlay = getOverlay('clock')
    const graph = {
      nodes: [{ id: 'a', overlays: { clock: { clock: 'clk_a' } } }],
      edges: [],
    }
    let fillAttr = '#f5f5f5' // producer's default neutral fill
    const shape = {
      style: { fill: 'red' },
      setAttribute(name, val) { if (name === 'fill') fillAttr = val },
      removeAttribute(name) { if (name === 'fill') fillAttr = '' },
      getFill: () => fillAttr,
    }
    const group = {
      shape,
      setAttribute() {},
      removeAttribute() {},
      querySelector() { return shape },
    }
    const svgRoot = { querySelector: () => group }
    overlay.apply(svgRoot, graph, false)
    expect(shape.style.fill).toBe('')
    expect(shape.getFill()).toBe('#f5f5f5')
  })

  it('wave overlay is registered and exposes a legend()', () => {
    expect(getOverlay('wave')).not.toBeNull()
    expect(getOverlay('wave').name).toBe('wave')
    // No overlay_meta + no static snapshot → empty legend, not undefined.
    expect(getOverlay('wave').legend({})).toEqual([])
  })

  it('wave overlay legend surfaces overlay_meta.wave.t_fs', () => {
    const overlay = getOverlay('wave')
    const legend = overlay.legend({ overlay_meta: { wave: { t_fs: '12500000' } } })
    expect(legend).toEqual([
      { label: '@ 12500000 fs', swatch: null, kind: 'note' },
    ])
  })

  it('resolvePortValues prefers live cache over static snapshot', () => {
    // Phase-8 producer wrote ``data_in = 32'h0`` at the file's
    // sample time. Hub then pushed ``32'hDEAD_BEEF`` for the same
    // signal at a later cursor position. The renderer should paint
    // the live value, not the stale one.
    const node = {
      id: 'tb.dut',
      ports: [
        { name: 'data_in' },
        { name: 'clk' },
      ],
      overlays: {
        wave: {
          ports: [
            { name: 'data_in', value: "32'h00000000" },
            { name: 'clk',     value: "1'b0" },
          ],
        },
      },
    }
    const live = { 'tb.dut.data_in': "32'hDEAD_BEEF" }
    const values = resolvePortValues(node, live)
    expect(values.get('data_in')).toBe("32'hDEAD_BEEF")
    // clk wasn't refreshed by the live cache — static value stays.
    expect(values.get('clk')).toBe("1'b0")
  })

  it('resolvePortValues falls back to static-only when no live cache', () => {
    const node = {
      id: 'tb.dut',
      ports: [{ name: 'q' }],
      overlays: { wave: { ports: [{ name: 'q', value: "1'b1" }] } },
    }
    expect(resolvePortValues(node, {}).get('q')).toBe("1'b1")
  })

  it('resolvePortValues honours explicit wave_scope/signal overrides on ports', () => {
    // A producer that knows the wave-side variable doesn't match
    // ``${node.id}.${port.name}`` (e.g. testbench wrapping)
    // overrides via per-port wave_scope/signal.
    const node = {
      id: 'tb.dut',
      ports: [{ name: 'q', wave_scope: 'top.dut', signal: 'q_int' }],
      overlays: {},
    }
    const live = { 'top.dut.q_int': "1'b1" }
    expect(resolvePortValues(node, live).get('q')).toBe("1'b1")
  })

  it('resolvePortValues walks node-path prefixes to match testbench-wrapped scopes', () => {
    // The bridge broadcasts wave_values_changed keyed by the
    // VCD/FST's hierarchy (``tb_top.clk``). The viewer's node.id is
    // design-relative (``test_module_3``). The wave overlay must
    // walk node-path prefixes shedding one segment at a time so the
    // join terminates at the bare suffix (``clk``) — without this,
    // the live cascade never paints badges on a typical testbench.
    const node = {
      id: 'test_module_3',
      ports: [{ name: 'clk' }, { name: 'rst' }, { name: 'z' }],
      overlays: {},
    }
    const live = {
      'tb_top.clk': '1',
      'tb_top.rst': '0',
      'tb_top.z':   '1010',
    }
    const out = resolvePortValues(node, live)
    expect(out.get('clk')).toBe('1')
    expect(out.get('rst')).toBe('0')
    expect(out.get('z')).toBe('1010')
  })

  it('resolvePortValues skips interface ports — bundles are not value-badge-eligible (#102)', () => {
    // Interface ports (``test_mem_if.sub m``) are bundles, not
    // scalars. Painting a single literal next to them would be
    // ambiguous. Even if the live cache has a suffix match for
    // ``m`` (perhaps a coincidental wire elsewhere in the design),
    // the overlay must skip the entry.
    const node = {
      id: 'tb_top.dut',
      ports: [
        { name: 'clk' },
        {
          name: 'm',
          dir: null,
          port_kind: 'interface',
          interface_type: 'test_mem_if',
          modport: 'sub',
        },
        { name: 'z' },
      ],
      overlays: {},
    }
    const live = {
      'tb_top.clk': '1',
      'tb_top.m': '1010', // would be ambiguous if we painted it
      'tb_top.z': '11',
    }
    const out = resolvePortValues(node, live)
    expect(out.get('clk')).toBe('1')
    expect(out.get('z')).toBe('11')
    // Interface port omitted from the values map even though the
    // live cache had a match for its name.
    expect(out.has('m')).toBe(false)
  })

  it('resolvePortValues prefers shortest matching path on ambiguous suffix', () => {
    // Two cache keys end with ``.q``. The shorter (closer to leaf)
    // wins — the design subtree usually sits below the testbench
    // wrapper, so shorter == more-specific by construction.
    const node = {
      id: 'design_top',
      ports: [{ name: 'q' }],
      overlays: {},
    }
    const live = {
      'tb.dut.q':            'A',
      'tb.dut.u_inner.q':    'B',
    }
    expect(resolvePortValues(node, live).get('q')).toBe('A')
  })

  it('resolvePortValues walks prefixes for nested instances', () => {
    // node.id ``counter.u_ff``, VCD path ``tb.dut.u_ff.q``. The
    // walker drops ``counter`` (no match for ``counter.u_ff.q``),
    // then finds ``u_ff.q`` as a suffix of ``tb.dut.u_ff.q``.
    const node = {
      id: 'counter.u_ff',
      ports: [{ name: 'q' }],
      overlays: {},
    }
    const live = { 'tb.dut.u_ff.q': '1' }
    expect(resolvePortValues(node, live).get('q')).toBe('1')
  })

  it('wave overlay applies port-value badges and clears them on toggle-off', () => {
    const overlay = getOverlay('wave')
    const graph = {
      nodes: [
        {
          id: 'tb.dut',
          ports: [{ name: 'q' }],
          overlays: { wave: { ports: [{ name: 'q', value: "1'b1" }] } },
        },
      ],
    }
    // Stub the SVG group. The new wave overlay's paintBadge does
    // querySelectorAll('.rb-wave-badge') to clear stale badges, then
    // queries svgRoot for ``[data-bf-id="bf-in:..."]`` cells; in the
    // hier-view fallback path it queries the group for the polygon
    // shape and appends a single stacked text. Track every appended
    // child so we can assert.
    const appended = []
    const shape = {
      getBBox: () => ({ x: 0, y: 0, width: 50, height: 30 }),
    }
    const group = {
      attrs: {},
      setAttribute(name, val) { this.attrs[name] = val },
      removeAttribute(name) { delete this.attrs[name] },
      querySelector(sel) {
        if (sel.includes('rb-wave-badge')) return null
        return shape
      },
      querySelectorAll() { return appended.slice() },
      appendChild(node) { appended.push(node) },
    }
    const origCreate = global.document?.createElementNS
    global.document = {
      createElementNS(_ns, _name) {
        return {
          children: [],
          attrs: {},
          textContent: '',
          firstChild: null,
          setAttribute(k, v) { this.attrs[k] = v },
          appendChild(c) {
            this.children.push(c)
            if (!this.firstChild) this.firstChild = c
          },
          removeChild(c) {
            const i = this.children.indexOf(c)
            if (i >= 0) this.children.splice(i, 1)
            this.firstChild = this.children[0] || null
          },
          remove() { /* clearBadge → remove() */ },
        }
      },
    }
    // svgRoot has no block-flow cells → paintBadge falls through to
    // the stacked-hier-view path.
    const svgRoot = {
      querySelector(sel) {
        if (sel.startsWith('[data-bf-id')) return null
        return group
      },
    }

    overlay.apply(svgRoot, graph, true, { waveValuesByKey: {} })
    expect(appended.length).toBe(1)
    const badge = appended[0]
    expect(badge.attrs.class).toBe('rb-wave-badge')
    expect(badge.children.length).toBe(1)
    expect(badge.children[0].textContent).toBe("q=1'b1")

    // Toggle off: clearBadge calls querySelectorAll → returns the
    // currently-appended badges, then ``remove()`` is invoked. Track
    // the calls so we can assert the badge was cleared.
    let removed = 0
    badge.remove = () => { removed += 1 }
    overlay.apply(svgRoot, graph, false, { waveValuesByKey: {} })
    expect(removed).toBe(1)

    global.document.createElementNS = origCreate
  })

  it('wave overlay tags the node carrying a selected signal', () => {
    const overlay = getOverlay('wave')
    const graph = {
      nodes: [
        { id: 'tb.dut', ports: [{ name: 'q' }], overlays: {} },
      ],
    }
    const group = {
      attrs: {},
      setAttribute(k, v) { this.attrs[k] = v },
      removeAttribute(k) { delete this.attrs[k] },
      querySelector() { return null },  // no shape → no badge painted
      querySelectorAll() { return [] },  // no stale badges to clear
      appendChild() {},
    }
    overlay.apply(
      { querySelector: () => group },
      graph,
      true,
      { waveValuesByKey: {}, selectedSignal: { wave_scope: 'tb.dut', signal: 'q' } },
    )
    expect(group.attrs['data-wave-selected']).toBe('true')

    // Different signal → tag removed.
    overlay.apply(
      { querySelector: () => group },
      graph,
      true,
      { waveValuesByKey: {}, selectedSignal: { wave_scope: 'tb.dut', signal: 'other' } },
    )
    expect(group.attrs['data-wave-selected']).toBeUndefined()
  })

  it('coverage overlay is registered and exposes a heat-ramp legend', () => {
    const overlay = getOverlay('coverage')
    expect(overlay).not.toBeNull()
    expect(overlay.name).toBe('coverage')
    const legend = overlay.legend({ overlay_meta: { coverage: { metric: 'branches' } } })
    expect(legend.map((e) => e.label)).toEqual([
      '0% branches',
      '50% branches',
      '100% branches',
      'no coverage data',
    ])
  })

  it('coverage heatColor ramps red→green and clamps out-of-range pct', () => {
    expect(heatColor(0)).toBe('hsl(0, 70%, 82%)')
    expect(heatColor(50)).toBe('hsl(60, 70%, 82%)')
    expect(heatColor(100)).toBe('hsl(120, 70%, 82%)')
    expect(heatColor(-5)).toBe(heatColor(0))
    expect(heatColor(140)).toBe(heatColor(100))
  })

  it('tintMetric defaults to lines and honours overlay_meta.coverage.metric', () => {
    expect(tintMetric({})).toBe('lines')
    expect(tintMetric({ overlay_meta: { coverage: { metric: 'toggles' } } })).toBe('toggles')
  })

  it('coverage overlay tints by the configured metric, grays no-data nodes, clears on toggle-off', () => {
    const overlay = getOverlay('coverage')
    const graph = {
      overlays_present: ['coverage'],
      overlay_meta: { coverage: { metric: 'lines' } },
      nodes: [
        {
          id: 'top.u_covered',
          overlays: { coverage: { lines: { covered: 9, total: 10, pct: 90.0 } } },
        },
        { id: 'top.u_nodata', overlays: {} },
      ],
      edges: [],
    }
    const groups = {}
    const svgRoot = {
      querySelector(sel) {
        const m = sel.match(/data-node-id="([^"]+)"/)
        if (!m) return null
        const id = m[1].replace(/\\/g, '')
        if (!groups[id]) {
          const shape = { style: { fill: '' } }
          groups[id] = {
            shape,
            attrs: {},
            setAttribute(k, v) { this.attrs[k] = v },
            removeAttribute(k) { delete this.attrs[k] },
            querySelector() { return shape },
          }
        }
        return groups[id]
      },
    }
    overlay.apply(svgRoot, graph, true)
    expect(groups['top.u_covered'].shape.style.fill).toBe(heatColor(90))
    expect(groups['top.u_covered'].attrs['data-overlay-coverage']).toBe('90')
    // No coverage block → explicit "no data" gray while enabled.
    expect(groups['top.u_nodata'].shape.style.fill).toBe('#e5e7eb')
    expect(groups['top.u_nodata'].attrs['data-overlay-coverage']).toBe('no-data')

    overlay.apply(svgRoot, graph, false)
    expect(groups['top.u_covered'].shape.style.fill).toBe('')
    expect(groups['top.u_covered'].attrs['data-overlay-coverage']).toBeUndefined()
    expect(groups['top.u_nodata'].shape.style.fill).toBe('')
  })

  it('clock overlay legend reads top-level overlay_meta.clock.clocks when present', () => {
    // SDC-declared clocks that bind to no flop (e.g. used only on an
    // output port) wouldn't otherwise appear in the legend because
    // the node walk never sees them. The optional
    // ``overlay_meta.clock.clocks`` manifest gives producers a way
    // to surface them.
    const overlay = getOverlay('clock')
    const graph = {
      nodes: [{ id: 'a', overlays: { clock: { clock: 'clk_a' } } }],
      overlay_meta: {
        clock: {
          clocks: [{ name: 'clk_unbound' }, 'clk_other'],
        },
      },
    }
    const labels = overlay.legend(graph).map((e) => e.label)
    expect(labels).toEqual(['clk_a', 'clk_other', 'clk_unbound'])
  })
})

// ---------------------------------------------------------------------
// phys overlay (Phase 7b — rtl-buddy/rtl-buddy-sch#22)
// ---------------------------------------------------------------------
//
// The area/power channels, the two-axis composition rules, and the
// self-vs-subtree scope. The DOM fake below is richer than the
// coverage one above because the power ring is its own element —
// cloned off the node shape so the reset overlay's border on the
// shape itself survives underneath it.

function physSvg(ids) {
  const groups = {}
  for (const id of ids) {
    const shape = {
      style: {},
      attrs: {},
      setAttribute(k, v) {
        this.attrs[k] = v
      },
      removeAttribute(k) {
        delete this.attrs[k]
      },
      cloneNode() {
        return {
          style: {},
          attrs: {},
          setAttribute(k, v) {
            this.attrs[k] = v
          },
          removeAttribute(k) {
            delete this.attrs[k]
          },
        }
      },
    }
    groups[id] = {
      shape,
      ring: null,
      attrs: {},
      classList: { contains: () => false },
      setAttribute(k, v) {
        this.attrs[k] = v
      },
      removeAttribute(k) {
        delete this.attrs[k]
      },
      appendChild(child) {
        this.ring = child
      },
      removeChild() {
        this.ring = null
      },
      querySelector(sel) {
        if (sel.includes('data-phys-ring')) return this.ring
        return this.shape
      },
      // The wave overlay is applied unconditionally by
      // ``applyOverlays``; give it the empty badge list it expects.
      querySelectorAll() {
        return []
      },
    }
  }
  return {
    groups,
    svgRoot: {
      querySelector(sel) {
        const m = sel.match(/data-node-id="([^"]+)"/)
        if (!m) return null
        return groups[m[1].replace(/\\/g, '')] || null
      },
    },
  }
}

const PHYS_GRAPH = {
  overlays_present: ['phys'],
  overlay_meta: {
    phys: {
      join_root: 'phys_top',
      halves: { modules: true, instances: true },
      notes: [],
    },
  },
  nodes: [
    {
      id: 'phys_top',
      module: 'phys_top',
      overlays: {
        phys: {
          cell_count: 3,
          area_um2: 11.172,
          self_area_um2: 1.064,
          total_uw: 0.075,
          subtree_total_uw: 2.8565,
        },
      },
    },
    {
      id: 'phys_top.u_sub',
      module: 'phys_sub',
      overlays: {
        phys: {
          cell_count: 2,
          area_um2: 5.586,
          self_area_um2: 1.064,
          total_uw: 0.0888,
          subtree_total_uw: 2.5154,
        },
      },
    },
    { id: 'phys_top.u_none', module: 'blackbox', overlays: {} },
  ],
  edges: [],
}

describe('phys overlay', () => {
  it('is registered and reports the two channels in its flat legend', () => {
    const overlay = getOverlay('phys')
    expect(overlay).not.toBeNull()
    expect(overlay.name).toBe('phys')
    const labels = overlay.legend(PHYS_GRAPH, {}).map((e) => e.label)
    expect(labels).toEqual([
      'small area (subtree)',
      'large area (subtree)',
      'low power (subtree)',
      'high power (subtree)',
      'not measured',
    ])
    // The legend follows the scope toggle, so the panel never labels
    // one scope's swatches while the canvas paints the other's.
    expect(overlay.legend(PHYS_GRAPH, { physScope: 'self' })[0].label).toBe(
      'small area (self)',
    )
  })

  it('reads the sequential heat ramp from the vendored --heat-* tokens', () => {
    // hsl(h, s, lerp(l0, l1)) — the expression the hub's /phy pane
    // computes in CSS calc. A page-local ramp would drift from it.
    expect(heatRampColor(0)).toBe('hsl(18, 85%, 96%)')
    expect(heatRampColor(1)).toBe('hsl(18, 85%, 66%)')
    expect(heatRampColor(0.5)).toBe('hsl(18, 85%, 81%)')
    expect(heatRampColor(-1)).toBe(heatRampColor(0))
    expect(heatRampColor(9)).toBe(heatRampColor(1))
    expect(heatNoneColor()).toBe('#e5e7eb')
  })

  it('scope selection defaults to subtree and validates the value', () => {
    expect(physScope({})).toBe('subtree')
    expect(physScope({ physScope: 'nonsense' })).toBe('subtree')
    expect(physScope({ physScope: 'self' })).toBe('self')
  })

  it('picks subtree vs self figures per scope, and omits what is absent', () => {
    const block = PHYS_GRAPH.nodes[1].overlays.phys
    expect(areaOf(block, 'subtree')).toBe(5.586)
    expect(areaOf(block, 'self')).toBe(1.064)
    expect(powerOf(block, 'subtree')).toBe(2.5154)
    expect(powerOf(block, 'self')).toBe(0.0888)
    // ``self_area_um2`` is omitted by the producer whenever the
    // subtraction would have been a guess — the self scope then has
    // no area rather than a wrong one.
    expect(areaOf({ area_um2: 4 }, 'self')).toBeNull()
    expect(powerOf(null, 'subtree')).toBeNull()
  })

  it('fills by area fraction and rings by power fraction, relative to the max', () => {
    const overlay = getOverlay('phys')
    const { groups, svgRoot } = physSvg([
      'phys_top',
      'phys_top.u_sub',
      'phys_top.u_none',
    ])
    overlay.apply(svgRoot, PHYS_GRAPH, true, { enabledOverlays: new Set(['phys']) })

    // phys_top owns the largest area AND the hottest subtree power.
    expect(groups['phys_top'].shape.style.fill).toBe(heatRampColor(1))
    expect(groups['phys_top'].ring.style.stroke).toBe(heatRampColor(1))
    expect(groups['phys_top'].attrs['data-overlay-phys-area']).toBe('100')
    expect(groups['phys_top'].attrs['data-overlay-phys-power']).toBe('100')

    // u_sub is half the area and most of the power.
    expect(groups['phys_top.u_sub'].attrs['data-overlay-phys-area']).toBe('50')
    expect(groups['phys_top.u_sub'].attrs['data-overlay-phys-power']).toBe('88')

    // A node the model said nothing about: the explicit not-measured
    // grey while the overlay is on, never a zero-area colour.
    expect(groups['phys_top.u_none'].shape.style.fill).toBe(heatNoneColor())
    expect(groups['phys_top.u_none'].attrs['data-overlay-phys-area']).toBeUndefined()
  })

  it('re-styles on a scope switch without touching layout inputs', () => {
    const overlay = getOverlay('phys')
    const { groups, svgRoot } = physSvg(['phys_top', 'phys_top.u_sub'])
    const context = { enabledOverlays: new Set(['phys']) }
    overlay.apply(svgRoot, PHYS_GRAPH, true, context)
    const subtreeFill = groups['phys_top.u_sub'].shape.style.fill

    // Self scope: both nodes' self areas are equal (1.064), so u_sub
    // saturates to the max where the subtree scope had it at half.
    overlay.apply(svgRoot, PHYS_GRAPH, true, { ...context, physScope: 'self' })
    expect(groups['phys_top.u_sub'].shape.style.fill).not.toBe(subtreeFill)
    expect(groups['phys_top.u_sub'].attrs['data-overlay-phys-area']).toBe('100')
    // Self power: u_sub 0.0888 of a 0.0888 max.
    expect(groups['phys_top.u_sub'].attrs['data-overlay-phys-power']).toBe('100')
    expect(groups['phys_top'].attrs['data-overlay-phys-power']).toBe('84')
  })

  it('takes the clock hue and drives only saturation when clock is on', () => {
    const overlay = getOverlay('phys')
    const graph = {
      ...PHYS_GRAPH,
      overlays_present: ['clock', 'phys'],
      nodes: PHYS_GRAPH.nodes.map((n) => ({
        ...n,
        overlays: { ...n.overlays, clock: { clock: 'clk_a' } },
      })),
    }
    const { groups, svgRoot } = physSvg([
      'phys_top',
      'phys_top.u_sub',
      'phys_top.u_none',
    ])
    overlay.apply(svgRoot, graph, true, {
      enabledOverlays: new Set(['clock', 'phys']),
    })
    // One clock → the first palette pastel, #dbeafe = hsl(214, 94%, 93%).
    // At the top (full area) the fill IS that pastel; at half area the
    // hue and lightness hold and the saturation drops.
    expect(groups['phys_top'].shape.style.fill).toBe('hsl(214.3, 94.6%, 92.7%)')
    expect(groups['phys_top.u_sub'].shape.style.fill).toBe('hsl(214.3, 53.3%, 92.7%)')
    expect(saturateBy('#dbeafe', 1)).toBe('hsl(214.3, 94.6%, 92.7%)')
    expect(saturateBy('not-a-colour', 1)).toBeNull()
  })

  it('yields the fill to coverage and says so, keeping the ring', () => {
    const overlay = getOverlay('phys')
    const enabledOverlays = new Set(['coverage', 'phys'])
    const { groups, svgRoot } = physSvg(['phys_top', 'phys_top.u_sub'])
    // Coverage has already painted this fill (it runs first — the
    // registry iterates ``overlays_present``, and 'coverage' sorts
    // before 'phys'); phys must leave it exactly as found.
    groups['phys_top'].shape.style.fill = 'coverage-tint'
    overlay.apply(svgRoot, PHYS_GRAPH, true, { enabledOverlays })
    expect(groups['phys_top'].shape.style.fill).toBe('coverage-tint')
    expect(groups['phys_top'].ring.style.stroke).toBe(heatRampColor(1))
    expect(physFillNote({ enabledOverlays })).toContain('coverage owns the fill')
    expect(physFillNote({ enabledOverlays: new Set(['phys']) })).toBe('')
    // Toggling phys off must not clear a fill it never owned.
    overlay.apply(svgRoot, PHYS_GRAPH, false, { enabledOverlays })
    expect(groups['phys_top'].shape.style.fill).toBe('coverage-tint')
    expect(groups['phys_top'].ring).toBeNull()
  })

  it('rings clusters but never fills them', () => {
    const overlay = getOverlay('phys')
    const { groups, svgRoot } = physSvg(['phys_top'])
    groups['phys_top'].classList = { contains: (c) => c === 'cluster' }
    overlay.apply(svgRoot, PHYS_GRAPH, true, { enabledOverlays: new Set(['phys']) })
    expect(groups['phys_top'].shape.style.fill).toBeUndefined()
    expect(groups['phys_top'].ring.style.stroke).toBe(heatRampColor(1))
  })

  it('clears both channels on toggle-off, idempotently', () => {
    const overlay = getOverlay('phys')
    const context = { enabledOverlays: new Set(['phys']) }
    const { groups, svgRoot } = physSvg(['phys_top'])
    overlay.apply(svgRoot, PHYS_GRAPH, true, context)
    overlay.apply(svgRoot, PHYS_GRAPH, false, context)
    overlay.apply(svgRoot, PHYS_GRAPH, false, context)
    expect(groups['phys_top'].shape.style.fill).toBe('')
    expect(groups['phys_top'].ring).toBeNull()
    expect(groups['phys_top'].attrs['data-overlay-phys-area']).toBeUndefined()
    expect(groups['phys_top'].attrs['data-overlay-phys-power']).toBeUndefined()
  })

  it('reports the maxima it scales against, and no fraction without one', () => {
    expect(physMax(PHYS_GRAPH, 'subtree')).toEqual({
      area: 11.172,
      power: 2.8565,
    })
    expect(physMax({ nodes: [] }, 'subtree')).toEqual({ area: 0, power: 0 })
    expect(fractionOf(5, 10)).toBe(0.5)
    expect(fractionOf(5, 0)).toBeNull()
    expect(fractionOf(null, 10)).toBeNull()
  })

  it('exposes a 3x3 bivariate key over both channels', () => {
    const key = bivariateLegend()
    expect(key.rows).toHaveLength(3)
    for (const row of key.rows) expect(row.cells).toHaveLength(3)
    expect(key.areaLabels).toEqual(['small', 'mid', 'large'])
    expect(key.powerLabels).toEqual(['hot', 'mid', 'cool'])
    // Hottest row first, and each cell carries BOTH channels — a
    // reader matches a node to a cell rather than to two ramps.
    expect(key.rows[0].cells[2]).toMatchObject({
      fill: heatRampColor(1),
      ring: heatRampColor(1),
    })
    expect(key.rows[2].cells[0]).toMatchObject({
      fill: heatRampColor(0),
      ring: heatRampColor(0),
    })
  })

  it('applyOverlays forwards the enabled set so phys can compose', () => {
    // The composition rules above are only reachable if the registry
    // tells each overlay what ELSE is on. Regression guard for the
    // context plumbing in overlays/index.js.
    const { groups, svgRoot } = physSvg(['phys_top'])
    groups['phys_top'].shape.style.fill = 'coverage-tint'
    applyOverlays(svgRoot, PHYS_GRAPH, new Set(['coverage', 'phys']))
    expect(groups['phys_top'].shape.style.fill).toBe('coverage-tint')
  })
})
