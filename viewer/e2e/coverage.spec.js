// Live coverage tinting, end to end.
//
// The join under test crosses three boundaries that unit tests can
// only meet one at a time: the hub's ``/cov.json`` (stubbed here with
// ``page.route``), the gate global the hub injects, and the SVG
// Graphviz actually produced. So this suite asserts on real rendered
// fills rather than on a fake DOM.
//
// Fixture: counter_with_subs — ``counter`` (a cluster) over the
// leaves ``counter.u_ff`` (module ``counter_ff``) and ``counter.u_x``
// (module ``sub_x``), and no overlays at all in the payload. The
// empty ``overlays_present`` is the point: live coverage must show up
// on a view whose producer emitted nothing.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PAYLOAD = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'counter_with_subs.json'), 'utf-8'),
)

function metrics(found, hit) {
  const ratio = found > 0 ? hit / found : null
  return { found, hit, ratio }
}

// ``counter_ff__W8`` exercises the elaborated→source strip rule:
// view.json's node.module is ``counter_ff``, the payload's is not.
const COV_JSON = {
  generated_at: '2026-08-07T09:15:00Z',
  totals: { line: metrics(20, 12) },
  tests: [{ name: 'smoke' }],
  modules: ['counter_ff__W8', 'sub_x'],
  files: [
    {
      path: 'rtl/counter_ff.sv',
      modules: ['counter_ff__W8'],
      totals: {
        line: metrics(10, 2),
        branch: metrics(4, 1),
        toggle: metrics(0, 0),
        expression: metrics(2, 2),
        cover: metrics(0, 0),
      },
      line: [],
    },
    {
      path: 'rtl/sub_x.sv',
      modules: ['sub_x'],
      totals: {
        line: metrics(10, 10),
        branch: metrics(2, 2),
        toggle: metrics(0, 0),
        expression: metrics(0, 0),
        cover: metrics(0, 0),
      },
      line: [],
    },
  ],
}

// The ramp: hue = pct * 1.2 at the light theme's --cov-l. The overlay
// writes these strings verbatim, but reading ``style.fill`` back gets
// CSSOM's serialization (``rgb(241, 203, 177)``), so expectations are
// round-tripped through the same serializer rather than hand-computed.
const RED_20 = 'hsl(24, 70%, 82%)'
const GREEN_100 = 'hsl(120, 70%, 82%)'

function serializeColor(page, css) {
  return page.evaluate((c) => {
    const el = document.createElement('span')
    el.style.color = c
    return el.style.color
  }, css)
}

/**
 * Boot the SPA with the fixture inlined.
 *
 * ``hub`` decides whether we pretend a hub with coverage is serving
 * us: it installs the two injected globals AND the ``/cov.json``
 * route. Without it neither exists, which is the standalone /
 * embed.py / dev-server case.
 */
async function boot(page, { hub = true } = {}) {
  const covRequests = []
  await page.route('**/cov.json', async (route) => {
    covRequests.push(route.request().url())
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(COV_JSON),
    })
  })
  await page.addInitScript(
    ({ data, hub }) => {
      window.__RTL_BUDDY_VIEW_DATA__ = data
      if (hub) {
        window.__RTL_BUDDY_HUB__ = 'ws://localhost:5173/ws'
        window.__RTL_BUDDY_COV_URL__ = '/cov'
      }
    },
    { data: PAYLOAD, hub },
  )
  await page.goto('/')
  await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
  return { covRequests }
}

/** The inline fill of a leaf box, as the overlay wrote it. */
function leafFill(page, nodeId) {
  return page.evaluate((id) => {
    const g = document.querySelector(`g.node[data-node-id="${CSS.escape(id)}"]`)
    if (!g) return null
    const shape = g.querySelector('polygon, path')
    return shape ? shape.style.fill : null
  }, nodeId)
}

test.describe('live coverage tinting', () => {
  test('tints leaf boxes from the hub payload, joined through the __W8 suffix', async ({
    page,
  }) => {
    const { covRequests } = await boot(page)

    // The fetch happens once, against the constant route — the
    // injected global is a gate, not a URL.
    await expect.poll(() => covRequests.length, { timeout: 5_000 }).toBe(1)
    expect(covRequests[0]).toContain('/cov.json')

    const red20 = await serializeColor(page, RED_20)
    const green100 = await serializeColor(page, GREEN_100)
    await expect
      .poll(() => leafFill(page, 'counter.u_ff'), { timeout: 5_000 })
      .toBe(red20)
    expect(await leafFill(page, 'counter.u_x')).toBe(green100)
    // The two leaves must be visibly different — a ramp that collapses
    // to one colour is the failure mode this whole feature exists to
    // avoid.
    expect(await leafFill(page, 'counter.u_ff')).not.toBe(
      await leafFill(page, 'counter.u_x'),
    )
  })

  test('offers a live overlay entry saying which run the numbers are from', async ({
    page,
  }) => {
    await boot(page)
    const toggle = page.locator('[data-testid="cov-live-toggle"]')
    await expect(toggle).toBeVisible({ timeout: 5_000 })
    // Default ON when data is present.
    await expect(toggle).toBeChecked()
    await expect(page.locator('.overlay-sub')).toHaveText(
      "from the hub's latest run · 2026-08-07",
    )
  })

  test('unticking the overlay clears the tint; re-ticking restores it', async ({ page }) => {
    await boot(page)
    const toggle = page.locator('[data-testid="cov-live-toggle"]')
    await expect(toggle).toBeVisible({ timeout: 5_000 })
    const red20 = await serializeColor(page, RED_20)
    await expect.poll(() => leafFill(page, 'counter.u_ff')).toBe(red20)

    await toggle.uncheck()
    // Cleared back to the Graphviz attribute floor (inline style
    // emptied), NOT painted some other colour.
    await expect.poll(() => leafFill(page, 'counter.u_ff')).toBe('')

    await toggle.check()
    await expect.poll(() => leafFill(page, 'counter.u_ff')).toBe(red20)
  })

  test('a selected node shows its metrics and a link into the coverage pane', async ({
    page,
  }) => {
    await boot(page)
    await page.locator('g.node[data-node-id="counter.u_ff"]').click()
    const section = page.locator('[data-testid="node-live-coverage"]')
    await expect(section).toBeVisible({ timeout: 5_000 })
    // Metrics with no data (toggle, cover) are skipped rather than
    // printed as 0%.
    await expect(section.locator('.live-cov-metrics')).toHaveText(
      'L 20.0% · B 25.0% · E 100.0%',
    )
    const link = section.locator('a.live-cov-link')
    await expect(link).toHaveText('open in coverage ↗')
    await expect(link).toHaveAttribute('href', '/cov')
    await expect(link).toHaveAttribute('target', '_blank')
    await expect(link).toHaveAttribute('rel', 'noopener')
  })

  test('without the hub globals nothing is fetched and no fill is touched', async ({
    page,
  }) => {
    // The standalone case: embed.py output, the Vite dev server, a
    // file:// open. The feature must be invisible, not broken.
    const { covRequests } = await boot(page, { hub: false })
    await expect(page.locator('g.node').first()).toBeVisible()

    expect(await leafFill(page, 'counter.u_ff')).toBe('')
    expect(await leafFill(page, 'counter.u_x')).toBe('')
    await expect(page.locator('[data-testid="cov-live-toggle"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="node-live-coverage"]')).toHaveCount(0)
    expect(covRequests).toEqual([])
  })
})
