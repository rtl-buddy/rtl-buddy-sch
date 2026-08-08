// Playwright e2e for Phase 10d viewer↔hub wiring (rtl-buddy-view#23).
//
// We don't spin up a real `rb hub` binary here — the protocol is
// the contract, and a mock /ws server gives us deterministic
// coverage of the six common flows + the diagnostics_set replay
// path. Playwright 1.48+ `page.routeWebSocket` lets us answer the
// SPA's `/ws` connection in-process; no native sockets needed.
//
// The mock server's job is to:
//   1. Accept hello, reply with welcome (advertising registered peers).
//   2. Push events on demand from the test (cursor_time_changed,
//      selection_changed, diagnostics_set).
//   3. Record everything the viewer sends so we can assert origin
//      tagging and loop-prevention behavior.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURE = path.join(__dirname, 'fixtures', 'counter_with_subs.json')
const PAYLOAD = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8'))

const PROTO = {
  v: 1,
  hello: (env) => ({
    v: 1,
    id: env.id,
    origin: 'wave',
    kind: 'response',
    type: 'welcome',
    payload: {
      server_version: '1.0.0-mock',
      registered_clients: ['view', 'wave', 'src'],
    },
  }),
}

function uuid() {
  return '00000000-0000-4000-8000-' + Math.random().toString(16).slice(2, 14).padEnd(12, '0')
}

async function installMockHub(page, opts = {}) {
  const recv = [] // envelopes received from the page
  const wsRef = { ws: null }

  await page.routeWebSocket(/\/ws$/, (ws) => {
    wsRef.ws = ws
    ws.onMessage((raw) => {
      let env
      try { env = JSON.parse(typeof raw === 'string' ? raw : raw.toString()) }
      catch { return }
      recv.push(env)
      if (env.type === 'hello') {
        ws.send(JSON.stringify(PROTO.hello(env)))
        // Optional replay-on-welcome: simulate the hub redelivering
        // cached diagnostics so a late-joining viewer sees them.
        if (opts.replayDiagnostics) {
          for (const d of opts.replayDiagnostics) {
            ws.send(JSON.stringify({
              v: 1, id: uuid(), origin: d.origin || 'cli',
              kind: 'event', type: 'diagnostics_set',
              payload: { source: d.source, items: d.items },
            }))
          }
        }
      }
    })
  })

  return {
    recv,
    // Push an envelope from the mock hub to the page. Call only
    // after the pill has flipped to 'ready' so `wsRef.ws` is bound.
    sendFromMock: (env) => wsRef.ws.send(JSON.stringify(env)),
  }
}

async function bootViewer(page) {
  // Inject the fixture before the SPA boots, mirroring snapshot.spec.js.
  await page.addInitScript((data) => {
    window.__RTL_BUDDY_VIEW_DATA__ = data
  }, PAYLOAD)
  await page.goto('/')
  // Wait for the viewer to render at least one node so we know the
  // hub composable has been mounted (initHub() fires in onMounted).
  await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
}

