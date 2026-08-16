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
//      ``/gph`` are 404s (or worse, someone else's site), so the
//      switcher is empty and App.vue hides the group.
//   2. A sibling app is listed on DATA PRESENCE, the rule the hub's
//      landing cards already follow: the hub sets
//      ``__RTL_BUDDY_GRAPH_URL__`` only when it has a built graph.json
//      to serve. ``⌂ hub`` is unconditional — the landing page always
//      exists and is never a peer, so it can never evict a tab.
//
// Siblings open in a NEW TAB (``target=_blank``), which is why their
// labels carry a trailing ``↗`` — the same treatment the graph and cov
// panes give their own switchers. The reason is the hub protocol, not
// taste: the SPA is the ``view`` WS peer, and one client per origin
// means navigating this tab to /gph drops the view registration
// (the graph pane's "sync design view" then has nothing to talk to,
// and coming back re-registers as a fresh tab). ``⌂ hub`` is the one
// same-tab link: the landing page is never a peer, so leaving for it
// costs nothing that going Back does not restore.

import { displayOrigin } from './displayNames.js'

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
//
// Labels carry the family's SHORT display names (displayNames.js) —
// ``key``, ``href`` and ``gate`` are wire/route names and unchanged.
// Titles stay natural English: the short names are labels, not prose.
const SIBLINGS = [
  {
    key: 'graph',
    label: `${displayOrigin('graph')} ↗`,
    href: '/gph',
    gate: '__RTL_BUDDY_GRAPH_URL__',
    title: 'Open the design knowledge graph pane in a new tab',
  },
  {
    key: 'cov',
    label: `${displayOrigin('cov')} ↗`,
    href: '/cov',
    gate: '__RTL_BUDDY_COV_URL__',
    title: 'Open the coverage pane in a new tab',
  },
]

/** True when this bundle is being served by a hub (not embed/dev). */
export function isHubServed(win = typeof window !== 'undefined' ? window : null) {
  return Boolean(win && typeof win.__RTL_BUDDY_HUB__ === 'string')
}

/**
 * A sibling app's same-origin route, or ``null`` when opening it
 * would 404.
 *
 * Same two rules the switcher follows (hub-served, and the app's own
 * data-presence global set), exposed for the affordances that open a
 * sibling from somewhere other than the switcher — NodeDetail's
 * "open in cov ↗" link. Sharing the ``SIBLINGS`` table
 * keeps one gate per app rather than a second copy that can drift.
 */
export function siblingAppHref(key, win = typeof window !== 'undefined' ? window : null) {
  if (!isHubServed(win)) return null
  const app = SIBLINGS.find((s) => s.key === key)
  if (!app) return null
  return typeof win[app.gate] === 'string' && win[app.gate].length > 0 ? app.href : null
}

/**
 * The switcher entries for the current page, in display order.
 *
 * Returns ``[]`` when the hub is not serving us, which is App.vue's
 * signal to render no switcher at all.
 */
export function hubApps(win = typeof window !== 'undefined' ? window : null) {
  if (!isHubServed(win)) return []
  const out = [
    {
      key: 'hub',
      label: '⌂ hub',
      href: HUB_LANDING_ROUTE,
      title: 'Hub landing page',
      external: false,
    },
  ]
  for (const app of SIBLINGS) {
    if (typeof win[app.gate] === 'string' && win[app.gate].length > 0) {
      out.push({
        key: app.key,
        label: app.label,
        href: app.href,
        title: app.title,
        external: true,
      })
    }
  }
  return out
}

/**
 * The anchor attributes for one switcher entry.
 *
 * Kept here rather than inline in App.vue's template so the "siblings
 * open in a new tab, ``⌂ hub`` does not" rule is one testable
 * function instead of a ternary in markup.
 */
export function switcherLinkAttrs(app) {
  const attrs = { href: app.href }
  if (app.external) {
    attrs.target = '_blank'
    // Never hand a new tab an ``opener`` handle back to the SPA.
    attrs.rel = 'noopener'
  }
  return attrs
}
