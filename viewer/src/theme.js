// Design-token reader + theme-change signal.
//
// Canvas and SVG drawing code can't say ``var(--accent)`` — viz.js
// bakes colours into the DOT it lays out, overlays write concrete
// values into inline styles and SVG attributes, and chart.js wants a
// string. So those paths read the token *at draw time* through
// ``token()`` and re-draw when ``themeVersion`` bumps.
//
// Every read carries the light-theme literal as its fallback, which
// keeps the module usable in three places where no stylesheet is
// attached: unit tests (happy-dom), the pure DOT builders, and the
// brief window before the bundle's CSS lands.

import { ref } from 'vue'

// Light-theme fallbacks. Deliberately the same values the vendored
// hub sheet + tokens.css carry for ``:root`` — a mismatch here would
// show up as a colour that changes when CSS finishes loading.
const FALLBACK = {
  '--bg': '#f8fafc',
  '--panel': '#ffffff',
  '--panel-2': '#f1f5f9',
  '--line': '#e2e8f0',
  '--line-strong': '#cbd5e1',
  '--fg': '#1e293b',
  '--fg-muted': '#64748b',
  '--fg-faint': '#94a3b8',
  '--accent': '#2563eb',
  '--accent-contrast': '#ffffff',
  '--ok': '#16a34a',
  '--warn': '#b45309',
  '--err': '#dc2626',
  '--info': '#0ea5e9',
  '--ok-bg': '#dcfce7',
  '--warn-bg': '#fef3c7',
  '--err-bg': '#fee2e2',
  '--cov-l': '82%',
  '--cov-none': '#e5e7eb',
  '--clk-1': '#dbeafe',
  '--clk-2': '#dcfce7',
  '--clk-3': '#fef9c3',
  '--clk-4': '#fce7f3',
  '--clk-5': '#ede9fe',
  '--clk-6': '#fed7aa',
  '--clk-7': '#cffafe',
  '--reset-bind': '#ea580c',
  '--reset-sync': '#0d9488',
  '--font-mono': 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  '--font-sans': '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
}

/**
 * Resolve a design token to a concrete value.
 *
 * Returns the light-theme fallback when there is no document, no
 * computed style, or the property is unset (happy-dom returns an
 * empty string for custom properties it has never seen).
 */
export function token(name) {
  const fallback = FALLBACK[name] ?? ''
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') {
    return fallback
  }
  const root = document.documentElement
  if (!root) return fallback
  let raw
  try {
    raw = getComputedStyle(root).getPropertyValue(name)
  } catch {
    return fallback
  }
  const value = typeof raw === 'string' ? raw.trim() : ''
  return value.length > 0 ? value : fallback
}

/** The token table this module falls back to. Exported for tests. */
export { FALLBACK as TOKEN_FALLBACKS }

// ---------------------------------------------------------------------
// Theme-change signal
// ---------------------------------------------------------------------
//
// Two things can flip the palette under a running SPA: the OS/browser
// colour-scheme preference, and an explicit ``data-theme`` pin on
// <html> (what the hub chrome's toggle would set). Both need the
// canvas re-drawn, because the colours are baked, not inherited.

export const themeVersion = ref(0)

let _installed = false

/** Current theme name — the pin if there is one, else the OS preference. */
export function currentTheme() {
  if (typeof document !== 'undefined' && document.documentElement) {
    const pinned = document.documentElement.getAttribute('data-theme')
    if (pinned === 'light' || pinned === 'dark') return pinned
  }
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    try {
      if (window.matchMedia('(prefers-color-scheme: dark)').matches) return 'dark'
    } catch {
      /* matchMedia unavailable (happy-dom without the shim) */
    }
  }
  return 'light'
}

/**
 * Start watching for theme changes. Idempotent — App.vue calls it on
 * mount and repeated calls are no-ops, so a remount doesn't stack
 * listeners.
 */
export function initTheme() {
  if (_installed) return
  _installed = true
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    try {
      const mq = window.matchMedia('(prefers-color-scheme: dark)')
      const onChange = () => {
        themeVersion.value += 1
      }
      if (typeof mq.addEventListener === 'function') {
        mq.addEventListener('change', onChange)
      } else if (typeof mq.addListener === 'function') {
        mq.addListener(onChange)
      }
    } catch {
      /* no matchMedia — the data-theme observer below still works */
    }
  }
  if (
    typeof MutationObserver === 'function' &&
    typeof document !== 'undefined' &&
    document.documentElement
  ) {
    const observer = new MutationObserver(() => {
      themeVersion.value += 1
    })
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    })
  }
}

/** Test seam: forget that ``initTheme`` ran. */
export function _resetThemeForTests() {
  _installed = false
  themeVersion.value = 0
}
