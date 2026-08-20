// The producer-DOT theme safety net must not flatten hazard accents (#153).
//
// ``GraphCanvas`` re-tints the producer's embedded DOT because that
// string is generated with the light palette baked in and this repo
// cannot rebuild it — on a dark ground its edges would be
// unreadable. The re-tint is a stylesheet rule and Graphviz writes
// its colours as *presentation attributes*, which a stylesheet
// outranks, so an un-scoped rule repaints every edge including the
// ones whose colour means something: CDC red and RDC orange.
//
// Both directions matter and the test pins both, because the obvious
// narrow fix ("only tint edges Graphviz left at its default black")
// would quietly undo the legibility the rule exists for — producer
// edges are *not* un-coloured, they carry baked neutrals.
//
// The fixture's own DOT carries no hazard colour: since #57 the
// embedded layout block is structure-only and the SPA owns overlay
// paint. So the hazard edge here is injected, standing in for a
// pre-#57 payload — which is exactly the case the safety net has to
// keep honest.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const CDC_RED = 'rgb(220, 38, 38)' // #dc2626, as getComputedStyle reports it
const BAKED_NEUTRAL = '#cbd5e1' // what render/dot.py bakes on an untyped edge

function payloadWithOneHazardEdge() {
  const raw = JSON.parse(
    fs.readFileSync(path.join(__dirname, 'fixtures', 'block_diagram_demo.json'), 'utf-8'),
  )
  const dot = raw.layout.dot
  expect(dot).toContain(BAKED_NEUTRAL)
  // Recolour exactly one edge, leaving its sibling neutral so a
  // single render exercises both branches of the rule.
  raw.layout.dot = dot.replace(`color="${BAKED_NEUTRAL}"`, 'color="#dc2626"')
  expect(raw.layout.dot).toContain('#dc2626')
  expect(raw.layout.dot).toContain(BAKED_NEUTRAL) // the other edge survives
  return raw
}

async function openHier(page, data) {
  await page.addInitScript((d) => {
    window.__RTL_BUDDY_VIEW_DATA__ = d
  }, data)
  await page.goto('/')
  await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
  // The re-tint is gated on this class; without it the test would
  // pass for the wrong reason.
  await expect(page.locator('.svg-host.producer-dot')).toHaveCount(1)
}

test('a producer-baked CDC accent survives the theme re-tint', async ({ page }) => {
  await openHier(page, payloadWithOneHazardEdge())
  const strokes = await page
    .locator('.svg-host.producer-dot g.edge path')
    .evaluateAll((els) => els.map((e) => getComputedStyle(e).stroke))
  expect(strokes).toContain(CDC_RED)
})

test('a neutral producer edge is still re-tinted to the theme tier', async ({ page }) => {
  await openHier(page, payloadWithOneHazardEdge())
  const seen = await page
    .locator('.svg-host.producer-dot g.edge path')
    .evaluateAll((els) => {
      const muted = getComputedStyle(document.documentElement)
        .getPropertyValue('--fg-muted')
        .trim()
      return {
        strokes: els.map((e) => getComputedStyle(e).stroke),
        muted,
      }
    })
  // The light-palette neutral must NOT reach the screen — that is
  // the unreadable-on-dark case the rule exists for.
  expect(seen.strokes).not.toContain('rgb(203, 213, 225)')
  expect(seen.strokes.length).toBeGreaterThan(1)
  // …and at least one edge carries the theme tier instead.
  expect(seen.strokes.some((s) => s !== CDC_RED)).toBe(true)
})

test('the arrowhead follows its edge, accent and neutral alike', async ({ page }) => {
  await openHier(page, payloadWithOneHazardEdge())
  const fills = await page
    .locator('.svg-host.producer-dot g.edge polygon')
    .evaluateAll((els) => els.map((e) => getComputedStyle(e).fill))
  expect(fills).toContain(CDC_RED)
  expect(fills).not.toContain('rgb(203, 213, 225)')
})
