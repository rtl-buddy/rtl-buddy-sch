// Store-side coverage for the "no view available" states
// (rtl-buddy-view#130).
//
// The SPA has to distinguish four situations that all look like "an
// empty screen" today, and it derives them entirely from what the hub
// puts on the wire:
//
//   1. nothing requested          — status stays 'idle'
//   2. hub up, no active model    — 409 {"error":{"kind":"no_active_model"}}
//   3. view generation failed     — 500 {"error":{"kind":"view_generation_failed", …}}
//   4. hub gone                   — WebSocket-level, covered in hub.spec.js
//
// plus the compatibility case that matters most in practice: an older
// hub that answers with a bare text/plain 500. That must degrade to
// the generic failure card, never to a crash or a blank screen.

import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { parseHubError, useViewerStore } from '../src/store.js'

const LOG_TAIL = [
  "error: cannot resolve interface port 'apb' on module apb_intf",
  'rtl-buddy-view exited with code 1',
]

function jsonError(kind, extra = {}) {
  return JSON.stringify({ error: { kind, message: `${kind} happened`, ...extra } })
}

function mockFetch(handler) {
  const original = window.fetch
  window.fetch = handler
  return () => {
    window.fetch = original
  }
}

function response({ ok = false, status = 500, statusText = '', body = '' }) {
  return {
    ok,
    status,
    statusText,
    text: async () => body,
    json: async () => JSON.parse(body),
  }
}

describe('parseHubError', () => {
  it('decodes a view_generation_failed envelope in full', () => {
    const parsed = parseHubError(
      JSON.stringify({
        error: {
          kind: 'view_generation_failed',
          model: 'apb_intf',
          message: 'rtl-buddy-view exited with code 1',
          log_path: '/w/artefacts/hier/apb_intf/hier.log',
          log_tail: LOG_TAIL,
        },
      }),
    )
    expect(parsed.kind).toBe('view_generation_failed')
    expect(parsed.model).toBe('apb_intf')
    expect(parsed.message).toBe('rtl-buddy-view exited with code 1')
    expect(parsed.logPath).toBe('/w/artefacts/hier/apb_intf/hier.log')
    expect(parsed.logTail).toEqual(LOG_TAIL)
  })

  it('returns null for a plain-text body (old hub) and for junk', () => {
    // The exact shape rtl-buddy-view#130 quotes from the demo.
    expect(
      parseHubError(
        'rb hub --model apb_intf: rtl-buddy-view exited with code 1; ' +
          'see .../artefacts/hier/apb_intf/hier.log for details.',
      ),
    ).toBeNull()
    expect(parseHubError('')).toBeNull()
    expect(parseHubError('{"error": "not an object"}')).toBeNull()
    // A JSON envelope we don't know how to render is treated as
    // unstructured too — better the generic card than a half-blank one.
    expect(parseHubError('{"error": {"kind": "teapot"}}')).toBeNull()
  })

  it('drops non-string log_tail entries', () => {
    const parsed = parseHubError(
      JSON.stringify({
        error: { kind: 'view_generation_failed', message: 'x', log_tail: ['a', 3, null, 'b'] },
      }),
    )
    expect(parsed.logTail).toEqual(['a', 'b'])
  })
})

describe('view.json failure contract (#130)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('state 3: a 500 view_generation_failed lands with log tail + path', async () => {
    const restore = mockFetch(async () =>
      response({
        status: 500,
        body: JSON.stringify({
          error: {
            kind: 'view_generation_failed',
            model: 'apb_intf',
            message: 'rtl-buddy-view exited with code 1',
            log_path: '/w/artefacts/hier/apb_intf/hier.log',
            log_tail: LOG_TAIL,
          },
        }),
      }),
    )
    try {
      const store = useViewerStore()
      const ok = await store.switchModel('apb_intf', { updateUrl: false })
      expect(ok).toBe(false)
      expect(store.status).toBe('error')
      expect(store.errorMeta.kind).toBe('view_generation_failed')
      expect(store.errorMeta.status).toBe(500)
      expect(store.errorMeta.url).toBe('/view.json?model=apb_intf')
      expect(store.errorMeta.logTail).toEqual(LOG_TAIL)
      expect(store.error).toBe('rtl-buddy-view exited with code 1')
      // A structured answer is proof of a #130-era hub.
      expect(store.hubDetected).toBe(true)
    } finally {
      restore()
    }
  })

  it('degrades a plain-text 500 (pre-#130 hub) to the generic card', async () => {
    const text =
      'rb hub --model apb_intf: rtl-buddy-view exited with code 1; ' +
      'see .../artefacts/hier/apb_intf/hier.log for details.'
    const restore = mockFetch(async () => response({ status: 500, body: text }))
    try {
      const store = useViewerStore()
      await store.switchModel('apb_intf', { updateUrl: false })
      expect(store.status).toBe('error')
      // No kind → the placeholder renders the generic reasons list and
      // quotes the body verbatim.
      expect(store.errorMeta.kind).toBeUndefined()
      expect(store.error).toBe(text)
      expect(store.hubDetected).toBe(false)
    } finally {
      restore()
    }
  })

  it('404 unknown_model is decoded and keeps the model name', async () => {
    const restore = mockFetch(async () =>
      response({ status: 404, body: jsonError('unknown_model', { model: 'ghost' }) }),
    )
    try {
      const store = useViewerStore()
      await store.switchModel('ghost', { updateUrl: false })
      expect(store.errorMeta.kind).toBe('unknown_model')
      expect(store.errorMeta.model).toBe('ghost')
    } finally {
      restore()
    }
  })

  it('retryLastLoad re-issues the failed request and can recover', async () => {
    let calls = 0
    const payload = {
      schema_version: '1.0',
      top: 'top',
      nodes: [
        { id: 'top', module: 'top', is_blackbox: false, parameters: {}, ports: [], overlays: {} },
      ],
      edges: [],
      overlays_present: [],
    }
    const restore = mockFetch(async () => {
      calls += 1
      if (calls === 1) {
        return response({
          status: 500,
          body: jsonError('view_generation_failed', { model: 'apb_intf' }),
        })
      }
      return response({ ok: true, status: 200, body: JSON.stringify(payload) })
    })
    try {
      const store = useViewerStore()
      await store.switchModel('apb_intf', { updateUrl: false })
      expect(store.status).toBe('error')
      const ok = await store.retryLastLoad()
      expect(ok).toBe(true)
      expect(calls).toBe(2)
      expect(store.status).toBe('ready')
      expect(store.errorMeta).toBeNull()
    } finally {
      restore()
    }
  })

  it('retryLastLoad is a no-op with nothing to retry', async () => {
    const store = useViewerStore()
    store.loadFromText('not json {')
    expect(store.status).toBe('error')
    expect(await store.retryLastLoad()).toBe(false)
  })
})

