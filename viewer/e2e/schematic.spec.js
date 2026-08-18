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

// --- P3: expand / collapse ----------------------------------------------------
//
// The compound in this fixture is ``blk_top.u_prod`` (child:
// ``u_stage``), the same shape ``u_afifo`` has in the template's demo
// design — a block whose insides are synchronisers nobody wants on the
// sheet while reading the dataflow.

const COMPOUND = 'blk_top.u_prod'
const INNER = 'blk_top.u_prod.u_stage'

test.describe('collapse / expand', () => {
  test('double-clicking a compound folds its children away and back', async ({
    page,
  }) => {
    await openSchematic(page, payload)
    // Expanded: a containment frame with the child drawn inside it.
    await expect(page.locator(`.sch-frame[data-node-id="${COMPOUND}"]`)).toHaveCount(1)
    await expect(page.locator(`[data-node-id="${INNER}"]`).first()).toBeVisible()

    await page
      .locator(`.sch-frame[data-node-id="${COMPOUND}"] rect`)
      .dblclick({ position: { x: 5, y: 5 } })

    // Folded: one leaf-shaped block, no frame, and nothing left of the
    // subtree it swallowed — pins included.
    await expect(
      page.locator(`.sch-block[data-node-id="${COMPOUND}"][data-collapsed="true"]`),
    ).toHaveCount(1)
    await expect(page.locator(`.sch-frame[data-node-id="${COMPOUND}"]`)).toHaveCount(0)
    await expect(page.locator(`[data-node-id="${INNER}"]`)).toHaveCount(0)

    // The boundary is intact: the edges that crossed the border still
    // terminate on the folded box's own pins.
    await expect(
      page.locator(`.sch-wire[data-edge-target="${COMPOUND}:cmd"]`).first(),
    ).toBeVisible()
    await expect(page.locator(`.sch-pin[data-port-id="${COMPOUND}:cmd"]`)).toHaveCount(1)

    // And it opens again.
    await page
      .locator(`.sch-block[data-node-id="${COMPOUND}"] rect`)
      .dblclick({ force: true })
    await expect(page.locator(`.sch-frame[data-node-id="${COMPOUND}"]`)).toHaveCount(1)
    await expect(page.locator(`[data-node-id="${INNER}"]`).first()).toBeVisible()
  })

  test('the refdes affordance toggles the same state', async ({ page }) => {
    await openSchematic(page, payload)
    const toggle = page.locator(`[data-collapse-toggle="${COMPOUND}"]`)
    await expect(toggle).toHaveText('▾')
    await toggle.click({ force: true })
    await expect(
      page.locator(`.sch-block[data-node-id="${COMPOUND}"][data-collapsed="true"]`),
    ).toHaveCount(1)
    // The affordance survives the fold — it is what unfolds it.
    await expect(page.locator(`[data-collapse-toggle="${COMPOUND}"]`)).toHaveText('▸')
    // A leaf never offers one.
    await expect(
      page.locator('[data-collapse-toggle="blk_top.u_cons"]'),
    ).toHaveCount(0)
  })

  test('the whole layout re-runs per toggle, fast enough for the main thread', async ({
    page,
  }) => {
    await openSchematic(page, payload)
    const svg = page.locator('svg.sch-svg')
    const before = Number(await svg.getAttribute('data-layout-ms'))
    await page.locator(`[data-collapse-toggle="${COMPOUND}"]`).click({ force: true })
    await expect(
      page.locator(`.sch-block[data-node-id="${COMPOUND}"][data-collapsed="true"]`),
    ).toHaveCount(1)
    const after = Number(await svg.getAttribute('data-layout-ms'))
    // Not a performance gate (CI machines vary wildly) — a sanity
    // bound on the decision recorded in the component: elkjs on the
    // main thread, no worker, because a re-layout of this design is
    // well inside a frame budget's worth of jank.
    expect(Number.isFinite(before)).toBe(true)
    expect(after).toBeGreaterThan(0)
    expect(after).toBeLessThan(2000)
  })
})

// --- P3: net hover highlighting ----------------------------------------------

test.describe('hover highlighting', () => {
  test('hovering a wire lights the net and its endpoint pins', async ({ page }) => {
    await openSchematic(page, payload)
    expect(await page.locator('.sch-wire.hot').count()).toBe(0)
    await page.locator('.sch-wire .sch-hit').first().hover({ force: true })
    expect(await page.locator('.sch-wire.hot').count()).toBeGreaterThan(0)
    // The pins at both ends light with it — a net you can trace to its
    // terminals, not a highlighted squiggle.
    expect(await page.locator('.sch-pin.hot, .sch-flag.hot').count()).toBeGreaterThan(0)
    // Leaving clears it.
    await page.mouse.move(2, 2)
    await expect(page.locator('.sch-wire.hot')).toHaveCount(0)
  })

  test('hovering a pin lights every wire touching it', async ({ page }) => {
    await openSchematic(page, payload)
    // A pin that is a wire endpoint: u_prod's cmd input.
    const pin = page.locator(`.sch-pin[data-port-id="${COMPOUND}:cmd"]`)
    await pin.hover({ force: true })
    await expect(pin).toHaveClass(/hot/)
    expect(await page.locator('.sch-wire.hot').count()).toBeGreaterThan(0)
  })
})

