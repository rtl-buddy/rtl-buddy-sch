// Net hover-highlight in the Hierarchy view (rtl-buddy-sch#142).
//
// The decision (which edges are "the net") is unit-tested in
// ``tests/net_hover.spec.js``; this suite pins the browser half that a
// unit test cannot: a real pointer over a real viz.js SVG lands on the
// edge (through the invisible hit twin), the right groups get
// ``data-rb-edge-hover`` and actually repaint, the mark goes away on
// mouseleave, and the Block Flow click highlight is left alone.
//
// Fixture: block_diagram_demo — the producer's embedded block-diagram
// DOT, with two edges spliced in so one scope input fans out and one
// dataflow edge carries the same net as a scope output anchor.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const IN_PROD = '_in_cmd_in:e->blk_top.u_prod'
const IN_CONS = '_in_cmd_in:e->blk_top.u_cons'
const FLOW = 'blk_top.u_prod:e->blk_top.u_cons:w'
const OUT = 'blk_top.u_cons:e->_out_result_out:w'

function payload() {
  const raw = JSON.parse(
    fs.readFileSync(path.join(__dirname, 'fixtures', 'block_diagram_demo.json'), 'utf-8'),
  )
  const anchor = '    "blk_top.u_cons" -> "_out_result_out"'
  expect(raw.layout.dot).toContain(anchor)
  raw.layout.dot = raw.layout.dot.replace(
    anchor,
    '    "_in_cmd_in" -> "blk_top.u_cons" [tailport=e];\n' +
      '    "blk_top.u_prod" -> "blk_top.u_cons" ' +
      '[tailport=e, headport=w, xlabel="result_out\\l", fontsize=9];\n' +
      anchor,
  )
  return raw
}

// Titles of every edge currently marked by the hover.
async function hotEdges(page) {
  return page
    .locator('g.edge[data-rb-edge-hover]')
    .evaluateAll((els) => els.map((e) => e.querySelector('title').textContent).sort())
}

// Client coordinates of the midpoint of the edge with this title.
async function edgeMidpoint(page, title) {
  return page.evaluate((t) => {
    const g = Array.from(document.querySelectorAll('g.edge')).find(
      (e) => e.querySelector('title').textContent === t,
    )
    if (!g) return null
    const p = g.querySelector('path')
    const at = p.getPointAtLength(p.getTotalLength() / 2)
    const m = p.getScreenCTM()
    return { x: at.x * m.a + at.y * m.c + m.e, y: at.x * m.b + at.y * m.d + m.f }
  }, title)
}

async function nodeCentre(page, nodeId) {
  const box = await page.locator(`g.node[data-node-id="${nodeId}"]`).boundingBox()
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((data) => {
    window.__RTL_BUDDY_VIEW_DATA__ = data
  }, payload())
  await page.goto('/')
  await expect(page.locator('g.edge')).toHaveCount(4, { timeout: 30_000 })
  // Let the post-layout fit settle before measuring anything.
  await expect(page.locator('.svg-host svg > g')).toHaveAttribute('transform', /scale/)
})

test('hovering an edge lights its net, and only its net', async ({ page }) => {
  const pt = await edgeMidpoint(page, IN_PROD)
  await page.mouse.move(pt.x, pt.y)
  await expect.poll(() => hotEdges(page)).toEqual([IN_CONS, IN_PROD].sort())
})

test('the hovered edge actually repaints in the accent', async ({ page }) => {
  const pt = await edgeMidpoint(page, IN_PROD)
  await page.mouse.move(pt.x, pt.y)
  await expect.poll(() => hotEdges(page)).toContain(IN_PROD)
  const seen = await page.evaluate((t) => {
    const probe = document.createElement('div')
    probe.style.color = 'var(--accent)'
    document.body.appendChild(probe)
    const accent = getComputedStyle(probe).color
    probe.remove()
    const g = Array.from(document.querySelectorAll('g.edge')).find(
      (e) => e.querySelector('title').textContent === t,
    )
    const [visible, hit] = g.querySelectorAll('path')
    return {
      accent,
      stroke: getComputedStyle(visible).stroke,
      head: getComputedStyle(g.querySelector('polygon')).fill,
      hitIsTwin: hit.hasAttribute('data-rb-edge-hit'),
      hitOpacity: getComputedStyle(hit).strokeOpacity,
    }
  }, IN_PROD)
  expect(seen.stroke).toBe(seen.accent)
  expect(seen.head).toBe(seen.accent)
  // The fat hit twin is what the pointer landed on; it must stay unseen.
  expect(seen.hitIsTwin).toBe(true)
  expect(seen.hitOpacity).toBe('0')
})

test('a dataflow edge and the port anchor of the same net light together', async ({ page }) => {
  const pt = await edgeMidpoint(page, OUT)
  await page.mouse.move(pt.x, pt.y)
  await expect.poll(() => hotEdges(page)).toEqual([FLOW, OUT].sort())
})

test('hovering an endpoint lights what is attached to it', async ({ page }) => {
  // A scope port anchor: its net.
  let pt = await nodeCentre(page, '_in_cmd_in')
  await page.mouse.move(pt.x, pt.y)
  await expect.poll(() => hotEdges(page)).toEqual([IN_CONS, IN_PROD].sort())
  // An instance: every edge at it, across nets.
  pt = await nodeCentre(page, 'blk_top.u_cons')
  await page.mouse.move(pt.x, pt.y)
  await expect.poll(() => hotEdges(page)).toEqual([FLOW, IN_CONS, OUT].sort())
})

test('the highlight is transient: leaving the canvas clears it', async ({ page }) => {
  const pt = await edgeMidpoint(page, IN_PROD)
  await page.mouse.move(pt.x, pt.y)
  await expect.poll(() => hotEdges(page)).not.toEqual([])
  // Off the svg host entirely (the header strip).
  await page.mouse.move(2, 2)
  await expect.poll(() => hotEdges(page)).toEqual([])
})

test('hover never touches the Block Flow click highlight', async ({ page }) => {
  await page.getByRole('button', { name: 'Block Flow', exact: true }).click()
  const port = page.locator('[data-bf-id="bf-out:blk_top.u_prod:valid"]')
  await expect(port).toHaveCount(1, { timeout: 30_000 })
  await port.click()
  const clicked = page.locator('g.edge[data-rb-edge-highlighted]')
  await expect(clicked).not.toHaveCount(0)
  const before = await clicked.count()
  // Sweep the pointer over the highlighted edge and off the canvas.
  const box = await clicked.first().boundingBox()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.move(2, 2)
  await expect(clicked).toHaveCount(before)
  await expect(page.locator('[data-rb-edge-hover]')).toHaveCount(0)
})
