// Design-token lockstep.
//
// Three copies of one palette is three chances to drift, so each pair
// that has to agree gets a test:
//
//   1. tokens.css's ``:root`` vs ``[data-theme="light"]``, and its dark
//      media query vs ``[data-theme="dark"]`` — the pin has to win in
//      both directions, and it can only do that if it carries the same
//      values;
//   2. the CSS layout constants vs ``layout/constants.js`` — the drag
//      clamp and ``calc(100vh - …)`` are the same numbers;
//   3. ``theme.js``'s light fallbacks vs what the stylesheets actually
//      declare — a mismatch there shows up as a colour that changes
//      once the CSS finishes loading.

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import { TOKEN_FALLBACKS } from '../src/theme.js'
import {
  HEADER_HEIGHT_PX,
  SIDEBAR_DEFAULT_WIDTH_PX,
  STATUS_STRIP_HEIGHT_PX,
} from '../src/layout/constants.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = resolve(HERE, '..', 'src')

const tokensCss = readFileSync(resolve(SRC, 'tokens.css'), 'utf8')
const themeCss = readFileSync(resolve(SRC, 'theme.css'), 'utf8')

// Strip comments first — the sheets document each family inline and a
// commented-out declaration must not read as a declaration.
function decomment(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

/**
 * Pull the custom-property declarations out of the first block whose
 * selector line matches ``selectorRe``. Returns ``Map<name, value>``.
 */
function blockVars(css, selectorRe) {
  const text = decomment(css)
  const match = text.match(selectorRe)
  if (!match) throw new Error(`no block matching ${selectorRe}`)
  const start = text.indexOf('{', match.index)
  // Brace-matched slice so a nested block (the dark media query wraps
  // a ``:root`` block) is captured whole.
  let depth = 0
  let end = start
  for (let i = start; i < text.length; i++) {
    if (text[i] === '{') depth += 1
    else if (text[i] === '}') {
      depth -= 1
      if (depth === 0) {
        end = i
        break
      }
    }
  }
  const body = text.slice(start + 1, end)
  const out = new Map()
  for (const line of body.split(';')) {
    const m = line.match(/(--[a-z0-9-]+)\s*:\s*([^;]+)/i)
    if (m) out.set(m[1], m[2].trim())
  }
  return out
}

describe('tokens.css light/dark lockstep', () => {
  const rootVars = blockVars(tokensCss, /^:root\s*\{/m)
  const lightPin = blockVars(tokensCss, /^:root\[data-theme='light'\]\s*\{/m)
  const darkMedia = blockVars(tokensCss, /@media \(prefers-color-scheme: dark\)/)
  const darkPin = blockVars(tokensCss, /^:root\[data-theme='dark'\]\s*\{/m)

  it('every themed token in :root is re-stated identically by the light pin', () => {
    expect(lightPin.size).toBeGreaterThan(0)
    for (const [name, value] of lightPin) {
      expect(rootVars.get(name), `${name} missing from :root`).toBe(value)
    }
  })

  it('the dark media query and the dark pin declare the same values', () => {
    expect(darkMedia.size).toBeGreaterThan(0)
    expect([...darkPin.keys()].sort()).toEqual([...darkMedia.keys()].sort())
    for (const [name, value] of darkMedia) {
      expect(darkPin.get(name), `${name} differs between dark blocks`).toBe(value)
    }
  })

  it('every token with a dark value also has a light one', () => {
    for (const name of darkMedia.keys()) {
      expect(rootVars.has(name), `${name} has no light value`).toBe(true)
    }
  })
})

describe('layout constants', () => {
  const rootVars = blockVars(tokensCss, /^:root\s*\{/m)

  it('CSS layout tokens match layout/constants.js', () => {
    expect(rootVars.get('--header-h')).toBe(`${HEADER_HEIGHT_PX}px`)
    expect(rootVars.get('--sidebar-w')).toBe(`${SIDEBAR_DEFAULT_WIDTH_PX}px`)
    expect(rootVars.get('--status-h')).toBe(`${STATUS_STRIP_HEIGHT_PX}px`)
  })
})

describe('theme.js fallbacks', () => {
  // ``:root`` of the vendored hub sheet, plus the SPA's own tokens.
  const declared = new Map([
    ...blockVars(themeCss, /^:root\s*\{/m),
    ...blockVars(tokensCss, /^:root\s*\{/m),
  ])

  it('every fallback names a token some sheet actually declares', () => {
    for (const name of Object.keys(TOKEN_FALLBACKS)) {
      expect(declared.has(name), `${name} is declared by no stylesheet`).toBe(true)
    }
  })

  it('every fallback equals the light-theme value', () => {
    for (const [name, value] of Object.entries(TOKEN_FALLBACKS)) {
      expect(declared.get(name), `${name} fallback drifted`).toBe(value)
    }
  })
})

describe('vendored hub sheet', () => {
  it('is the sheet the SPA layers on top of, not a fork', () => {
    // A one-line canary: the header the rtl_buddy generator writes.
    expect(themeCss).toContain('rtl-buddy hub theme')
    expect(themeCss).toContain(':root[data-theme="light"]')
    expect(themeCss).toContain(':root[data-theme="dark"]')
  })
})
