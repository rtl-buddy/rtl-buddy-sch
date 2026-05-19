// Viewer entry. Wires Vue 3 + Pinia, mounts App, and hands off to
// the store for view.json loading. The store's source-of-truth
// inputs are: ?view= URL query param (highest priority), the
// `window.__RTL_BUDDY_VIEW_DATA__` injection point (for embed.py
// self-contained HTML), drag-and-drop, and the file picker.
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { useViewerStore } from './store.js'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.mount('#app')

// Kick off the initial load on next tick so the store is wired
// through Pinia before the loader writes into it.
queueMicrotask(() => {
  const store = useViewerStore()
  store.bootstrap()
})
