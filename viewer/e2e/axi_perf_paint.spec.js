// Real-browser verification of the axi-perf interface-pin paint
// (Phase 1 of the axi-perf ↔ tb-top unification).
//
// Injects a view.json whose DUT node carries
// ``overlays['axi-perf'].bundle_pins`` (generated from the
// interface_port_module fixture with a hand-authored axi-perf overlay)
// and asserts the overlay actually decorates the rendered SVG:
//   - default hier view → an aggregate ``.rb-axi-badge`` lands on the
//     node carrying the bundle (the per-port interface cells only
//     exist in flow view, so hier falls back to a node badge),
//   - toggling the overlay off clears the decoration.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const payload = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'axi_perf_iface.json'), 'utf-8'),
)
const axi2x2 = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'axi_2x2.json'), 'utf-8'),
)

test.describe('axi-perf interface-pin paint', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript((data) => {
      window.__RTL_BUDDY_VIEW_DATA__ = data
    }, payload)
    await page.goto('/')
    const svg = page.locator('svg').first()
    await expect(svg).toBeVisible({ timeout: 30_000 })
    await page.waitForFunction(
      () => document.querySelectorAll('svg [data-node-id]').length > 0,
      { timeout: 30_000 },
    )
  })

  test('decorates the bundle-carrying node in the default hier view', async ({ page }) => {
    // axi-perf is in overlays_present → enabled on boot → applyOverlays
    // paints the hier-view aggregate badge.
    await expect
      .poll(() => page.locator('.rb-axi-badge').count(), { timeout: 15_000 })
      .toBeGreaterThan(0)
    const text = await page.locator('.rb-axi-badge').first().textContent()
    expect(text).toContain('AXI')
    // slverr=1 in the fixture → error glyph on the aggregate badge.
    expect(text).toContain('⚠')
  })

  test('paints the interface pin + boundary stub in block-flow view', async ({ page }) => {
    // Switch to Block Flow, where the interface port is drawn as a
    // pin cell — the headline "decorate the DUT interface pin" artifact.
    await page.getByRole('button', { name: 'Block Flow' }).click()
    await page.waitForFunction(
      () => document.querySelectorAll('svg [data-node-id]').length > 0,
      { timeout: 30_000 },
    )
    // Per-pin outline lands on the interface cell(s)...
    await expect
      .poll(() => page.locator('.rb-axi-pin').count(), { timeout: 15_000 })
      .toBeGreaterThan(0)
    // ...and the procedural-master peer renders a dashed boundary stub.
    expect(await page.locator('.rb-axi-stub').count()).toBeGreaterThan(0)
  })
})

test.describe('demo_axi_2x2 — manifest-described bundles on the real tb_top', () => {
  // The macro-flat AXI ports are invisible to the parser; json_render
  // synthesizes 4 bundle pins (in0/in1/out0/out1) on the real
  // tb_axi_2x2.dut node from the axi-bundles.yaml — no system_view.sv.
  test.beforeEach(async ({ page }) => {
    await page.addInitScript((data) => {
      window.__RTL_BUDDY_VIEW_DATA__ = data
    }, axi2x2)
    await page.goto('/')
    await page.locator('svg').first().waitFor({ timeout: 30_000 })
    await page.waitForFunction(
      () => document.querySelectorAll('svg [data-node-id]').length > 0,
      { timeout: 30_000 },
    )
  })

  test('draws all four bundle pins on dut in block-flow, decorated', async ({ page }) => {
    await page.getByRole('button', { name: 'Block Flow' }).click()
    await page.waitForFunction(
      () => document.querySelectorAll('svg [data-node-id]').length > 0,
      { timeout: 30_000 },
    )
    // One synthesized ▶▶ interface cell per bundle on the real dut node.
    for (const name of ['in0', 'in1', 'out0', 'out1']) {
      await expect
        .poll(
          () =>
            page.locator(`[data-bf-id="bf-iface:tb_axi_2x2.dut:${name}"]`).count(),
          { timeout: 15_000 },
        )
        .toBeGreaterThan(0)
    }
    // axi-perf overlay decorates them (4 pins → ≥4 outlines).
    await expect
      .poll(() => page.locator('.rb-axi-pin').count(), { timeout: 15_000 })
      .toBeGreaterThanOrEqual(4)
  })

  test('AXI Performance tab lists all four bundles, master-first then name', async ({ page }) => {
    await page.getByRole('button', { name: 'AXI Performance' }).click()
    const list = page.locator('.bundle-list .bundle-name')
    await expect.poll(() => list.count(), { timeout: 15_000 }).toBe(4)
    // DOM order = sort order: initiator (master) ports first, then
    // target (slave) ports; alphabetical within each type.
    const names = (await list.allTextContents()).map((s) => s.trim())
    expect(names).toEqual(['out0', 'out1', 'in0', 'in1'])
  })
})