// --- P4: clock-domain shading + sheet frame ----------------------------------

/** The fixture with a two-domain clock overlay grafted on. */
function withClockOverlay(base) {
  const data = JSON.parse(JSON.stringify(base))
  const clockOf = (id) => (id.includes('u_cons') ? 'clk_b' : 'clk_a')
  data.overlays_present = ['clock']
  for (const node of data.nodes) {
    node.overlays = { ...(node.overlays || {}), clock: { clock: clockOf(node.id) } }
  }
  // The schematic reads the clock off the ELK payload's ``rb.clock``
  // (the exporter's own copy of the same overlay), and the palette off
  // ``nodes[].overlays`` — both have to say the same thing.
  const paint = (n) => {
    n.rb = { ...(n.rb || {}), clock: clockOf(n.id) }
    for (const c of n.children || []) paint(c)
  }
  paint(data.layout.elk)
  for (const edge of data.edges) {
    if (edge.to !== 'blk_top.u_cons') continue
    edge.overlays = {
      ...(edge.overlays || {}),
      clock: {
        crossing: true,
        pairs: [{ src_clock: 'clk_a', dst_clock: 'clk_b', flops: 2 }],
      },
    }
  }
  return data
}

test.describe('clock-domain shading', () => {
  test('tints block plates per domain and dashes the crossings', async ({ page }) => {
    await openSchematic(page, withClockOverlay(payload))
    const tinted = page.locator('.sch-block[data-clock]')
    expect(await tinted.count()).toBeGreaterThan(0)
    // Two domains, two different pastels — from the shared palette
    // module, so the colour agrees with the hier tab's legend.
    const fills = await tinted.evaluateAll((els) =>
      els.map((e) => e.querySelector('rect').style.fill),
    )
    expect(new Set(fills.filter(Boolean)).size).toBeGreaterThan(1)
    // ⚠CDC edges are dashed.
    expect(await page.locator('.sch-wire.cdc').count()).toBeGreaterThan(0)
    const dash = await page
      .locator('.sch-wire.cdc path:not(.sch-hit)')
      .first()
      .evaluate((el) => getComputedStyle(el).strokeDasharray)
    expect(dash).not.toBe('none')
  })

  test('degrades to the plain look with no overlay', async ({ page }) => {
    await openSchematic(page, payload)
    await expect(page.locator('.sch-block[data-clock]')).toHaveCount(0)
    await expect(page.locator('.sch-wire.cdc')).toHaveCount(0)
  })
})

test.describe('sheet frame + title block', () => {
  test('frames the sheet and names the design and tool, with no date', async ({
    page,
  }) => {
    await openSchematic(page, payload)
    await expect(page.locator('.sch-sheet-frame rect.sch-title-block')).toHaveCount(1)
    const rows = page.locator('.sch-title-row')
    await expect(rows.first()).toContainText('blk_top')
    await expect(page.locator('.sch-sheet-frame')).toContainText('rtl-buddy-sch')
    // No timestamp anywhere in the block: an export has to be
    // reproducible, and a date is the one thing that would make two
    // runs over the same design differ.
    const text = await page
      .locator('.sch-sheet-frame')
      .evaluate((el) => el.textContent)
    expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}/)
  })
})

// --- P5: static export --------------------------------------------------------

test.describe('export', () => {
  test('downloads a self-contained SVG of the sheet', async ({ page }) => {
    await openSchematic(page, payload)
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'SVG', exact: true }).click(),
    ])
    expect(download.suggestedFilename()).toBe('blk_top-schematic.svg')
    const file = await download.path()
    const text = fs.readFileSync(file, 'utf-8')
    expect(text.length).toBeGreaterThan(1000)
    expect(text).toContain('<svg')
    expect(text).toContain('xmlns="http://www.w3.org/2000/svg"')
    // Self-contained: document CSS does not travel with the file, so a
    // surviving var() is a stroke that renders as nothing.
    expect(text).not.toContain('var(--')
    // The sheet frame + title block are in the file, not painted by
    // the app around it.
    expect(text).toContain('sch-title-block')
    expect(text).toContain('rtl-buddy-sch')
    // And the identity the canvas was built around survives the trip.
    expect(text).toContain('data-node-id="blk_top.u_prod"')
  })

  test('downloads a 2x PNG', async ({ page }) => {
    await openSchematic(page, payload)
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'PNG', exact: true }).click(),
    ])
    expect(download.suggestedFilename()).toBe('blk_top-schematic.png')
    const bytes = fs.readFileSync(await download.path())
    expect(bytes.length).toBeGreaterThan(1000)
    // PNG magic — proof the canvas rasterised rather than handing back
    // an error page.
    expect(bytes.subarray(0, 4)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47]))
  })

  test('exports what is on the sheet, collapse included', async ({ page }) => {
    await openSchematic(page, payload)
    await page.locator(`[data-collapse-toggle="${COMPOUND}"]`).click({ force: true })
    await expect(
      page.locator(`.sch-block[data-node-id="${COMPOUND}"][data-collapsed="true"]`),
    ).toHaveCount(1)
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'SVG', exact: true }).click(),
    ])
    const text = fs.readFileSync(await download.path(), 'utf-8')
    expect(text).toContain(`data-node-id="${COMPOUND}"`)
    expect(text).not.toContain(`data-node-id="${INNER}"`)
  })
})
