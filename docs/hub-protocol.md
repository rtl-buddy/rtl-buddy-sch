# rtl-buddy-hub protocol v1

Wire contract between the three views of an RTL design — schematic
(rtl-buddy-view viewer), waveform (surfer via WCP), and source (nvim /
VS Code) — mediated by a single shared daemon, `rtl-buddy-hub`.

Three independent implementations (the daemon, the nvim plugin, the
viewer) are built against this spec; it is locked at v1 ahead of any
code so they stay coherent. Versioning is per-document, signaled by the
`v` field in the envelope (§2) and the `schema_version` in the JSON
Schema (`schemas/hub-protocol-v1.json`).

> Phase 10a (see `rtl-buddy/rtl-buddy-view#19`). No implementation in
> this document — daemon is Phase 10b, nvim plugin Phase 10c, viewer
> wiring Phase 10d.

## Table of contents

1. [Coordinate systems](#1-coordinate-systems)
2. [Message envelope](#2-message-envelope)
3. [Event catalog](#3-event-catalog)
4. [Lifecycle & discovery](#4-lifecycle--discovery)
5. [Config](#5-config)
6. [Loop prevention](#6-loop-prevention)
7. [Asymmetric selection collapse](#7-asymmetric-selection-collapse)
8. [Worked examples](#8-worked-examples)
9. [WCP mapping](#9-wcp-mapping)
10. [nvim mapping](#10-nvim-mapping)
11. [Capability negotiation](#11-capability-negotiation)
12. [Glossary](#12-glossary)

---

## 1. Coordinate systems

The hub's only intelligence is translating among three coordinate
systems plus a derived fourth (source). Every other piece of behavior
falls out of these four mappings.

| Name    | Example                          | Owner of the mapping                |
| ------- | -------------------------------- | ----------------------------------- |
| `view`  | `top.u_fifo.u_wr_ptr`            | `view.json` v1 from rtl-buddy-view  |
| `wave`  | `tb.dut.u_fifo.u_wr_ptr`         | surfer / WCP                        |
| `src`   | `{file, line, col}`              | view.json source anchors            |
| signal  | `top.u_fifo.wr_ptr_q` (a signal) | view.json edges + surfer            |

### Translation tables

#### view ↔ src

Trivial; every node in `view.json` carries `{file, line, col}` from the
extractor's source anchors. Round-trip is exact when the source has
not changed since extraction.

#### view ↔ wave

The hub strips a configurable testbench prefix (`tb_prefix`, see §5)
from the wave path; the remainder must match a `view` instance path
exactly.

- `wave="tb.dut.u_fifo.u_wr_ptr"`, `tb_prefix="tb.dut."` →
  `view="u_fifo.u_wr_ptr"`. The hub then prepends `view.json.top` if
  the result is not already rooted — yielding `top.u_fifo.u_wr_ptr`.
- A wave path that does not start with `tb_prefix`, or that strips to
  a name not present in `view.json`, produces an `error{code:
  "unresolvable"}` event (§3). The hub does not guess.

Optional per-instance `signal_aliases` in config (§5) cover legacy
post-rename testbenches; aliases are applied before the prefix strip.

#### wave ↔ src

Derived: wave → view via the prefix strip, view → src via the source
anchor. No independent storage.

#### signal → driver instance

Edges in `view.json` carry `port_pairs` from rtl-buddy-view's CST
extractor: `{port_name, net_expr_text, expr_kind}`. The hub walks the
edge graph to find the unique driver of a named signal. When the
signal name resolves to a port net (e.g. `wr_ptr_q`) that is driven by
multiple instances (a bus, a generate replication), the result is a
**list** of driver instance paths, not a single value — see §7.

---

## 2. Message envelope

Every message on the hub channel — regardless of `kind` or `type` —
shares one envelope:

```json
{
  "v": 1,
  "id": "9c3f8e5f-7d1b-4d3a-9a3b-1a2f5c8e7d3a",
  "origin": "view",
  "kind": "event",
  "type": "selection_changed",
  "payload": { "instance_path": "top.u_fifo.u_wr_ptr" }
}
```

| Field     | Type            | Meaning                                                                                                                |
| --------- | --------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `v`       | integer (= 1)   | Protocol major version. Mismatch → `error{code: "protocol_mismatch"}`. Minor additions to `type` are forward-compatible. |
| `id`      | string (uuid)   | Per-message UUID. Used for request/response correlation (§3) and for the loop-prevention dedupe window (§6).            |
| `origin`  | enum            | One of `view`, `wave`, `src`, `cli`, `notebook`, `graph`, `cov`. The loop-prevention discriminator (§6). Every client tags messages it produces. |
| `kind`    | enum            | One of `event`, `request`, `response`, `error`. See §3 for the per-`type` allowed kinds.                                 |
| `type`    | string          | The message type from the event catalog (§3) or one of the request/response/error type names.                            |
| `payload` | object \| null  | Type-specific. Shapes are defined in the JSON Schema (`schemas/hub-protocol-v1.json`).                                  |

The transport is line-delimited JSON over a TCP socket the hub listens
on; one JSON object per line, UTF-8. Clients connect, exchange
`hello`/`welcome` (§11), and then stream messages bidirectionally.

---

## 3. Event catalog

### State events (broadcast)

Broadcast events are delivered to every connected client *except* the
one whose `origin` matches the event's `origin` (see §6). `kind:
"event"`, no response expected.

| `type`                  | Direction       | Payload                                                                | Notes                                                                                                                            |
| ----------------------- | --------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `selection_changed`     | any → all       | `{ "instance_path": "top.u_fifo.u_wr_ptr" \| string[] }`                | "Selected instance" in any view. String for unambiguous selection; array when collapsed from a multi-driver signal (§7).         |
| `signal_selected`       | wave → all      | `{ "signal": "wr_ptr_q", "wave_scope": "tb.dut.u_fifo" }`               | A waveform signal selection. The hub augments the broadcast with a derived `selection_changed` of the driver instance path(s).   |
| `cursor_time_changed`   | wave → all      | `{ "t_fs": "0" }` (femtoseconds, decimal string, may be very large)    | Surfer cursor moved. Requires the WCP `cursor_set` event in the surfer fork (§9).                                                |
| `scope_changed`         | wave → all      | `{ "wave_scope": "tb.dut.u_fifo" }`                                    | Surfer scope navigation. Requires the WCP `scope_changed` event in the surfer fork (§9).                                         |
| `source_focused`        | src → all       | `{ "file": "rtl/fifo.sv", "line": 42, "col": 5 }`                      | nvim's explicit `:RtlBuddyShow` broadcast — not on every cursor move (would spam the bus). Paths are absolute.                  |
| `diagnostics_set`       | any → all       | `{ "source": "rtl-buddy-cdc", "items": [{file, line, severity, message, [instance_path], …}] }` | Full diagnostic set for the given `source`. Latest-writer-wins per source on the hub's cache. Empty `items` clears that source. Each item carries `file`+`line` for resolution and MAY carry an `instance_path` hint so consumers map directly to a view.json node without walking source ranges. |
| `graph_focus`           | any → all       | `{ "node": "test:verif/dma#smoke" }`                                   | Focus the design-knowledge-graph pane (§4.8) on one node of `artefacts/graph/graph.json`. `node` is a graph node id — `module:<name>`, `inst:<top>/<dot.path>`, `test:<suite>#<name>`, the vocabulary of `docs/graph-json-v1.md` (design tier) and rtl_buddy's `docs/concepts/graph.md` (config + binding tiers). A node the pane's loaded graph does not contain is a soft miss: the pane reports it and keeps its current focus, the same way an unknown overlay name is handled. The hub caches the last one and replays it on registration, so `rb hub send graph-focus NODE` before the tab is open still lands. |
| `cov_focus`             | any → all       | `{ "target": "file:rtl/fifo.sv", "metric": "branch", "line": 42, "item": "b3" }` | Focus the coverage pane (§4.9) on one target of the run's coverage model (`GET /cov.json`). `target` is prefixed — `file:<path>` (as `/cov.json` keys files; an absolute path is accepted), `module:<name>`, `test:<suite>#<name>` — and an unprefixed string is read as a file path. `metric` (`line`, `branch`, `toggle`, `expression`, `cover`), `line`, and `item` are optional narrowing hints; omitting `metric` leaves the pane's current selection alone, and `line` (1-based) applies to a `file:` target — or a `module:` target that resolves to a single file — and is ignored, not an error, for any other target. A target the pane's loaded model does not contain is a soft miss: the pane reports it and keeps its current focus, exactly like `graph_focus`. The hub caches the last one and replays it on registration, so `rb hub send cov-focus TARGET` before the tab is open still lands. |
| `view_changed`          | hub → all peers | `{ "model": "ip_dtnpu_dma", "models_file": "/abs/path/to/models.yaml", "view_url": "/view.json?model=ip_dtnpu_dma" }` | Broadcast by the hub (`origin: cli`) on every active-model change — driven by a SPA `?model=` switch on `GET /view.json`, or by future file-watch refresh. Consumers refetch model-scoped state.                                                                       |

### Request/response (point-to-point)

`kind: "request"` requires `kind: "response"` with matching `id`, or
`kind: "error"` with matching `id`. Default timeout 5 s; on timeout the
client SHOULD treat as `error{code: "timeout"}` locally and not retry
automatically.

| `type`                    | Direction          | Request payload                                  | Response payload                                              | Notes                                                                                          |
| ------------------------- | ------------------ | ------------------------------------------------ | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `open_source`             | hub → src          | `{ "file": "...", "line": 42, "col": 5 }`        | `{ "ok": true }`                                              | Open the file at the given anchor in the source editor.                                        |
| `wave_add_variables`      | hub → wave         | `{ "variables": ["tb.dut.u_fifo.wr_ptr_q"] }`    | `{ "ids": [17, 18], "not_found"?: ["tb.dut.bogus"] }`         | Maps onto WCP `add_variables` (§9). `not_found` lists input paths that didn't match a variable in the loaded waveform; omitted when empty so older clients keep the legacy shape. |
| `wave_set_scope`          | hub → wave         | `{ "wave_scope": "tb.dut.u_fifo" }`              | `{ "ok": true, "ids": [...], "not_found"?: ["tb.dut.bogus"] }` | Approximated via WCP `add_scope` in v1; see §9 for caveat. `not_found` carries the requested scope string if it doesn't exist in the waveform. |
| `wave_set_cursor`         | hub → wave         | `{ "t_fs": "12500000" }`                         | `{ "ok": true }`                                              | Maps onto WCP `set_cursor`.                                                                    |
| `wave_set_viewport`       | hub → wave         | `{ "t_fs": "12500000" }`                         | `{ "ok": true }`                                              | Pan surfer's viewport to center on the timestamp (zoom unchanged). Maps onto WCP `set_viewport_to`. |
| `wave_zoom_to_range`      | hub → wave         | `{ "start_fs": "0", "end_fs": "1000000000" }`    | `{ "ok": true }`                                              | Zoom + pan to fit `[start_fs, end_fs]`. Maps onto WCP `set_viewport_range`.                    |
| `wave_zoom_to_fit`        | hub → wave         | `{}`                                             | `{ "ok": true }`                                              | Zoom out to fit the whole waveform. Maps onto WCP `zoom_to_fit`.                               |
| `view_pan_to`             | hub → view         | `{ "instance_path": "top.u_fifo" }`              | `{ "ok": true }`                                              | Pan/center the viewer on the named instance.                                                   |
| `view_capture`            | hub → view         | `{ "format"?: "png"\|"svg", "scale"?: 2.0 }`     | `{ "format": "png", "bytes_b64": "iVBOR…", "width": 800, "height": 600 }` | Snapshot the current graph (no surrounding UI). Default PNG via offscreen canvas; SVG returns the live vector. `scale` upsamples PNG (1.0 = native). Surfaced by `rb hub send capture --out FILE`. |
| `resolve_view_to_wave`    | any → hub          | `{ "instance_path": "top.u_fifo.u_wr_ptr" }`     | `{ "wave_scope": "tb.dut.u_fifo.u_wr_ptr" }`                  | Pure mapping; uses `tb_prefix` and aliases.                                                    |
| `resolve_wave_to_view`    | any → hub          | `{ "wave_scope": "tb.dut.u_fifo.u_wr_ptr" }`     | `{ "instance_path": "top.u_fifo.u_wr_ptr" }`                  | Reverse of `resolve_view_to_wave`. Strips `tb_prefix`, then prepends the design top; `unresolvable` when the trimmed path is not in `view.json`. |
| `resolve_signal_to_view`  | any → hub          | `{ "signal": "wr_ptr_q", "wave_scope": "tb.dut.u_fifo" }` | `{ "instance_path": ["top.u_fifo.u_wr_ptr"], "port": "q" }` | Returns a list of driver instance paths; `port` is the driven port on each (§7).               |
| `state_snapshot`          | any → hub          | `{}`                                              | `{ "active_model": "...", "selection": {...} \| null, "cursor_time": {...} \| null, "wave_scope": {...} \| null, "peers": [...], "diagnostics_sources": [...] }` | Returns the hub's cached view of the world (last selection / cursor / scope by origin, registered peers, active model, sources with cached diagnostics bundles). All four state fields are nullable for a freshly-started hub. |

### Lifecycle

| `type`     | Direction         | Payload                                                                                | Notes                                                                                                                              |
| ---------- | ----------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `hello`    | client → hub      | `{ "client": "view", "version": "0.1.0", "capabilities": ["selection_changed", ...] }` | Required first message. `client` is one of `view`, `wave`, `src`, `cli`, `notebook`, `graph`, `cov` — the same closed vocabulary as the envelope's `origin`, so `"viewer"` and friends are `bad_request`. `capabilities` lists `type`s the client understands. |
| `welcome`  | hub → client      | `{ "server_version": "0.1.0", "registered_clients": ["wave", "src"] }`                  | Hub's reply. Failure to negotiate → `error{code: "protocol_mismatch"}` then disconnect.                                            |
| `bye`      | either direction  | `{}`                                                                                   | Clean disconnect; no response expected. Hub broadcasts `bye` to remaining clients with the leaving client's `origin` in the envelope. |
| `peer_joined` | hub → all peers (except joining) | `{}` | Broadcast after a new client completes its hello handshake. The joining client's origin is in the envelope's `origin` field, mirroring `bye`'s shape so consumers can update their peer lists symmetrically (otherwise the list a peer received in its own `welcome` would never grow as later clients connect). |

### Errors

`kind: "error"` carries the original message's `id` when applicable;
when emitted spontaneously (e.g. from a broadcast that failed to
resolve), the `id` is a fresh UUID.

```json
{
  "v": 1,
  "id": "...",
  "origin": "cli",
  "kind": "error",
  "type": "error",
  "payload": { "code": "unresolvable", "message": "...", "context": { ... } }
}
```

| `code`                | Meaning                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `unresolvable`        | A coordinate translation failed (wave path not in design, signal not found, instance unknown). `context` carries the offending input. |
| `not_connected`       | A request was routed to a client `origin` that has no current connection (e.g. surfer is closed).                  |
| `bad_request`         | Malformed envelope or payload that fails the JSON Schema. `context.path` points at the failing JSON pointer.       |
| `protocol_mismatch`   | Mismatched `v`, or `hello` failed capability negotiation.                                                          |
| `timeout`             | Client-side only; never sent on the wire.                                                                          |

---

## 4. Lifecycle & discovery

How the hub starts, how each client finds it, and how a typical user
session is composed.

### 4.1 Hub startup

The hub runs as the `rb hub` subcommand (Phase 10b,
`rtl-buddy/rtl_buddy#115`). Exactly two commands start it; everything
else only *connects* to a hub that already exists:

| Starter             | Lifecycle                                                                                                 |
| ------------------- | --------------------------------------------------------------------------------------------------------- |
| `rb hub`            | Long-running daemon. Stays alive until Ctrl-C or `rb hub stop`. The canonical way to start a hub.         |
| `rb hier --serve`   | Convenience: auto-spawns `rb hub --daemon` as a child if no hub is running for the project, then bundles the viewer. Exits the hub on viewer close iff `rb hier --serve` started it. |

The following commands **never spawn the hub**:

- `rb wave <test>` — opportunistically registers if a hub is running (§4.5); otherwise operates standalone. Spawning a hub per-`rb wave` invocation would create ambiguity when several tests are open at once.
- The nvim plugin — pure consumer; shows `[rtlbuddy: no hub]` in the statusline if absent.
- The viewer (browser SPA) — has no shell access; can only be served by a hub that's already running (`rb hub --serve-viewer` or `rb hier --serve`).

If a user wants cross-view sync without `rb hier --serve`, the
expected flow is: `rb hub &` in one terminal, then `rb wave` / open
nvim / open the viewer URL — each connects to that hub.

On startup the hub:

1. Reads `<project_root>/.rtl-buddy/hub.toml` (§5).
2. Binds a TCP socket on `[hub] listen_port` (`0` = auto-assign).
3. Writes its discovery record to `<project_root>/.rtl-buddy/hub.json`
   — **per-project only; there is no user-global discovery file**.
   The hub is always scoped to one rtl-buddy project; cross-project
   clients are not supported in v1.

   ```json
   {
     "v": 1,
     "pid": 12345,
     "tcp": "127.0.0.1:54321",
     "server_version": "0.1.0",
     "project_root": "/abs/path/to/project",
     "started_at": "2026-05-19T14:32:10Z"
   }
   ```
4. Connects out to any pre-configured surfer instance (§4.5).
5. Optionally serves the viewer (§4.4).
6. Logs to `<project_root>/.rtl-buddy/hub.log`; stays in the foreground
   unless `--daemon` is passed.

On clean shutdown (SIGINT, SIGTERM, or `rb hub stop`) the hub:

- Broadcasts `bye` to all connected clients.
- Removes its `hub.json` if the file's `pid` still matches its own
  (so a crashed-and-replaced hub doesn't clobber the live one's file).

### 4.2 Client discovery

Every client finds the hub via project-root lookup. There is no
user-global fallback — each hub belongs to exactly one project, and
clients must resolve that project to connect.

Lookup order:

1. `$RTL_BUDDY_HUB` environment variable, if set — value is
   `host:port` (skips file lookup entirely). Useful for tests and
   scripted launches; also the mechanism the hub uses to inject its
   own address into spawned subprocesses (browser launch, surfer
   auto-spawn variants).
2. Walk up from the client's CWD looking for the first
   `.rtl-buddy/hub.json`. The directory containing it is the project
   root.

If neither resolves: the client treats the hub as not present, logs
at INFO, falls back to standalone mode (§8.4 in the worked examples).
A CLI client launched outside any project tree exits with a clear
error pointing at `cd <project>` or `$RTL_BUDDY_HUB`.

Browser clients (the viewer) have no CWD; they receive their hub
address from the hub's own HTTP layer at page-load time (§4.4) and
never read `hub.json` directly.

### 4.3 nvim plugin (`src` client)

User flow:

1. User starts the hub in one terminal: `rb hub` (in the project dir).
2. User opens nvim in a second terminal, also in the project dir. The
   `rtlbuddy.nvim` plugin is loaded normally (Lazy / Packer / etc.).
3. The plugin connects lazily — on the first relevant trigger
   (`:RtlBuddyShow`, or buffer-load of a SystemVerilog file when the
   user has set `rtlbuddy.auto_connect = true`):
   - Resolve the hub address via §4.2.
   - Open a TCP socket.
   - Send `hello { client: "src", version, capabilities }`.
   - On `welcome`, the plugin is registered as the `src` client.
4. Subsequent `open_source` requests are dispatched by the plugin
   using `nvim_command("edit ...")` + `nvim_win_set_cursor(...)`.
   `:RtlBuddyShow` broadcasts `source_focused` over the socket.

The plugin does **not** auto-start the hub; that's the user's job
(either explicitly, or by running `rb hier --serve` from another
window). The plugin shows a one-line `[rtlbuddy: no hub]` status in
the statusline when no hub is reachable.

### 4.4 Schematic viewer (`view` client, browser)

The viewer is a Vue/Vite SPA (Phase 5,
`rtl-buddy/rtl-buddy-view#18`). Because it runs in a browser, it
cannot speak raw TCP — it bridges via the hub's HTTP/WebSocket layer.

The hub embeds an HTTP server on `[hub] listen_port + 1` (or a
separately configured `[hub] http_port`) that:

- Serves the viewer's static bundle at `/`.
- Injects the hub's `host:port` into the page via a
  `window.__RTL_BUDDY_HUB__` script preamble.
- Exposes the hub's JSON-message channel as a WebSocket at `/ws`,
  framed one envelope per WebSocket message (no line-delimiting in
  this transport — WebSocket already frames).

User flow:

1. User runs `rb hub --serve-viewer` (or any rtl-buddy command that
   auto-spawns the hub with the viewer flag — `rb hier --serve` is
   the convention).
2. Hub prints `viewer ready at http://localhost:<http_port>` and
   optionally opens the URL in the default browser via
   `xdg-open`/`open`/`start`.
3. Browser loads the SPA, reads `window.__RTL_BUDDY_HUB__`, opens
   the WebSocket, sends `hello { client: "view", ... }`.
4. Hub responds `welcome`; viewer is the `view` client.

For Phase 5 development against a separate Vite dev server: the SPA
falls back to a `?hub=host:port` URL query parameter when
`window.__RTL_BUDDY_HUB__` is absent.

### 4.5 surfer via `rb wave` (`wave` client)

surfer is launched through `rb wave <test>`, the existing
rtl_buddy subcommand that handles FST discovery (running the debug
sim if needed), surfer config (`cfg-surfer` in
`root_config.yaml`), and WCP port allocation. `rb wave` *is* the
wave adapter — the hub never speaks raw WCP itself.

User flow:

1. User runs `rb wave <test>` in the project. `rb wave`:
   - Resolves the test's FST (running the debug sim if absent).
   - Binds a WCP listener socket on an ephemeral port.
   - Launches surfer with `--wcp-initiate <port>` pointing at that
     socket.
   - Starts its WCP listener thread, as today.
2. `rb wave` then checks for a project hub via §4.2's lookup. If
   `hub.json` resolves and the hub accepts a TCP connect:
   - `rb wave` connects to the hub, sends
     `hello { client: "wave", ... }`.
   - On `welcome`, the wave adapter is live: WCP events received
     from surfer are translated and broadcast onto the hub bus
     (§9.2), and hub `wave_*` requests are translated into WCP
     commands going back to surfer (§9.1).
   - When the hub is absent, `rb wave` continues to operate
     standalone exactly as before — no behavior change for the
     non-hub case.
3. `rb wave` exits when surfer exits (or on Ctrl-C); it sends `bye`
   to the hub on shutdown.

This split keeps responsibilities clean: `rb wave` owns the surfer
lifecycle and WCP transport (which already work and are tested in
rtl_buddy), and the hub owns the cross-client bus. The hub config
does **not** need an `[adapters.surfer]` section — surfer
configuration is in `root_config.yaml` where `rb wave` already
reads it.

Multi-surfer: a project can have multiple `rb wave` instances
running concurrently for different tests; the hub admits the first
one as the `wave` client and refuses additional ones with `error{code:
"not_connected", message: "wave client already registered"}` on
their `hello`. v2 may multiplex by test name.

### 4.6 Typical user session

The most common shape — explicit hub, three views:

```
$ cd ~/proj
$ rb hub --serve-viewer &                              # writes ./.rtl-buddy/hub.json
[hub] listening on 127.0.0.1:54320
[hub] viewer ready at http://localhost:54321
$ rb wave my_test &                                    # spawns surfer; auto-joins hub
$ nvim rtl/fifo.sv                                     # plugin auto-connects on first
                                                       # :RtlBuddyShow
# browser opened to http://localhost:54321 by --serve-viewer
```

The simpler shape — auto-spawned hub from `rb hier`:

```
$ cd ~/proj && rb hier my_top --serve
# rb spawns rb hub --serve-viewer in the background; viewer opens;
# user runs `rb wave <test>` / opens nvim as above. The hub exits
# when rb hier --serve does, on Ctrl-C.
```

The single-view shape (no hub, no cross-view sync) — `rb wave`, the
viewer, or the nvim plugin all operate standalone:

```
$ cd ~/proj && rb hier my_top --format json > view.json
# open view.json in any compatible viewer; no hub required.
# rb wave my_test continues to work without a hub — surfer launches,
# WCP runs locally inside rb wave as before.
```

### 4.7 Multi-project isolation

Two projects on the same machine each get their own hub on their own
port, scoped entirely by project root:

- A client launched inside project A's tree finds A's `hub.json` and
  connects to A's hub. Same for project B.
- A client launched outside any project tree finds nothing and stays
  in standalone mode (or errors, depending on the client's
  fallback semantics) — there is no "last-started hub" fallback.
- Two hubs for the same project cannot coexist: starting a second
  `rb hub` inside a project whose `hub.json` points at a still-live
  PID exits non-zero with `error: hub already running for this
  project (pid <N>)`. Use `rb hub stop` or kill the prior process
  first.

Two hubs trying to bind the same TCP port still fail at the OS level
with `EADDRINUSE`. Auto-assign (`listen_port = 0`) avoids the
collision entirely and is the recommended default.

### 4.8 Design-knowledge-graph pane (`graph` client, browser)

The hub serves the merged design knowledge graph
([epic `rtl-buddy/rtl_buddy#375`](https://github.com/rtl-buddy/rtl_buddy/issues/375))
as a page at `GET /graph` on the same `http_port` as the SPA, reading
`artefacts/graph/graph.json` (design tier from `rtl-buddy-view graph`,
config + binding tiers from `rb graph build`) joined in memory with the
volatile `artefacts/graph/results-overlay.json`. Implementation is
rtl_buddy's (`hub/graph_page.py`, `rtl-buddy/rtl_buddy#382`); what
belongs in *this* spec is the wire contract it registers under.

It is a peer with its **own origin**, not a second `view`. The hub
allows one client per origin — a second `hello` for an already
registered origin is refused unless it sets `takeover: true`, which
evicts the older peer — and the pane is meant to be open
*alongside* the schematic — clicking a module in the graph selects it
in the design view — so sharing the `view` slot would make the two
panes evict each other.

User flow:

1. Browser loads `http://localhost:<http_port>/graph`, reads
   `window.__RTL_BUDDY_HUB__` exactly as the SPA does, opens the
   WebSocket at `/ws`, sends `hello { client: "graph", ... }`.
2. Hub responds `welcome`; the pane is the `graph` client and appears
   in every peer's `registered_clients` / `state_snapshot.peers`.
3. Clicking a node emits the **same envelopes the SPA emits** — a
   `selection_changed` for anything that resolves to a design-view
   instance path (an `inst:` node id already *is* that coordinate; a
   `module:` node resolves through the shallowest instance of it), and
   an `open_source` request for any node that carries `file`/`line`.
   The pane introduces no message type of its own in this direction.
4. The reverse direction is `graph_focus` (§3): `rb hub send
   graph-focus NODE`, or any peer, points the pane at one node. A
   `selection_changed` arriving from the SPA or the editor highlights
   the matching instance node.

Because `graph_focus` is a state event the hub caches (like
`selection_changed`), a focus sent before the tab exists is replayed
to the pane when it registers — the pane opens already focused rather
than dropping the event that preceded it.

### 4.9 Coverage pane (`cov` client, browser)

The hub serves the run's coverage model
([epic `rtl-buddy/rtl_buddy#397`](https://github.com/rtl-buddy/rtl_buddy/issues/397))
as a page at `GET /cov` on the same `http_port` as the SPA, backed by
`GET /cov.json` — the structured per-file / per-line / per-branch /
per-toggle model plus per-test attribution that `rb cov` builds from
the coverage artefacts already on disk. Implementation is rtl_buddy's
(`hub/cov_page.py`, `rtl-buddy/rtl_buddy#400`); what belongs in *this*
spec is the wire contract it registers under.

Coverage numbers are **not** diagnostics. `diagnostics_set` is a
closed, `additionalProperties: false` shape with no numeric field, and
widening it to carry hit counts would make every existing consumer's
severity-based rendering wrong. Coverage therefore gets its own
channel: `/cov.json` for the data, `cov_focus` for the pointer.

Like the graph pane, it is a peer with its **own origin**, not a
second `view`. The hub allows one client per origin — a second
`hello` for an already registered origin is refused unless it sets
`takeover: true`, which evicts the older peer — and the coverage pane
is meant to be open *alongside* the schematic and the graph, so
sharing a slot would make the panes evict each other.

User flow:

1. Browser loads `http://localhost:<http_port>/cov`, reads
   `window.__RTL_BUDDY_HUB__` exactly as the SPA does, opens the
   WebSocket at `/ws`, sends `hello { client: "cov", ... }`.
2. Hub responds `welcome`; the pane is the `cov` client and appears
   in every peer's `registered_clients` / `state_snapshot.peers`.
3. Clicking a file or module emits the **same envelopes the other
   panes emit** — a `selection_changed` for anything that resolves to
   a design-view instance path, and an `open_source` request for a
   row whose `file`/`line` is known. The pane introduces no message
   type of its own in this direction.
4. The reverse direction is `cov_focus` (§3): `rb hub send cov-focus
   TARGET`, or any peer, points the pane at a file, module, or test —
   optionally narrowed to one `metric`, `line`, or `item`. A
   `selection_changed` arriving from the SPA or the editor highlights
   the matching file's coverage.

Because `cov_focus` is a state event the hub caches (like
`graph_focus`), a focus sent before the tab exists is replayed to the
pane when it registers. Only the latest `cov_focus` is kept —
latest-writer-wins, one slot, no history — so a late-joining pane
opens on the most recent target rather than replaying a backlog.

---

## 5. Config

`<project_root>/.rtl-buddy/hub.toml` — per-project, alongside the
runtime `hub.json` discovery file. The hub does not read any
user-global config file; defaults are baked into `rb hub`.

```toml
[hub]
listen_port = 0          # 0 = auto-assign; the chosen port is written
                         # to .rtl-buddy/hub.json for client discovery
http_port   = 0          # 0 = auto-assign; serves the viewer SPA + /ws
log_path    = ".rtl-buddy/hub.log"

[mapping]
# Stripped from wave paths to recover view instance paths.
tb_prefix      = "tb.dut."

# Optional per-instance fixups for legacy / renamed testbench paths.
# Applied BEFORE the tb_prefix strip.
signal_aliases = [
  # { wave = "tb.dut.foo_pre_renamed", view = "top.foo" },
]
```

Surfer configuration is **not** here — surfer is launched by
`rb wave`, which reads its own `cfg-surfer` section in
`root_config.yaml` (see rtl_buddy docs). The hub never speaks WCP
directly.

The nvim plugin needs no hub-side config — it discovers the hub via
the project-local `hub.json` (§4.2) and uses standard nvim API
methods (§10).

The hub writes its actual listen address (after the `0` auto-assign)
into `<project_root>/.rtl-buddy/hub.json` on startup (§4.1). Clients
discover the hub by reading that file; no environment variables
required for the common case, but `$RTL_BUDDY_HUB` overrides it for
tests and scripted launches.

---

## 6. Loop prevention

The hub treats messages as **strictly originator-suppressed**: an
event from `origin: X` is never delivered to a client whose `origin`
also matches `X`. Combined with the rule that every client tags its
own outgoing messages with its `origin`, this eliminates the trivial
echo loop.

Three additional rules cover the indirect cycles:

1. **Request → response → side-effect → event** is allowed once. When
   the viewer requests `wave_set_scope`, surfer accepts, and surfer
   subsequently broadcasts a `scope_changed` event with `origin:
   wave`, the hub forwards that event to the viewer. The viewer
   dedupes by remembering which scope it just asked surfer to set; it
   MUST NOT issue a second `wave_set_scope` in response. The hub does
   not enforce this — clients are responsible.

2. **Request ID dedupe window.** The hub maintains a bounded LRU of
   in-flight request IDs (default capacity 1024, default TTL 60 s).
   A response or error with an unknown `id` is logged at WARN and
   dropped. A duplicate request (same `id`) is dropped.

3. **Synthetic broadcasts derived from another event** are tagged with
   the source event's `origin`. When the hub observes a wave-side
   `signal_selected` and synthesizes the corresponding
   `selection_changed`, that `selection_changed` carries `origin:
   wave` — so it reaches `view` and `src` but not surfer.

---

## 7. Asymmetric selection collapse

The three views don't have the same notion of "what is selected." A
single click in one resolves to a *set* of things in another. The hub
collapses these per fixed rules so downstream clients don't have to
re-derive them.

### Bus / multi-driver signals

`signal_selected` for a bus (a wave variable driven by N flops in a
generate or by a packed-array assignment) → `selection_changed` event
whose `payload.instance_path` is the **list** of driving instance
paths in extraction order. Clients that only handle the single-path
case SHOULD treat the first element as the canonical selection and
optionally surface the others as alternatives.

### Source focus on a non-module line

`source_focused` on a line that is inside a module body (not at a
module/instance declaration) resolves to the **enclosing module
instance**, by `(file, line, col)` containment against the source
anchors in `view.json`. If the file is not in `view.json` at all, the
hub emits `error{code: "unresolvable"}` and broadcasts nothing — there
is no useful fallback.

### Cursor without a signal selection

`cursor_time_changed` with no current `signal_selected` does **not**
synthesize a `selection_changed`. The viewer tints based on all port
values; no specific instance is "selected." (When this event becomes
available — see §9 — clients SHOULD render the time, not the
selection.)

### Multi-instance resolution from view → wave

`resolve_view_to_wave` for an instance whose source appears multiple
times in the testbench (e.g. parameterized modules instantiated under
several scopes) returns the unique match if there is one, else an
`error{code: "unresolvable"}` with `context.candidates` enumerating
the ambiguous wave paths. The hub does not pick.

---

## 8. Worked examples

Each example shows the full sequence on the wire. `id` values are
abbreviated; in practice they are full UUIDs.

### 8.1 Click in viewer → wave + src

User clicks `top.u_fifo.u_wr_ptr` in the viewer.

```json
// 1. viewer broadcasts its selection
{ "v":1, "id":"a1", "origin":"view", "kind":"event", "type":"selection_changed",
  "payload":{ "instance_path":"top.u_fifo.u_wr_ptr" } }

// 2. hub forwards to wave + src (not back to view); src acts on it
{ "v":1, "id":"a2", "origin":"view", "kind":"event", "type":"selection_changed",
  "payload":{ "instance_path":"top.u_fifo.u_wr_ptr" } }   // → wave
{ "v":1, "id":"a3", "origin":"view", "kind":"event", "type":"selection_changed",
  "payload":{ "instance_path":"top.u_fifo.u_wr_ptr" } }   // → src

// 3. wave adapter resolves instance_path → wave_scope, asks surfer to add it
{ "v":1, "id":"a4", "origin":"cli", "kind":"request", "type":"resolve_view_to_wave",
  "payload":{ "instance_path":"top.u_fifo.u_wr_ptr" } }
{ "v":1, "id":"a4", "origin":"cli", "kind":"response", "type":"resolve_view_to_wave",
  "payload":{ "wave_scope":"tb.dut.u_fifo.u_wr_ptr" } }

// 4. src adapter opens the file at the source anchor
{ "v":1, "id":"a5", "origin":"cli", "kind":"request", "type":"open_source",
  "payload":{ "file":"rtl/fifo.sv", "line":42, "col":5 } }
{ "v":1, "id":"a5", "origin":"src", "kind":"response", "type":"open_source",
  "payload":{ "ok":true } }
```

### 8.2 Cursor scrub in wave → view tint + src goto_declaration

When surfer emits `cursor_time_changed` (added by the surfer fork —
see §9):

```json
// 1. surfer broadcasts cursor time
{ "v":1, "id":"b1", "origin":"wave", "kind":"event", "type":"cursor_time_changed",
  "payload":{ "t_fs":"12500000" } }

// 2. hub forwards to view + src; view re-tints
{ "v":1, "id":"b2", "origin":"wave", "kind":"event", "type":"cursor_time_changed",
  "payload":{ "t_fs":"12500000" } }   // → view
// (src ignores cursor_time_changed by default; nothing to navigate to)
```

WCP `goto_declaration` events are handled separately — they map onto
`source_focused` with `origin: wave` after the hub resolves the signal
name to a source anchor.

### 8.3 `:RtlBuddyShow` in nvim → view + wave

User invokes `:RtlBuddyShow` at line 42 of `rtl/fifo.sv`.

```json
// 1. nvim plugin broadcasts source focus
{ "v":1, "id":"c1", "origin":"src", "kind":"event", "type":"source_focused",
  "payload":{ "file":"/abs/rtl/fifo.sv", "line":42, "col":5 } }

// 2. hub resolves to enclosing instance, broadcasts selection_changed
{ "v":1, "id":"c2", "origin":"src", "kind":"event", "type":"selection_changed",
  "payload":{ "instance_path":"top.u_fifo" } }

// 3. wave adapter resolves + adds the scope to surfer
{ "v":1, "id":"c3", "origin":"cli", "kind":"request", "type":"resolve_view_to_wave",
  "payload":{ "instance_path":"top.u_fifo" } }
{ "v":1, "id":"c3", "origin":"cli", "kind":"response", "type":"resolve_view_to_wave",
  "payload":{ "wave_scope":"tb.dut.u_fifo" } }
```

### 8.4 Viewer offline (no hub)

Viewer starts, finds no `~/.rtl-buddy/hub.json`, falls back to
standalone mode: no hub features (no cross-view sync), all overlay
data still loads from `view.json`. The viewer SHOULD show a small "no
hub connected" indicator and a button to start one (which spawns
`rb hub`).

### 8.5 Surfer disconnects mid-session

Surfer is closed. The hub detects the TCP disconnect; subsequent
`wave_*` requests respond with `error{code: "not_connected"}`. When
surfer reconnects, it sends a fresh `hello`; the hub broadcasts
`welcome` to all clients listing the now-registered clients, and
**replays its cached state to the just-welcomed peer**: the last
`selection_changed` / `cursor_time_changed` / `scope_changed` events
(each carrying the original `origin` that produced them), plus every
cached `diagnostics_set` bundle. The replay is unicast to the
welcoming peer — not broadcast — so existing peers don't see
duplicated state. Peers that don't care about a particular replayed
type (a fresh surfer ignoring a cached `scope_changed` from a stale
session, say) silently drop it.

The same replay fires for any client that hello's after the cache
has been populated: a new browser tab joining, an `rb hub send`
one-shot, an nvim restart. A peer dropping in mid-session
immediately knows what the user is looking at without scraping
events.

### 8.6 Resolution failure

User clicks an instance in the viewer that the testbench doesn't
contain (e.g. a sub-module that lives under a generate that's
disabled in this build):

```json
{ "v":1, "id":"f1", "origin":"cli", "kind":"request", "type":"resolve_view_to_wave",
  "payload":{ "instance_path":"top.u_dbg.u_probe" } }
{ "v":1, "id":"f1", "origin":"cli", "kind":"error", "type":"error",
  "payload":{ "code":"unresolvable", "message":"no wave scope matches instance",
              "context":{ "instance_path":"top.u_dbg.u_probe", "tb_prefix":"tb.dut." } } }
```

The viewer surfaces the error to the user; no fallback wave_scope is
synthesized.

---

## 9. WCP mapping

Source of truth: `surfer-wcp/src/proto.rs` on `rtl-buddy/surfer @
rtl-buddy` (the fork the hub pins). Upstream is
`surfer-project/surfer`. We own the fork and extend WCP there to meet
v1 requirements; the additions in §9.3 below are dependencies of this
spec.

### 9.1 Commands the hub issues

| Hub request          | WCP command                                      | Notes                                                                                                          |
| -------------------- | ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| `wave_add_variables` | `add_variables { variables }`                    | Returns `WcpResponse::add_variables { ids }`. The hub surfaces `ids` back to the requester.                    |
| `wave_set_scope`     | `set_scope { scope }` *(fork addition, §9.3)*    | "Navigate to scope" without adding its variables. Upstream's closest match is `add_scope`, which also adds vars — the fork separates the two so the hub can cleanly drive navigation.  |
| `wave_set_cursor`    | `set_cursor { timestamp }`                       | Hub passes `t_fs` through after timestamp-unit reconciliation (WCP uses surfer's currently-loaded time unit).   |

The hub does not use: `set_item_color`, `set_viewport_to`,
`set_viewport_range`, `remove_items`, `focus_item`, `clear`, `load`,
`zoom_to_fit`, `add_markers`, `add_items`, `reload`, `shutdown`,
`get_item_list`, `get_item_info`, `add_scope`. They remain available
to clients that connect directly to surfer (bypassing the hub) for
non-mediated operations.

### 9.2 Events the hub subscribes to

| WCP event                       | Hub action                                                                                                       |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `waveforms_loaded`              | Logged. Cached `wave` ↔ `view` lookups are invalidated.                                                          |
| `goto_declaration { variable }` | Resolve `variable` → source anchor via signal → driver lookup → instance source anchor; emit `source_focused`.   |
| `cursor_set { timestamp }` *(fork, §9.3)*    | Map onto `cursor_time_changed` event (§3).                                                          |
| `scope_changed { scope }` *(fork, §9.3)*     | Map onto `scope_changed` event (§3).                                                                |
| `add_drivers`                   | Reserved for v2 ("trace drivers"). Logged but not broadcast in v1.                                              |
| `add_loads`                     | Same as `add_drivers`.                                                                                          |

### 9.3 Fork additions required for v1

The hub depends on the following extensions to `surfer-wcp` on the
`rtl-buddy` branch of `rtl-buddy/surfer`. These are tracked separately
from this spec but are listed here as load-bearing for v1.

**New WCP commands:**

- `set_scope { scope: String }` — surfer navigates to the given scope
  without adding its variables to the view. Responds with
  `WcpResponse::ack`. Upstream's `add_scope` is unsuitable: it also
  adds variables, which causes the viewer to spam the panel every
  time the user clicks an instance.

**New WCP events:**

- `cursor_set { timestamp }` — emitted when the user moves the surfer
  waveform cursor (any of: drag, keyboard step, marker click). The
  hub broadcasts this as `cursor_time_changed` (§3).
- `scope_changed { scope }` — emitted when the user navigates surfer's
  active scope. The hub broadcasts this as `scope_changed` (§3).

Both events MUST be debounced surfer-side at ≥30 Hz to avoid drag
storms — the hub does not debounce.

Tracked in [`rtl-buddy/surfer#2`](https://github.com/rtl-buddy/surfer/issues/2). Until those land, the wave-side
adapter operates degraded: `wave_set_scope` falls back to
`add_scope`, and `cursor_time_changed` / `scope_changed` are simply
not emitted (the type names remain in §3 so client code can compile
but no broadcasts arrive).

---

## 10. nvim mapping

The nvim plugin (Phase 10c, `rtl-buddy/rtl_buddy#113`, future repo
`rtl-buddy/rtlbuddy.nvim`) is a thin Lua module that:

- connects to the hub over the TCP socket discovered via
  `~/.rtl-buddy/hub.json`,
- exposes `:RtlBuddyShow` as a user command (broadcasts
  `source_focused` on the current `(file, line, col)`),
- listens for `open_source` requests and uses nvim's built-in
  `nvim_command("edit ...")` + `nvim_win_set_cursor(...)` via
  msgpack-rpc (no custom RPC surface; everything via the standard nvim
  API).

The plugin does **not** broadcast on every cursor move — only on
explicit `:RtlBuddyShow`. Continuous broadcasting would saturate the
bus and is a non-goal in v1.

### nvim API methods used

| API method              | Where used                                                                          |
| ----------------------- | ----------------------------------------------------------------------------------- |
| `nvim_command`          | `:edit <file>` when handling `open_source`.                                          |
| `nvim_win_set_cursor`   | Move cursor to `{line, col}` after `:edit`.                                          |
| `nvim_get_current_buf`  | Resolve `file` for the `:RtlBuddyShow` payload.                                      |
| `nvim_buf_get_name`     | Same.                                                                               |
| `nvim_win_get_cursor`   | Resolve `{line, col}` for the `:RtlBuddyShow` payload.                              |

All are stable nvim 0.9+ API; no external dependencies.

---

## 11. Capability negotiation

Older clients must co-exist with future protocol versions. The contract:

- A client MUST send `hello` first, listing the `type`s it understands
  in `capabilities`.
- The hub's `welcome` lists `registered_clients` — but not their
  capabilities. Clients that need to know whether (say) a wave-side
  adapter supports `cursor_time_changed` SHOULD probe by issuing the
  relevant request and treating `error{code: "bad_request"}` /
  `error{code: "not_connected"}` as "unsupported."
- When a future v2 adds a new event `type`, v1 clients ignore it
  (clients MUST silently drop unknown `type`s, logging at DEBUG).
- When v2 removes or renames a `type`, the major version bumps to
  `v: 2`; v1 clients receive `error{code: "protocol_mismatch"}` on
  `hello` and disconnect.

The hub itself can speak multiple protocol versions; the version
applied per connection is the `v` from that connection's `hello`.

---

## 12. Glossary

| Term            | Definition                                                                                                            |
| --------------- | --------------------------------------------------------------------------------------------------------------------- |
| Adapter         | Process-side component that mediates between the hub and one of the three views (viewer, surfer, nvim).               |
| Capability      | A `type` name a client advertises in its `hello.capabilities` list.                                                  |
| Client          | Anything connected to the hub: an adapter, the CLI, or a future programmatic consumer.                                |
| Hub             | `rtl-buddy-hub`, the daemon defined by this spec; Phase 10b implementation.                                          |
| `id`            | Per-message UUID for request/response correlation and dedup.                                                          |
| `instance_path` | A hierarchical instance reference rooted at `view.json.top`, e.g. `top.u_fifo.u_wr_ptr`.                              |
| `graph.json`    | The design-knowledge-graph contract (`docs/graph-json-v1.md`); its node ids are the coordinate `graph_focus` speaks. |
| `cov.json`      | The coverage model the hub serves at `GET /cov.json` (rtl_buddy's `rb cov`); its file / module / test keys are the coordinate `cov_focus` speaks. |
| `origin`        | One of `view`, `wave`, `src`, `cli`, `notebook`, `graph`, `cov` — the conceptual originator of a message.            |
| `tb_prefix`     | Configured prefix stripped from wave paths to recover view instance paths.                                            |
| `view.json`     | The JSON contract emitted by `rtl-buddy-view --format json` once Phase 4 lands; see `rtl-buddy/rtl-buddy-view#17`.   |
| `wave_scope`    | A path into the surfer-loaded waveform, e.g. `tb.dut.u_fifo`.                                                       |
| WCP             | Waveform Control Protocol — surfer's native external-control socket. Source of truth: `surfer-wcp/src/proto.rs`.    |

---

## References

- Tracking issue: `rtl-buddy/rtl-buddy-view#19`
- Meta: `rtl-buddy/rtl_buddy#112`
- Phase 10b daemon: `rtl-buddy/rtl_buddy#115`
- Phase 10c nvim plugin: `rtl-buddy/rtl_buddy#113`
- Phase 10d viewer wiring: `rtl-buddy/rtl-buddy-view#23`
- Phase 4 view.json v1 contract: `rtl-buddy/rtl-buddy-view#17`
- Design-knowledge-graph pane (§4.8): `rtl-buddy/rtl_buddy#382`
- Coverage pane (§4.9): `rtl-buddy/rtl_buddy#400`; protocol half `rtl-buddy/rtl-buddy-view#133`
- WCP source of truth (fork): https://github.com/rtl-buddy/surfer/blob/rtl-buddy/surfer-wcp/src/proto.rs
- WCP upstream: https://gitlab.com/surfer-project/surfer/-/blob/main/surfer-wcp/src/proto.rs
- In-flight WCP work (value annotation, related fork branch): https://github.com/rtl-buddy/surfer/tree/feature/rtl-buddy-52-wcp-src-value-annotation
- v1 fork additions required (§9.3): [`rtl-buddy/surfer#2`](https://github.com/rtl-buddy/surfer/issues/2)
- JSON Schema: `schemas/hub-protocol-v1.json`
