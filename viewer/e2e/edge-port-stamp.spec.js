// Port-routed CDC edges on the Hierarchy canvas (rtl-buddy-sch#197).
//
// When a node aggregates two or more CDC clock pairs, ``graphToDot``
// draws it as a per-clock grid and routes each crossing edge to its
// ``in_<clk>`` cell (``portRefForPair``). Graphviz keeps that port in
// the edge ``<title>``, and the canvas used to copy the title halves
// verbatim into ``data-edge-from`` / ``data-edge-to`` — so the edge read
// ``top.u_fifo.u_rd_ptr:in_clk_a``, a click on it selected nothing and
// the clock overlay never painted it. The decision is unit-tested in
// ``tests/edge_endpoints.spec.js``; this suite pins it on a real viz.js
// SVG.
//
// Fixture: two_clock_design with two clock pairs spliced onto the one
// crossing edge, which makes the destination a grid node and splits
// the edge into one ported DOT edge per pair.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const FROM = 'top.u_fifo'
const TO = 'top.u_fifo.u_rd_ptr'

function payload() {
  const raw = JSON.parse(
    fs.readFileSync(path.join(__dirname, 'fixtures', 'two_clock_design.json'), 'utf-8'),
  )
  const edge = raw.edges.find((e) => e.from === FROM && e.to === TO)
  expect(edge.overlays.clock.crossing).toBe(true)
  edge.overlays.clock.pairs = [
    { src_clock: 'clk_a', dst_clock: 'clk_b', flops: 2 },
    { src_clock: 'clk_c', dst_clock: 'clk_b', flops: 1 },
  ]
  return raw
}

const ported = (page) => page.locator('g.edge[data-edge-to-port^="in_"]')

// Client coordinates of the midpoint of an edge group's visible path.
async function midpoint(locator) {
  return locator.evaluate((g) => {
    const p = g.querySelector('path')
    const at = p.getPointAtLength(p.getTotalLength() / 2)
    const m = p.getScreenCTM()
    return { x: at.x * m.a + at.y * m.c + m.e, y: at.x * m.b + at.y * m.d + m.f }
  })
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((data) => {
    window.__RTL_BUDDY_VIEW_DATA__ = data
  }, payload())
  await page.goto('/')
  await expect(ported(page)).toHaveCount(2, { timeout: 30_000 })
  await expect(page.locator('.svg-host svg > g')).toHaveAttribute('transform', /scale/)
})

test('a ported CDC edge is stamped with plain ids, its port kept apart', async ({ page }) => {
  const stamps = await ported(page).evaluateAll((els) =>
    els.map((g) => ({
      title: g.querySelector('title').textContent,
      from: g.getAttribute('data-edge-from'),
      to: g.getAttribute('data-edge-to'),
      toPort: g.getAttribute('data-edge-to-port'),
    })),
  )
  // The raw title still carries the port — the stamp must not.
  for (const s of stamps) {
    expect(s.title).toContain(`${TO}:in_`)
    expect(s.from).toBe(FROM)
    expect(s.to).toBe(TO)
  }
  expect(stamps.map((s) => s.toPort).sort()).toEqual(['in_clk_a', 'in_clk_c'])
})

test('the clock overlay paints both ported renderings of the crossing', async ({ page }) => {
  const painted = await ported(page).evaluateAll((els) =>
    els.map((g) => g.querySelector('path').getAttribute('data-overlay-clock')),
  )
  expect(painted).toEqual(['crossing', 'crossing'])
})

test('clicking a ported CDC edge selects it', async ({ page }) => {
  await expect(page.locator('.edge-detail')).toHaveCount(0)
  const pt = await midpoint(ported(page).first())
  await page.mouse.click(pt.x, pt.y)
  await expect(page.locator('.edge-detail h3')).toHaveText(`${FROM} → ${TO}`)
})
