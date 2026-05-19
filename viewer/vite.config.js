// Vite config for the rtl-buddy-view interactive viewer.
//
// Layout in the browser via viz.js (Graphviz compiled to WASM), so
// the viewer is fully static and distributable as a single HTML
// file (see embed.py — wraps the build output + an inlined
// view.json into one .html). No server runtime; no fetches at
// open time once embedded.
//
// `base: ''` keeps every asset reference relative so the build
// loads correctly whether served from `/`, a path prefix, or
// opened straight off the filesystem after embed.py inlining.
//
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '',
  plugins: [vue()],
  build: {
    outDir: 'dist',
    sourcemap: true,
    // Cap viz.js WASM at the budget called out in #18 (≤2 MB
    // gzipped for the standalone HTML); the warning prints in CI
    // if a future change pushes us over.
    chunkSizeWarningLimit: 2048,
    rollupOptions: {
      output: {
        // Inline dynamic ``import('@viz-js/viz')`` into the main
        // bundle for the standalone HTML use case — ``embed.py``
        // walks ``<script src>`` references and would otherwise
        // miss the runtime-resolved chunk. The dev server is
        // unaffected (it doesn't run rollup), so first-render
        // perception cost stays at "Vue shell → WASM" both ways.
        inlineDynamicImports: true,
      },
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
  },
})
