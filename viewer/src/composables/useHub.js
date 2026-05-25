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

import { captureGraphImage } from '../capture.js'

const PROTOCOL_VERSION = 1
const CLIENT_VERSION = '0.1.0'
const RECONNECT_INITIAL_MS = 500
const RECONNECT_MAX_MS = 15000
const RECONNECT_FACTOR = 1.8
// Coalesce wave_values_changed bursts. Surfer cursor scrubbing can
// fire at ~60 Hz; the renderer doesn't need to repaint that fast,
// and the store mutation is the dominant cost. We collapse all
// envelopes received within ``WAVE_VALUES_FLUSH_MS`` into a single
// store write that applies the LATEST t_fs and the merged value set
// (latest-wins per signal). This gives ~30 Hz worst-case redraw and
// is the throttling layer #24's acceptance criteria asks for.
const WAVE_VALUES_FLUSH_MS = 33
// How long the disambiguation popover stays visible after a multi-
// match ``selection_changed`` lands. The smallest-range default
// (``instance_path[0]``) is applied immediately so the canvas reacts;
// the picker just gives the user a window to override before it
// auto-dismisses. 4s is short enough not to clutter the toolbar but
// long enough to read a handful of paths and click one.
const SELECTION_PICKER_AUTODISMISS_MS = 4000

const state = ref('disconnected')
const peers = ref([])
const serverVersion = ref(null)
const lastError = ref(null)
const lastClick = ref(null)
// When the hub kicks us with code=superseded, we know another
// browser tab took over the view slot — fighting to reconnect
// would just kick the new tab back. Stays true until the user
// explicitly calls reconnect() (e.g. clicks a "take back" button).
const superseded = ref(false)
// Latches the first time we see a ``welcome``. Drives the
// "Reconnecting…" banner: a fresh tab that never reached `ready`
// shouldn't show the banner (it's just connecting for the first
// time), but a tab whose hub dropped mid-session should. Reset on
// explicit ``disconnect()`` so a user-initiated stop doesn't keep
// showing the banner.
const wasEverReady = ref(false)

let _socket = null
let _reconnectTimer = null
let _reconnectDelay = RECONNECT_INITIAL_MS
let _autoReconnect = true
let _wsFactory = (url) => new WebSocket(url)
let _store = null
let _initialised = false
// Coalescing buffer for wave_values_changed. ``_waveTimer`` is the
// pending setTimeout (null when no flush is queued); ``_wavePending``
// holds the merged payload — t_fs is overwritten each receive (the
// latest sample wins), and values is a Map keyed by
// "${wave_scope}.${signal}" so multiple samples of the same signal
// in a burst collapse to the most recent literal.
let _waveTimer = null
let _wavePending = null
// Track whether the *next* hello should ask the hub to evict any
// existing view registration. Set when a prior hello got an
// "already registered" error so the retry takes over.
let _pendingTakeover = false
// Auto-dismiss timer for the disambiguation popover. Lives on the
// composable singleton (not in the component) so a Vue remount mid-
// timeout doesn't strand the picker open.
let _pickerDismissTimer = null

function clearPickerDismissTimer() {
  if (_pickerDismissTimer) {
    clearTimeout(_pickerDismissTimer)
    _pickerDismissTimer = null
  }
}

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

function flushWaveValues() {
  _waveTimer = null
  const pending = _wavePending
  _wavePending = null
  if (!pending) return
  const values = []
  for (const [key, value] of pending.values) {
    const idx = key.indexOf('.')
    // Key format guards in queueWaveValues guarantee a dot is present
    // and not at position 0, so this slice is always well-formed.
    values.push({
      wave_scope: key.slice(0, idx),
      signal: key.slice(idx + 1),
      value,
    })
  }
  _store?.applyWaveValues({ t_fs: pending.t_fs, values })
}

function queueWaveValues(payload) {
  if (!payload || typeof payload !== 'object') return
  const t = typeof payload.t_fs === 'string' ? payload.t_fs : null
  const incoming = Array.isArray(payload.values) ? payload.values : []
  if (!_wavePending) _wavePending = { t_fs: t, values: new Map() }
  if (t !== null) _wavePending.t_fs = t
  for (const v of incoming) {
    if (
      !v ||
      typeof v.wave_scope !== 'string' || v.wave_scope.length === 0 ||
      typeof v.signal !== 'string' || v.signal.length === 0 ||
      typeof v.value !== 'string'
    ) continue
    _wavePending.values.set(`${v.wave_scope}.${v.signal}`, v.value)
  }
  if (_waveTimer === null) {
    _waveTimer = setTimeout(flushWaveValues, WAVE_VALUES_FLUSH_MS)
  }
}

