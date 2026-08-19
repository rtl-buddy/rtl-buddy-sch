// Tests for the pure half of the elkjs schematic canvas (#163 P2).
//
// The elkjs layout itself is exercised in a real browser by
// ``e2e/schematic.spec.js``; everything here is transformation:
// payload → elkjs input (sides, port order, sizes, wrapper) and
// elkjs output → draw model (absolute coordinates, dress semantics).
// Both directions are pure functions precisely so they can be pinned
// without a WASM engine or a DOM.

import { describe, expect, it } from 'vitest'
import {
  MIN_NODE_WIDTH,
  PORT_CONSTRAINTS,
  ROOT_LAYOUT_OPTIONS,
  SCHEMATIC_ROOT_ID,
  approxMeasure,
  assignPortIndices,
  buildElkGraph,
  busSlash,
  edgeLabelText,
  flagPath,
  isActiveLow,
  makeCanvasMeasurer,
  orderPorts,
  paramLines,
  polylinePath,
  portSideFor,
  resolveMeasure,
  subtreeOf,
  toSchematic,
} from '../src/layout/elkSchematic.js'

function port(name, direction, extra = {}) {
  return {
    id: `top.u_a:${name}`,
    rb: {
      name,
      direction,
      is_clock: false,
      is_reset: false,
      width: 1,
      connected: true,
      ...extra,
    },
  }
}

const payload = {
  id: 'top',
  rb: {
    instance_name: null,
    module_name: 'top',
    is_blackbox: false,
    param_overrides: [],
    clock: null,
    export: { schema_version: 1 },
  },
  ports: [
    { id: 'top:clk', rb: { name: 'clk', direction: 'input', is_clock: true } },
    { id: 'top:q', rb: { name: 'q', direction: 'output' } },
  ],
  children: [
    {
      id: 'top.u_a',
      rb: {
        instance_name: 'u_a',
        module_name: 'fifo',
        is_blackbox: false,
        param_overrides: [
          ['DEPTH', '8'],
          [null, '19'],
        ],
        clock: 'clk',
      },
      ports: [
        port('clk', 'input', { is_clock: true }),
        port('rst_n', 'input', { is_reset: true }),
        port('d', 'input'),
        port('q', 'output'),
      ],
      children: [],
      edges: [],
    },
  ],
  edges: [
    {
      id: 'top:clk->top.u_a',
      sources: ['top:clk'],
      targets: ['top.u_a'],
      rb: { nets: ['clk'], bits: 1, src_pins: ['clk'], dst_pins: ['clk'] },
    },
  ],
}

// --- the wrapper (contract §5.6) --------------------------------------------

describe('buildElkGraph', () => {
  it('wraps the payload in a synthetic root', () => {
    // Not cosmetic: elkjs's importer cannot resolve ports owned by the
    // node handed to layout(), and the payload's root owns ports its
    // own edges reference. One level down it resolves. The exporter
    // ships the payload without the wrapper on purpose — the quirk is
    // elkjs's, not the graph's.
    const g = buildElkGraph(payload, { measure: approxMeasure })
    expect(g.id).toBe(SCHEMATIC_ROOT_ID)
    expect(g.children).toHaveLength(1)
    expect(g.children[0].id).toBe('top')
    expect(g.layoutOptions).toMatchObject(ROOT_LAYOUT_OPTIONS)
  })

  it('carries the layered/orthogonal/merge options the dress needs', () => {
    const g = buildElkGraph(payload, { measure: approxMeasure })
    expect(g.layoutOptions['elk.algorithm']).toBe('layered')
    expect(g.layoutOptions['elk.direction']).toBe('RIGHT')
    expect(g.layoutOptions['elk.edgeRouting']).toBe('ORTHOGONAL')
    expect(g.layoutOptions['elk.hierarchyHandling']).toBe('INCLUDE_CHILDREN')
    // mergeEdges is what makes ELK emit junctionPoints at all.
    expect(g.layoutOptions['elk.layered.mergeEdges']).toBe('true')
  })

  it('returns null for a payload that is not one', () => {
    expect(buildElkGraph(null)).toBeNull()
    expect(buildElkGraph({})).toBeNull()
  })

  it('pins every block to a fixed port order', () => {
    const g = buildElkGraph(payload, { measure: approxMeasure })
    const block = g.children[0].children[0]
    expect(block.layoutOptions['elk.portConstraints']).toBe(PORT_CONSTRAINTS)
    expect(block.layoutOptions['elk.portLabels.placement']).toBe('[INSIDE]')
  })
})

