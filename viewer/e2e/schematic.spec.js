// The elkjs schematic canvas end to end (rtl-buddy-sch#163 P2).
//
// One spec, and it is deliberately about the property the unit tests
// cannot reach: that identity is *born* on the element. The whole
// point of moving off viz.js for this view is that no ``<title>`` is
// scraped and no ``cluster_lookup`` is consulted — so the test asserts
// that a real browser, after a real elkjs layout, produces SVG whose
// ``data-node-id`` values are instance paths, and that clicking one
// drives the same store selection the hier canvas drives.
//
// Fixture: block_diagram_demo — the one in-repo design whose scopes
// carry real sibling dataflow (so the sheet has wires and junction
// dots on it) and whose tree is three levels deep (so a compound
// block renders as a containment frame).

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const fixture = (name) =>
  JSON.parse(
    fs.readFileSync(path.join(__dirname, 'fixtures', `${name}.json`), 'utf-8'),
  )

const payload = fixture('block_diagram_demo')

async function openSchematic(page, data) {
  await page.addInitScript((d) => {
    window.__RTL_BUDDY_VIEW_DATA__ = d
  }, data)
  await page.goto('/')
  await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'Schematic' }).click()
  await expect(page.locator('.sch-block').first()).toBeVisible({ timeout: 30_000 })
}

// The fixture must actually carry the payload this canvas consumes;
// without the guard a producer regression would show up as "the
// empty state renders", which passes for the wrong reason.
test('the fixture carries a layout.elk payload', () => {
  expect(payload.layout.elk).toBeTruthy()
  expect(payload.layout.elk.id).toBe('blk_top')
})

test.describe('schematic canvas', () => {
  test('renders blocks whose data-node-id is the instance path', async ({
    page,
  }) => {
    await openSchematic(page, payload)
    const blocks = page.locator('.sch-block[data-node-id]')
    const ids = await blocks.evaluateAll((els) =>
      els.map((e) => e.getAttribute('data-node-id')),
    )
    expect(ids.length).toBeGreaterThan(0)
    // Every id is a real node in the payload — no synthetic ids, no
    // Graphviz-sanitized names needing a reverse lookup.
    const known = new Set(payload.nodes.map((n) => n.id))
    for (const id of ids) expect(known.has(id)).toBe(true)
    // And the containment frame for the design top is there too.
    await expect(page.locator('.sch-frame.sheet[data-node-id="blk_top"]')).toHaveCount(1)
  })

  test('draws pins, wires and off-page flags', async ({ page }) => {
    await openSchematic(page, payload)
    expect(await page.locator('.sch-pin').count()).toBeGreaterThan(0)
    expect(await page.locator('.sch-wire').count()).toBeGreaterThan(0)
    expect(await page.locator('.sch-flag').count()).toBeGreaterThan(0)
    // Clock pins keep their wedge even though clock nets aren't routed.
    expect(await page.locator('.sch-pin.clock').count()).toBeGreaterThan(0)
    expect(await page.locator('.sch-clock-wedge').count()).toBeGreaterThan(0)
    // A block with children is a containment frame, not a filled box —
    // static nesting now, expand/collapse in P3.
    expect(await page.locator('.sch-frame:not(.sheet)').count()).toBeGreaterThan(0)
  })

  test('explains itself when the producer predates layout.elk', async ({
    page,
  }) => {
    const { layout: _drop, ...older } = payload
    await page.addInitScript((d) => {
      window.__RTL_BUDDY_VIEW_DATA__ = d
    }, older)
    await page.goto('/')
    await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
    await page.getByRole('button', { name: 'Schematic' }).click()
    // Not a blank canvas: name the missing key and the command that
    // produces it, the way the other no-data states do.
    const empty = page.locator('.sch-empty')
    await expect(empty).toBeVisible()
    await expect(empty).toContainText('layout.elk')
    await expect(empty).toContainText('rtl-buddy-view')
  })

  test('clicking a block selects it, matching the hier canvas', async ({
    page,
  }) => {
    await openSchematic(page, payload)
    const block = page.locator('.sch-block[data-node-id]').first()
    const id = await block.getAttribute('data-node-id')
    await block.locator('rect').click({ force: true })
    await expect(
      page.locator(`.sch-block[data-node-id="${id}"][data-rb-selected="true"]`),
    ).toHaveCount(1)
    // The sidebar's Node detail panel is driven by the same store
    // selection the hier canvas writes — parity, not a parallel path.
    await expect(page.locator('.sidebar')).toContainText(id)
  })
})
