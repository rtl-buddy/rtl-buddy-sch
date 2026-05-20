// Hub composable — Phase 10d (rtl-buddy-view#23).
//
// Replaces the Phase 5 stub with a live WebSocket client to
// rtl-buddy-hub's /ws endpoint on the SPA's own origin. The hub
// injects window.__RTL_BUDDY_HUB__ when it serves the bundle
// (rtl-buddy/rtl_buddy#121), so the SPA never needs to discover the
// address itself — same-origin /ws is the contract.
//
// Public surface is the small object useHub() returns:
//   - state: 'disconnected' | 'connecting' | 'ready' | 'error'
//   - peers: ref<string[]>          — registered_clients from welcome
//   - serverVersion: ref<string|null>
//   - lastError: ref<{code,message,at}|null>
//   - lastClick: ref<Node|null>     — Phase 5 surface, kept for compat
//   - notifyClick(node)             — primary action: send selection_changed
//                                     when live; fall back to opening
//                                     node.link in offline mode.
//
// Everything else (the connection lifecycle, the protocol dispatch)
// is module-scoped state so the singleton survives Vue remounts and
// HMR. Two non-default arguments to initHub() exist for testing only:
// a custom WebSocket factory and a store handle (so unit tests can
// run without window.location and without a Pinia app).

import { ref } from 'vue'

const PROTOCOL_VERSION = 1
const CLIENT_VERSION = '0.1.0'
const RECONNECT_INITIAL_MS = 500
const RECONNECT_MAX_MS = 15000
const RECONNECT_FACTOR = 1.8

const state = ref('disconnected')
const peers = ref([])
const serverVersion = ref(null)
const lastError = ref(null)
const lastClick = ref(null)

let _socket = null
let _reconnectTimer = null
let _reconnectDelay = RECONNECT_INITIAL_MS
let _autoReconnect = true
let _wsFactory = (url) => new WebSocket(url)
let _store = null
let _initialised = false

function makeId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  // Fallback for older test environments without crypto.randomUUID.
  const hex = '0123456789abcdef'
  let s = ''
  for (let i = 0; i < 36; i++) {
    if (i === 8 || i === 13 || i === 18 || i === 23) s += '-'
    else if (i === 14) s += '4'
    else if (i === 19) s += hex[(Math.random() * 4) | 8]
    else s += hex[(Math.random() * 16) | 0]
  }
  return s
}

