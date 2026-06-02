// Playwright e2e for the Phase 9 wave-live overlay (rtl-buddy-view#24,
// follow-up #97). The last open acceptance item from #24 was an
// end-to-end test of the wave overlay's two value sources and the
// hub-disconnect freeze behaviour shipped in PR #95.
//
// Like hub.spec.js we do NOT spin up a real `rb hub` + surfer: the
// wire protocol is the contract, and Playwright 1.48+'s
// `page.routeWebSocket` lets a mock hub answer the SPA's /ws
// connection in-process. A real hub + surfer is impractical in CI and
// would only re-test the Python producer, which tests/test_wave_overlay
// already covers on its own side. This spec validates the SPA's
// rendering + reactive layer:
//
//   - static path:  node.overlays.wave.ports[] paints badges offline,
//   - live path:    a wave_values_changed event paints + auto-enables,
//   - live wins:    a live value overrides a static one for the same port,
//   - coalesce:     a 60 Hz burst collapses to the latest value (33 ms window),
//   - disconnect:   badges freeze + the amber reconnecting banner appears,
//                   and clears once the hub welcomes again,
//   - loop-prevent: a wave_values_changed tagged origin=view is dropped.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const BASE = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'fixtures', 'counter_with_subs.json'), 'utf-8'),
)

// The fixture's counter.u_ff leaf carries ports clk + q, so it renders
// as a hier-view shape node the wave overlay can decorate.
const NODE = 'counter.u_ff'
const STATIC_Q = "8'h2a"
const LIVE_Q = "8'h3b"

function uuid() {
  return '00000000-0000-4000-8000-' + Math.random().toString(16).slice(2, 14).padEnd(12, '0')
}

// Build a view.json payload from the shared fixture. With
// ``withStatic`` the wave overlay is pre-listed in overlays_present and
// counter.u_ff gets a static port-value snapshot; without it the
// payload is the bare fixture so the live path can prove it
// auto-enables the overlay on the first wave_values_changed.
function makePayload({ withStatic = false } = {}) {
  const p = JSON.parse(JSON.stringify(BASE))
  if (withStatic) {
    p.overlays_present = ['wave']
    const node = p.nodes.find((n) => n.id === NODE)
    node.overlays = { wave: { ports: [{ name: 'q', value: STATIC_Q }] } }
  }
  return p
}

// A mock /ws hub. Beyond hub.spec.js's installMockHub it gains an
// ``online`` gate + goOffline/goOnline so the disconnect-freeze test
// can drop the socket and recover it deterministically without
// fighting the SPA's exponential-backoff reconnect timing.
async function installWaveMock(page) {
  const recv = []
  const wsRef = { ws: null }
  const ctl = { online: true }

  function welcome(ws, env) {
    ws.send(JSON.stringify({
      v: 1,
      id: env.id,
      origin: 'wave',
      kind: 'response',
      type: 'welcome',
      payload: { server_version: '1.0.0-mock', registered_clients: ['view', 'wave'] },
    }))
  }

  await page.routeWebSocket(/\/ws$/, (ws) => {
    wsRef.ws = ws
    ws.onMessage((raw) => {
      let env
      try { env = JSON.parse(typeof raw === 'string' ? raw : raw.toString()) }
      catch { return }
      recv.push(env)
      if (env.type === 'hello' && ctl.online) welcome(ws, env)
    })
  })

  const helloCount = () => recv.filter((e) => e.type === 'hello').length

  return {
    recv,
    helloCount,
    sendValues: (values, opts = {}) =>
      wsRef.ws.send(JSON.stringify({
        v: 1,
        id: uuid(),
        origin: opts.origin || 'wave',
        kind: 'event',
        type: 'wave_values_changed',
        payload: { t_fs: opts.t_fs || '1000', values },
      })),
    // Drop the live socket and refuse to welcome further reconnects so
    // the SPA stays non-ready (banner persists) until goOnline().
    goOffline: () => {
      ctl.online = false
      if (wsRef.ws) wsRef.ws.close()
    },
    // Welcome the (already-reconnected, unwelcomed) socket so the SPA
    // flips back to ready and the banner clears.
    goOnline: () => {
      ctl.online = true
      if (wsRef.ws) {
        wsRef.ws.send(JSON.stringify({
          v: 1,
          id: uuid(),
          origin: 'wave',
          kind: 'response',
          type: 'welcome',
          payload: { server_version: '1.0.0-mock', registered_clients: ['view', 'wave'] },
        }))
      }
    },
  }
}

async function bootViewer(page, payload) {
  await page.addInitScript((data) => {
    window.__RTL_BUDDY_VIEW_DATA__ = data
  }, payload)
  await page.goto('/')
  await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
  await page.waitForFunction(
    () => document.querySelectorAll('svg [data-node-id]').length > 0,
    { timeout: 30_000 },
  )
}

// Text of the wave badge painted on a given node group (hier view
// renders one stacked <text class="rb-wave-badge"> with a "name=value"
// tspan per port). null when no badge is present.
function badgeText(page, nodeId) {
  return page.evaluate((id) => {
    const g = document.querySelector(`[data-node-id="${id}"]`)
    if (!g) return null
    const b = g.querySelector('.rb-wave-badge')
    return b ? b.textContent : null
  }, nodeId)
}

