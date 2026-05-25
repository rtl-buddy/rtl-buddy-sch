// Event-sync composable — Phase 3 of axi-profiler#16 (SPA↔notebook sync).
//
// Distinct channel from useHub.js. The hub's /ws is the rich
// envelope protocol used by rtl-buddy itself; /api/events/sync is a
// dumb pub/sub broker the hub added to relay opaque JSON between the
// SPA and a spawned marimo notebook (see rtl_buddy#208).
//
// Public surface:
//   - initEventSync({ store, socketFactory? })
//   - useEventSync() → { connected, publish(topic, data), on(topic, cb), off(topic, cb) }
//
// The dispatcher routes inbound messages by topic; subscribers
// register a callback. The broker on the hub already excludes the
// sender, but inbound messages with ``source === 'spa'`` are still
// dropped here as a defence-in-depth against future broker changes
// that would otherwise create a feedback loop.

import { ref } from 'vue'

const SOURCE = 'spa'
const RECONNECT_INITIAL_MS = 500
const RECONNECT_MAX_MS = 8000
const RECONNECT_FACTOR = 1.8

let _socket = null
let _socketFactory = null
let _store = null
let _reconnectTimer = null
let _reconnectDelay = RECONNECT_INITIAL_MS
let _disposed = false

const connected = ref(false)
// topic → Set<callback>. ``*`` matches every topic; used by tests.
const _subscribers = new Map()

function defaultSocketFactory() {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return new WebSocket(`${proto}//${window.location.host}/api/events/sync`)
}

function _emit(topic, env) {
  for (const cb of _subscribers.get(topic) ?? []) cb(env)
  for (const cb of _subscribers.get('*') ?? []) cb(env)
}

function _dispatch(env) {
  if (!env || typeof env !== 'object') return
  if (env.source === SOURCE) return
  if (typeof env.topic !== 'string') return
  if (env.topic === 'time-window' && _store) {
    _store.applyAxiPerfTimeWindow(env.data ?? null)
  }
  _emit(env.topic, env)
}

function _connect() {
  if (_disposed) return
  try {
    _socket = (_socketFactory ?? defaultSocketFactory)()
  } catch (err) {
    _scheduleReconnect()
    return
  }
  _socket.onopen = () => {
    connected.value = true
    _reconnectDelay = RECONNECT_INITIAL_MS
  }
  _socket.onmessage = (ev) => {
    try {
      _dispatch(JSON.parse(ev.data))
    } catch {
      /* malformed envelope — drop silently */
    }
  }
  _socket.onclose = () => {
    connected.value = false
    _socket = null
    _scheduleReconnect()
  }
  _socket.onerror = () => {
    // ``onclose`` follows; cleanup happens there.
  }
}

function _scheduleReconnect() {
  if (_disposed || _reconnectTimer) return
  const delay = _reconnectDelay
  _reconnectDelay = Math.min(_reconnectDelay * RECONNECT_FACTOR, RECONNECT_MAX_MS)
  _reconnectTimer = setTimeout(() => {
    _reconnectTimer = null
    _connect()
  }, delay)
}

export function initEventSync({ store = null, socketFactory = null } = {}) {
  if (store !== null) _store = store
  if (socketFactory !== null) _socketFactory = socketFactory
  _disposed = false
  _connect()
}

export function useEventSync() {
  return {
    connected,
    publish(topic, data) {
      if (!_socket || _socket.readyState !== 1) return false
      const env = { topic, data, source: SOURCE, ts: Date.now() }
      try {
        _socket.send(JSON.stringify(env))
        return true
      } catch {
        return false
      }
    },
    on(topic, cb) {
      let set = _subscribers.get(topic)
      if (!set) {
        set = new Set()
        _subscribers.set(topic, set)
      }
      set.add(cb)
    },
    off(topic, cb) {
      const set = _subscribers.get(topic)
      if (!set) return
      set.delete(cb)
      if (set.size === 0) _subscribers.delete(topic)
    },
  }
}

// Test-only surface. Mirrors useHub's _testing object.
export const _testing = {
  reset() {
    _disposed = true
    if (_reconnectTimer) {
      clearTimeout(_reconnectTimer)
      _reconnectTimer = null
    }
    if (_socket) {
      try {
        _socket.close()
      } catch {
        /* noop */
      }
    }
    _socket = null
    _socketFactory = null
    _store = null
    _reconnectDelay = RECONNECT_INITIAL_MS
    _subscribers.clear()
    connected.value = false
  },
  setStore(s) {
    _store = s
  },
  currentSocket() {
    return _socket
  },
  dispatch(env) {
    _dispatch(env)
  },
}
