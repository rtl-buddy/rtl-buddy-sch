// The origin vocabulary fence: schema ↔ SPA.
//
// `properties.origin.enum` in `schemas/hub-protocol-v1.json` owns the
// wire vocabulary. This repo's Python suite
// (`tests/test_hub_protocol_schema.py`) pins the enum and sweeps the
// eight places the schema repeats it; nothing until now tied the SPA's
// copies to it, so an origin could land on the wire and simply never
// get a row in the peers strip.
//
// The schema is READ here, not hand-copied — a second literal list in
// a test is a third thing to keep in lockstep, which is the bug this
// file exists to prevent. See `docs/hub-protocol.md` §13 for the full
// cross-repo checklist.

import { describe, expect, it } from 'vitest'

// The schema file itself, out of the repo root — Vite resolves the JSON
// at transform time, so the test breaks loudly if the file moves rather
// than silently reading a stale vendored copy.
import schema from '../../schemas/hub-protocol-v1.json'
import { ORIGIN_DISPLAY, displayOrigin } from '../src/displayNames.js'
import { NON_APP_ORIGINS, PEER_ROLES } from '../src/peerRoles.js'

const ORIGINS = schema.properties?.origin?.enum

const sorted = (xs) => [...xs].sort()

describe('origin vocabulary is the schema’s, not a second copy', () => {
  // Guard the guard: if a schema refactor moved or emptied the enum,
  // every assertion below would pass vacuously against `undefined`.
  it('finds a non-empty enum at properties.origin.enum', () => {
    expect(Array.isArray(ORIGINS)).toBe(true)
    expect(ORIGINS.length).toBeGreaterThan(0)
    for (const origin of ORIGINS) expect(typeof origin).toBe('string')
    expect(sorted(new Set(ORIGINS))).toEqual(sorted(ORIGINS))
  })

  it('partitions the enum into peer rows and the explicit non-apps', () => {
    const rowOrigins = PEER_ROLES.map((r) => r.origin)
    // No origin may be claimed twice — a row AND a declared non-app
    // would make the partition below pass while the strip renders a
    // roster nobody meant.
    expect(sorted(new Set([...rowOrigins, ...NON_APP_ORIGINS]))).toEqual(
      sorted([...rowOrigins, ...NON_APP_ORIGINS]),
    )
    // The fence. Add an origin to the schema and forget HubStatus and
    // this fails with the missing name; invent one in the SPA that the
    // wire has never heard of and it fails with that name instead.
    expect(sorted([...rowOrigins, ...NON_APP_ORIGINS])).toEqual(sorted(ORIGINS))
  })

  it('gives every peer row a label and a display name', () => {
    for (const role of PEER_ROLES) {
      expect(role.label, `role ${role.origin} has no label`).toBeTruthy()
      expect(displayOrigin(role.origin)).toBeTruthy()
    }
  })

  it('renames only origins the wire actually has', () => {
    // ORIGIN_DISPLAY is deliberately a table of DIFFERENCES (everything
    // else passes through), so it is a subset — but a key that is not a
    // real origin is dead code that will never fire.
    for (const origin of Object.keys(ORIGIN_DISPLAY)) {
      expect(ORIGINS, `ORIGIN_DISPLAY renames unknown origin ${origin}`).toContain(origin)
    }
  })
})