test.describe('Phase 10d hub wiring', () => {
  test('handshake: SPA sends hello with origin=view; pill flips to ready on welcome', async ({ page }) => {
    const mock = await installMockHub(page)
    await bootViewer(page)

    // The handshake runs as soon as the WS opens. Wait for the
    // pill to render the live state.
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })

    // The recorded inbound envelopes include the hello — verify
    // its origin and shape.
    expect(mock.recv.length).toBeGreaterThanOrEqual(1)
    const hello = mock.recv.find((e) => e.type === 'hello')
    expect(hello).toBeTruthy()
    expect(hello.origin).toBe('view')
    expect(hello.kind).toBe('request')
    expect(hello.payload.capabilities).toContain('selection_changed')
  })

  test('clicking a node emits selection_changed with origin=view', async ({ page }) => {
    const mock = await installMockHub(page)
    await bootViewer(page)
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })

    // Click any node; we don't care which — the assertion is on
    // the wire shape.
    await page.locator('g.node').first().click()

    await expect.poll(() => mock.recv.filter((e) => e.type === 'selection_changed').length, {
      timeout: 5_000,
    }).toBeGreaterThan(0)
    const sel = mock.recv.find((e) => e.type === 'selection_changed')
    expect(sel.origin).toBe('view')
    expect(sel.kind).toBe('event')
    expect(typeof sel.payload.instance_path).toBe('string')
  })

  test('a hub-side selection_changed from wave updates store.selection', async ({ page }) => {
    const mock = await installMockHub(page)
    await bootViewer(page)
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })

    const targetId = PAYLOAD.nodes[1].id
    mock.sendFromMock({
      v: 1, id: uuid(), origin: 'wave', kind: 'event',
      type: 'selection_changed',
      payload: { instance_path: targetId },
    })

    // NodeDetail in the sidebar shows the selection — scope the
    // assertion to its h3 so we don't match the SVG <title> or
    // graph-edge title text.
    await expect(page.locator('.node-detail h3 .inst-path')).toHaveText(targetId, { timeout: 5_000 })
  })

  test('diagnostics_set populates the panel; empty items clears that source', async ({ page }) => {
    const mock = await installMockHub(page)
    await bootViewer(page)
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })

    mock.sendFromMock({
      v: 1, id: uuid(), origin: 'cli', kind: 'event',
      type: 'diagnostics_set',
      payload: {
        source: 'rtl-buddy-cdc',
        items: [{ file: '/x.sv', line: 1, severity: 'error', message: 'no sync' }],
      },
    })

    // Sidebar lights up with the source-group row.
    const group = page.locator('.source-group[data-source="rtl-buddy-cdc"]')
    await expect(group).toBeVisible({ timeout: 5_000 })
    await expect(group).toHaveAttribute('data-count', '1')

    // Clearing: empty items withdraws the source.
    mock.sendFromMock({
      v: 1, id: uuid(), origin: 'cli', kind: 'event',
      type: 'diagnostics_set',
      payload: { source: 'rtl-buddy-cdc', items: [] },
    })

    await expect(group).toHaveCount(0, { timeout: 5_000 })
  })

  test('hub error envelope surfaces in ToastHost', async ({ page }) => {
    const mock = await installMockHub(page)
    await bootViewer(page)
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })

    mock.sendFromMock({
      v: 1, id: uuid(), origin: 'wave', kind: 'error',
      type: 'error',
      payload: { code: 'not_connected', message: 'no wave client attached' },
    })

    await expect(page.locator('.toast')).toBeVisible({ timeout: 5_000 })
    await expect(page.locator('.toast')).toHaveAttribute('data-code', 'not_connected')
    // R7a: the toast leads with a SENTENCE; the raw ``code — message``
    // is the secondary detail line, not the headline.
    await expect(page.locator('.toast .message')).toContainText(
      'peer that request needed is not connected',
    )
    await expect(page.locator('.toast .detail')).toContainText('no wave client attached')
    // R7c: the strip carries a short status, not a second full copy.
    await expect(page.locator('.hub-message')).not.toContainText('no wave client attached')
    await expect(page.locator('.hub-message')).toContainText('peer not connected')
  })

  test('replay-on-welcome: cached diagnostics appear after handshake', async ({ page }) => {
    const items = [
      { file: '/a.sv', line: 4, severity: 'warning', message: 'depth small' },
    ]
    await installMockHub(page, {
      replayDiagnostics: [{ source: 'rtl-buddy-cdc', items, origin: 'cli' }],
    })
    await bootViewer(page)
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })
    const group = page.locator('.source-group[data-source="rtl-buddy-cdc"]')
    await expect(group).toBeVisible({ timeout: 5_000 })
    await expect(group).toHaveAttribute('data-count', '1')
  })

  test('offline mode (no /ws) shows disconnected; left-click no-ops, right-click dispatches node.link', async ({ page, context }) => {
    // No routeWebSocket installer: the SPA's connect attempt will
    // hit the dev server and fail; the pill stays disconnected.
    await page.addInitScript((data) => {
      window.__RTL_BUDDY_VIEW_DATA__ = data
    }, PAYLOAD)

    // Intercept window.open so we can verify the offline-fallback
    // dispatch (right-click only — rtl-buddy-view #99 follow-up
    // removed the left-click fallback because every standalone-mode
    // left-click was opening a blank tab when the OS lacks an
    // rtlbuddy:// handler).
    await page.addInitScript(() => {
      window.__opened__ = []
      window.open = (url) => { window.__opened__.push(String(url)) }
    })

    await page.goto('/')
    await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
    // The pill should not be 'ready'.
    await expect(page.locator('.hub-status')).not.toHaveAttribute('data-state', 'ready')

    // Click a node that has a link. Skip the design top — graphToDot
    // wraps it as a ``cluster_top`` that encloses every child, so a
    // click on the cluster's interior gets intercepted by whichever
    // child node sits under the pointer instead of dispatching the
    // root's link. Any non-root linked node still exercises the
    // dispatch path identically.
    const linked = PAYLOAD.nodes.find((n) => n.link && n.id !== PAYLOAD.top)
    if (linked) {
      const nodeEl = page.locator(`g.node[data-node-id="${linked.id}"]`).first()
      // Left-click is a *selection* gesture — does NOT open a tab.
      // This pins the new contract (#99): left-click in offline mode
      // populates NodeDetail but never navigates, so dblclick-to-
      // descend isn't poisoned by a first-click tab opening.
      await nodeEl.click()
      await page.waitForTimeout(500)
      const afterLeftClick = await page.evaluate(
        () => window.__opened__?.length || 0,
      )
      expect(afterLeftClick).toBe(0)
      // Right-click ("open source" gesture) still falls back to the
      // rtlbuddy:// URI via window.open so standalone users can wire
      // up an OS handler if they want.
      await nodeEl.click({ button: 'right' })
      await expect.poll(async () => {
        return await page.evaluate(() => window.__opened__?.length || 0)
      }, { timeout: 5_000 }).toBeGreaterThan(0)
    } else {
      test.info().annotations.push({
        type: 'note',
        description: 'fixture has no node.link to validate fallback against',
      })
    }
  })
})

