// App-level keyboard shortcuts.
//
// Factored out of App.vue so the behaviour is unit-testable against a
// plain KeyboardEvent without mounting the whole shell. App.vue owns
// the register/unregister lifecycle.
//
// Shortcuts (discoverable from the NodeDetail button titles):
//   Esc — dismiss the disambiguation picker if it is open, otherwise
//         clear the selection. One key, two steps, most-recent-first.
//   u   — ascend one level. No-op unless a scope is set, so the key
//         is inert on a design sitting at its top.
//
// Deliberately NOT handled: anything with a modifier (that is a
// browser/OS shortcut), and anything typed into a form control — the
// model/test pickers are ``<select>`` elements where ``u`` is
// type-ahead.

const FORM_TAGS = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'OPTION'])

/** True when the event came from somewhere the user is typing. */
export function isTypingTarget(target) {
  if (!target || typeof target !== 'object') return false
  const tag = typeof target.tagName === 'string' ? target.tagName.toUpperCase() : ''
  if (FORM_TAGS.has(tag)) return true
  return target.isContentEditable === true
}

/**
 * Build the document-level keydown handler.
 *
 * @param {{store: object, hub: object}} deps
 * @returns {(event: KeyboardEvent) => void}
 */
export function makeGlobalKeydownHandler({ store, hub }) {
  return function onGlobalKeydown(event) {
    if (!event || event.defaultPrevented) return
    if (event.ctrlKey || event.metaKey || event.altKey) return
    if (isTypingTarget(event.target)) return

    if (event.key === 'Escape') {
      const candidates = store?.selectionCandidates
      if (Array.isArray(candidates) && candidates.length > 0) {
        hub?.dismissSelectionCandidates?.()
      } else {
        store?.clearSelection?.()
      }
      event.preventDefault()
      return
    }

    if (event.key === 'u' || event.key === 'U') {
      if (store?.rootInstancePath || store?.flowScope) {
        store.ascend()
        event.preventDefault()
      }
    }
  }
}
