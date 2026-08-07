// Build identity: which rtl-buddy is on the other end of /ws, and
// which SPA bundle this tab actually loaded.
//
// Both used to ride in the top bar as chips. They answer a question
// that comes up perhaps once a session ("am I on the build I just
// staged?"), so they now live in the hub popover next to the rest of
// the connection facts; the top bar keeps only controls a user
// touches.

/**
 * Truncate the long ``rtl-buddy`` build string to its leading semver
 * portion. The full string stays available for the popover's title.
 */
export function shortServerVersion(raw) {
  const s = typeof raw === 'string' ? raw : ''
  const m = s.match(/^[0-9]+\.[0-9]+\.[0-9]+(?:\.[a-z0-9]+)?/i)
  return m ? m[0] : s
}

/**
 * Extract the SPA bundle hash from the loaded ``index-<hash>.js``
 * filename so the user can confirm which build they're on (handy when
 * an editable-install hub re-stages the bundle but the browser tab
 * might still be on the old one). Empty under the Vite dev server (no
 * hashed asset) and the offline embed flow — both surface as "no
 * bundle row".
 */
export function readBundleHash(doc = typeof document === 'undefined' ? null : document) {
  if (!doc || typeof doc.querySelectorAll !== 'function') return ''
  for (const s of doc.querySelectorAll('script[src]')) {
    const m = String(s.src || '').match(/index-([\w-]+)\.js/)
    if (m) return m[1]
  }
  return ''
}
