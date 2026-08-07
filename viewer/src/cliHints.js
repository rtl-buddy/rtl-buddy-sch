// Copy-pasteable producer commands for the SPA's empty / disabled
// states. An empty state that only says what is missing makes the
// user go find the docs; these say what to run.
//
// Spelling is the repo's docs, not the SPA's guess:
//   README.md §"Overlays — `--overlay name=path`"
//   docs/overlays.md §5a
//   docs/axi-perf-overlay.md §"render the hierarchy with the overlay"
// The renderer is the ``rtl-buddy-view`` console script and overlays
// ride on a repeatable ``--overlay <name>=<path>`` flag. (There is no
// ``rb view render`` verb in this repo — under rtl_buddy the same
// render is driven by ``rb hier``/the hub, which passes these same
// overlay flags through.)

export const RENDER_WITH_OVERLAYS_HINT =
  'rtl-buddy-view --top <top> --filelist <files.f> ' +
  '--overlay clock=<clock_map.json> --overlay reset=<reset_map.json>'

export const RENDER_WITH_AXI_PERF_HINT =
  'rtl-buddy-view --top <top> --filelist <files.f> ' +
  '--overlay axi-perf=axi-perf.json'

// Tooltip for the disabled AXI Performance tab. One string so the tab
// and the tab's own empty state cannot drift apart.
export const AXI_PERF_HINT =
  `No axi-perf overlay in this view — re-render with \`${RENDER_WITH_AXI_PERF_HINT}\` and reload.`
