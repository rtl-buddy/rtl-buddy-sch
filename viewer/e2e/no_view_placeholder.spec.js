// Playwright e2e for the "no view available" placeholder
// (rtl-buddy-view#130).
//
// Everything here is driven through mocked HTTP: the SPA's whole
// notion of "why is there no view" comes from what ``/view.json``
// answers, so ``page.route`` is a faithful stand-in for a hub in each
// failure mode — including a pre-#130 hub that replies text/plain.
//
// The hub-gone case (state 4) is a WebSocket-lifecycle story instead:
// welcome the SPA once, then refuse every subsequent connection.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURE = path.join(__dirname, 'fixtures', 'counter_with_subs.json')
const PAYLOAD = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8'))

const LOG_TAIL = [
  "error: cannot resolve interface port 'apb' on module apb_intf",
  'rtl-buddy-view: 1 error',
  'rtl-buddy-view exited with code 1',
]

/** Answer ``/models`` so the picker populates (and the SPA knows a hub is here). */
async function routeModels(page, models = []) {
  await page.route('**/models', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ models, active: null }),
    }),
  )
  await page.route('**/tests', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tests: [] }),
    }),
  )
}

test.describe('#130 no-view placeholder', () => {
  test('state 1: no hub, no ?view= keeps the drop-a-file screen', async ({ page }) => {
    // The dev server answers /view.json with index.html (200, HTML) —
    // exactly what any static host does, and the SPA must read that as
    // "no hub", not as a broken view.
    await page.goto('/')
    const pane = page.locator('[data-placeholder="idle"]')
    await expect(pane).toBeVisible({ timeout: 15_000 })
    await expect(pane).toContainText('Drop a view.json file here')
    await expect(pane.locator('input[type="file"]')).toBeVisible()
  })

  test('state 2: 409 no_active_model points at the picker', async ({ page }) => {
    await routeModels(page, [{ name: 'counter', models_file: '/m.yaml' }])
    await page.route('**/view.json', (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            kind: 'no_active_model',
            message: 'no model is active on this hub',
            models_url: '/models',
          },
        }),
      }),
    )
    await page.goto('/')
    const pane = page.locator('[data-placeholder="no_active_model"]')
    await expect(pane).toBeVisible({ timeout: 15_000 })
    await expect(pane).toContainText('The hub is connected')
    await expect(pane).toContainText('rb hub start --serve-viewer --model')
    // The picker is populated from /models, so the suggested next
    // click actually exists.
    await expect(page.locator('select.model-picker')).toBeVisible()
  })

  test('state 3: 500 view_generation_failed shows the log tail, causes, and retries', async ({
    page,
  }) => {
    await routeModels(page, [{ name: 'apb_intf', models_file: '/m.yaml', view_status: 'failed' }])
    let attempts = 0
    await page.route('**/view.json?*', (route) => {
      attempts += 1
      if (attempts === 1) {
        return route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({
            error: {
              kind: 'view_generation_failed',
              model: 'apb_intf',
              message: 'rtl-buddy-view exited with code 1',
              log_path: '/w/artefacts/hier/apb_intf/hier.log',
              log_tail: LOG_TAIL,
            },
          }),
        })
      }
      // Second attempt: the user fixed the cause and hit Retry.
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(PAYLOAD),
      })
    })

    await page.goto('/?model=apb_intf')
    const pane = page.locator('[data-placeholder="view_generation_failed"]')
    await expect(pane).toBeVisible({ timeout: 15_000 })
    await expect(pane).toContainText('rtl-buddy-view exited with code 1')
    await expect(pane).toContainText('/w/artefacts/hier/apb_intf/hier.log')
    await expect(pane.locator('pre.log-tail-pre')).toContainText(
      "cannot resolve interface port 'apb'",
    )
    await expect(pane).toContainText('frontend: slang')
    await expect(pane).toContainText('git submodule update --init')
    await expect(pane).toContainText('library entries in filelists are unsupported')

    // The failing model is badged in the picker so the next user
    // doesn't rediscover this by clicking.
    await expect(page.locator('select.model-picker option[data-view-status="failed"]')).toHaveCount(1)

    await pane.locator('button.retry-button').click()
    await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
    expect(attempts).toBe(2)
  })

  test('degradation: a pre-#130 plain-text 500 falls back to the generic card', async ({
    page,
  }) => {
    await routeModels(page)
    await page.route('**/view.json?*', (route) =>
      route.fulfill({
        status: 500,
        contentType: 'text/plain',
        body:
          'rb hub --model apb_intf: rtl-buddy-view exited with code 1; ' +
          'see .../artefacts/hier/apb_intf/hier.log for details.',
      }),
    )
    await page.goto('/?model=apb_intf')
    const pane = page.locator('[data-placeholder="generic_error"]')
    await expect(pane).toBeVisible({ timeout: 15_000 })
    await expect(pane).toContainText('Could not load this schematic')
    // The body text is quoted verbatim — it's all the information the
    // old hub gives us.
    await expect(pane).toContainText('see .../artefacts/hier/apb_intf/hier.log')
    await expect(pane).toContainText('Possible reasons')
  })

  test('state 4: hub dies mid-session → full-pane "hub is gone"', async ({ page }) => {
    let connections = 0
    await page.routeWebSocket(/\/ws$/, (ws) => {
      connections += 1
      if (connections > 1) {
        // Hub process is dead: every reconnect is refused.
        ws.close()
        return
      }
      ws.onMessage((raw) => {
        let env
        try {
          env = JSON.parse(typeof raw === 'string' ? raw : raw.toString())
        } catch {
          return
        }
        if (env.type !== 'hello') return
        ws.send(
          JSON.stringify({
            v: 1,
            id: env.id,
            origin: 'wave',
            kind: 'response',
            type: 'welcome',
            payload: { server_version: '1.0.0-mock', registered_clients: ['view'] },
          }),
        )
        // Now pull the rug: the hub goes away right after welcoming us.
        setTimeout(() => ws.close(), 200)
      })
    })
    await page.addInitScript((data) => {
      window.__RTL_BUDDY_VIEW_DATA__ = data
    }, PAYLOAD)
    await page.goto('/')
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', {
      timeout: 15_000,
    })

    const pane = page.locator('[data-placeholder="hub_gone"]')
    await expect(pane).toBeVisible({ timeout: 20_000 })
    await expect(pane).toContainText('The hub is gone')
    await expect(pane).toContainText('rb hub start --serve-viewer')
    // Must not read as the superseded case (rtl-buddy-view#129).
    await expect(pane).toContainText('nothing else took over this tab')
    // …and the strip agrees with the overlay rather than still
    // promising a reconnect (rtl-buddy-view#130 banner rank).
    await expect(page.locator('.hub-gone-banner')).toBeVisible()
    await expect(page.locator('.reconnecting-banner')).toHaveCount(0)
  })
})
