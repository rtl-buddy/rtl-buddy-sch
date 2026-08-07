// Canvas legibility + orientation contract for the hier view.
//
// Everything here is about the *descended* canvas, because that is
// where the SPA renders its own DOT: ``store.displayGraph`` drops the
// producer's embedded layout when ``rootInstancePath`` is set, so a
// scoped view always goes through ``graphToDot``. The four things
// pinned below were all broken there and nowhere else:
//
//   - typeface (graphToDot set no ``fontname`` → Graphviz's Times)
//   - fit scale (uncapped aspect-fit → a two-node scope as a billboard)
//   - edge contrast (edges on the hairline ``--fg-faint`` tier)
//   - "where am I" (Block Flow had a scope pill; Hierarchy had nothing)
//
// Fixture: two_clock_two_reset_design — three levels (top →
// u_fifo/u_rstgen → sync leaves), no embedded layout.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const payload = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, 'fixtures', 'two_clock_two_reset_design.json'),
    'utf-8',
  ),
)

const CONTAINER = 'top.u_fifo'

// Mid-left of the cluster backdrop, 6px in — the same interior point
// descend.spec.js uses. Corners are rounded, so a bbox corner can fall
// outside the path and hit the enclosing cluster instead.
async function interiorPoint(page, nodeId) {
  return page.evaluate((id) => {
    const g = Array.from(document.querySelectorAll('g.cluster')).find(
      (c) => c.getAttribute('data-node-id') === id,
    )
    if (!g) return null
    const backdrop = g.querySelector('path, polygon')
    const r = backdrop.getBoundingClientRect()
    return { x: r.x + 6, y: r.y + r.height / 2 }
  }, nodeId)
}

// The canvas's zoom lives on the root ``<g>``'s transform, written by
// ``applyTransform`` as ``translate(x,y) scale(s)``.
async function canvasScale(page) {
  return page.evaluate(() => {
    const g = document.querySelector('.svg-host svg > g')
    if (!g) return null
    const m = /scale\(([-\d.]+)\)/.exec(g.getAttribute('transform') || '')
    return m ? Number(m[1]) : null
  })
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((data) => {
    window.__RTL_BUDDY_VIEW_DATA__ = data
  }, payload)
  await page.goto('/')
  await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
})

test.describe('hier-view scope breadcrumbs', () => {
  const crumbs = (page) => page.locator('[data-rb-scope-breadcrumb] .crumb')

  test('absent at the design top, appears with the path after Descend', async ({
    page,
  }) => {
    // Nothing to orient by at the top — the canvas is the whole design
    // and a permanent one-segment strip would just be chrome.
    await expect(page.locator('[data-rb-scope-breadcrumb]')).toHaveCount(0)

    const pt = await interiorPoint(page, CONTAINER)
    expect(pt).not.toBeNull()
    await page.mouse.dblclick(pt.x, pt.y)

    await expect(crumbs(page)).toHaveText(['top', 'u_fifo'])
    // The trailing segment is where you are, so it isn't a link.
    await expect(crumbs(page).nth(1)).toBeDisabled()
    await expect(crumbs(page).nth(0)).toBeEnabled()
  })

  test('clicking the root crumb restores the full scope', async ({ page }) => {
    const fullCount = await page.locator('g.node[data-node-id]').count()
    const pt = await interiorPoint(page, CONTAINER)
    await page.mouse.dblclick(pt.x, pt.y)
    await expect
      .poll(async () =>
        page
          .locator('g.node[data-node-id]')
          .evaluateAll((els) => els.map((e) => e.getAttribute('data-node-id'))),
      )
      .toEqual(['top.u_fifo.u_rd_ptr', 'top.u_fifo.u_wr_ptr'])

    await crumbs(page).nth(0).click()

    await expect
      .poll(async () => page.locator('g.node[data-node-id]').count())
      .toBe(fullCount)
    // Back at the top → the strip retires with the scope.
    await expect(page.locator('[data-rb-scope-breadcrumb]')).toHaveCount(0)
  })

  test('the Block Flow tab shows the same strip', async ({ page }) => {
    // One computed feeds both tabs; flow always has a scope (it falls
    // back to graph.top) so its strip is visible from the start.
    await page.getByRole('button', { name: 'Block Flow', exact: true }).click()
    await expect(crumbs(page)).toHaveText(['top'])
  })
})

