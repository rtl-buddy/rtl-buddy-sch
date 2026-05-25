// blockFlow.js — derives a one-level signal-flow DOT from
// view.json's per-node port expressions. Tests pin the derivation
// rules (output→input net matching, scope-port boundary edges,
// clock/reset filtering) without standing up viz.js.

import { describe, expect, it } from 'vitest'
import { buildBlockFlowDot } from '../src/layout/blockFlow.js'

function makeGraph(overrides = {}) {
  return {
    schema_version: '1.0',
    top: 'top',
    overlays_present: [],
    nodes: [
      {
        id: 'top',
        module: 'top',
        instance_name: null,
        is_blackbox: false,
        parameters: {},
        ports: [
          { name: 'din', dir: 'input', expr: null, anchor: null },
          { name: 'dout', dir: 'output', expr: null, anchor: null },
          { name: 'clk', dir: 'input', expr: null, anchor: null },
        ],
        overlays: {},
      },
      {
        id: 'top.u_a',
        module: 'a_mod',
        instance_name: 'u_a',
        is_blackbox: false,
        parameters: {},
        ports: [
          { name: 'd_in', dir: 'input', expr: 'din', anchor: null },
          { name: 'q', dir: 'output', expr: 'inter_net', anchor: null },
          { name: 'clk', dir: 'input', expr: 'clk', anchor: null },
        ],
        overlays: {},
      },
      {
        id: 'top.u_b',
        module: 'b_mod',
        instance_name: 'u_b',
        is_blackbox: false,
        parameters: {},
        ports: [
          { name: 'd_in', dir: 'input', expr: 'inter_net', anchor: null },
          { name: 'q', dir: 'output', expr: 'dout', anchor: null },
          { name: 'clk', dir: 'input', expr: 'clk', anchor: null },
        ],
        overlays: {},
      },
    ],
    edges: [
      { from: 'top', to: 'top.u_a', port_pairs: [], overlays: {} },
      { from: 'top', to: 'top.u_b', port_pairs: [], overlays: {} },
    ],
    ...overrides,
  }
}

