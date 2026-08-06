// Brand identity marks for the SPA bundle.
//
// The hub deliberately does NOT inject a favicon into the served
// bundle — a bundle that owns its own ``<link rel=icon>`` looks the
// same whether it is served at ``/view``, opened from a single-file
// ``embed.py`` export, or run off the Vite dev server. So we own it.
//
// All three PNGs are under Vite's 4 kB inline threshold, which means
// they arrive as ``data:`` URIs in the built bundle: no extra request
// under the hub, and ``embed.py`` needs no new inlining rule for the
// standalone HTML.
//
// Usage is minimal by decree: a favicon, and one ~18px chip beside the
// wordmark in the header. No hero art, no watermarks, no per-panel
// decoration.

import favicon16 from './assets/rtl-buddy-favicon-16.png'
import favicon32 from './assets/rtl-buddy-favicon-32.png'
import logo from './assets/rtl-buddy-logo-80.png'

export const LOGO_URL = logo
export const FAVICON_16_URL = favicon16
export const FAVICON_32_URL = favicon32

/**
 * Install ``<link rel="icon">`` tags for both sizes.
 *
 * Idempotent: re-running replaces the tags this function wrote rather
 * than appending. A favicon the host page already declared with a
 * different marker is left alone.
 */
export function installFavicon(doc = typeof document !== 'undefined' ? document : null) {
  if (!doc || !doc.head) return
  for (const el of Array.from(doc.querySelectorAll('link[data-rb-favicon]'))) {
    el.remove()
  }
  for (const [size, href] of [
    ['32x32', favicon32],
    ['16x16', favicon16],
  ]) {
    const link = doc.createElement('link')
    link.setAttribute('data-rb-favicon', 'true')
    link.setAttribute('rel', 'icon')
    link.setAttribute('type', 'image/png')
    link.setAttribute('sizes', size)
    link.setAttribute('href', href)
    doc.head.appendChild(link)
  }
}