test.describe('descended-canvas legibility', () => {
  test('renders in the mono face, not Graphviz default serif', async ({ page }) => {
    const pt = await interiorPoint(page, CONTAINER)
    await page.mouse.dblclick(pt.x, pt.y)
    await expect(page.locator('g.node').first()).toBeVisible()
    const families = await page
      .locator('.svg-host svg text')
      .evaluateAll((els) =>
        els.map((e) => e.getAttribute('font-family') || getComputedStyle(e).fontFamily),
      )
    expect(families.length).toBeGreaterThan(0)
    for (const f of families) {
      expect(f.toLowerCase()).toContain('courier')
      expect(f.toLowerCase()).not.toContain('times')
    }
  })

  test('fit-to-window does not blow a small scope up past 1.5x', async ({ page }) => {
    const pt = await interiorPoint(page, CONTAINER)
    await page.mouse.dblclick(pt.x, pt.y)
    // Two leaf nodes in a 1280x800 canvas: uncapped aspect-fit put this
    // at several times natural size.
    await expect.poll(async () => canvasScale(page)).not.toBeNull()
    const scale = await canvasScale(page)
    expect(scale).toBeLessThanOrEqual(1.5 + 1e-6)
    expect(scale).toBeGreaterThan(0)

    // Manual zoom stays unbounded by the fit cap.
    const zoom = page.locator('.graph-toolbar button', { hasText: '+' })
    await zoom.click()
    await zoom.click()
    await zoom.click()
    expect(await canvasScale(page)).toBeGreaterThan(1.5)
  })

  test('label text stays inside its node box', async ({ page }) => {
    const pt = await interiorPoint(page, CONTAINER)
    await page.mouse.dblclick(pt.x, pt.y)
    await expect(page.locator('g.node').first()).toBeVisible()
    // The node margin is what buys this room; without it a long
    // instance path overhangs the rounded box on both sides.
    const overflows = await page.locator('g.node').evaluateAll((groups) =>
      groups
        .map((g) => {
          const box = g.querySelector('polygon, path')
          const texts = Array.from(g.querySelectorAll('text'))
          if (!box || texts.length === 0) return null
          const b = box.getBBox()
          const worst = Math.max(
            ...texts.map((t) => {
              const r = t.getBBox()
              return Math.max(b.x - r.x, r.x + r.width - (b.x + b.width))
            }),
          )
          return worst > 0 ? { id: g.getAttribute('data-node-id'), worst } : null
        })
        .filter(Boolean),
    )
    expect(overflows).toEqual([])
  })

  test('selection paints an accent stroke plus a halo', async ({ page }) => {
    const pt = await interiorPoint(page, CONTAINER)
    await page.mouse.dblclick(pt.x, pt.y)
    const leaf = page.locator('g.node[data-node-id="top.u_fifo.u_wr_ptr"]')
    await leaf.click()
    await expect(leaf).toHaveAttribute('data-rb-selected', 'true')
    // ``style="rounded,filled"`` makes Graphviz emit a <path>, not the
    // <polygon> a square box would give — the rule covers both.
    const style = await leaf.locator('polygon, path').first().evaluate((el) => {
      const cs = getComputedStyle(el)
      return { stroke: cs.stroke, width: cs.strokeWidth, filter: cs.filter }
    })
    expect(parseFloat(style.width)).toBeGreaterThanOrEqual(2.5)
    // Two drop-shadow stops — the halo, not just a fatter border.
    expect(style.filter).toContain('drop-shadow')
    expect(style.filter.match(/drop-shadow/g).length).toBe(2)
  })
})