test.describe('wave overlay — static snapshot path', () => {
  test('paints node.overlays.wave.ports[] values offline (no hub)', async ({ page }) => {
    // No mock hub: the overlay is enabled via overlays_present and the
    // badge renders purely from the static snapshot, like the offline
    // Phase-8 producer output.
    await bootViewer(page, makePayload({ withStatic: true }))
    await expect.poll(() => page.locator('.rb-wave-badge').count(), { timeout: 15_000 })
      .toBeGreaterThan(0)
    await expect.poll(() => badgeText(page, NODE), { timeout: 15_000 })
      .toContain(`q=${STATIC_Q}`)
  })
})

test.describe('wave overlay — live hub path', () => {
  test('a wave_values_changed event auto-enables the overlay and paints', async ({ page }) => {
    // Bare fixture: 'wave' is NOT in overlays_present. Receiving live
    // values is itself the signal to turn the overlay on (store
    // applyWaveValues auto-enable), so a badge appears where there was
    // none before.
    const mock = await installWaveMock(page)
    await bootViewer(page, makePayload())
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })
    expect(await page.locator('.rb-wave-badge').count()).toBe(0)

    mock.sendValues([{ wave_scope: NODE, signal: 'q', value: LIVE_Q }])

    await expect.poll(() => badgeText(page, NODE), { timeout: 15_000 })
      .toContain(`q=${LIVE_Q}`)
  })

  test('live value wins over a static snapshot value for the same port', async ({ page }) => {
    const mock = await installWaveMock(page)
    await bootViewer(page, makePayload({ withStatic: true }))
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })
    // Static snapshot paints first.
    await expect.poll(() => badgeText(page, NODE), { timeout: 15_000 }).toContain(`q=${STATIC_Q}`)

    mock.sendValues([{ wave_scope: NODE, signal: 'q', value: LIVE_Q }])

    // Live cache wins; the static literal is gone.
    await expect.poll(() => badgeText(page, NODE), { timeout: 15_000 }).toContain(`q=${LIVE_Q}`)
    expect(await badgeText(page, NODE)).not.toContain(STATIC_Q)
  })

  test('a 60 Hz burst coalesces to the latest value (33 ms window)', async ({ page }) => {
    const mock = await installWaveMock(page)
    await bootViewer(page, makePayload())
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })

    // Ten samples of the same signal back-to-back, each a new value.
    // The composable collapses the burst into a single store write
    // applying the LATEST value; the badge must settle on it. (We
    // assert the steady-state value rather than "exactly one write",
    // which isn't observable from the DOM, but final-value correctness
    // is the coalesce contract that matters.)
    const last = "8'h09"
    for (let i = 0; i < 10; i++) {
      mock.sendValues([{ wave_scope: NODE, signal: 'q', value: `8'h0${i}` }])
    }

    await expect.poll(() => badgeText(page, NODE), { timeout: 15_000 }).toContain(`q=${last}`)
  })
})

test.describe('wave overlay — resilience', () => {
  test('hub disconnect freezes badges + shows the reconnecting banner; welcome clears it', async ({ page }) => {
    const mock = await installWaveMock(page)
    await bootViewer(page, makePayload())
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })

    const frozen = "8'hca"
    mock.sendValues([{ wave_scope: NODE, signal: 'q', value: frozen }])
    await expect.poll(() => badgeText(page, NODE), { timeout: 15_000 }).toContain(`q=${frozen}`)

    // Drop the socket. The store never clears waveValuesByKey on
    // disconnect, so the badge must stay painted at the last sample.
    mock.goOffline()
    await expect(page.locator('.reconnecting-banner')).toBeVisible({ timeout: 10_000 })
    expect(await badgeText(page, NODE)).toContain(`q=${frozen}`)

    // The SPA auto-reconnects (we refused to welcome it, so it sits in
    // 'connecting' and the banner persists). Once the hub welcomes the
    // reconnected socket, the banner clears.
    await expect.poll(() => mock.helloCount(), { timeout: 10_000 }).toBeGreaterThanOrEqual(2)
    mock.goOnline()
    await expect(page.locator('.reconnecting-banner')).toHaveCount(0, { timeout: 10_000 })
    // Frozen value survived the round-trip.
    expect(await badgeText(page, NODE)).toContain(`q=${frozen}`)
  })

  test('wave_values_changed tagged origin=view is dropped (loop-prevention)', async ({ page }) => {
    const mock = await installWaveMock(page)
    await bootViewer(page, makePayload())
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })

    // A legitimate wave-origin value paints.
    const good = "8'hab"
    mock.sendValues([{ wave_scope: NODE, signal: 'q', value: good }])
    await expect.poll(() => badgeText(page, NODE), { timeout: 15_000 }).toContain(`q=${good}`)

    // A view-origin envelope is the viewer's own echo — the dispatch
    // filter drops it before the coalesce buffer, so the badge must NOT
    // change to the bad value.
    const bad = "8'hff"
    mock.sendValues([{ wave_scope: NODE, signal: 'q', value: bad }], { origin: 'view' })
    // Give it well past the 33 ms flush window to (not) take effect.
    await page.waitForTimeout(200)
    const text = await badgeText(page, NODE)
    expect(text).toContain(`q=${good}`)
    expect(text).not.toContain(bad)
  })
})
