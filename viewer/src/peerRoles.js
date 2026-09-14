// The peer roster: which hub origins the SPA draws a row for.
//
// The origin vocabulary is owned by the protocol schema
// (`schemas/hub-protocol-v1.json`, `properties.origin.enum`) and is
// hand-copied into four repos' worth of surfaces — see the lockstep
// checklist in `docs/hub-protocol.md` §13. This module is the SPA's
// copy, kept in its own file (rather than inline in HubStatus.vue) so
// `tests/origin_vocabulary.spec.js` can import it and check it against
// the schema on every run: an origin added to the schema and forgotten
// here is a test failure, not a missing row someone notices in the UI
// six weeks later.
//
// The keys are WIRE origins — they are matched against `hub.peers`,
// which is what `welcome.registered_clients` carries. The text a user
// reads comes from `displayNames.js`.

/**
 * Every origin a user can have open as an app, connected or not.
 *
 * Rendered in full — including the disconnected ones — so the user can
 * tell at a glance which adapter is missing instead of just seeing a
 * shorter list. Same roster `rb hub status` prints (which deliberately
 * shows the raw wire origins; see rtl_buddy's `docs/concepts/hub.md`).
 *
 * `cov` is DISPLAY-ONLY until the coverage pane's hub client lands
 * (rtl-buddy/rtl_buddy#400), and `phys` likewise until the synth+power
 * pane lands (rtl-buddy/rtl_buddy#558); until a hub speaks one of them
 * the row simply reads "not connected", which is the honest answer
 * either way.
 */
export const PEER_ROLES = Object.freeze([
  { origin: 'view', label: '(this schematic)' },
  { origin: 'src', label: '(editor)' },
  { origin: 'wave', label: '(surfer)' },
  { origin: 'graph', label: '(graph pane)' },
  { origin: 'cov', label: '(coverage pane)' },
  { origin: 'phys', label: '(physical-metrics pane)' },
])

/**
 * The origins deliberately absent from the roster above.
 *
 * Not apps a user keeps open: `cli` is `rb hub send`, a one-shot, and
 * `notebook` is one marimo session rather than an adapter. Naming them
 * rather than just omitting them is what lets the fence test assert an
 * EXACT partition of the schema enum — otherwise "origin missing from
 * the roster" and "origin deliberately not a row" are indistinguishable
 * and the test can only check a subset.
 */
export const NON_APP_ORIGINS = Object.freeze(['cli', 'notebook'])
