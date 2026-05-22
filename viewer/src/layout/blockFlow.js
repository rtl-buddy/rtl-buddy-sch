// Block-flow DOT generator.
//
// The hier-view shows the full nested-cluster hierarchy from the
// producer's ``layout.dot``. This module produces a *one-level*
// view: pick a scope (an instance path), render its direct
// children as sibling boxes, and connect them via inferred signal
// flow.
//
// Connectivity is derived in the SPA from each child's
// ``ports[].expr`` field (the net expression the parent wired to
// that port). For each unique non-clock/reset net:
//   - ``out``-direction ports on child A drive ``in``-direction
//     ports on child B → emit ``A:p_out -> B:p_in`` with the net
//     as label.
//   - Nets matching a top-level scope input port → emit
//     ``_in_<port> -> child:p_in`` (signal enters the scope).
//   - Nets matching a top-level scope output port → emit
//     ``child:p_out -> _out_<port>`` (signal leaves the scope).
//
// Clock and reset nets are skipped because they fan out to nearly
// every child and would bury the data flow.
//
// Children render as ``shape=Mrecord`` with named record ports so
// every input/output gets its own attachment point along the west
// / east edge — polyline no longer piles every edge onto the
// geometric center of the side.

const CLOCK_RESET_RE = /(?:^|_)(?:clk|clock|rst|reset)(?:$|_)/i
// Bare-identifier net expressions only — drops slices / concats /
// expressions like ``a[0]`` or ``{x, y}`` so the diagram doesn't
// chase fragments through partial-bus connections. Matches the
// dot renderer's port-signal-flow filter.
const IDENTIFIER_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

function isClockOrResetName(name) {
  if (typeof name !== 'string') return false
  return CLOCK_RESET_RE.test(name)
}

