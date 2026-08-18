// Static export of the schematic canvas (#163 P5).
//
// The interesting property is not "an SVG comes out" — it is that the
// file is *self-contained*: no ``var(--…)`` survives, the pan/zoom
// transform is gone, and the plate is opaque. All three are things a
// human only notices after opening the export in something that isn't
// the SPA, which is exactly the class of bug a test should catch.

import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  EXPORT_BACKGROUND,
  EXPORT_BLEED,
  INLINE_STYLE_PROPS,
  downloadUrl,
  exportFileName,
  inlineComputedStyles,
  standaloneSvgString,
  withLightTheme,
} from '../src/schExport.js'

const SVG_NS = 'http://www.w3.org/2000/svg'

/** A miniature of what SchematicCanvas renders. */
function makeSvg() {
  const host = document.createElement('div')
  const svg = document.createElementNS(SVG_NS, 'svg')
  svg.setAttribute('width', '400')
  svg.setAttribute('height', '300')
  const root = document.createElementNS(SVG_NS, 'g')
  root.setAttribute('transform', 'translate(12,34) scale(2)')
  const rect = document.createElementNS(SVG_NS, 'rect')
  rect.setAttribute('x', '10')
  rect.setAttribute('y', '10')
  rect.setAttribute('width', '80')
  rect.setAttribute('height', '40')
  rect.style.fill = 'rgb(1, 2, 3)'
  rect.style.stroke = 'rgb(4, 5, 6)'
  rect.style.strokeWidth = '1.5'
  root.appendChild(rect)
  svg.appendChild(root)
  host.appendChild(svg)
  document.body.appendChild(host)
  return { svg, root, rect, host }
}

beforeEach(() => {
  document.body.innerHTML = ''
  document.documentElement.removeAttribute('data-theme')
})

describe('exportFileName', () => {
  it('derives a stable, filesystem-safe name from the design top', () => {
    expect(exportFileName('blk_top', 'svg')).toBe('blk_top-schematic.svg')
    expect(exportFileName('top.u_p', 'png')).toBe('top.u_p-schematic.png')
  })

  it('replaces anything a filesystem would argue about', () => {
    expect(exportFileName('a/b c', 'svg')).toBe('a_b_c-schematic.svg')
  })

  it('names the file even with no design', () => {
    expect(exportFileName(null, 'svg')).toBe('schematic-schematic.svg')
  })

  it('carries no timestamp, so a re-export overwrites its predecessor', () => {
    expect(exportFileName('t', 'svg')).toBe(exportFileName('t', 'svg'))
  })
})

describe('withLightTheme', () => {
  it('pins light for the duration and restores "no pin" after', () => {
    let seen = null
    withLightTheme(() => {
      seen = document.documentElement.getAttribute('data-theme')
    })
    expect(seen).toBe('light')
    // ``system`` is spelled as the absence of the attribute; clobbering
    // it into ``light`` would silently change the user's theme.
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
  })

  it('restores an explicit pin', () => {
    document.documentElement.setAttribute('data-theme', 'dark')
    withLightTheme(() => {})
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('restores after a throw', () => {
    document.documentElement.setAttribute('data-theme', 'dark')
    expect(() =>
      withLightTheme(() => {
        throw new Error('boom')
      }),
    ).toThrow('boom')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })
})

describe('inlineComputedStyles', () => {
  it('writes the presentation properties onto the clone', () => {
    const { svg, rect } = makeSvg()
    const clone = svg.cloneNode(true)
    inlineComputedStyles(svg, clone)
    const cloned = clone.querySelector('rect')
    expect(cloned.getAttribute('style')).toContain('fill:')
    expect(cloned.getAttribute('style')).toContain('stroke:')
    // The live element is untouched — the export must not disturb the
    // canvas the user is still looking at.
    expect(rect.getAttribute('style')).not.toContain('font-family')
  })

  it('inlines only the listed properties', () => {
    expect(INLINE_STYLE_PROPS).toContain('fill')
    expect(INLINE_STYLE_PROPS).toContain('stroke-dasharray')
    // ``filter`` is viewer state (the selection halo), not drawing.
    expect(INLINE_STYLE_PROPS).not.toContain('filter')
  })
})

describe('standaloneSvgString', () => {
  const viewBox = { x: -18, y: -18, width: 436, height: 390 }

  it('takes its extent from the sheet frame, not the element', () => {
    const { svg } = makeSvg()
    const xml = standaloneSvgString(svg, { viewBox })
    // The frame is drawn in negative coordinates around the content,
    // so the element's own width/height would crop the border off.
    // Plus a bleed, because the frame's border is stroked ON the box
    // and half of it would otherwise fall outside the viewBox.
    expect(xml).toContain(
      `viewBox="${-18 - EXPORT_BLEED} ${-18 - EXPORT_BLEED} ` +
        `${436 + EXPORT_BLEED * 2} ${390 + EXPORT_BLEED * 2}"`,
    )
    expect(xml).toContain(`width="${436 + EXPORT_BLEED * 2}"`)
  })

  it('drops the pan/zoom transform', () => {
    const { svg } = makeSvg()
    const xml = standaloneSvgString(svg, { viewBox })
    expect(xml).not.toContain('translate(12,34)')
  })

  it('puts the drawing on an opaque plate', () => {
    const { svg } = makeSvg()
    const xml = standaloneSvgString(svg, { viewBox })
    expect(xml).toContain(`fill="${EXPORT_BACKGROUND}"`)
    // First child, so everything paints over it.
    expect(xml.indexOf('<rect')).toBeLessThan(xml.indexOf('<g'))
  })

  it('leaves no design token unresolved', () => {
    const { svg } = makeSvg()
    const xml = standaloneSvgString(svg, { viewBox })
    // Document CSS does not travel with the serialized clone: a
    // surviving var() is a stroke that renders as SVG's default black
    // (or nothing) once the file leaves the app.
    expect(xml).not.toContain('var(--')
  })

  it('declares the SVG namespace so the file opens standalone', () => {
    const { svg } = makeSvg()
    expect(standaloneSvgString(svg, { viewBox })).toContain(
      'xmlns="http://www.w3.org/2000/svg"',
    )
  })

  it('refuses to serialize nothing', () => {
    expect(() => standaloneSvgString(null, { viewBox })).toThrow(/no schematic/)
  })
})

describe('downloadUrl', () => {
  it('clicks a synthetic anchor and cleans it up', () => {
    const clicks = []
    const spy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function click() {
        clicks.push({ href: this.href, download: this.download })
      })
    downloadUrl('blob:fake', 'x-schematic.svg')
    expect(clicks).toHaveLength(1)
    expect(clicks[0].download).toBe('x-schematic.svg')
    // No orphan anchor left in the document.
    expect(document.querySelectorAll('a').length).toBe(0)
    spy.mockRestore()
  })
})