// --- side mapping ------------------------------------------------------------

describe('portSideFor', () => {
  it('maps declared directions', () => {
    expect(portSideFor('input')).toBe('WEST')
    expect(portSideFor('output')).toBe('EAST')
    // inout shares the output side: a bidirectional pin still reads
    // as "this block's edge of the net".
    expect(portSideFor('inout')).toBe('EAST')
  })

  it('falls back to usage for an undeclared direction', () => {
    // Interface bundles and blackbox pins have a null direction by
    // contract; guessing from the net's role beats putting them all
    // on one side.
    expect(portSideFor(null, { drives: true })).toBe('WEST')
    expect(portSideFor(null, { sinks: true })).toBe('EAST')
    expect(portSideFor(null)).toBe('WEST')
  })

  it('lands inputs on WEST and outputs on EAST in a built graph', () => {
    const g = buildElkGraph(payload, { measure: approxMeasure })
    const block = g.children[0].children[0]
    const bySide = Object.fromEntries(block.ports.map((p) => [p.rb.name, p.side]))
    expect(bySide).toEqual({ clk: 'WEST', rst_n: 'WEST', d: 'WEST', q: 'EAST' })
    expect(block.ports.every((p) => p.layoutOptions['elk.port.side'] === p.side))
      .toBe(true)
  })
})

// --- pin ordering ------------------------------------------------------------

describe('orderPorts', () => {
  it('puts data pins before clock and reset', () => {
    const ports = [
      port('clk', 'input', { is_clock: true }),
      port('d', 'input'),
      port('rst_n', 'input', { is_reset: true }),
      port('en', 'input'),
    ]
    expect(orderPorts(ports).map((p) => p.rb.name)).toEqual([
      'd',
      'en',
      'clk',
      'rst_n',
    ])
  })

  it('is stable within a group, preserving declaration order', () => {
    const ports = [port('b', 'input'), port('a', 'input')]
    expect(orderPorts(ports).map((p) => p.rb.name)).toEqual(['b', 'a'])
  })
})

describe('assignPortIndices', () => {
  it('numbers WEST bottom-up so the sorted order reads top-down', () => {
    // ELK numbers ports clockwise from the top-left corner, so the
    // WEST side runs bottom-to-top. Handing it our list unchanged
    // would park clk/rst_n at the TOP of the left border — above the
    // signal path, which is exactly what orderPorts set out to avoid.
    const ports = [
      { side: 'WEST', rb: { name: 'd' }, layoutOptions: {} },
      { side: 'WEST', rb: { name: 'clk' }, layoutOptions: {} },
      { side: 'EAST', rb: { name: 'q' }, layoutOptions: {} },
    ]
    assignPortIndices(ports)
    const idx = Object.fromEntries(
      ports.map((p) => [p.rb.name, p.layoutOptions['elk.port.index']]),
    )
    expect(idx.q).toBe('0')
    expect(idx.clk).toBe('1')
    expect(idx.d).toBe('2')
  })
})

// --- sizing ------------------------------------------------------------------