function dotEscape(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

// Escape for embedding inside an HTML-like Graphviz label
// (``label=<...>`` syntax). The string lives inside table cells,
// so the standard HTML entity set applies.
function htmlEscape(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function dotId(id) {
  return `"${dotEscape(id)}"`
}

// Direct children of ``scopeId``: nodes whose instance path starts
// with ``<scopeId>.`` and has exactly one further dot-segment.
function directChildrenOf(graph, scopeId) {
  if (!graph || !graph.nodes || !scopeId) return []
  const prefix = scopeId + '.'
  const prefixDepth = prefix.length
  return graph.nodes.filter((n) => {
    if (!n.id.startsWith(prefix)) return false
    const rest = n.id.slice(prefixDepth)
    return rest.length > 0 && !rest.includes('.')
  })
}

function findNode(graph, id) {
  return graph && graph.nodes ? graph.nodes.find((n) => n.id === id) : null
}

function isInputDir(d) {
  return d === 'input'
}
function isOutputDir(d) {
  return d === 'output' || d === 'inout'
}

/**
 * Build the DOT source for a block-flow view of ``scopeId``'s
 * immediate children. Returns a DOT string ready for viz.js.
 *
 * Layout choices mirror the hier-view's standalone settings so the
 * two tabs share the same look-and-feel:
 *   - ``rankdir=LR``, ``splines=polyline`` for schematic-style edges
 *   - ``Courier`` monospace font
 *   - Top-level cluster framing the scope with port anchors at
 *     ``rank=source`` (inputs) / ``rank=sink`` (outputs)
 *   - Children render as ``shape=Mrecord`` with named record ports
 *     so each connection attaches at a distinct slot along the
 *     west / east edge of the box.
 */
export function buildBlockFlowDot(graph, scopeId) {
  if (!graph || !scopeId) return _emptyDigraph('no scope selected')
  const scope = findNode(graph, scopeId)
  if (!scope) return _emptyDigraph(`scope ${scopeId} not in graph`)
  const children = directChildrenOf(graph, scopeId)
  if (children.length === 0) {
    return _emptyDigraph(`${scopeId} has no children`)
  }

  // Index ports by net name across all children, partitioned by
  // direction. Skip clock/reset and non-identifier nets.
  const driversByNet = new Map() // net -> [{ child, port }]
  const sinksByNet = new Map() //   net -> [{ child, port }]
  for (const child of children) {
    for (const port of child.ports || []) {
      const expr = typeof port.expr === 'string' ? port.expr.trim() : ''
      if (!expr || !IDENTIFIER_RE.test(expr)) continue
      if (isClockOrResetName(expr)) continue
      if (isOutputDir(port.dir)) {
        if (!driversByNet.has(expr)) driversByNet.set(expr, [])
        driversByNet.get(expr).push({ child, port })
      } else if (isInputDir(port.dir)) {
        if (!sinksByNet.has(expr)) sinksByNet.set(expr, [])
        sinksByNet.get(expr).push({ child, port })
      }
    }
  }

  // Per-child port lists for the Mrecord label. Only ports that
  // actually participate in a derived connection get a record
  // slot — emitting every port would clutter the diagram with
  // unconnected stubs.
  const childInputPorts = new Map() // child.id -> [port name, ...]
  const childOutputPorts = new Map()
  for (const child of children) {
    childInputPorts.set(child.id, [])
    childOutputPorts.set(child.id, [])
  }
  const seenInPort = new Set() // `${child.id}:${port.name}`
  const seenOutPort = new Set()
  for (const drivers of driversByNet.values()) {
    for (const { child, port } of drivers) {
      const key = `${child.id}:${port.name}`
      if (seenOutPort.has(key)) continue
      seenOutPort.add(key)
      childOutputPorts.get(child.id).push(port.name)
    }
  }
  for (const sinks of sinksByNet.values()) {
    for (const { child, port } of sinks) {
      const key = `${child.id}:${port.name}`
      if (seenInPort.has(key)) continue
      seenInPort.add(key)
      childInputPorts.get(child.id).push(port.name)
    }
  }
  // Stable visual ordering: alphabetical within each side.
  for (const list of childInputPorts.values()) list.sort()
  for (const list of childOutputPorts.values()) list.sort()

  // Scope ports become the external interface. Their *names* are
  // also the net names that cross the scope boundary.
  const scopeInputs = new Set()
  const scopeOutputs = new Set()
  for (const port of scope.ports || []) {
    if (isClockOrResetName(port.name)) continue
    if (isInputDir(port.dir)) scopeInputs.add(port.name)
    else if (isOutputDir(port.dir)) scopeOutputs.add(port.name)
  }

  const lines = []
  lines.push('digraph block_flow {')
  lines.push('  rankdir="LR";')
  lines.push('  compound=true;')
  // ``splines=true`` = bezier routing. Drops the schematic
  // right-angle look in favour of smooth curves that respect
  // node boundaries reliably (viz.js's ortho router cuts through
  // nodes when an edge spans multiple ranks, and polyline
  // ditto for some layouts).
  lines.push('  splines=true;')
  lines.push('  nodesep=0.5;')
  lines.push('  ranksep=1.44;')
  lines.push('  fontname="Courier,monospace";')
  // ``shape=plaintext`` so the HTML-table label is drawn as-is —
  // the table itself supplies the border, background and per-cell
  // ports. (Mrecord would give rounded corners but doesn't support
  // per-cell fontsize, which we want for compact port labels.)
  lines.push(`  node [shape=plaintext, fontname="Courier,monospace"];`)
  lines.push('  edge [fontname="Courier,monospace"];')
  lines.push('')
  lines.push('  subgraph cluster_flow_scope {')
  // Title shape mirrors the child boxes (instance name on top,
  // module type below) — but when the two would be identical
  // (typical for the design top, where ``instance_name`` is null
  // and the fallback collapses to ``module``), emit one line to
  // avoid the redundant "X / X" stack.
  //
  // ``\l`` is the DOT left-aligned-newline marker. ``dotEscape``
  // doubles backslashes (correct for literal text but wrong for
  // record metachars), so escape the identifiers FIRST and
  // concatenate ``\l`` afterwards.
  const instName = scope.instance_name || ''
  const titleLines =
    instName && instName !== scope.module
      ? [instName, scope.module]
      : [scope.module]
  const title = titleLines.map((l) => `${dotEscape(l)}\\l`).join('')
  lines.push(`    label="${title}";`)
  lines.push('    labelloc="t";')
  lines.push('    labeljust="l";')
  lines.push('    style="rounded";')
  lines.push('    color="#94a3b8";')
  lines.push('    penwidth=2;')
  lines.push('    margin="20,20";')

  // Port anchors. Same shape as the hier-view's so the eye doesn't
  // have to relearn the convention when switching tabs.
  if (scopeInputs.size > 0) {
    lines.push('    { rank=source;')
    for (const p of [...scopeInputs].sort()) {
      lines.push(
        `      "_in_${p}" [shape=plaintext, label="${dotEscape(p)} ▶", fontsize=9];`,
      )
    }
    lines.push('    }')
  }
  if (scopeOutputs.size > 0) {
    lines.push('    { rank=sink;')
    for (const p of [...scopeOutputs].sort()) {
      lines.push(
        `      "_out_${p}" [shape=plaintext, label="▶ ${dotEscape(p)}", fontsize=9];`,
      )
    }
    lines.push('    }')
  }

  // HTML-table child labels. One row per port slot, with the
  // middle cell spanning all rows via ROWSPAN:
  //
  //   ┌──────────┬─────────┬───────────┐
  //   │ d_in     │  u_a    │      q    │
  //   │ aux_in   │  a_mod  │           │
  //   └──────────┴─────────┴───────────┘
  //
  // Input cells are ALIGN=LEFT, output cells ALIGN=RIGHT so the
  // text hugs the edge of the box on the side where the wire
  // attaches. Port labels use POINT-SIZE=9 to keep the box
  // compact even when a module has many ports.
  const PORT_FONT_SIZE = 9
  const BG = '#f5f5f5'
  for (const child of children) {
    const inPorts = childInputPorts.get(child.id) || []
    const outPorts = childOutputPorts.get(child.id) || []
    const inst = child.instance_name || child.module
    const rowCount = Math.max(inPorts.length, outPorts.length, 1)
    const rows = []
    for (let i = 0; i < rowCount; i++) {
      const tds = []
      // Left column: input port or filler. ``HREF`` is the only
      // per-cell attribute viz.js actually propagates to the SVG
      // (becomes ``<a xlink:href>``) — ``ID`` is silently dropped
      // on TDs. Click handlers walk up to the nearest ``<a>``
      // and route based on the href prefix.
      if (i < inPorts.length) {
        const p = inPorts[i]
        const cellId = `bf-in:${htmlEscape(child.id)}:${htmlEscape(p)}`
        tds.push(
          `<TD HREF="${cellId}" TITLE="${cellId}" PORT="${htmlEscape(p)}" ALIGN="LEFT">` +
            `<FONT POINT-SIZE="${PORT_FONT_SIZE}">${htmlEscape(p)}</FONT></TD>`,
        )
      } else {
        tds.push('<TD></TD>')
      }
      // Middle column: instance + module, only on first row.
      if (i === 0) {
        const cellId = `bf-ctr:${htmlEscape(child.id)}`
        tds.push(
          `<TD HREF="${cellId}" TITLE="${cellId}" ROWSPAN="${rowCount}" ALIGN="CENTER">` +
            `<B>${htmlEscape(inst)}</B><BR/>${htmlEscape(child.module)}</TD>`,
        )
      }
      // Right column: output port or filler.
      if (i < outPorts.length) {
        const p = outPorts[i]
        const cellId = `bf-out:${htmlEscape(child.id)}:${htmlEscape(p)}`
        tds.push(
          `<TD HREF="${cellId}" TITLE="${cellId}" PORT="${htmlEscape(p)}" ALIGN="RIGHT">` +
            `<FONT POINT-SIZE="${PORT_FONT_SIZE}">${htmlEscape(p)}</FONT></TD>`,
        )
      } else {
        tds.push('<TD></TD>')
      }
      rows.push(`<TR>${tds.join('')}</TR>`)
    }
    const label =
      `<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0"` +
      ` CELLPADDING="4" BGCOLOR="${BG}">${rows.join('')}</TABLE>>`
    const attrs = [`label=${label}`, `group="cluster_flow_scope"`]
    if (child.is_blackbox) {
      attrs.push('style="dashed"')
    }
    lines.push(`    ${dotId(child.id)} [${attrs.join(', ')}];`)
  }

  // External signal-flow: scope input -> child sink, child driver -> scope output.
  const seenIn = new Set()
  const seenOut = new Set()
  for (const [net, sinks] of sinksByNet) {
    if (!scopeInputs.has(net)) continue
    for (const { child, port } of sinks) {
      const key = `${net}->${child.id}:${port.name}`
      if (seenIn.has(key)) continue
      seenIn.add(key)
      lines.push(
        `    "_in_${net}" -> ${dotId(child.id)}:${port.name}:w ` +
          `[color="#cbd5e1", penwidth=1.2, arrowsize=0.6, tailport=e];`,
      )
    }
  }
  for (const [net, drivers] of driversByNet) {
    if (!scopeOutputs.has(net)) continue
    for (const { child, port } of drivers) {
      const key = `${child.id}:${port.name}->${net}`
      if (seenOut.has(key)) continue
      seenOut.add(key)
      lines.push(
        `    ${dotId(child.id)}:${port.name}:e -> "_out_${net}" ` +
          `[color="#cbd5e1", penwidth=1.2, arrowsize=0.6, headport=w];`,
      )
    }
  }

  // Internal signal-flow: driver -> sink for nets that don't touch
  // the scope boundary. ``label="net_name"`` so the reviewer can
  // tell which signal carries the connection at a glance.
  const seenInternal = new Set()
  for (const [net, drivers] of driversByNet) {
    if (scopeOutputs.has(net)) continue // already emitted above
    const sinks = sinksByNet.get(net) || []
    for (const drv of drivers) {
      for (const snk of sinks) {
        if (drv.child.id === snk.child.id) continue
        const key = `${drv.child.id}:${drv.port.name}->${snk.child.id}:${snk.port.name}`
        if (seenInternal.has(key)) continue
        seenInternal.add(key)
        lines.push(
          `    ${dotId(drv.child.id)}:${drv.port.name}:e -> ` +
            `${dotId(snk.child.id)}:${snk.port.name}:w ` +
            `[label="${dotEscape(net)}", penwidth=1.2, arrowsize=0.7];`,
        )
      }
    }
  }

  lines.push('  }')
  lines.push('}')
  return lines.join('\n')
}

function _emptyDigraph(reason) {
  // viz.js needs a valid digraph even when the scope is empty —
  // shows a placeholder so the canvas isn't a blank rectangle.
  return [
    'digraph block_flow_empty {',
    '  rankdir="LR";',
    '  fontname="Courier,monospace";',
    `  "_empty" [shape=plaintext, label="${dotEscape(reason)}"];`,
    '}',
  ].join('\n')
}