function defaultWsUrl() {
  if (typeof window === 'undefined' || !window.location) return null
  const loc = window.location
  const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${loc.host}/ws`
}

function clearReconnectTimer() {
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer)
    _reconnectTimer = null
  }
}

function scheduleReconnect() {
  if (!_autoReconnect) return
  clearReconnectTimer()
  _reconnectTimer = setTimeout(connect, _reconnectDelay)
  _reconnectDelay = Math.min(_reconnectDelay * RECONNECT_FACTOR, RECONNECT_MAX_MS)
}

function sendEnvelope(env) {
  if (!_socket || _socket.readyState !== 1 /* OPEN */) return false
  try {
    _socket.send(JSON.stringify(env))
    return true
  } catch {
    return false
  }
}

function sendHello() {
  sendEnvelope({
    v: PROTOCOL_VERSION,
    id: makeId(),
    origin: 'view',
    kind: 'request',
    type: 'hello',
    payload: {
      client: 'view',
      version: CLIENT_VERSION,
      capabilities: [
        'selection_changed',
        'cursor_time_changed',
        'scope_changed',
        'diagnostics_set',
      ],
    },
  })
}

// Apply one decoded envelope. Exported for unit tests so we can
// exercise dispatch without standing up a WebSocket. The hub
// already suppresses echo-back by origin class, but we add a
// belt-and-suspenders guard so a misbehaving server can't make the
// viewer chase its own tail.
function applyEnvelope(env) {
  if (!env || typeof env !== 'object') return
  if (env.v !== PROTOCOL_VERSION || typeof env.type !== 'string') return

  switch (env.type) {
    case 'welcome': {
      const p = env.payload || {}
      serverVersion.value = typeof p.server_version === 'string' ? p.server_version : null
      peers.value = Array.isArray(p.registered_clients)
        ? p.registered_clients.slice()
        : []
      state.value = 'ready'
      break
    }

    case 'cursor_time_changed': {
      if (env.origin === 'view') break
      const t = env.payload?.t_fs
      if (typeof t === 'string') _store?.applyHubCursorTime(t)
      break
    }

    case 'selection_changed': {
      if (env.origin === 'view') break
      const ip = env.payload?.instance_path
      const id = Array.isArray(ip) ? ip[0] : ip
      if (typeof id === 'string' && id.length > 0) {
        _store?.applyHubSelection(id)
      }
      break
    }

    case 'scope_changed': {
      if (env.origin === 'view') break
      _store?.applyHubScope(env.payload || {})
      break
    }

    case 'diagnostics_set': {
      const p = env.payload || {}
      if (typeof p.source === 'string' && p.source.length > 0) {
        const items = Array.isArray(p.items) ? p.items : []
        _store?.applyDiagnostics(p.source, items)
      }
      break
    }

    case 'error': {
      const p = env.payload || {}
      const err = {
        code: typeof p.code === 'string' ? p.code : 'unknown',
        message: typeof p.message === 'string' ? p.message : '(no message)',
        at: Date.now(),
      }
      lastError.value = err
      _store?.applyHubError(err)
      break
    }

    case 'bye': {
      // The hub builds `bye` envelopes with `origin` set to the
      // leaving peer (`_bye_envelope` in hub/server.py), so we use
      // that to drop the peer from the visible list rather than
      // waiting for the next welcome (which never fires for an
      // already-established SPA session — peer lists would otherwise
      // go stale every time nvim quits or `rb wave` exits).
      if (typeof env.origin === 'string' && env.origin && env.origin !== 'cli') {
        peers.value = peers.value.filter((p) => p !== env.origin)
      }
      break
    }

    case 'peer_joined': {
      // Symmetric to `bye`: the joining peer's origin is in
      // env.origin, payload is empty. Without this case the popover
      // would never paint a green dot for any adapter that connects
      // *after* the SPA's own welcome — the welcome's
      // registered_clients snapshot is the only other source of the
      // peers list, and it only fires once per session.
      if (typeof env.origin === 'string' && env.origin && env.origin !== 'cli') {
        if (!peers.value.includes(env.origin)) {
          peers.value = [...peers.value, env.origin]
        }
      }
      break
    }

    default:
      // Unknown types are silently dropped (protocol §11).
      break
  }
}

function onOpen() {
  _reconnectDelay = RECONNECT_INITIAL_MS
  state.value = 'connecting'
  sendHello()
  // We stay in 'connecting' until the welcome flips us to 'ready'.
}

function onMessage(ev) {
  let payload
  try {
    payload = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data))
  } catch {
    return
  }
  applyEnvelope(payload)
}

function onClose() {
  state.value = 'disconnected'
  peers.value = []
  serverVersion.value = null
  _socket = null
  scheduleReconnect()
}

function onSocketError() {
  // The 'error' state is reserved for hub-side `error` envelopes;
  // socket-level failures land as a disconnected state via the
  // matching 'close' event the browser fires next.
}

function connect() {
  if (typeof window === 'undefined') return
  const url = defaultWsUrl()
  if (!url) return
  if (_socket && (_socket.readyState === 0 || _socket.readyState === 1)) {
    return
  }
  clearReconnectTimer()
  state.value = 'connecting'
  try {
    _socket = _wsFactory(url)
  } catch {
    _socket = null
    state.value = 'disconnected'
    scheduleReconnect()
    return
  }
  _socket.onopen = onOpen
  _socket.onmessage = onMessage
  _socket.onclose = onClose
  _socket.onerror = onSocketError
}

function disconnect() {
  _autoReconnect = false
  clearReconnectTimer()
  if (_socket) {
    try { _socket.close() } catch { /* ignore */ }
    _socket = null
  }
  state.value = 'disconnected'
  peers.value = []
  serverVersion.value = null
}

// Initialise the hub composable. Idempotent — calling twice is safe
// (the second call refreshes the store/factory and reconnects only
// if we're not already connected).
export function initHub({ store, wsFactory } = {}) {
  if (store) _store = store
  if (wsFactory) _wsFactory = wsFactory
  _autoReconnect = true
  if (!_initialised) {
    _initialised = true
    connect()
  } else if (!_socket) {
    connect()
  }
}

export function useHub() {
  return {
    state,
    peers,
    serverVersion,
    lastError,
    lastClick,
    notifyClick(node) {
      lastClick.value = node || null
      if (state.value === 'ready' && node && typeof node.id === 'string') {
        sendEnvelope({
          v: PROTOCOL_VERSION,
          id: makeId(),
          origin: 'view',
          kind: 'event',
          type: 'selection_changed',
          payload: { instance_path: node.id },
        })
        return
      }
      // Offline fallback: dispatch the source URI directly so a
      // click is never a no-op when the hub isn't around.
      if (node && node.link && typeof window !== 'undefined') {
        try { window.open(node.link, '_blank') } catch { /* ignore */ }
      }
    },
    disconnect,
    reconnect() {
      _autoReconnect = true
      _reconnectDelay = RECONNECT_INITIAL_MS
      connect()
    },
  }
}

// Test-only surface: lets unit tests inject envelopes without
// touching the WebSocket layer, and reset module state between
// cases.
export const _testing = {
  applyEnvelope,
  reset() {
    clearReconnectTimer()
    if (_socket) {
      try { _socket.close() } catch { /* ignore */ }
      _socket = null
    }
    state.value = 'disconnected'
    peers.value = []
    serverVersion.value = null
    lastError.value = null
    lastClick.value = null
    _autoReconnect = true
    _reconnectDelay = RECONNECT_INITIAL_MS
    _initialised = false
    _store = null
    _wsFactory = (url) => new WebSocket(url)
  },
  setStore(store) { _store = store },
  setWsFactory(factory) { _wsFactory = factory },
  getSocket() { return _socket },
  connect,
}
