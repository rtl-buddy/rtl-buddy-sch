// R13 — global keyboard shortcuts.
//
// The handler is a plain function so these run against a bare
// KeyboardEvent instead of mounting the app shell.

import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { isTypingTarget, makeGlobalKeydownHandler } from '../src/keyboard.js'
import { useViewerStore } from '../src/store.js'

function graph(nodes) {
  return {
    schema_version: '1.0',
    top: 'top',
    nodes: nodes.map((n) => ({
      is_blackbox: false,
      parameters: {},
      ports: [],
      overlays: {},
      ...n,
    })),
    edges: [],
    overlays_present: [],
  }
}

describe('global keydown handler', () => {
  let store
  let dismissed
  let handler

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useViewerStore()
    store.loadFromText(
      JSON.stringify(
        graph([
          { id: 'top', module: 'top' },
          { id: 'top.u_a', module: 'a' },
          { id: 'top.u_a.u_b', module: 'b' },
        ]),
      ),
    )
    dismissed = 0
    handler = makeGlobalKeydownHandler({
      store,
      hub: { dismissSelectionCandidates: () => { dismissed++; store.dismissSelectionCandidates() } },
    })
  })

  function press(key, init = {}) {
    const ev = new KeyboardEvent('keydown', { key, cancelable: true, ...init })
    // happy-dom's ``target`` is null for an undispatched event; the
    // handler treats that as "not a form control", which is what we
    // want for the plain cases.
    handler(ev)
    return ev
  }

  it('Esc dismisses the picker first, and only then clears selection', () => {
    store.presentSelectionCandidates(['top.u_a', 'top.u_a.u_b'])
    expect(store.selectionCandidates).not.toBeNull()

    press('Escape')
    expect(dismissed).toBe(1)
    expect(store.selectionCandidates).toBeNull()
    // The applied default survives the first Esc.
    expect(store.selection).toBe('top.u_a')

    press('Escape')
    expect(store.selection).toBeNull()
  })

  it('u ascends one level, but only when a scope is set', () => {
    // No scope → inert. The design is already at its top.
    press('u')
    expect(store.rootInstancePath).toBeNull()

    store.descend('top.u_a')
    expect(store.rootInstancePath).toBe('top.u_a')
    press('u')
    expect(store.rootInstancePath).toBe('top')
    press('u')
    expect(store.rootInstancePath).toBeNull()
  })

  it('leaves modified keystrokes to the browser', () => {
    store.descend('top.u_a')
    press('u', { metaKey: true })
    press('u', { ctrlKey: true })
    press('u', { altKey: true })
    expect(store.rootInstancePath).toBe('top.u_a')
  })

  it('ignores keys typed into form controls', () => {
    store.descend('top.u_a')
    for (const tag of ['INPUT', 'SELECT', 'TEXTAREA']) {
      handler({ key: 'u', target: { tagName: tag } })
    }
    handler({ key: 'u', target: { tagName: 'DIV', isContentEditable: true } })
    expect(store.rootInstancePath).toBe('top.u_a')
    // …and does act on one that isn't.
    handler({ key: 'u', target: { tagName: 'DIV' }, preventDefault() {} })
    expect(store.rootInstancePath).toBe('top')
  })

  it('marks the keystrokes it consumed as handled', () => {
    store.select('top.u_a')
    expect(press('Escape').defaultPrevented).toBe(true)
    // An unhandled key is left alone.
    expect(press('q').defaultPrevented).toBe(false)
  })

  it('/ focuses the hierarchy filter, and yields the key when it cannot', () => {
    let focused = 0
    const withTree = makeGlobalKeydownHandler({
      store,
      hub: {},
      focusTreeFilter: () => {
        focused++
        return true
      },
    })
    const ev = new KeyboardEvent('keydown', { key: '/', cancelable: true })
    withTree(ev)
    expect(focused).toBe(1)
    expect(ev.defaultPrevented).toBe(true)

    // Panel collapsed / tree not mounted → the browser keeps the key
    // (quick-find in Firefox, plain typing everywhere else).
    const noTree = makeGlobalKeydownHandler({
      store,
      hub: {},
      focusTreeFilter: () => false,
    })
    const ev2 = new KeyboardEvent('keydown', { key: '/', cancelable: true })
    noTree(ev2)
    expect(ev2.defaultPrevented).toBe(false)

    // No callback at all (the pre-tree call shape) must not throw.
    expect(() => press('/')).not.toThrow()
  })

  it('leaves / alone while the filter box itself has focus', () => {
    let focused = 0
    const withTree = makeGlobalKeydownHandler({
      store,
      hub: {},
      focusTreeFilter: () => {
        focused++
        return true
      },
    })
    withTree({ key: '/', target: { tagName: 'INPUT' } })
    expect(focused).toBe(0)
  })

  it('isTypingTarget covers the picker <select> that shares the "u" key', () => {
    expect(isTypingTarget({ tagName: 'select' })).toBe(true)
    expect(isTypingTarget({ tagName: 'BUTTON' })).toBe(false)
    expect(isTypingTarget(null)).toBe(false)
  })
})
