// The instance-hierarchy panel, end to end.
//
// The tree is the outline every commercial viewer is built around, so
// what matters here is that it is not a second, parallel app: a row
// click has to land in the same place a canvas click lands
// (NodeDetail updates), and a row double-click has to scope the canvas
// exactly as a canvas double-click does.
//
// Fixture: two_clock_two_reset_design — six instances over three
// levels (top → u_fifo/u_rstgen → three ff leaves), two of which
// answer to "sync" (``top.u_rstgen`` by its ``rstsync`` module,
// ``top.u_rstgen.u_sync`` by its instance name).

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

const ALL_INSTANCES = [
  'top',
  'top.u_fifo',
  'top.u_fifo.u_rd_ptr',
  'top.u_fifo.u_wr_ptr',
  'top.u_rstgen',
  'top.u_rstgen.u_sync',
]

const rowIds = (page) =>
  page
    .locator('.tree-row')
    .evaluateAll((els) => els.map((e) => e.getAttribute('data-inst')))

test.describe('hierarchy tree', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript((data) => {
      window.__RTL_BUDDY_VIEW_DATA__ = data
    }, payload)
    await page.goto('/')
    await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.hierarchy-tree')).toBeVisible()
  })

  test('renders every instance once the containers are opened', async ({ page }) => {
    // Default: the top and its direct children — a deep design must not
    // open as a wall of text.
    expect(await rowIds(page)).toEqual(['top', 'top.u_fifo', 'top.u_rstgen'])
    for (const id of ['top.u_fifo', 'top.u_rstgen']) {
      await page.locator(`.tree-row[data-inst="${id}"] .tree-caret`).click()
    }
    expect((await rowIds(page)).sort()).toEqual([...ALL_INSTANCES].sort())
    // Instance name in mono, module type muted after it.
    await expect(
      page.locator('.tree-row[data-inst="top.u_rstgen"] .tree-inst'),
    ).toHaveText('u_rstgen')
    await expect(
      page.locator('.tree-row[data-inst="top.u_rstgen"] .tree-module'),
    ).toHaveText('rstsync')
  })

  test('a row click selects, same as a canvas click', async ({ page }) => {
    await page.locator('.tree-row[data-inst="top.u_rstgen"]').click()
    await expect(page.locator('.node-detail h3 .inst-path')).toHaveText(
      /top\.?u_rstgen/,
    )
    await expect(
      page.locator('.tree-row[data-inst="top.u_rstgen"]'),
    ).toHaveClass(/is-selected/)
  })

  test('a canvas click highlights the matching row', async ({ page }) => {
    // The reverse direction: selection is followed, never owned.
    await page.locator('g.node[data-node-id="top.u_rstgen.u_sync"]').click()
    await expect(
      page.locator('.tree-row[data-inst="top.u_rstgen.u_sync"]'),
    ).toHaveClass(/is-selected/)
  })

  test('a row double-click descends into the subtree', async ({ page }) => {
    await page.locator('.tree-row[data-inst="top.u_fifo"]').dblclick()
    await expect
      .poll(async () =>
        page
          .locator('g.node[data-node-id]')
          .evaluateAll((els) => els.map((e) => e.getAttribute('data-node-id'))),
      )
      .toEqual(['top.u_fifo.u_rd_ptr', 'top.u_fifo.u_wr_ptr'])
    // The scope is marked in the tree, which still shows the whole
    // design — the panel is the map, the canvas is the view.
    await expect(
      page.locator('.tree-row[data-inst="top.u_fifo"] .tree-scope-mark'),
    ).toBeVisible()
    expect(await rowIds(page)).toContain('top.u_rstgen')
  })

  test('the filter shows matches plus their ancestors, with a count', async ({ page }) => {
    await page.locator('.tree-filter-input').fill('sync')
    await expect
      .poll(async () => rowIds(page))
      .toEqual(['top', 'top.u_rstgen', 'top.u_rstgen.u_sync'])
    // Two matches out of six instances; ``top`` is only an ancestor and
    // is dimmed.
    await expect(page.locator('.tree-count')).toHaveText(/2\s*\/\s*6/)
    await expect(page.locator('.tree-row[data-inst="top"]')).toHaveClass(
      /is-dimmed/,
    )
    await expect(
      page.locator('.tree-row[data-inst="top.u_rstgen"]'),
    ).not.toHaveClass(/is-dimmed/)
    // Enter picks the first match, not the first row.
    await page.locator('.tree-filter-input').press('Enter')
    await expect(page.locator('.node-detail h3 .inst-path')).toHaveText(
      /top\.?u_rstgen/,
    )
    // Esc clears the filter and stops there.
    await page.locator('.tree-filter-input').press('Escape')
    await expect(page.locator('.tree-filter-input')).toHaveValue('')
    await expect(page.locator('.node-detail h3 .inst-path')).toHaveText(
      /top\.?u_rstgen/,
    )
  })

  test('/ focuses the filter box without typing the slash', async ({ page }) => {
    await page.locator('.canvas-wrap').click({ position: { x: 5, y: 5 } })
    await page.keyboard.press('/')
    await expect(page.locator('.tree-filter-input')).toBeFocused()
    await expect(page.locator('.tree-filter-input')).toHaveValue('')
  })

  test('arrow keys walk the tree and Enter selects', async ({ page }) => {
    await page.locator('[role="tree"]').focus()
    await page.keyboard.press('ArrowDown') // top
    await page.keyboard.press('ArrowDown') // top.u_fifo
    await page.keyboard.press('ArrowRight') // expand
    await expect(page.locator('.tree-row[data-inst="top.u_fifo.u_rd_ptr"]')).toBeVisible()
    await page.keyboard.press('ArrowRight') // step into the first child
    await page.keyboard.press('Enter')
    await expect(page.locator('.node-detail h3 .inst-path')).toHaveText(
      /top\.?u_fifo\.?u_rd_ptr/,
    )
  })
})
