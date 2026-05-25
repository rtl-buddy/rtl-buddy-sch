// Graph-only image capture. Serializes the SVG currently rendered by
// GraphCanvas to PNG (via an offscreen canvas) or raw SVG bytes.
//
// Surfaced two ways:
//   1. ``view_capture`` request envelope handler in useHub.js — lets
//      the hub (and any peer connected to it, including ``rb hub send
//      capture``) ask the SPA for a snapshot.
//   2. Future in-app "Save image" button — same entry point.
//
// Graph-only by design: side panels, toolbar, and the overlay legend
// are not included. v1 scope (rtl_buddy hub-side capture feature).

let _svgProvider = null

/**
 * GraphCanvas calls this on mount with a callable that returns its
 * current SVG element (or null if nothing is rendered yet). The
 * function form — instead of a ref to the element — keeps the
 * indirection alive across re-renders: viz.js rewrites ``innerHTML``
 * on every graph change, so the underlying <svg> identity moves.
 */
export function registerSvgProvider(fn) {
  _svgProvider = typeof fn === 'function' ? fn : null
}

export function unregisterSvgProvider(fn) {
  if (_svgProvider === fn) _svgProvider = null
}

/**
 * Returns the SVG element currently held by GraphCanvas, or null
 * when no graph is rendered (and therefore no capture is possible).
 */
export function currentGraphSvg() {
  return _svgProvider ? _svgProvider() : null
}

/**
 * Capture the current graph as a PNG (default) or SVG string.
 *
 * Returns ``{format, bytes_b64, width, height}``. Throws when no
 * graph is rendered, the format is unknown, or PNG rasterisation
 * fails (e.g. an in-SVG ``<image href=...>`` that violates CORS —
 * not used today but worth knowing).
 *
 * ``scale`` upsamples the PNG raster — useful for slide decks. SVG
 * ignores ``scale`` (vector).
 */
export async function captureGraphImage(format = 'png', { scale = 1 } = {}) {
  const svg = currentGraphSvg()
  if (!svg) {
    throw new Error('no graph rendered — load a view.json first')
  }
  const xml = serializeSvg(svg)
  const { width, height } = svgDimensions(svg)
  if (format === 'svg') {
    return {
      format: 'svg',
      bytes_b64: utf8ToBase64(xml),
      width,
      height,
    }
  }
  if (format !== 'png') {
    throw new Error(`unsupported capture format: ${format}`)
  }
  const w = Math.max(1, Math.ceil(width * scale))
  const h = Math.max(1, Math.ceil(height * scale))
  const dataUrl = await renderSvgToPng(xml, w, h)
  // ``data:image/png;base64,XXXX`` → ``XXXX``.
  const idx = dataUrl.indexOf(',')
  const bytes_b64 = idx >= 0 ? dataUrl.slice(idx + 1) : dataUrl
  return { format: 'png', bytes_b64, width: w, height: h }
}

// --- internals ---

function serializeSvg(svg) {
  // Clone so we can patch xmlns + dimensions without mutating the
  // live SVG. ``cloneNode(true)`` is a deep copy of the DOM — viz.js's
  // generated nodes use plain attributes (no shadow DOM), so the
  // clone reproduces correctly.
  const clone = svg.cloneNode(true)
  if (!clone.getAttribute('xmlns')) {
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  }
  if (!clone.getAttribute('xmlns:xlink')) {
    clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  }
  // viz.js often emits ``width="…pt"`` / ``height="…pt"``. For
  // standalone use we want the viewBox to be the source of truth; a
  // PNG-side raster scale beats hard-coded points.
  const { width, height } = svgDimensions(svg)
  clone.setAttribute('width', String(width))
  clone.setAttribute('height', String(height))
  return new XMLSerializer().serializeToString(clone)
}

function svgDimensions(svg) {
  const vb = svg.getAttribute('viewBox')
  if (vb) {
    const parts = vb.trim().split(/\s+/).map(Number)
    if (parts.length === 4 && parts.every((n) => Number.isFinite(n))) {
      return { width: parts[2], height: parts[3] }
    }
  }
  const w = parseFloat(svg.getAttribute('width')) || svg.clientWidth || 800
  const h = parseFloat(svg.getAttribute('height')) || svg.clientHeight || 600
  return { width: w, height: h }
}

function renderSvgToPng(svgXml, width, height) {
  return new Promise((resolve, reject) => {
    const blob = new Blob([svgXml], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const img = new Image()
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas')
        canvas.width = width
        canvas.height = height
        const ctx = canvas.getContext('2d')
        // Solid white backdrop — the SVG has no background and most
        // consumers (slides, docs, agent reads) prefer opaque PNGs
        // to checker-pattern transparency.
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, width, height)
        ctx.drawImage(img, 0, 0, width, height)
        const dataUrl = canvas.toDataURL('image/png')
        URL.revokeObjectURL(url)
        resolve(dataUrl)
      } catch (err) {
        URL.revokeObjectURL(url)
        reject(err)
      }
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('failed to rasterise SVG (image load error)'))
    }
    img.src = url
  })
}

function utf8ToBase64(str) {
  // ``btoa`` is latin-1 only; UTF-8 names in the SVG would corrupt.
  const bytes = new TextEncoder().encode(str)
  let bin = ''
  // Chunked to avoid call-stack blowups on very large strings.
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK))
  }
  return btoa(bin)
}
