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

  it('reset overlay legend lists the two visual roles', () => {
    const overlay = getOverlay('reset')
    const legend = overlay.legend()
    expect(legend.map((e) => e.label)).toEqual([
      'reset-binding',
      'reset-synchroniser',
    ])
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
