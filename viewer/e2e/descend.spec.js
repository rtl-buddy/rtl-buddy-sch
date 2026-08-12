// Container selection + scope navigation in the hier view.
//
// Containers render as Graphviz *clusters*, whose backdrop is
// ``fill:none`` — without an explicit ``pointer-events: all`` opt-in
// (GraphCanvas.vue) SVG hit-testing skips unpainted fill and the only
// clickable part of a container is its hairline border. That made the
// NodeDetail Descend/Up buttons look permanently dead and dblclick-
// descend a no-op on real hierarchical designs. These tests pin the
// interior-click contract end to end.
//
// Fixture: two_clock_two_reset_design — three levels (top →
// u_fifo/u_rstgen → sync leaves), no embedded layout, so graphToDot's
// nested-cluster path renders ``top.u_fifo`` as a cluster.

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

// A point inside the cluster's backdrop that is NOT covered by any
// child node: 6px in from the middle of the left edge. Mid-edge, not
// a corner — the backdrop's corners are rounded, so a point inside
// the bbox corner can fall OUTSIDE the path and hit the enclosing
// cluster instead. Cluster padding (Graphviz's default 8pt margin)
// keeps children away from the edge itself.
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

test.describe('cluster-interior selection + descend', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript((data) => {
      window.__RTL_BUDDY_VIEW_DATA__ = data
    }, payload)
    await page.goto('/')
    await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
  })

  test('clicking a cluster interior selects the container node', async ({ page }) => {
    const pt = await interiorPoint(page, CONTAINER)
    expect(pt).not.toBeNull()
    await page.mouse.click(pt.x, pt.y)
    await expect(page.locator('.node-detail h3 .inst-path')).toHaveText(
      new RegExp(CONTAINER.replaceAll('.', '\\.?')),
    )
    // The container has children, so Descend must be enabled.
    await expect(
      page.getByRole('button', { name: 'Descend', exact: true }),
    ).toBeEnabled()
  })

  test('Descend scopes to the subtree and Up restores the full graph', async ({ page }) => {
    const fullCount = await page.locator('g.node[data-node-id]').count()
    const pt = await interiorPoint(page, CONTAINER)
    await page.mouse.click(pt.x, pt.y)
    await page.getByRole('button', { name: 'Descend', exact: true }).click()
    // Scoped render: only the subtree's leaves remain.
    await expect
      .poll(async () =>
        page
          .locator('g.node[data-node-id]')
          .evaluateAll((els) => els.map((e) => e.getAttribute('data-node-id'))),
      )
      .toEqual(['top.u_fifo.u_rd_ptr', 'top.u_fifo.u_wr_ptr'])
    await expect(page.getByRole('button', { name: 'Up', exact: true })).toBeEnabled()
    await page.getByRole('button', { name: 'Up', exact: true }).click()
    await expect
      .poll(async () => page.locator('g.node[data-node-id]').count())
      .toBe(fullCount)
  })

  test('double-clicking a cluster interior descends directly', async ({ page }) => {
    const pt = await interiorPoint(page, CONTAINER)
    await page.mouse.dblclick(pt.x, pt.y)
    await expect
      .poll(async () =>
        page
          .locator('g.node[data-node-id]')
          .evaluateAll((els) => els.map((e) => e.getAttribute('data-node-id'))),
      )
      .toEqual(['top.u_fifo.u_rd_ptr', 'top.u_fifo.u_wr_ptr'])
  })
})
