// Hub chrome: the app switcher, the identity marks, and the theme pin.
//
// These three are the parts of rtl-buddy/rtl-buddy-view#132 that have
// a decision in them rather than a colour: WHAT the switcher lists,
// WHERE the favicon comes from, and WHICH theme wins.

import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'

import { hubApps, isHubServed, switcherLinkAttrs } from '../src/hubApps.js'
import { installFavicon, FAVICON_16_URL, FAVICON_32_URL, LOGO_URL } from '../src/identity.js'
import {
  applyStoredThemePreference,
  currentTheme,
  setThemePreference,
  themePreference,
  themeVersion,
} from '../src/theme.js'

describe('app switcher', () => {
  it('lists nothing when the hub is not serving the bundle', () => {
    // embed.py single file, or the Vite dev server: ``/`` and
    // ``/graph`` belong to someone else (or nobody).
    expect(isHubServed({})).toBe(false)
    expect(hubApps({})).toEqual([])
  })

  it('always offers ⌂ hub under the hub, even with no sibling data', () => {
    const apps = hubApps({ __RTL_BUDDY_HUB__: '127.0.0.1:8123' })
    expect(apps.map((a) => a.key)).toEqual(['hub'])
    expect(apps[0].href).toBe('/')
  })

  it('advertises a sibling only when the hub injected its data URL', () => {
    const win = {
      __RTL_BUDDY_HUB__: '127.0.0.1:8123',
      __RTL_BUDDY_GRAPH_URL__: '/graph.json',
    }
    expect(hubApps(win).map((a) => a.key)).toEqual(['hub', 'graph'])

    // Same data-presence rule the hub's landing cards follow: an
    // empty string is "no data", not "a URL".
    expect(hubApps({ ...win, __RTL_BUDDY_GRAPH_URL__: '' }).map((a) => a.key)).toEqual(['hub'])
  })

  it('carries the cov pane ahead of the pane itself', () => {
    // rtl-buddy/rtl_buddy#400 sets the global; until it does the entry
    // simply never appears, so this needs no second edit.
    const apps = hubApps({
      __RTL_BUDDY_HUB__: '127.0.0.1:8123',
      __RTL_BUDDY_COV_URL__: '/cov.json',
    })
    expect(apps.map((a) => a.href)).toEqual(['/', '/cov'])
  })

  it('labels siblings the way the panes do, arrow included', () => {
    const apps = hubApps({
      __RTL_BUDDY_HUB__: '127.0.0.1:8123',
      __RTL_BUDDY_GRAPH_URL__: '/graph.json',
      __RTL_BUDDY_COV_URL__: '/cov.json',
    })
    expect(apps.map((a) => a.label)).toEqual(['⌂ hub', 'graph ↗', 'coverage ↗'])
  })

  it('opens siblings in a new tab so the view peer slot survives', () => {
    // One WS client per origin: navigating THIS tab to /graph drops
    // the SPA's ``view`` registration, which is the peer the graph
    // pane's "sync design view" talks to. The panes open siblings in
    // a new tab for the same reason — the ↗ in the label is the
    // promise this assertion keeps.
    const apps = hubApps({
      __RTL_BUDDY_HUB__: '127.0.0.1:8123',
      __RTL_BUDDY_GRAPH_URL__: '/graph.json',
      __RTL_BUDDY_COV_URL__: '/cov.json',
    })
    const byKey = Object.fromEntries(apps.map((a) => [a.key, switcherLinkAttrs(a)]))
    expect(byKey.graph).toEqual({ href: '/graph', target: '_blank', rel: 'noopener' })
    expect(byKey.cov).toEqual({ href: '/cov', target: '_blank', rel: 'noopener' })
    // ⌂ hub is the exception: the landing page is never a peer, so
    // same-tab costs nothing that Back does not undo.
    expect(byKey.hub).toEqual({ href: '/' })
    expect(byKey.hub.target).toBeUndefined()
  })
})

describe('identity marks', () => {
  it('bundles the marks as inlineable assets, not remote URLs', () => {
    // Vite rewrites the import to a data: URI (they are all under the
    // 4 kB inline threshold) or a bundled path; either way nothing
    // routes off the origin, which is the rule hub panes live by.
    for (const url of [FAVICON_16_URL, FAVICON_32_URL, LOGO_URL]) {
      expect(typeof url).toBe('string')
      expect(url).not.toMatch(/^https?:/)
    }
  })

  it('installs both favicon sizes and is idempotent', () => {
    installFavicon(document)
    installFavicon(document)
    const links = document.querySelectorAll('link[data-rb-favicon]')
    expect(links.length).toBe(2)
    expect([...links].map((l) => l.getAttribute('sizes')).sort()).toEqual(['16x16', '32x32'])
  })

  it('leaves a favicon the host page declared itself alone', () => {
    const theirs = document.createElement('link')
    theirs.setAttribute('rel', 'icon')
    theirs.setAttribute('href', '/theirs.ico')
    document.head.appendChild(theirs)
    installFavicon(document)
    expect(document.querySelector('link[href="/theirs.ico"]')).not.toBeNull()
    theirs.remove()
  })
})

describe('theme preference', () => {
  // happy-dom's global ``localStorage`` is a bare object with none of
  // the Storage methods, so the suite brings its own. That is also the
  // only way to test the throwing backend (private mode, file:// under
  // a strict UA), which the SPA has to survive without losing the pin.
  function fakeStorage({ throwOnWrite = false } = {}) {
    const data = new Map()
    return {
      getItem: (k) => (data.has(k) ? data.get(k) : null),
      setItem: (k, v) => {
        if (throwOnWrite) throw new Error('quota')
        data.set(k, String(v))
      },
      removeItem: (k) => {
        if (throwOnWrite) throw new Error('quota')
        data.delete(k)
      },
    }
  }

  beforeEach(() => {
    document.documentElement.removeAttribute('data-theme')
    vi.stubGlobal('localStorage', fakeStorage())
  })
  afterEach(() => {
    document.documentElement.removeAttribute('data-theme')
    vi.unstubAllGlobals()
  })

  it('defaults to system, which writes no attribute', () => {
    expect(applyStoredThemePreference()).toBe('system')
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
  })

  it('a pin writes data-theme and survives a reload', () => {
    setThemePreference('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(currentTheme()).toBe('dark')

    // "Reload": forget the in-memory ref, re-read storage.
    themePreference.value = 'system'
    document.documentElement.removeAttribute('data-theme')
    expect(applyStoredThemePreference()).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('returning to system clears both the attribute and the memory', () => {
    setThemePreference('light')
    setThemePreference('system')
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
    expect(localStorage.getItem('rb-theme')).toBeNull()
  })

  it('bumps themeVersion so baked-colour consumers redraw', () => {
    const before = themeVersion.value
    setThemePreference('dark')
    expect(themeVersion.value).toBeGreaterThan(before)
  })

  it('survives a storage backend that throws', () => {
    vi.stubGlobal('localStorage', fakeStorage({ throwOnWrite: true }))
    expect(() => setThemePreference('dark')).not.toThrow()
    // The pin still applies — only remembering it across reloads is lost.
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })
})
