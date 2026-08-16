// Build identity: which rtl-buddy is on the other end of /ws, and
// which SPA bundle this tab actually loaded.
//
// Both used to ride in the top bar as chips. They answer a question
// that comes up perhaps once a session ("am I on the build I just
// staged?"), so the full detail now lives in the hub popover next to
// the rest of the connection facts; the top bar keeps only controls a
// user touches. The bottom strip carries the SHORT form
// (``versionLabel``), identical in wording across every hub app.

/**
 * Truncate the long ``rtl-buddy`` build string to its leading semver
 * portion. The full string stays available for the popover's title.
 */
export function shortServerVersion(raw) {
  const s = typeof raw === 'string' ? raw : ''
  const m = s.match(/^[0-9]+\.[0-9]+\.[0-9]+(?:\.[a-z0-9]+)?/i)
  return m ? m[0] : s
}

// The version label the STRIP shows. Same helper, same output, in all
// three hub apps (SPA, /gph, /cov): one string a user can read off
// any pane and compare with `rb --version` without translating between
// three abbreviations. The popover keeps the full raw strings.
const SHA_RE = /(?:^|[.\-_])g([0-9a-f]{4,})(?=$|[.\-_])/i
const SHA_MAX = 9

/**
 * Turn a raw rtl-buddy version string into its display form.
 *
 * ``6.26.2.dev13+g3f5b890e3.d20260806`` → ``6.26.2.dev13 @ 3f5b890e3``
 * ``6.26.2``                           → ``6.26.2``
 *
 * The part before ``+`` is the base; the local segment after it is
 * mined for a ``g``-prefixed hex run (setuptools-scm's commit sha),
 * which is the only part of it worth screen space — the ``.dYYYYMMDD``
 * dirty-date suffix and non-sha locals are dropped. Returns null when
 * there is no version to show, so callers can render nothing rather
 * than a placeholder.
 */
export function versionLabel(raw) {
  if (typeof raw !== 'string') return null
  const s = raw.trim()
  if (!s) return null
  const [base, ...localParts] = s.split('+')
  // A sha with no base version to hang it off is not a label — the
  // panes' duplicated copy agrees (their empty-base case is null too).
  if (!base) return null
  const m = localParts.join('+').match(SHA_RE)
  const sha = m ? m[1].slice(0, SHA_MAX) : ''
  return sha ? `${base} @ ${sha}` : base
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
