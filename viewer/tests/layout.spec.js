// Tests for the DOT-generation helper. The viz.js WASM layout
// itself is integration-tested via the Playwright snapshot
// (next PR); here we only check the DOT we hand to viz.js, which
// is pure string transformation.

import { describe, expect, it } from 'vitest'
import { graphToDot, dotEscape, dotId } from '../src/layout/viz.js'

const graph = {
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
  it('emits a digraph with every node + edge', () => {
    const dot = graphToDot(graph)
    expect(dot).toMatch(/^digraph view \{/)
    // Every node id is quoted (dots aren't legal in bare ids).
    expect(dot).toContain('"top"')
    expect(dot).toContain('"top.u_a"')
    expect(dot).toContain('"top.u_bb"')
    // Edges in source order.
    expect(dot).toContain('"top" -> "top.u_a"')
    expect(dot).toContain('"top" -> "top.u_bb"')
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