function sendHello() {
  const payload = {
    client: 'view',
    version: CLIENT_VERSION,
    capabilities: [
      'selection_changed',
      'cursor_time_changed',
      'scope_changed',
      'signal_selected',
      'wave_values_changed',
      'diagnostics_set',
    ],
  }
  // ``takeover`` is opt-in per-hello: the hub kicks any pre-
  // existing view registration and welcomes us instead. Set only
  // after a prior hello failed with "already registered" so the
  // first hello stays polite for the common case (no other tab
  // open) but a stale tab can't permanently block us.
  if (_pendingTakeover) payload.takeover = true
  sendEnvelope({
    v: PROTOCOL_VERSION,
    id: makeId(),
    origin: 'view',
    kind: 'request',
    type: 'hello',
    payload,
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
      // Latch: we made it to ``ready`` at least once this session.
      // The reconnecting banner uses this to distinguish "first
      // connect in progress" (no banner) from "lost the hub mid-
      // session" (banner).
      wasEverReady.value = true
      // Hello accepted — clear the takeover-retry flag so the next
      // reconnect starts polite again.
      _pendingTakeover = false
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
      if (Array.isArray(ip) && ip.length > 1) {
        // Multi-match: smallest-range default ([0]) is applied
        // immediately so the canvas reacts in the common case, but
        // we also surface the picker so the user can override if
        // the resolver's tie-break picked the wrong sibling
        // (rtl-buddy-view#55).
        _store?.presentSelectionCandidates(ip)
        clearPickerDismissTimer()
        _pickerDismissTimer = setTimeout(() => {
          _store?.dismissSelectionCandidates()
          _pickerDismissTimer = null
        }, SELECTION_PICKER_AUTODISMISS_MS)
        break
      }
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

    case 'wave_values_changed': {
      // Origin filter: the viewer never produces wave_values_changed,
      // so anything tagged view is a misbehaving peer — drop it. The
      // throttle/coalesce layer happens here, not in the store, so
      // a 60-Hz scrub stays in JS land until the next animation
      // window opens.
      if (env.origin === 'view') break
      queueWaveValues(env.payload || {})
      break
    }

    case 'signal_selected': {
      if (env.origin === 'view') break
      _store?.applySignalSelected(env.payload || {})
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
      // Hub kicked us out because a newer view tab took the slot.
      // Stop auto-reconnect (we'd just keep losing the race) and
      // flip the ``superseded`` flag so the UI can surface a
      // banner. ``reconnect()`` clears it if the user asks to
      // come back.
      if (err.code === 'superseded') {
        superseded.value = true
        _autoReconnect = false
        clearReconnectTimer()
        if (_socket) {
          try { _socket.close() } catch { /* ignore */ }
        }
        break
      }
      // First hello refused because the slot is in use. Retry
      // once with ``takeover=true`` so the new tab wins. We don't
      // loop on this — if takeover ALSO fails (e.g. due to a
      // different cause), we let it surface as an ordinary error
      // and stop.
      if (err.code === 'not_connected' && !_pendingTakeover) {
        const msg = err.message || ''
        if (/already registered/i.test(msg)) {
          _pendingTakeover = true
          sendHello()
        }
      }
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

    case 'view_capture': {
      // Hub-routed request from a peer (typically ``rb hub send
      // capture`` driven by the CLI): rasterise the current graph
      // SVG and reply with base64 bytes. Graph-only — surrounding
      // panels are not in scope for v1.
      if (env.kind !== 'request') break
      handleViewCapture(env)
      break
    }

    case 'view_overlay_set': {
      // Hub-routed request: flip an overlay's enabled state. Lets
      // remote drivers (LLMs over `rb hub send overlay`, nvim, CI)
      // present an issue by toggling the right overlay layer rather
      // than clicking the panel in the SPA.
      if (env.kind !== 'request') break
      const p = env.payload || {}
      const name = typeof p.name === 'string' ? p.name : ''
      const enabled = p.enabled === true
      if (name) _store?.setOverlayEnabled(name, enabled)
      sendEnvelope({
        v: PROTOCOL_VERSION,
        id: env.id,
        origin: 'view',
        kind: 'response',
        type: 'view_overlay_set',
        payload: { ok: !!name },
      })
      break
    }

    case 'view_changed': {
      // Hub-driven model switch (rtl_buddy#174). Forwarded to the
      // store, which dedupes against ``activeModel`` so a switch we
      // initiated ourselves doesn't trigger a duplicate refetch
      // (the hub broadcasts to every WS peer including the one
      // whose HTTP request caused the change — see #174 close-out).
      _store?.applyViewChanged(env.payload || {})
      break
    }

    default:
      // Unknown types are silently dropped (protocol §11).
      break
  }
}

async function handleViewCapture(env) {
  // Response uses the request ``id`` per protocol §4 — that is the
  // correlation key the hub uses to route the answer back to the
  // requester. ``in_reply_to`` is the conceptual name in the Python
  // helpers; on the wire it's just ``id``.
  const reqId = env.id
  const payload = env.payload || {}
  const format = payload.format === 'svg' ? 'svg' : 'png'
  const scale = Number.isFinite(payload.scale) && payload.scale > 0 ? payload.scale : 1
  try {
    const result = await captureGraphImage(format, { scale })
    sendEnvelope({
      v: PROTOCOL_VERSION,
      id: reqId,
      origin: 'view',
      kind: 'response',
      type: 'view_capture',
      payload: result,
    })
  } catch (err) {
    sendEnvelope({
      v: PROTOCOL_VERSION,
      id: reqId,
      origin: 'view',
      kind: 'error',
      type: 'error',
      payload: {
        code: 'bad_request',
        message: String((err && err.message) || err || 'capture failed'),
      },
    })
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
  // User-initiated stop — don't show the reconnecting banner. The
  // socket close handler would otherwise leave ``wasEverReady`` set
  // and the banner would linger on a tab the user explicitly took
  // offline.
  wasEverReady.value = false
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
    /**
     * Ask the hub to open a node's source location in the user's
     * editor. Sends an ``open_source`` REQUEST envelope which the
     * hub routes to any registered editor adapter (rtl-buddy-nvim
     * implements it via ``RtlBuddyOpen``). Falls back to dispatching
     * the ``rtlbuddy://`` URI through the OS when the hub send
     * fails — covers ``state === 'disconnected'``, transient
     * reconnect windows where ``_socket.readyState !== 1``, AND
     * the case where the envelope just doesn't get sent — so the
     * action is never silently a no-op.
     *
     * Returns true when the request was sent or the URI was
     * dispatched, false if there's no source info to act on at
     * all.
     */
    requestOpenSource(node) {
      if (!node) return false
      // Prefer the structured source block when available — the
      // ``node.link`` URI carries the same info but stringly-typed,
      // and the hub schema wants integers for line / col.
      const src = node.source || null
      if (src && typeof src.file === 'string') {
        const sent = sendEnvelope({
          v: PROTOCOL_VERSION,
          id: makeId(),
          origin: 'view',
          kind: 'request',
          type: 'open_source',
          payload: {
            file: src.file,
            line: typeof src.start_line === 'number' ? src.start_line : 1,
            col: typeof src.start_column === 'number' ? src.start_column : 1,
          },
        })
        if (sent) return true
      }
      // Fallback (hub down, reconnecting, or no structured source
      // block): dispatch the ``rtlbuddy://`` URI through the OS so
      // the click still has an effect.
      if (node.link && typeof window !== 'undefined') {
        try { window.open(node.link, '_blank') } catch { /* ignore */ }
        return true
      }
      return false
    },
    disconnect,
    superseded,
    wasEverReady,
    /**
     * Lock the user's pick from the multi-match disambiguation popover
     * (rtl-buddy-view#55). Updates the store (selection + clears the
     * candidate list) AND broadcasts a ``selection_changed`` envelope
     * from origin=view so the other peers (nvim, wave) lock onto the
     * same path. Cancels the auto-dismiss timer so a fast double-pick
     * doesn't get clobbered by a still-pending timeout.
     */
    chooseSelectionCandidate(path) {
      if (typeof path !== 'string' || path.length === 0) return
      clearPickerDismissTimer()
      _store?.chooseSelectionCandidate(path)
      sendEnvelope({
        v: PROTOCOL_VERSION,
        id: makeId(),
        origin: 'view',
        kind: 'event',
        type: 'selection_changed',
        payload: { instance_path: path },
      })
    },
    /**
     * Dismiss the disambiguation popover without changing the
     * selection. Used by the close button on the popover, and on
     * unmount so a dangling timer doesn't keep firing into a
     * detached store.
     */
    dismissSelectionCandidates() {
      clearPickerDismissTimer()
      _store?.dismissSelectionCandidates()
    },
    /**
     * Reconnect to the hub, optionally asking it to evict any
     * existing registration in the view slot. ``takeover=true`` is
     * what the "Take back" button passes when a previous tab
     * superseded us — without it the hub would refuse the
     * connection and we'd just disconnect again.
     */
    reconnect(opts = {}) {
      const takeover = opts.takeover === true || superseded.value
      _autoReconnect = true
      _reconnectDelay = RECONNECT_INITIAL_MS
      superseded.value = false
      _pendingTakeover = takeover
      connect()
    },
  }
}

// Test-only surface: lets unit tests inject envelopes without
// touching the WebSocket layer, and reset module state between
// cases.
export const _testing = {
  applyEnvelope,
  flushWaveValues,
  reset() {
    clearReconnectTimer()
    clearPickerDismissTimer()
    if (_socket) {
      try { _socket.close() } catch { /* ignore */ }
      _socket = null
    }
    if (_waveTimer !== null) {
      clearTimeout(_waveTimer)
      _waveTimer = null
    }
    _wavePending = null
    state.value = 'disconnected'
    peers.value = []
    serverVersion.value = null
    lastError.value = null
    lastClick.value = null
    superseded.value = false
    wasEverReady.value = false
    _autoReconnect = true
    _reconnectDelay = RECONNECT_INITIAL_MS
    _initialised = false
    _store = null
    _pendingTakeover = false
    _wsFactory = (url) => new WebSocket(url)
  },
  // Expose the constant so tests / docs can reference the canonical
  // auto-dismiss window without duplicating the number.
  SELECTION_PICKER_AUTODISMISS_MS,
  setStore(store) { _store = store },
  setWsFactory(factory) { _wsFactory = factory },
  getSocket() { return _socket },
  connect,
}