// The selected node, elsewhere: NodeDetail's cross-app row. The
// ordering contract is what earns an e2e — "open X ↗" is only correct
// because the focus envelope is on the wire BEFORE the tab exists, so
// the hub's replay lands it on the right node. Asserting that against
// a real WS connection is the closest we get to the hub's own replay.
test.describe('cross-app send / open (NodeDetail)', () => {
  async function bootHubServed(page) {
    await page.addInitScript((data) => {
      window.__RTL_BUDDY_VIEW_DATA__ = data
      // What the hub injects when it serves the bundle: the WS
      // address, plus one data-presence global per sibling app.
      window.__RTL_BUDDY_HUB__ = '127.0.0.1:8399'
      window.__RTL_BUDDY_GRAPH_URL__ = '/graph.json'
      window.__RTL_BUDDY_COV_URL__ = '/cov.json'
      window.__opened__ = []
      window.open = (url, target, features) => {
        window.__opened__.push([String(url), String(target), String(features)])
      }
    }, PAYLOAD)
    await page.goto('/')
    await expect(page.locator('g.node').first()).toBeVisible({ timeout: 30_000 })
  }

  test('send → graph pushes a graph_focus and opens nothing', async ({ page }) => {
    const mock = await installMockHub(page)
    await bootHubServed(page)
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })

    // The mock welcome advertises view/wave/src — no graph peer — so
    // "send" must be refused until one joins.
    await expect(page.locator('.send-graph')).toBeHidden()
    await page.locator('g.node').first().click()
    await expect(page.locator('.send-graph')).toBeDisabled()

    mock.sendFromMock({
      v: 1, id: uuid(), origin: 'graph', kind: 'event',
      type: 'peer_joined', payload: {},
    })
    await expect(page.locator('.send-graph')).toBeEnabled({ timeout: 5_000 })
    await page.locator('.send-graph').click()

    await expect.poll(() => mock.recv.filter((e) => e.type === 'graph_focus').length, {
      timeout: 5_000,
    }).toBe(1)
    const focus = mock.recv.find((e) => e.type === 'graph_focus')
    expect(focus.origin).toBe('view')
    expect(focus.kind).toBe('event')
    expect(focus.payload.node).toMatch(/^module:/)
    expect(await page.evaluate(() => window.__opened__)).toEqual([])
  })

  test('send → coverage emits cov_focus and opens nothing', async ({ page }) => {
    // The row is sends-only — opening an app fresh is the top bar's job.
    const mock = await installMockHub(page)
    await bootHubServed(page)
    await expect(page.locator('.hub-status')).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })
    await page.locator('g.node').first().click()

    // No cov peer in the mock welcome — let one join so send enables.
    mock.sendFromMock({
      v: 1, id: uuid(), origin: 'cov', kind: 'event',
      type: 'peer_joined', payload: {},
    })
    await expect(page.locator('.send-cov')).toBeEnabled({ timeout: 5_000 })
    await page.locator('.send-cov').click()
    await expect.poll(() => mock.recv.filter((e) => e.type === 'cov_focus').length, {
      timeout: 5_000,
    }).toBe(1)
    expect(mock.recv.find((e) => e.type === 'cov_focus').payload.target).toMatch(/^module:/)
    expect(await page.evaluate(() => window.__opened__)).toEqual([])
  })
})
