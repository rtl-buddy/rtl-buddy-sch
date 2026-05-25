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
import { resolvePortValues } from '../src/overlays/wave.js'

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
    // Stub the SVG group: querySelector finds the polygon shape on
    // first hit and the badge on second hit. Track DOM mutations so
    // we can assert what the overlay wrote.
    const removed = []
    let badge = null
    const shape = {
      getBBox: () => ({ x: 0, y: 0, width: 50, height: 30 }),
    }
    const group = {
      _selectorHits: 0,
      attrs: {},
      setAttribute(name, val) { this.attrs[name] = val },
      removeAttribute(name) { delete this.attrs[name] },
      querySelector(sel) {
        if (sel.includes('rb-wave-badge')) return badge
        return shape
      },
      appendChild(node) { badge = node },
    }
    const document_ns = []
    const origCreate = global.document?.createElementNS
    global.document = {
      createElementNS(_ns, name) {
        const el = {
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
        }
        document_ns.push(name)
        return el
      },
    }
    const svgRoot = { querySelector: () => group }

    overlay.apply(svgRoot, graph, true, { waveValuesByKey: {} })
    expect(badge).not.toBeNull()
    expect(badge.attrs.class).toBe('rb-wave-badge')
    expect(badge.children.length).toBe(1)
    expect(badge.children[0].textContent).toBe("q=1'b1")

    // Toggle off: badge is removed via group.querySelector → badge,
    // then badge has no .remove() in our stub; the overlay calls
    // existing.remove(). Provide it.
    badge.remove = function () { removed.push(this); badge = null }
    overlay.apply(svgRoot, graph, false, { waveValuesByKey: {} })
    expect(removed.length).toBe(1)
    expect(badge).toBeNull()

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
