// The app switcher's contents.
//
// Hub chrome contract (rtl_buddy docs/concepts/hub.md): the top bar's
// right slot is "``⌂ hub`` back to the landing, then the sibling apps".
// The SPA is one app among several the same hub process serves at the
// same origin, so the switcher is plain same-origin links — no routing,
// no fetch, nothing that could take the page off localhost.
//
// Two rules decide what is listed:
//
//   1. Nothing is listed unless the hub is serving us. Opened from an
//      ``embed.py`` single file or the Vite dev server, ``/`` and
//      ``/graph`` are 404s (or worse, someone else's site), so the
//      switcher is empty and App.vue hides the group.
//   2. A sibling app is listed on DATA PRESENCE, the rule the hub's
//      landing cards already follow: the hub sets
//      ``__RTL_BUDDY_GRAPH_URL__`` only when it has a built graph.json
//      to serve. ``⌂ hub`` is unconditional — the landing page always
//      exists and is never a peer, so it can never evict a tab.

/** Where the hub serves its landing page. */
export const HUB_LANDING_ROUTE = '/'

/**
 * Sibling apps, each gated on the injected global that says the hub
 * has data for it.
 *
 * ``cov`` is listed ahead of the pane itself (rtl-buddy/rtl_buddy#400):
 * the global is simply never set until that lands, so the entry costs
 * nothing and the switcher needs no second edit when it does.
 */
const SIBLINGS = [
  { key: 'graph', label: 'graph', href: '/graph', gate: '__RTL_BUDDY_GRAPH_URL__' },
  { key: 'cov', label: 'cov', href: '/cov', gate: '__RTL_BUDDY_COV_URL__' },
]

/** True when this bundle is being served by a hub (not embed/dev). */
export function isHubServed(win = typeof window !== 'undefined' ? window : null) {
  return Boolean(win && typeof win.__RTL_BUDDY_HUB__ === 'string')
}

/**
 * The switcher entries for the current page, in display order.
 *
 * Returns ``[]`` when the hub is not serving us, which is App.vue's
 * signal to render no switcher at all.
 */
export function hubApps(win = typeof window !== 'undefined' ? window : null) {
  if (!isHubServed(win)) return []
  const out = [{ key: 'hub', label: '⌂ hub', href: HUB_LANDING_ROUTE, title: 'Hub landing page' }]
  for (const app of SIBLINGS) {
    if (typeof win[app.gate] === 'string' && win[app.gate].length > 0) {
      out.push({ key: app.key, label: app.label, href: app.href, title: `Open the ${app.label} pane` })
    }
  }
  return out
}