describe('bootstrap probe (#130 states 1 and 2)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.__RTL_BUDDY_VIEW_DATA__ = null
    window.__RTL_BUDDY_VIEW_URL__ = null
  })

  it('state 2: a 409 no_active_model surfaces the hub-connected placeholder', async () => {
    const seen = []
    const restore = mockFetch(async (url) => {
      seen.push(url)
      if (url === '/view.json') {
        return response({
          status: 409,
          body: JSON.stringify({
            error: {
              kind: 'no_active_model',
              message: 'no model is active on this hub',
              models_url: '/models',
            },
          }),
        })
      }
      return response({ ok: true, status: 200, body: '{"models": [], "active": null}' })
    })
    try {
      const store = useViewerStore()
      await store.bootstrap()
      expect(seen[0]).toBe('/view.json')
      expect(store.status).toBe('error')
      expect(store.errorMeta.kind).toBe('no_active_model')
      expect(store.errorMeta.modelsUrl).toBe('/models')
      expect(store.hubDetected).toBe(true)
      // Populating the picker is the whole point of this state.
      expect(seen).toContain('/models')
    } finally {
      restore()
    }
  })

  it('state 1: a static server handing back index.html leaves us idle', async () => {
    const restore = mockFetch(async () =>
      response({ ok: true, status: 200, body: '<!doctype html><html></html>' }),
    )
    try {
      const store = useViewerStore()
      await store.bootstrap()
      expect(store.status).toBe('idle')
      expect(store.hubDetected).toBe(false)
    } finally {
      restore()
    }
  })

  it('state 1: an old hub 500 with no envelope also leaves us idle', async () => {
    // We never asked for this view — dropping the user on a failure
    // page for a probe they didn't initiate would be worse than the
    // drop-a-file screen.
    const restore = mockFetch(async () =>
      response({ status: 500, body: 'rb hub: no model configured' }),
    )
    try {
      const store = useViewerStore()
      await store.bootstrap()
      expect(store.status).toBe('idle')
    } finally {
      restore()
    }
  })

  it('the probe installs a graph when the hub has one and injected nothing', async () => {
    const payload = {
      schema_version: '1.0',
      top: 'top',
      nodes: [
        { id: 'top', module: 'top', is_blackbox: false, parameters: {}, ports: [], overlays: {} },
      ],
      edges: [],
      overlays_present: [],
    }
    const restore = mockFetch(async (url) => {
      if (url === '/view.json') {
        return response({ ok: true, status: 200, body: JSON.stringify(payload) })
      }
      return response({ ok: true, status: 200, body: '{"models": [], "active": null}' })
    })
    try {
      const store = useViewerStore()
      await store.bootstrap()
      expect(store.status).toBe('ready')
      expect(store.graph.top).toBe('top')
      expect(store.hubDetected).toBe(true)
    } finally {
      restore()
    }
  })

  it('/models answering marks the hub as detected (state 1 wording)', async () => {
    const restore = mockFetch(async () =>
      response({ ok: true, status: 200, body: '{"models": [{"name": "a"}], "active": null}' }),
    )
    try {
      const store = useViewerStore()
      await store.loadAvailableModels()
      expect(store.hubDetected).toBe(true)
      expect(store.availableModels).toHaveLength(1)
    } finally {
      restore()
    }
  })
})
