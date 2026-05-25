# Live waveform overlay (Phase 9)

The wave overlay paints SystemVerilog literal values next to port
pins on the schematic, sourced from a running simulation via the
rtl-buddy hub + surfer. Drag the cursor in surfer; the SPA's badges
re-tint within ~33 ms.

This page is the operator's guide — what each leg of the cascade
does, how to start a live session, how to verify each piece in
isolation when something goes quiet.

## Architecture

```
surfer (--wcp-initiate)         rtl-buddy-hub               viewer (SPA)
   │                                  │                          │
   │ cursor_moved {timestamp} ───────►│                          │
   │                                  │ cursor_time_changed ───►│  store.hubCursorTimeFs
   │                                  │ {t_fs, origin: wave}     │
   │                                  │                          │
   │ ◄── query_variable_values ──── (wave bridge, in background)│
   │      {variables, no-timestamp}   │                          │
   │ response: per-bit values ────►   │                          │
   │                                  │ wave_values_changed ───►│  store.waveValuesByKey
   │                                  │ {t_fs, values[]}         │  (coalesced @33 ms)
   │                                  │                          │  ▼
   │                                  │                          │  applyOverlays('wave')
   │                                  │                          │  → port badges repainted
```

Three repos, three PRs landed the wiring:

- [surfer rtl-buddy fork](https://github.com/rtl-buddy/surfer) —
  `query_variable_values` WCP command samples one-or-more variables
  at the cursor (or a chosen timestamp) and returns per-bit values.
- [rtl_buddy](https://github.com/rtl-buddy/rtl_buddy) — the
  hub-bridge in `rb wave` tracks added variables, queries surfer on
  each `cursor_moved`, and broadcasts `wave_values_changed` envelopes
  on the bus.
- [rtl-buddy-view](https://github.com/rtl-buddy/rtl-buddy-view) —
  `useHub` coalesces wave-value bursts at ~33 ms; `wave.js` overlay
  paints port badges; `store.waveValuesByKey` is the last-known-value
  cache.

## Quick start

In three terminals:

```bash
# 1. hub + serve viewer
rb hub start --serve-viewer        # prints http://127.0.0.1:<port>/

# 2. surfer with WCP outward connection
surfer --wcp-initiate <port>       # uses the hub's wave-bridge port

# 3. wave bridge (this is what advertises wave_values_changed)
rb wave my_test.vcd                # picks up RTL_BUDDY_HUB or hub.json
```

Open `http://127.0.0.1:<hub-port>/` in a browser. Click a flop on the
schematic — the bridge adds its `Q` signal to surfer (badge appears
empty at first). Drag surfer's cursor — within ~33 ms the badge fills
with the SV literal of the sampled value.

## How values join to ports

The viewer's wave overlay resolves each schematic port to a wave-side
`(wave_scope, signal)` pair using two routes, in order:

1. **Static snapshot** — if `node.overlays.wave.ports[]` is present
   in the loaded `view.json`, its `{name, value}` entries paint
   immediately at load time (no hub needed). Phase 8 (#21) producer.
2. **Live cache** — keyed by `${wave_scope}.${signal}`, sourced
   from `wave_values_changed` events. Live always wins on collision.

Default join convention:

- `wave_scope` = the instance path (`node.id`).
- `signal` = `port.name`.

Producers that need a different mapping (e.g. testbench wrapping)
can override per-port via `node.ports[].wave_scope` and
`node.ports[].signal`.

## Loop prevention

The viewer never re-emits a value-bearing event it just received.
`useHub.applyEnvelope` short-circuits any inbound envelope whose
`origin === 'view'`, and the wave-values reducer is a store-only
write (no envelope dispatch). The five-step cycle from the issue
spec — viewer click → hub → WCP `add_variables` → surfer
`scope_changed` → hub broadcast → viewer highlight → STOP —
terminates by construction.

## Hub disconnect

When the hub goes away mid-session:

- `store.waveValuesByKey` is **not cleared** — badges freeze at the
  last-known sample so the schematic stays readable.
- `HubStatus.vue` shows a "Hub disconnected — reconnecting…" banner
  (gated by `useHub.wasEverReady`, which latches on the first
  `welcome`).
- Background reconnect runs with exponential backoff (500 ms → 15 s).
- On the next `welcome` the bridge can re-track variables and resume
  the cascade.

A fresh tab that never reached `ready` does **not** show the
"reconnecting" banner — it's still on its first attempt.

## Troubleshooting

### Cursor moves in surfer, viewer's cursor marker moves, but badges don't update

Most common cause: the user hasn't added any variables to surfer yet.
The bridge only queries `query_variable_values` for variables it
knows are tracked (added via `wave_add_variables`).

- Click a flop in the schematic — that triggers
  `selection_changed{view}` → hub forwards to bridge → bridge calls
  WCP `add_variables` with the flop's `Q` signal. Badge should
  populate on the next cursor move.
- Check the hub log for `wave.hub.query_timeout` — a value of 1.0 s
  is the default; persistent timeouts mean surfer is slow / hung.

### "Hub disconnected" banner stays up

The browser tab can't reach `/ws`. Causes:

- Hub process exited.
- Hub is bound to a port the browser can't reach (firewall, container
  port mapping).
- Reverse proxy in front of the hub mangling websocket upgrade.

`useHub` auto-reconnects with exponential backoff. The popover under
the "hub: …" pill has a manual **Reconnect** button.

### Badges show "??" or stay blank

`node.overlays.wave.ports[]` is empty AND the live cache has no
matching key. Verify the convention:

- The default mapping is `wave_scope = node.id` and
  `signal = port.name`. If surfer's hierarchy uses a different prefix
  (e.g. `tb.dut.*` while view.json says `dut.*`), the join fails
  silently — values stream but no port matches them.
- Either add a `tb_prefix` mapping at the hub (planned) or override
  per-port via `wave_scope` / `signal` in the producer.

### Cursor scrubbing causes browser jank

The viewer already coalesces wave-value bursts on a 33 ms timer
(`WAVE_VALUES_FLUSH_MS` in `useHub.js`). The hub-side bridge also
runs a single-in-flight gate so successive cursor_moveds during a
fast scrub don't pile up WCP round-trips. If you still see jank,
profile with the browser devtools — overlay layout is by design
NOT triggering a viz.js re-run, only badge text updates.

## Verifying each leg in isolation

When something's wrong, narrow it down before opening an issue:

```bash
# 1. Can surfer + hub talk?
gh repo view rtl-buddy/surfer       # confirm fork is reachable
rb wave my_test.vcd                 # should not error on start

# 2. Is the hub broadcasting wave_values_changed?
rb hub state                        # snapshot — peers should include 'wave'
# Open browser devtools console at the SPA. In the Network tab, look
# at the /ws frame stream. On a cursor move you should see a
# wave_values_changed event from origin=wave.

# 3. Is the viewer subscribing?
# Devtools console:
#   useViewerStore().waveValuesByKey
# Should be non-empty after a cursor move with tracked variables.

# 4. Is the wave overlay rendering?
# In devtools, inspect a node group's <g> — it should have a
# child <text class="rb-wave-badge"> with one <tspan> per port=value.
```

## See also

- [view-json-v1.md](view-json-v1.md) — the locked schema for the
  per-node overlay container (`overlays.wave.ports[]`).
- [overlays.md](overlays.md) — third-party overlay authoring guide.
- [hub-protocol.md](hub-protocol.md) — the hub wire contract; lists
  `wave_values_changed` alongside the other state events.
- Phase 8 issue [#21](https://github.com/rtl-buddy/rtl-buddy-view/issues/21)
  — the offline producer for `node.overlays.wave.ports[]`.
- Phase 9 tracking issue
  [#24](https://github.com/rtl-buddy/rtl-buddy-view/issues/24) — this
  page is the user-facing close-out.
