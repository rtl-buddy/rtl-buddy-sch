// Hub composable — Phase 5 stub. The Phase 10d wiring will replace
// this with a real WebSocket connection to the rtl-buddy hub
// daemon; today it's a no-op that records the call sites so the
// viewer's click-to-open path is fully wired and Phase 10d only
// needs to swap the implementation.
//
// Keeping the surface stable here (notifyClick(node), .state)
// means the GraphCanvas + HubStatus components don't change at
// all in Phase 10d; only this file flips from stub to live.

import { ref } from 'vue'

const state = ref('disconnected')
const lastClick = ref(null)

export function useHub() {
  return {
    state,
    lastClick,
    notifyClick(node) {
      // Phase 5: record the click locally so HubStatus can hint
      // at it; defer the actual hub dispatch to Phase 10d.
      lastClick.value = node
    },
  }
}