describe('node sizing', () => {
  it('uses the measured width of the text it will actually draw', () => {
    // The exporter ships no sizes on purpose (contract §5.1): only the
    // consumer knows its font. Prove the measurement is load-bearing
    // by feeding a measurer that reports a very wide font.
    const wide = buildElkGraph(payload, { measure: (t) => t.length * 40 })
    const narrow = buildElkGraph(payload, { measure: (t) => t.length * 2 })
    const w = wide.children[0].children[0]
    const n = narrow.children[0].children[0]
    expect(w.width).toBeGreaterThan(n.width)
    expect(w.layoutOptions['elk.nodeSize.minimum']).not.toBe(
      n.layoutOptions['elk.nodeSize.minimum'],
    )
  })

  it('never draws a block narrower than the schematic minimum', () => {
    const tiny = buildElkGraph(payload, { measure: () => 0 })
    expect(tiny.children[0].children[0].width).toBe(MIN_NODE_WIDTH)
  })

  it('reserves port-label space from the measured label widths', () => {
    const g = buildElkGraph(payload, { measure: approxMeasure })
    const block = g.children[0].children[0]
    const d = block.ports.find((p) => p.rb.name === 'rst_n')
    expect(d.labels[0].text).toBe('rst_n')
    expect(d.labels[0].width).toBeGreaterThan(approxMeasure('rst_n', 9))
    // ELK is told to grow the box for ports AND their labels, so the
    // reservation is enforced by the layout rather than hoped for.
    expect(block.layoutOptions['elk.nodeSize.constraints']).toContain('PORT_LABELS')
  })

  it('gives the design top no port labels — its pins are flags', () => {
    const g = buildElkGraph(payload, { measure: approxMeasure })
    expect(g.children[0].ports.every((p) => p.labels.length === 0)).toBe(true)
  })

  it('carries refdes and type+params as ELK labels on each block', () => {
    const g = buildElkGraph(payload, { measure: approxMeasure })
    const block = g.children[0].children[0]
    const roles = block.labels.map((l) => l.rbRole)
    expect(roles).toEqual(['refdes', 'type'])
    expect(block.labels[0].text).toBe('u_a')
    // A positional override the exporter couldn't name renders as the
    // bare value rather than an invented parameter name.
    expect(block.labels[1].rbLines).toEqual(['fifo', 'DEPTH 8', '19'])
  })
})

describe('paramLines', () => {
  it('formats named and positional overrides', () => {
    expect(paramLines([['W', '16'], [null, '8']])).toEqual(['W 16', '8'])
    expect(paramLines(undefined)).toEqual([])
  })
})

// --- measurement -------------------------------------------------------------

describe('text measurement', () => {
  it('falls back to the monospace approximation without a real canvas', () => {
    // happy-dom hands back a 2D context whose measureText always
    // returns 0 — a measurer that "works" and reports zero is worse
    // than none, because every reserved box silently collapses.
    expect(makeCanvasMeasurer('monospace')).toBeNull()
    expect(resolveMeasure('monospace')).toBe(approxMeasure)
    expect(approxMeasure('abcd', 10)).toBeCloseTo(24)
  })
})

// --- bus / junction / flag data ----------------------------------------------

describe('busSlash', () => {
  it('annotates multi-bit nets only', () => {
    expect(busSlash(19)).toBe('/19')
    expect(busSlash(1)).toBeNull()
    // null width is "unknown", not "one bit" — a guessed bus width is
    // worse than an unlabelled wire (elk.json §6).
    expect(busSlash(null)).toBeNull()
  })
})

describe('edgeLabelText', () => {
  it('names the first net and counts the rest', () => {
    expect(edgeLabelText(['a'])).toBe('a')
    expect(edgeLabelText(['a', 'b', 'c'])).toBe('a +2')
    expect(edgeLabelText([])).toBeNull()
  })
})

describe('isActiveLow', () => {
  it('recognises the conventional low-asserted spellings', () => {
    expect(isActiveLow('rst_n')).toBe(true)
    expect(isActiveLow('areset_n')).toBe(true)
    expect(isActiveLow('rst_b')).toBe(true)
    expect(isActiveLow('rst')).toBe(false)
    expect(isActiveLow('reset')).toBe(false)
  })
})

// --- laid-out graph → draw model ---------------------------------------------

const laidOut = {
  id: '$root',
  x: 0,
  y: 0,
  width: 400,
  height: 300,
  children: [
    {
      id: 'top',
      x: 10,
      y: 10,
      width: 380,
      height: 280,
      rb: { module_name: 'top' },
      ports: [
        {
          id: 'top:clk',
          x: 0,
          y: 40,
          width: 1,
          height: 1,
          side: 'WEST',
          rb: { name: 'clk', is_clock: true },
        },
      ],
      labels: [],
      edges: [
        {
          id: 'e0',
          sources: ['top.u_a:q'],
          targets: ['top.u_b:d'],
          rb: { nets: ['bus_a', 'bus_b'], bits: 19 },
          sections: [
            {
              startPoint: { x: 100, y: 50 },
              bendPoints: [{ x: 150, y: 50 }],
              endPoint: { x: 150, y: 90 },
            },
          ],
          junctionPoints: [{ x: 150, y: 50 }],
          labels: [{ text: 'bus_a +1', x: 110, y: 40, width: 40, height: 10 }],
        },
      ],
      children: [
        {
          id: 'top.u_a',
          x: 20,
          y: 30,
          width: 120,
          height: 60,
          rb: { instance_name: 'u_a', module_name: 'fifo', is_blackbox: false },
          ports: [
            {
              id: 'top.u_a:rst_n',
              x: 0,
              y: 20,
              width: 1,
              height: 1,
              side: 'WEST',
              rb: { name: 'rst_n', is_reset: true, connected: true },
            },
          ],
          labels: [
            {
              rbRole: 'refdes',
              text: 'u_a',
              x: 0,
              y: -16,
              width: 20,
              height: 16,
            },
          ],
          children: [],
          edges: [],
        },
      ],
    },
  ],
}

