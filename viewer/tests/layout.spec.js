// Tests for the DOT-generation helper. The viz.js WASM layout
// itself is integration-tested via the Playwright snapshot
// (next PR); here we only check the DOT we hand to viz.js, which
// is pure string transformation.

import { describe, expect, it } from 'vitest'
import { graphToDot, dotEscape, dotId, pickDot, hasEmbeddedDot } from '../src/layout/viz.js'

const graph = {
  top: 'top',
  nodes: [
    {
      id: 'top',
      module: 'top',
      instance_name: null,
      is_blackbox: false,
    },
    {
      id: 'top.u_a',
      module: 'fifo',
      instance_name: 'u_a',
      is_blackbox: false,
    },
    {
      id: 'top.u_bb',
      module: 'mystery',
      instance_name: 'u_bb',
      is_blackbox: true,
    },
  ],
  edges: [
    { from: 'top', to: 'top.u_a' },
    { from: 'top', to: 'top.u_bb' },
  ],
}

describe('graphToDot', () => {
  it('emits a digraph with every node id present', () => {
    const dot = graphToDot(graph)
    expect(dot).toMatch(/^digraph view \{/)
    // Every node id is quoted (dots aren't legal in bare ids).
    expect(dot).toContain('"top"')
    expect(dot).toContain('"top.u_a"')
    expect(dot).toContain('"top.u_bb"')
    // The root is framed by ``cluster_top`` so its scope name is
    // visible. Plain parent→child containment edges are intentionally
    // NOT emitted any more — nesting (cluster_top + future nested
    // clusters) is the source of containment info.
    expect(dot).toContain('subgraph cluster_top')
    expect(dot).not.toContain('"top" -> "top.u_a"')
    expect(dot).not.toContain('"top" -> "top.u_bb"')
  })

  it('labels blackbox nodes with the (blackbox) tag', () => {
    const dot = graphToDot(graph)
    // viz.js's literal ``\n`` line break for multi-line labels.
    expect(dot).toMatch(/u_bb\\nmystery\\n\(blackbox\)/)
  })

  it('two-line label for non-blackbox children: inst + module', () => {
    const dot = graphToDot(graph)
    expect(dot).toMatch(/u_a\\nfifo/)
  })
})

describe('dotEscape', () => {
  it('escapes backslashes and quotes', () => {
    expect(dotEscape('a\\b"c')).toBe('a\\\\b\\"c')
  })
  it('handles non-string input via String()', () => {
    expect(dotEscape(42)).toBe('42')
  })
})

describe('dotId', () => {
  it('always quotes the id', () => {
    expect(dotId('plain')).toBe('"plain"')
    expect(dotId('with.dots')).toBe('"with.dots"')
  })
})

describe('pickDot', () => {
  it('returns graph.layout.dot verbatim when present', () => {
    // Producer-supplied DOT (e.g. from rtl-buddy-view --format json
    // with embed_layout=True) takes precedence over the in-JS
    // builder so the desktop and browser layouts stay in sync.
    const baked = 'digraph hierarchy { rankdir="LR"; "top"; }'
    const result = pickDot({ ...graph, layout: { engine: 'dot', dot: baked } })
    expect(result).toBe(baked)
  })

  it('falls back to graphToDot when layout is missing', () => {
    expect(pickDot(graph)).toBe(graphToDot(graph))
  })

  it('falls back when layout.dot is empty / whitespace', () => {
    // An empty string would crash viz.js with "syntax error"; treat
    // it as "producer opted out" and rebuild from nodes + edges.
    expect(pickDot({ ...graph, layout: { engine: 'dot', dot: '' } })).toBe(graphToDot(graph))
    expect(pickDot({ ...graph, layout: { engine: 'dot', dot: '   \n' } })).toBe(
      graphToDot(graph),
    )
  })

  it('falls back when layout.dot is not a string', () => {
    expect(pickDot({ ...graph, layout: { engine: 'dot', dot: 42 } })).toBe(graphToDot(graph))
    expect(pickDot({ ...graph, layout: {} })).toBe(graphToDot(graph))
  })
})

describe('hasEmbeddedDot', () => {
  // GraphCanvas asks this, not "did the SVG come out different" —
  // it decides whether to wear the ``.producer-dot`` class that gates
  // the canvas's theme re-tint rules. Those rules exist because the
  // producer's DOT is baked light and unrebuildable here; applied to
  // the in-JS builder's output they would repaint its deliberate
  // colours (CDC red) in body grey. So the predicate has to agree with
  // ``pickDot`` on every input, including the reject cases.
  const cases = [
    ['missing layout', graph, false],
    ['empty layout', { ...graph, layout: {} }, false],
    ['empty string', { ...graph, layout: { dot: '' } }, false],
    ['whitespace only', { ...graph, layout: { dot: '   \n' } }, false],
    ['non-string', { ...graph, layout: { dot: 42 } }, false],
    ['real dot', { ...graph, layout: { dot: 'digraph g { "top"; }' } }, true],
  ]
  for (const [name, g, expected] of cases) {
    it(`${name} → ${expected}, and pickDot agrees`, () => {
      expect(hasEmbeddedDot(g)).toBe(expected)
      expect(pickDot(g) === g.layout?.dot).toBe(expected)
    })
  }

  it('tolerates a missing graph', () => {
    expect(hasEmbeddedDot(null)).toBe(false)
    expect(hasEmbeddedDot(undefined)).toBe(false)
  })
})
