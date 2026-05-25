// Capture-path unit tests focused on the PNG rasterise timeout
// added for rtl-buddy-view#101. The happy SVG branch and the
// no-graph rejection are exercised at the envelope level by
// hub.spec.js — this file targets the canvas/Image branch that
// jsdom can't drive to completion.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  captureGraphImage,
  registerSvgProvider,
  unregisterSvgProvider,
} from '../src/capture.js'

function makeFakeSvg() {
  // Same shape as hub.spec.js's helper — attached to the document
  // so XMLSerializer keeps the <svg> tag rather than falling
  // through to the HTML serializer.
  const ns = 'http://www.w3.org/2000/svg'
  const svg = document.createElementNS(ns, 'svg')
  svg.setAttribute('viewBox', '0 0 100 50')
  svg.setAttribute('width', '100')
  svg.setAttribute('height', '50')
  document.body.appendChild(svg)
  return svg
}

describe('captureGraphImage PNG rasterise timeout (#101)', () => {
  let provider
  let warnSpy

  beforeEach(() => {
    const svg = makeFakeSvg()
    provider = () => svg
    registerSvgProvider(provider)
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    unregisterSvgProvider(provider)
    warnSpy.mockRestore()
    // Tear down the SVG we attached.
    document.body.innerHTML = ''
  })

  it('rejects with a timeout error when the Image never fires onload', async () => {
    // jsdom never resolves the Image data URL, so a short timeoutMs
    // is the only thing that ever ends this promise. That's the
    // exact wedge condition #101 reports.
    await expect(captureGraphImage('png', { timeoutMs: 20 })).rejects.toThrow(
      /PNG rasterise timed out after 20ms/,
    )
  })

  it('emits a diagnostic console.warn including an SVG sample on timeout', async () => {
    await expect(captureGraphImage('png', { timeoutMs: 20 })).rejects.toThrow(/timed out/)
    // The warn message should name the verb, the elapsed ms, the
    // SVG byte count, the work-around hint, and contain at least
    // a serialised ``<svg`` token from the head sample.
    expect(warnSpy).toHaveBeenCalledTimes(1)
    const msg = warnSpy.mock.calls[0][0]
    expect(msg).toMatch(/view_capture/)
    expect(msg).toMatch(/timed out after 20ms/)
    expect(msg).toMatch(/bytes of SVG/)
    expect(msg).toMatch(/rb hub send capture --out/)
    expect(msg).toMatch(/<svg/)
  })

  it('SVG format ignores timeoutMs (synchronous path)', async () => {
    // The SVG branch does not go through Image+canvas, so a near-zero
    // timeout must not affect it. Setting timeoutMs to 1ms and not
    // awaiting macrotasks would still resolve cleanly.
    const result = await captureGraphImage('svg', { timeoutMs: 1 })
    expect(result.format).toBe('svg')
    expect(result.width).toBe(100)
    expect(result.height).toBe(50)
    expect(warnSpy).not.toHaveBeenCalled()
  })

  it('rejects when no svg provider is registered', async () => {
    unregisterSvgProvider(provider)
    await expect(captureGraphImage('png', { timeoutMs: 20 })).rejects.toThrow(
      /no graph rendered/,
    )
    // No timeout fires — the precondition error is synchronous.
    expect(warnSpy).not.toHaveBeenCalled()
  })
})