describe('toSchematic', () => {
  const model = toSchematic(laidOut)

  it('drops the synthetic wrapper and keeps the design as the sheet', () => {
    const sheet = model.boxes.find((b) => b.sheet)
    expect(sheet.id).toBe('top')
    // A compound block is a containment frame, not a filled box.
    expect(model.boxes.find((b) => b.id === 'top.u_a').compound).toBe(false)
  })

  it('resolves child coordinates to absolute', () => {
    // 10 (design) + 20 (child) — ELK reports each level relative to
    // its parent, so a canvas that draws them verbatim stacks
    // everything in the top-left corner.
    const box = model.boxes.find((b) => b.id === 'top.u_a')
    expect(box.x).toBe(30)
    expect(box.y).toBe(40)
  })

  it('turns the design top ports into flags and the rest into pins', () => {
    expect(model.flags.map((f) => f.name)).toEqual(['clk'])
    expect(model.flags[0].isClock).toBe(true)
    expect(model.pins.map((p) => p.name)).toEqual(['rst_n'])
    // Pin identity is the OWNING instance path, so a click on a stub
    // selects its block with no lookup table in between.
    expect(model.pins[0].nodeId).toBe('top.u_a')
    expect(model.pins[0].kind).toBe('reset')
    expect(model.pins[0].activeLow).toBe(true)
  })

  it('extracts bus weight, slash and junction dots from the routing', () => {
    expect(model.wires).toHaveLength(1)
    expect(model.wires[0].bus).toBe(true)
    expect(model.wires[0].slash).toBe('/19')
    // start + bend + end, all shifted by the design's origin.
    expect(model.wires[0].points).toEqual([
      { x: 110, y: 60 },
      { x: 160, y: 60 },
      { x: 160, y: 100 },
    ])
    expect(model.junctions).toEqual([{ edgeId: 'e0', x: 160, y: 60 }])
  })

  it('keeps refdes and net labels with their absolute positions', () => {
    const refdes = model.labels.find((l) => l.role === 'refdes')
    expect(refdes.nodeId).toBe('top.u_a')
    expect(refdes.lines).toEqual(['u_a'])
    expect(refdes.y).toBe(24)
    const net = model.labels.find((l) => l.role === 'net')
    expect(net.lines).toEqual(['bus_a +1'])
    expect(net.x).toBe(120)
  })

  it('survives an empty graph', () => {
    expect(toSchematic(null).boxes).toEqual([])
    expect(toSchematic({ children: [] }).wires).toEqual([])
  })
})

// --- scoping + small geometry helpers ----------------------------------------

describe('subtreeOf', () => {
  it('finds a scope by instance path', () => {
    expect(subtreeOf(payload, 'top.u_a').id).toBe('top.u_a')
    expect(subtreeOf(payload, 'top').id).toBe('top')
  })

  it('returns null for an unknown or empty path', () => {
    expect(subtreeOf(payload, 'top.nope')).toBeNull()
    expect(subtreeOf(payload, null)).toBeNull()
  })
})

describe('geometry helpers', () => {
  it('draws an out-flag pointing right and an in-flag pointing right', () => {
    expect(flagPath(0, 0, 40, 20, true)).toContain('M0,0')
    expect(flagPath(0, 0, 40, 20, false)).toContain('M9,0')
  })

  it('emits square-cornered polylines', () => {
    expect(polylinePath([{ x: 1, y: 2 }, { x: 3, y: 4 }])).toBe('M1,2 L3,4')
  })
})
