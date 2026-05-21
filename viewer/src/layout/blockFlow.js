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
//     ports on child B → emit ``A -> B`` with the net as label.
//   - Nets matching a top-level scope input port → emit
//     ``_in_<port> -> child`` (signal enters the scope).
//   - Nets matching a top-level scope output port → emit
//     ``child -> _out_<port>`` (signal leaves the scope).
//
// Clock and reset nets are skipped because they fan out to nearly
// every child and would bury the data flow.

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
 *   - ``rankdir=LR``, ``splines=ortho`` for schematic-style edges
 *   - ``Courier`` monospace font with the wide node margins viz.js
 *     needs to keep labels inside polygons
 *   - Top-level cluster framing the scope with port anchors at
 *     ``rank=source`` (inputs) / ``rank=sink`` (outputs)
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
  const sinksByNet = new Map() // net -> [{ child, port }]
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
  lines.push('  splines="ortho";')
  lines.push('  nodesep=0.18;')
  lines.push('  ranksep=1.2;')
  lines.push('  fontname="Courier,monospace";')
  lines.push(
    `  node [shape=box, style="rounded,filled", fillcolor="#f5f5f5",` +
      ` fontname="Courier,monospace", margin="0.4,0.06"];`,
  )
  lines.push('  edge [fontname="Courier,monospace"];')
  lines.push('')
  lines.push('  subgraph cluster_flow_scope {')
  const title = dotEscape(`${scope.instance_name || scope.module}\\l${scope.module}\\l`)
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

  // Child boxes. Two-line label: instance name, module type.
  for (const child of children) {
    const inst = child.instance_name || child.module
    const label = `${dotEscape(inst)}\\l${dotEscape(child.module)}\\l`
    const attrs = [`label="${label}"`, `group="cluster_flow_scope"`]
    if (child.is_blackbox) {
      attrs.push('style="rounded,filled,dashed"')
    }
    lines.push(`    ${dotId(child.id)} [${attrs.join(', ')}];`)
  }

  // External signal-flow: scope input -> child sink, child driver -> scope output.
  const seenIn = new Set()
  const seenOut = new Set()
  for (const [net, sinks] of sinksByNet) {
    if (!scopeInputs.has(net)) continue
    for (const { child } of sinks) {
      const key = `${net}->${child.id}`
      if (seenIn.has(key)) continue
      seenIn.add(key)
      lines.push(
        `    "_in_${net}" -> ${dotId(child.id)} ` +
          `[color="#cbd5e1", penwidth=1.2, arrowsize=0.6, tailport=e];`,
      )
    }
  }
  for (const [net, drivers] of driversByNet) {
    if (!scopeOutputs.has(net)) continue
    for (const { child } of drivers) {
      const key = `${child.id}->${net}`
      if (seenOut.has(key)) continue
      seenOut.add(key)
      lines.push(
        `    ${dotId(child.id)} -> "_out_${net}" ` +
          `[color="#cbd5e1", penwidth=1.2, arrowsize=0.6,` +
          ` tailport=e, headport=w];`,
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
        const key = `${drv.child.id}->${snk.child.id}|${net}`
        if (seenInternal.has(key)) continue
        seenInternal.add(key)
        lines.push(
          `    ${dotId(drv.child.id)} -> ${dotId(snk.child.id)} ` +
            `[label="${dotEscape(net)}", penwidth=1.2,` +
            ` arrowsize=0.7, tailport=e, headport=w];`,
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
