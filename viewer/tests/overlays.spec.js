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
})
