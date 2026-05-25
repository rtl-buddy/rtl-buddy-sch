// Unit tests for the view.json v1 client-side validator.
//
// We test the validator's *errors* exhaustively because the rest
// of the viewer assumes any payload it sees has passed this gate;
// a structural bug here turns into a hard-to-debug Vue crash
// downstream. The happy-path coverage is one minimal-payload
// round-trip; the producer-side schema test in tests/test_render_json.py
// is the authoritative shape pin.

import { describe, expect, it } from 'vitest'
import { ViewJsonError, parseViewJson } from '../src/parse.js'

const minimalPayload = () => ({
  schema_version: '1.0',
  top: 'top',
  nodes: [
    { id: 'top', module: 'top', is_blackbox: false, parameters: {}, ports: [], overlays: {} },
  ],
  edges: [],
  overlays_present: [],
})

describe('parseViewJson', () => {
  it('returns the original payload on a minimal valid input', () => {
    const p = minimalPayload()
    expect(parseViewJson(p)).toBe(p)
  })

  it('accepts a 1.x minor version bump', () => {
    const p = minimalPayload()
    p.schema_version = '1.5'
    expect(() => parseViewJson(p)).not.toThrow()
  })

  it('rejects null / array top-level', () => {
    expect(() => parseViewJson(null)).toThrow(ViewJsonError)
    expect(() => parseViewJson([])).toThrow(/JSON object/)
  })

  it('rejects missing schema_version', () => {
    const p = minimalPayload()
    delete p.schema_version
    expect(() => parseViewJson(p)).toThrow(/schema_version/)
  })

  it('rejects an unsupported major version', () => {
    const p = minimalPayload()
    p.schema_version = '2.0'
    expect(() => parseViewJson(p)).toThrow(/not supported/)
  })

  it('rejects a non-numeric major', () => {
    const p = minimalPayload()
    p.schema_version = 'alpha.0'
    expect(() => parseViewJson(p)).toThrow(/numeric major/)
  })

  it('rejects missing top', () => {
    const p = minimalPayload()
    delete p.top
    expect(() => parseViewJson(p)).toThrow(/top must be a string/)
  })

  it('rejects non-array nodes', () => {
    const p = minimalPayload()
    p.nodes = {}
    expect(() => parseViewJson(p)).toThrow(/nodes must be an array/)
  })

  it('rejects nodes without a string id', () => {
    const p = minimalPayload()
    p.nodes = [{ id: 42 }]
    expect(() => parseViewJson(p)).toThrow(/nodes\[0\].id/)
  })

  it('rejects edges without from/to', () => {
    const p = minimalPayload()
    p.edges = [{ from: 'top' }]
    expect(() => parseViewJson(p)).toThrow(/edges\[0\].from\/.to/)
  })

  it('rejects non-array overlays_present', () => {
    const p = minimalPayload()
    p.overlays_present = 'clock,reset'
    expect(() => parseViewJson(p)).toThrow(/overlays_present must be an array/)
  })

  // --- v1.1 (issue #99) — dut_top / tb_top -----------------------------

  it('tolerates omitted dut_top / tb_top (v1.0 payload shape)', () => {
    const p = minimalPayload()
    // Neither field set; round-trip succeeds.
    expect(() => parseViewJson(p)).not.toThrow()
  })

  it('round-trips dut_top + tb_top when supplied as strings', () => {
    const p = minimalPayload()
    p.schema_version = '1.1'
    p.dut_top = 'dut'
    p.tb_top = 'tb_top'
    expect(parseViewJson(p)).toBe(p)
  })

  it('accepts dut_top / tb_top set to null', () => {
    const p = minimalPayload()
    p.dut_top = null
    p.tb_top = null
    expect(() => parseViewJson(p)).not.toThrow()
  })

  it('rejects dut_top / tb_top of the wrong type', () => {
    const p1 = minimalPayload()
    p1.dut_top = 42
    expect(() => parseViewJson(p1)).toThrow(/dut_top must be a string or null/)
    const p2 = minimalPayload()
    p2.tb_top = []
    expect(() => parseViewJson(p2)).toThrow(/tb_top must be a string or null/)
  })
})
