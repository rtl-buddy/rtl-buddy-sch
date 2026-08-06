// Both themes render — the acceptance check for
// rtl-buddy/rtl-buddy-view#132.
//
// A design-token migration fails in one of two ways that unit tests
// can't see, and both need a real browser with a real cascade:
//
//   1. a literal that was never migrated, which shows up as a light
//      surface on a dark page (or unreadable text on it);
//   2. a *baked* colour — the canvas and the Graphviz-generated SVG
//      carry concrete strings, not ``var(--…)``, so they only follow
//      the theme if the component re-draws on the flip.
//
// So each case boots the SPA under a colour scheme, asserts the page
// actually resolved to that palette, and re-checks after an in-app
// pin flip (which exercises the MutationObserver path rather than the
// media-query one). Screenshots are attached for human review.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURE = path.join(__dirname, 'fixtures', 'two_clock_design.json')
const payload = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8'))

// The vendored sheet's two page surfaces. Chromium reports computed
// colours as ``rgb(r, g, b)``.
const BG = {
  light: 'rgb(248, 250, 252)', // #f8fafc
  dark: 'rgb(15, 17, 21)', // #0f1115
}

async function boot(page) {
  await page.addInitScript((data) => {
    window.__RTL_BUDDY_VIEW_DATA__ = data
  }, payload)
  await page.goto('/')
  await expect(page.locator('svg').first()).toBeVisible({ timeout: 30_000 })
  await expect.poll(() => page.locator('svg g.node').count(), { timeout: 30_000 }).toBeGreaterThan(0)
}

/** Resolved value of a design token on <html>. */
function token(page, name) {
  return page.evaluate(
    (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(),
    name,
  )
}

for (const scheme of ['light', 'dark']) {
  test.describe(`theme: ${scheme}`, () => {
    test.use({ colorScheme: scheme })

    test('the page resolves to the matching palette', async ({ page }, testInfo) => {
      await boot(page)

      // The media query picked the right block…
      const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
      expect(bg).toBe(BG[scheme])

      // …and the chrome strips are on --panel, not a baked white.
      const header = await page
        .locator('.app-header')
        .evaluate((el) => getComputedStyle(el).backgroundColor)
      const strip = await page
        .locator('.app-status-strip')
        .evaluate((el) => getComputedStyle(el).backgroundColor)
      expect(header).toBe(await token(page, '--panel').then(hexToRgb))
      expect(strip).toBe(header)

      await testInfo.attach(`spa-${scheme}.png`, {
        body: await page.screenshot({ fullPage: false }),
        contentType: 'image/png',
      })
    })

    test('graph text follows the theme, not the baked Graphviz default', async ({ page }) => {
      await boot(page)
      // viz.js emits ``fill="#000000"`` on its <text>; the canvas's
      // themed rule has to outrank that presentation attribute, or
      // dark mode is black-on-black.
      const fill = await page
        .locator('svg g.node text')
        .first()
        .evaluate((el) => getComputedStyle(el).fill)
      expect(fill).toBe(await token(page, '--fg').then(hexToRgb))
    })
  })
}

test.describe('in-app theme pin', () => {
  // Start light so the pin has to beat the ``:root`` defaults, then
  // the media query on the way back.
  test.use({ colorScheme: 'light' })

  test('the toggle pins dark and the canvas redraws in it', async ({ page }, testInfo) => {
    await boot(page)
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(
      BG.light,
    )

    // system → light → dark.
    const toggle = page.locator('.theme-toggle')
    await toggle.click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
    await toggle.click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')

    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(BG.dark)
    // The SVG is re-laid-out on the flip, so its text is themed too.
    await expect
      .poll(
        () =>
          page
            .locator('svg g.node text')
            .first()
            .evaluate((el) => getComputedStyle(el).fill),
        { timeout: 10_000 },
      )
      .toBe(await token(page, '--fg').then(hexToRgb))

    await testInfo.attach('spa-pinned-dark.png', {
      body: await page.screenshot({ fullPage: false }),
      contentType: 'image/png',
    })

    // …and back to system, which drops the attribute entirely.
    await toggle.click()
    await expect(page.locator('html')).not.toHaveAttribute('data-theme', /.*/)
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(
      BG.light,
    )
  })
})

/** ``#rrggbb`` → ``rgb(r, g, b)`` so token values compare to computed ones. */
function hexToRgb(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return hex
  const n = parseInt(m[1], 16)
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`
}
