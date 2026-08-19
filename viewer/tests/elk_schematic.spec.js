// Tests for the pure half of the elkjs schematic canvas (#163 P2).
//
// The elkjs layout itself is exercised in a real browser by
// ``e2e/schematic.spec.js``; everything here is transformation:
// payload → elkjs input (sides, port order, sizes, wrapper) and
// elkjs output → draw model (absolute coordinates, dress semantics).
// Both directions are pure functions precisely so they can be pinned
// without a WASM engine or a DOM.

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'
import {
  MIN_NODE_WIDTH,
  NO_HIGHLIGHT,
  PORT_CONSTRAINTS,
  ROOT_LAYOUT_OPTIONS,
  SCHEMATIC_ROOT_ID,
  SHEET_MARGIN,
  TITLE_BLOCK_PAD,
  TITLE_ROW_HEIGHT,
  approxMeasure,
  assignPortIndices,
  buildElkGraph,
  busSlash,
  collapsePayload,
  collapsibleIds,
  edgeLabelText,
  flagPath,
  highlightFor,
  isActiveLow,
  isSymbolicSlash,
  makeCanvasMeasurer,
  nodeIdOfEndpoint,
  orderPorts,
  paramLines,
  polylinePath,
  portSideFor,
  resolveMeasure,
  sheetFrame,
  subtreeOf,
  titleBlockRows,
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

  it('prefers the declared name over the folded number', () => {
    // ``[WIDTH-1:0]`` bound ``#(.WIDTH(19))`` is both; the name is
    // what tells a reader which knob sets the bus (elk.json §6.2).
    expect(busSlash(19, 'WIDTH')).toBe('/WIDTH')
    expect(busSlash(null, 'PTR_W')).toBe('/PTR_W')
    expect(busSlash(undefined, 'PTR_W+1')).toBe('/PTR_W+1')
    // No name: the number, exactly as before.
    expect(busSlash(19, null)).toBe('/19')
    expect(busSlash(null, '  ')).toBeNull()
    expect(busSlash(null, 7)).toBeNull()
    expect(busSlash(null, null)).toBeNull()
  })

  it('keeps a resolved 1-bit wire a hairline, name or no name', () => {
    // A slash means "bus". This one is known to be a single wire, so
    // the name would be a promise the geometry contradicts.
    expect(busSlash(1, 'WIDTH')).toBeNull()
    expect(busSlash(1)).toBeNull()
  })

  it('flags the drawn label as a name so the canvas can italicise it', () => {
    expect(isSymbolicSlash(null, 'PTR_W')).toBe(true)
    expect(isSymbolicSlash(19, 'WIDTH')).toBe(true)
    expect(isSymbolicSlash(19, null)).toBe(false)
    expect(isSymbolicSlash(1, 'WIDTH')).toBe(false) // nothing is drawn
    expect(isSymbolicSlash(null, null)).toBe(false)
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

// --- collapse (P3) ------------------------------------------------------------

// Three levels, and the shape the exporter actually emits: edges are
// scope-local, and the ones crossing a compound's border terminate on
// that compound's OWN port ids (elk.json §4) — which is why folding is
// a prune, not a re-derivation.
const nested = {
  id: 'top',
  rb: { module_name: 'top', instance_name: null, param_overrides: [] },
  ports: [{ id: 'top:din', rb: { name: 'din', direction: 'input' } }],
  children: [
    {
      id: 'top.u_c',
      rb: { module_name: 'sink', instance_name: 'u_c', param_overrides: [] },
      ports: [{ id: 'top.u_c:d', rb: { name: 'd', direction: 'input' } }],
      children: [],
      edges: [],
    },
    {
      id: 'top.u_p',
      rb: { module_name: 'prod', instance_name: 'u_p', param_overrides: [] },
      ports: [
        { id: 'top.u_p:cmd', rb: { name: 'cmd', direction: 'input' } },
        { id: 'top.u_p:q', rb: { name: 'q', direction: 'output' } },
      ],
      children: [
        {
          id: 'top.u_p.u_s',
          rb: { module_name: 'stage', instance_name: 'u_s', param_overrides: [] },
          ports: [
            { id: 'top.u_p.u_s:din', rb: { name: 'din', direction: 'input' } },
            { id: 'top.u_p.u_s:dout', rb: { name: 'dout', direction: 'output' } },
          ],
          children: [],
          edges: [],
        },
      ],
      edges: [
        {
          id: 'in',
          sources: ['top.u_p:cmd'],
          targets: ['top.u_p.u_s:din'],
          rb: { nets: ['cmd'] },
        },
        {
          id: 'out',
          sources: ['top.u_p.u_s:dout'],
          targets: ['top.u_p:q'],
          rb: { nets: ['q'] },
        },
      ],
    },
  ],
  edges: [
    {
      id: 'feed',
      sources: ['top:din'],
      targets: ['top.u_p:cmd'],
      rb: { nets: ['din'] },
    },
    {
      id: 'drain',
      sources: ['top.u_p:q'],
      targets: ['top.u_c:d'],
      rb: { nets: ['q'] },
    },
  ],
}

describe('collapsePayload', () => {
  it('returns the payload untouched when nothing is collapsed', () => {
    expect(collapsePayload(nested, new Set())).toBe(nested)
    expect(collapsePayload(nested, null)).toBe(nested)
    // A path that names a leaf collapses nothing — there is no inside.
    expect(collapsePayload(nested, new Set(['top.u_c']))).toBe(nested)
  })

  it('folds a compound into a leaf that keeps its own pinout', () => {
    const folded = collapsePayload(nested, new Set(['top.u_p']))
    const block = folded.children.find((c) => c.id === 'top.u_p')
    expect(block.children).toHaveLength(0)
    // The scope-internal wiring goes with the children it wired.
    expect(block.edges).toHaveLength(0)
    // The pinout is the boundary: every port the block declares.
    expect(block.ports.map((p) => p.id)).toEqual(['top.u_p:cmd', 'top.u_p:q'])
  })

  it('leaves the boundary edges terminating on the folded box', () => {
    const folded = collapsePayload(nested, new Set(['top.u_p']))
    // Untouched, because they never reached through to a grandchild.
    expect(folded.edges.map((e) => [e.sources[0], e.targets[0]])).toEqual([
      ['top:din', 'top.u_p:cmd'],
      ['top.u_p:q', 'top.u_c:d'],
    ])
  })

  it('does not mutate the payload it folds', () => {
    const before = JSON.stringify(nested)
    collapsePayload(nested, new Set(['top.u_p']))
    expect(JSON.stringify(nested)).toBe(before)
  })

  it('re-points an edge that reached into the folded subtree', () => {
    // The contract says edges are scope-local, but elkjs throws on an
    // endpoint it cannot resolve — so a payload that reaches through
    // has to degrade to a border attachment, not to an exception.
    const reaching = {
      ...nested,
      edges: [
        ...nested.edges,
        {
          id: 'deep',
          sources: ['top.u_p.u_s:dout'],
          targets: ['top.u_c:d'],
          rb: { nets: ['deep'] },
        },
      ],
    }
    const folded = collapsePayload(reaching, new Set(['top.u_p']))
    const deep = folded.edges.find((e) => e.id === 'deep')
    expect(deep.sources).toEqual(['top.u_p'])
    expect(deep.targets).toEqual(['top.u_c:d'])
  })

  it('drops an edge whose two ends fold into the same box', () => {
    const reaching = {
      ...nested,
      edges: [
        ...nested.edges,
        {
          id: 'internal',
          sources: ['top.u_p.u_s:dout'],
          targets: ['top.u_p.u_s:din'],
          rb: { nets: ['loop'] },
        },
      ],
    }
    const folded = collapsePayload(reaching, new Set(['top.u_p']))
    expect(folded.edges.find((e) => e.id === 'internal')).toBeUndefined()
  })

  it('ignores a collapse nested under another collapse', () => {
    const folded = collapsePayload(nested, new Set(['top.u_p', 'top.u_p.u_s']))
    expect(folded.children.find((c) => c.id === 'top.u_p').children).toHaveLength(0)
  })
})

describe('collapsibleIds', () => {
  it('names every compound block but not the sheet', () => {
    const ids = collapsibleIds(nested)
    expect(ids.has('top.u_p')).toBe(true)
    expect(ids.has('top.u_c')).toBe(false)
    // The payload root is the sheet; folding it would leave nothing.
    expect(ids.has('top')).toBe(false)
  })
})

describe('buildElkGraph with a collapse set', () => {
  it('sizes and pins a folded block exactly like the leaf it now is', () => {
    const g = buildElkGraph(nested, {
      collapsed: new Set(['top.u_p']),
      measure: approxMeasure,
    })
    const block = g.children[0].children.find((c) => c.id === 'top.u_p')
    expect(block.children).toHaveLength(0)
    expect(block.layoutOptions['elk.portConstraints']).toBe(PORT_CONSTRAINTS)
    // No containment padding: there is nothing contained.
    expect(block.layoutOptions['elk.padding']).toBeUndefined()
    expect(block.rbCollapsed).toBe(true)
  })

  it('marks the folded block so the draw model can tell it from a leaf', () => {
    const g = buildElkGraph(nested, { measure: approxMeasure })
    const block = g.children[0].children.find((c) => c.id === 'top.u_p')
    expect(block.rbCollapsed).toBe(false)
  })
})

describe('toSchematic collapse markers', () => {
  it('draws a folded compound as a block, flagged collapsed', () => {
    const model = toSchematic({
      id: '$root',
      width: 100,
      height: 100,
      children: [
        {
          id: 'top',
          x: 0,
          y: 0,
          width: 100,
          height: 100,
          rb: {},
          children: [
            {
              id: 'top.u_p',
              x: 5,
              y: 5,
              width: 50,
              height: 40,
              rb: { instance_name: 'u_p', module_name: 'prod' },
              rbCollapsed: true,
              ports: [],
              children: [],
              edges: [],
            },
          ],
        },
      ],
    })
    const box = model.boxes.find((b) => b.id === 'top.u_p')
    expect(box.compound).toBe(false)
    expect(box.collapsed).toBe(true)
    // Still offers the toggle — that is the whole point of the flag.
    expect(box.collapsible).toBe(true)
  })
})

// --- hover highlighting (P3) --------------------------------------------------

const hoverModel = {
  wires: [
    // One net, two sections — a route ELK split at a bend.
    { id: 'e1', sourceId: 'top.u_a:q', targetId: 'top.u_b:d' },
    { id: 'e1', sourceId: 'top.u_a:q', targetId: 'top.u_b:d' },
    // Junction-merged sibling: same driver, different sink.
    { id: 'e2', sourceId: 'top.u_a:q', targetId: 'top.u_c:d' },
    // Unrelated net.
    { id: 'e3', sourceId: 'top.u_z:q', targetId: 'top.u_b:e' },
  ],
}

describe('highlightFor', () => {
  it('lights nothing without a target', () => {
    expect(highlightFor(hoverModel, {})).toBe(NO_HIGHLIGHT)
    expect(highlightFor(null, { edgeId: 'e1' })).toBe(NO_HIGHLIGHT)
  })

  it('lights every segment of a hovered net and its merged siblings', () => {
    const hot = highlightFor(hoverModel, { edgeId: 'e1' })
    // mergeEdges draws one trunk from the driver with junction dots on
    // it, so the sibling leaving the same pin is the same copper.
    expect([...hot.edges].sort()).toEqual(['e1', 'e2'])
    expect(hot.edges.has('e3')).toBe(false)
  })

  it('lights the endpoint pins of everything it lit', () => {
    const hot = highlightFor(hoverModel, { edgeId: 'e1' })
    expect([...hot.pins].sort()).toEqual([
      'top.u_a:q',
      'top.u_b:d',
      'top.u_c:d',
    ])
  })

  it('lights every edge touching a hovered pin', () => {
    const hot = highlightFor(hoverModel, { pinId: 'top.u_b:d' })
    expect([...hot.edges].sort()).toEqual(['e1'])
    expect(hot.pins.has('top.u_b:d')).toBe(true)
    expect(hot.pins.has('top.u_a:q')).toBe(true)
  })

  it('tolerates an endpoint that degraded to a node id', () => {
    const model = { wires: [{ id: 'e', sourceId: 'top.u_a', targetId: 'top.u_b' }] }
    const hot = highlightFor(model, { edgeId: 'e' })
    expect(hot.edges.has('e')).toBe(true)
    // Node ids are not pin ids; they simply match no pin.
    expect([...hot.pins].sort()).toEqual(['top.u_a', 'top.u_b'])
  })
})

describe('nodeIdOfEndpoint', () => {
  it('splits a port id at the colon and passes a node id through', () => {
    expect(nodeIdOfEndpoint('top.u_a:q')).toBe('top.u_a')
    expect(nodeIdOfEndpoint('top.u_a')).toBe('top.u_a')
    expect(nodeIdOfEndpoint(null)).toBeNull()
  })
})

// --- sheet frame + title block (P4) -------------------------------------------

describe('titleBlockRows', () => {
  it('prints the design, the tool and its version', () => {
    const rows = titleBlockRows({ top: 'blk_top', toolVersion: '1.2.3' })
    expect(rows.map((r) => r.label)).toEqual(['DESIGN', 'TOOL'])
    expect(rows[0].value).toBe('blk_top')
    expect(rows[1].value).toBe('rtl-buddy-sch 1.2.3')
  })

  it('adds the sheet scope and model only when they say something', () => {
    const rows = titleBlockRows({
      top: 'blk_top',
      toolVersion: '1.2.3',
      scope: 'blk_top.u_p',
      model: 'demo',
    })
    expect(rows.map((r) => r.label)).toEqual(['DESIGN', 'SHEET', 'MODEL', 'TOOL'])
    // A scope equal to the top is not a scope.
    const same = titleBlockRows({ top: 'blk_top', scope: 'blk_top' })
    expect(same.map((r) => r.label)).toEqual(['DESIGN', 'TOOL'])
  })

  it('carries nothing volatile', () => {
    // The no-volatile rule the payload lives under extends to the
    // drawing: a date would make two exports of one design differ.
    const text = JSON.stringify(titleBlockRows({ top: 't', toolVersion: '0' }))
    expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}/)
    expect(titleBlockRows({ top: 't', toolVersion: '0' })).toEqual(
      titleBlockRows({ top: 't', toolVersion: '0' }),
    )
  })
})

describe('sheetFrame', () => {
  const rows = titleBlockRows({ top: 'blk_top', toolVersion: '1.0' })
  const { frame, title } = sheetFrame({ width: 400, height: 300 }, rows)

  it('surrounds the laid-out extent with a margin', () => {
    expect(frame.x).toBe(-SHEET_MARGIN)
    expect(frame.y).toBe(-SHEET_MARGIN)
    expect(frame.width).toBe(400 + SHEET_MARGIN * 2)
  })

  it('reserves a band below the drawing for the title block', () => {
    // Not an overlay in the corner: a block ELK happened to place
    // bottom-right would be printed over.
    expect(frame.height).toBeGreaterThan(300 + SHEET_MARGIN * 2)
    expect(title.y).toBeGreaterThanOrEqual(300)
    expect(title.height).toBe(rows.length * TITLE_ROW_HEIGHT + TITLE_BLOCK_PAD * 2)
  })

  it('anchors the title block to the bottom-right corner', () => {
    expect(title.x + title.width).toBeCloseTo(frame.x + frame.width - TITLE_BLOCK_PAD)
    expect(title.y + title.height).toBeCloseTo(frame.y + frame.height - TITLE_BLOCK_PAD)
  })

  it('survives an empty model', () => {
    const empty = sheetFrame({ width: 0, height: 0 }, [])
    expect(empty.frame.width).toBe(SHEET_MARGIN * 2)
    expect(empty.title.rows).toEqual([])
  })
})

// --- algebraic bus widths -----------------------------------------------------

describe('algebraic widths in the draw model', () => {
  const withEdge = (rb) => ({
    id: '$root',
    x: 0,
    y: 0,
    width: 200,
    height: 100,
    children: [
      {
        id: 'top',
        x: 0,
        y: 0,
        width: 200,
        height: 100,
        rb: { module_name: 'top' },
        ports: [],
        edges: [
          {
            id: 'e0',
            sources: ['top.u_a:q'],
            targets: ['top.u_b:d'],
            rb,
            sections: [
              { startPoint: { x: 0, y: 0 }, endPoint: { x: 50, y: 0 } },
            ],
          },
        ],
        children: [],
      },
    ],
  })

  it('carries the expression onto the wire when bits is null', () => {
    const [wire] = toSchematic(withEdge({ nets: ['gray'], bits: null, bits_expr: 'PTR_W' })).wires
    expect(wire.bits).toBeNull()
    expect(wire.bitsExpr).toBe('PTR_W')
    expect(wire.slash).toBe('/PTR_W')
    // Unknown-but-parameterised reads as a bus: nobody parameterises
    // a single bit, and the italic says which kind of answer it is.
    expect(wire.bus).toBe(true)
    expect(wire.slashSymbolic).toBe(true)
  })

  it('draws the name, not the number, when the producer knows both', () => {
    const [wire] = toSchematic(withEdge({ nets: ['pay'], bits: 19, bits_expr: 'WIDTH' })).wires
    expect(wire.bits).toBe(19)
    expect(wire.slash).toBe('/WIDTH')
    expect(wire.slashSymbolic).toBe(true)
  })

  it('leaves a purely numeric wire exactly as it was', () => {
    const [wire] = toSchematic(withEdge({ nets: ['pay'], bits: 19, bits_expr: null })).wires
    expect(wire.slash).toBe('/19')
    expect(wire.slashSymbolic).toBe(false)
    expect(wire.bitsExpr).toBeNull()
  })

  it('keeps a 1-bit wire a hairline even when it has a name', () => {
    const [wire] = toSchematic(withEdge({ nets: ['en'], bits: 1, bits_expr: 'WIDTH' })).wires
    expect(wire.slash).toBeNull()
    expect(wire.bus).toBe(false)
    expect(wire.slashSymbolic).toBe(false)
  })

  it('stays silent for a producer that predates the key', () => {
    const [wire] = toSchematic(withEdge({ nets: ['w'], bits: null })).wires
    expect(wire.slash).toBeNull()
    expect(wire.bus).toBe(false)
    expect(wire.slashSymbolic).toBe(false)
  })
})

describe('the canvas styles the algebraic slash', () => {
  const canvas = readFileSync(
    resolve(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'components', 'SchematicCanvas.vue'),
    'utf8',
  )

  it('binds the symbolic class off the draw model', () => {
    expect(canvas).toContain("{ symbolic: wire.slashSymbolic }")
  })

  it('italicises it, with no colour decision of its own', () => {
    const rule = canvas.match(/\.sch-slash-text\.symbolic\s*\{([^}]*)\}/)
    expect(rule).not.toBeNull()
    expect(rule[1]).toContain('font-style: italic')
    // Colour lives in the token sheet (docs/design-tokens.md).
    expect(rule[1]).not.toMatch(/#[0-9a-f]{3,8}/i)
  })
})