describe('buildBlockFlowDot', () => {
  it('connects driver and sink children via a matching internal net', () => {
    const dot = buildBlockFlowDot(makeGraph(), 'top')
    // u_a drives ``inter_net`` on port ``q``; u_b consumes it on
    // ``d_in`` → internal edge between the named record ports.
    expect(dot).toMatch(
      /"top\.u_a":q:e\s*->\s*"top\.u_b":d_in:w\s*\[id="bf-edge:top\.u_a:q:top\.u_b:d_in"/,
    )
  })

  it('wires scope input port to consuming child', () => {
    const dot = buildBlockFlowDot(makeGraph(), 'top')
    expect(dot).toContain('"_in_din" -> "top.u_a":d_in:w')
  })

  it('wires producing child to scope output port', () => {
    const dot = buildBlockFlowDot(makeGraph(), 'top')
    expect(dot).toContain('"top.u_b":q:e -> "_out_dout"')
  })

  it('emits the port anchors with the ▶ glyph for visual symmetry with hier', () => {
    const dot = buildBlockFlowDot(makeGraph(), 'top')
    expect(dot).toMatch(/"_in_din"\s*\[shape=plaintext, label="din ▶"/)
    expect(dot).toMatch(/"_out_dout"\s*\[shape=plaintext, label="▶ dout"/)
  })

  it('skips clock-named nets so the data path stays legible', () => {
    const dot = buildBlockFlowDot(makeGraph(), 'top')
    // Even though both u_a and u_b have ``clk`` as input expr, no
    // ``clk``-keyed edges should appear — clocks fan out to every
    // child and would bury the data flow.
    expect(dot).not.toMatch(/label="clk"/)
    expect(dot).not.toContain('"_in_clk"')
  })

  it('renders a leaf scope as a single box with its declared ports', () => {
    // u_a is a leaf in this fixture (no children). Previously the
    // block-flow view bailed out with "X has no children" — but
    // hier-view still shows ports for leaves, so block-flow now
    // matches: render the scope as a single HTML-table box with
    // its inputs / outputs labelled.
    const dot = buildBlockFlowDot(makeGraph(), 'top.u_a')
    expect(dot).toMatch(/digraph block_flow_leaf/)
    // u_a's ports include d_in (input) and q (output); clk is also
    // there and we keep it (matches hier-view). Direction is
    // encoded as a ``▶`` glyph prefix on inputs / suffix on outputs.
    expect(dot).toContain('"top.u_a"')
    expect(dot).toContain('>▶ d_in<')
    expect(dot).toContain('>q ▶<')
    expect(dot).toContain('>▶ clk<')
    // Port cells retain the click identity so right-click open-source
    // still works for leaves.
    expect(dot).toContain('bf-in:top.u_a:d_in')
    expect(dot).toContain('bf-out:top.u_a:q')
    expect(dot).toContain('bf-ctr:top.u_a')
  })

  it('falls back to a placeholder when scope id is unknown', () => {
    const dot = buildBlockFlowDot(makeGraph(), 'top.no_such_node')
    expect(dot).toMatch(/not in graph/)
  })

  it('falls back to a placeholder when scope id is missing', () => {
    const dot = buildBlockFlowDot(makeGraph(), null)
    expect(dot).toMatch(/no scope selected/)
  })

  it('emits the scope title with single-backslash \\l markers (not double-escaped)', () => {
    // Regression: ``dotEscape`` doubles backslashes. Wrapping the
    // whole title (including ``\l``) in dotEscape turned every
    // ``\l`` into ``\\l``, which Graphviz reads as a literal
    // backslash + l — the user saw "top\ltop\l" rendered in the
    // SVG instead of stacked lines.
    const dot = buildBlockFlowDot(makeGraph(), 'top')
    // The cluster's ``label=…`` attribute must use single
    // backslashes — never the doubled form.
    expect(dot).not.toMatch(/label="[^"]*\\\\l/)
  })

  it('collapses the title to one line when instance_name is null', () => {
    // The design top in view.json has ``instance_name: null``; the
    // fallback collapses to ``module`` and the two-line stack would
    // be the same string twice. Emit one line instead.
    const dot = buildBlockFlowDot(makeGraph(), 'top')
    expect(dot).toMatch(/label="top\\l"/)
    // No two-line repeat:
    expect(dot).not.toMatch(/label="top\\ltop\\l"/)
  })

  it('renders SystemVerilog interface ports as an italic left-column row (#102)', () => {
    const g = makeGraph()
    // Pin the interface port onto u_a so the child has both wire
    // ports (the existing fixture) and an interface port. The
    // multi-child path (top → u_a, u_b) is the surface most users
    // see.
    const ua = g.nodes.find((n) => n.id === 'top.u_a')
    ua.ports.push({
      name: 'm',
      dir: null,
      expr: 'u_if.sub',
      anchor: null,
      port_kind: 'interface',
      interface_type: 'test_mem_if',
      modport: 'sub',
    })
    const dot = buildBlockFlowDot(g, 'top')
    // The interface cell uses the dedicated href prefix so click
    // handlers can route differently than wire ports.
    expect(dot).toContain('bf-iface:top.u_a:m')
    // The cell text wraps the port name in <I>…</I> with the ▶▶
    // glyph so the visual cue ("bundle, not scalar") is obvious.
    expect(dot).toMatch(/<I>▶▶\s+m<\/I>/)
    // Tooltip carries the interface_type.modport descriptor.
    expect(dot).toMatch(/TITLE="bf-iface:top\.u_a:m :: test_mem_if\.sub"/)
    // Amber background tints the interface row distinct from wires.
    expect(dot).toContain('BGCOLOR="#fef3c7"')
    // No spurious wire-edge emission — interface "expr" is u_if.sub
    // (a non-bare identifier), so the IDENTIFIER_RE filter drops
    // it. But we should also not accidentally treat 'm' as a wire
    // input port:
    expect(dot).not.toContain('bf-in:top.u_a:m')
  })

  it('flattened interface signals render as ▶ wire rows in leaf scope (#105)', () => {
    // When the producer flattens an interface port (port_kind ===
    // "interface_signal"), each signal has a real ``dir`` from its
    // modport. The leaf-scope renderer should treat them like normal
    // wires, picking up the ▶ cell-arrow polish for free.
    const g = makeGraph()
    const ua = g.nodes.find((n) => n.id === 'top.u_a')
    ua.ports.push(
      {
        name: 'm.req',
        dir: 'input',
        expr: 'u_if.sub',
        anchor: null,
        port_kind: 'interface_signal',
        interface_type: 'test_mem_if',
        modport: 'sub',
      },
      {
        name: 'm.addr',
        dir: 'input',
        expr: 'u_if.sub',
        anchor: null,
        port_kind: 'interface_signal',
        interface_type: 'test_mem_if',
        modport: 'sub',
      },
    )
    const dot = buildBlockFlowDot(g, 'top.u_a')
    // Flattened signals appear as bf-in cells (not bf-iface) and
    // carry the ▶ glyph from the cell-arrow polish (#105 ask 2).
    expect(dot).toContain('bf-in:top.u_a:m.req')
    expect(dot).toContain('bf-in:top.u_a:m.addr')
    expect(dot).toMatch(/>▶ m\.req</)
    expect(dot).toMatch(/>▶ m\.addr</)
    // No spurious bf-iface row for the flattened case.
    expect(dot).not.toContain('bf-iface:top.u_a:m.req')
  })

  it('renders interface port on a leaf scope (#102)', () => {
    const g = makeGraph()
    // u_a is a leaf in the base fixture; add an interface port to it
    // and descend.
    const ua = g.nodes.find((n) => n.id === 'top.u_a')
    ua.ports.push({
      name: 'm',
      dir: null,
      expr: null,
      anchor: null,
      port_kind: 'interface',
      interface_type: 'test_mem_if',
      modport: 'sub',
    })
    const dot = buildBlockFlowDot(g, 'top.u_a')
    expect(dot).toMatch(/digraph block_flow_leaf/)
    expect(dot).toContain('bf-iface:top.u_a:m')
    expect(dot).toMatch(/<I>▶▶\s+m<\/I>/)
  })

  it('emits two lines when instance_name differs from module', () => {
    const g = makeGraph()
    // Pretend the user descended into u_a; render with u_a as scope.
    const dot = buildBlockFlowDot(g, 'top.u_a')
    // u_a is a leaf in the fixture, so the canvas is the
    // placeholder — but the scope's title resolution happens
    // first, before the "no children" branch. Switch to a fixture
    // where u_a has a child.
    g.nodes.push({
      id: 'top.u_a.sub',
      module: 'subm',
      instance_name: 'sub',
      is_blackbox: false,
      parameters: {},
      ports: [],
      overlays: {},
    })
    const dot2 = buildBlockFlowDot(g, 'top.u_a')
    // u_a's view.json entry has ``instance_name: 'u_a'`` and
    // ``module: 'a_mod'`` (per makeGraph fixture).
    expect(dot2).toMatch(/label="u_a\\la_mod\\l"/)
  })
})
