"use client";

import { useMemo, useState } from "react";
import type { BlastNode, BlastRadius } from "@/lib/api-client";
import { cn } from "@/lib/utils";

const COLUMN_WIDTH = 220;
const COLUMN_GAP = 72;
const ROW_HEIGHT = 34;
const NODE_HEIGHT = 26;
const HEADER = 28;
const MAX_LABEL = 26;

function columnTitle(distance: number): string {
  if (distance === 0) return "Changed";
  if (distance === 1) return "Direct callers";
  return `${distance} calls away`;
}

function shortName(name: string): string {
  // "pkg.service.Loader.load" → "Loader.load": the module is in the tooltip.
  const parts = name.split(".");
  const tail = parts.slice(-2).join(".");
  return tail.length > MAX_LABEL ? `${tail.slice(0, MAX_LABEL - 1)}…` : tail;
}

function where(node: BlastNode): string {
  return `${node.file_path}:${node.line_start}${node.line_end !== node.line_start ? `-${node.line_end}` : ""}`;
}

/**
 * Blast radius: the symbols a pull request changes (left) and everything
 * that calls them, one column per call distance. Read off the branch's
 * code graph, not guessed by a model. Pointing at (or tabbing to) a symbol
 * highlights the calls into and out of it; the same information is listed
 * by file underneath.
 */
export function BlastRadiusView({ radius }: { radius: BlastRadius }) {
  const [active, setActive] = useState<number | null>(null);

  const layout = useMemo(() => {
    const columns = new Map<number, BlastNode[]>();
    for (const node of radius.nodes) {
      const column = columns.get(node.distance) ?? [];
      column.push(node);
      columns.set(node.distance, column);
    }
    const distances = [...columns.keys()].sort((a, b) => a - b);
    const position = new Map<number, { x: number; y: number }>();
    distances.forEach((distance, col) => {
      columns.get(distance)!.forEach((node, row) => {
        position.set(node.id, { x: col * (COLUMN_WIDTH + COLUMN_GAP), y: HEADER + row * ROW_HEIGHT });
      });
    });
    const rows = Math.max(1, ...[...columns.values()].map((c) => c.length));
    return {
      columns,
      distances,
      position,
      width: distances.length * COLUMN_WIDTH + (distances.length - 1) * COLUMN_GAP,
      height: HEADER + rows * ROW_HEIGHT,
    };
  }, [radius.nodes]);

  if (radius.changed === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        The change doesn&apos;t touch any function or class in the indexed code (for example, only non-Python files or
        top-level statements changed), so there is nothing to trace.
      </p>
    );
  }

  const byId = new Map(radius.nodes.map((n) => [n.id, n]));
  const connected = (id: number) =>
    radius.edges.some((e) => (e.caller === id || e.callee === id) && (active === e.caller || active === e.callee));
  const shown = active !== null ? byId.get(active) : null;
  const callsIn = shown ? radius.edges.filter((e) => e.callee === shown.id).length : 0;
  const callsOut = shown ? radius.edges.filter((e) => e.caller === shown.id).length : 0;

  const byFile = new Map<string, BlastNode[]>();
  for (const node of radius.nodes) {
    if (node.distance === 0) continue;
    byFile.set(node.file_path, [...(byFile.get(node.file_path) ?? []), node]);
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm">
        <span className="font-medium tabular-nums">{radius.changed}</span> changed{" "}
        {radius.changed === 1 ? "symbol is" : "symbols are"} called by{" "}
        <span className="font-medium tabular-nums">{radius.impacted}</span> other{" "}
        {radius.impacted === 1 ? "symbol" : "symbols"} in{" "}
        <span className="font-medium tabular-nums">{radius.impacted_files}</span> other{" "}
        {radius.impacted_files === 1 ? "file" : "files"}, up to {radius.depth} calls away.
        {radius.truncated && " Showing the nearest ones."}
      </p>
      <p className="min-h-5 font-mono text-xs text-muted-foreground" aria-live="polite">
        {shown
          ? `${shown.name} · ${where(shown)}${callsIn ? ` · called by ${callsIn} shown` : ""}${callsOut ? ` · calls ${callsOut} shown` : ""}`
          : "Point at a symbol to see where it is and what it calls."}
      </p>

      <div className="overflow-x-auto rounded-lg border bg-muted/20 p-3">
        <svg
          role="img"
          aria-label={`Call graph: ${radius.changed} changed symbols and ${radius.impacted} callers`}
          width={layout.width}
          height={layout.height}
          className="block"
        >
          {layout.distances.map((distance, col) => (
            <text
              key={distance}
              x={col * (COLUMN_WIDTH + COLUMN_GAP)}
              y={14}
              className="fill-muted-foreground text-[11px] font-semibold uppercase tracking-wide"
            >
              {columnTitle(distance)}
            </text>
          ))}

          {radius.edges.map((edge) => {
            const from = layout.position.get(edge.caller);
            const to = layout.position.get(edge.callee);
            if (!from || !to) return null;
            // Caller (right) → callee (left): from the caller's left edge.
            const x1 = from.x;
            const y1 = from.y + NODE_HEIGHT / 2;
            const x2 = to.x + COLUMN_WIDTH;
            const y2 = to.y + NODE_HEIGHT / 2;
            const mid = (x1 + x2) / 2;
            const lit = active !== null && (edge.caller === active || edge.callee === active);
            return (
              <path
                key={`${edge.caller}-${edge.callee}`}
                d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                fill="none"
                className={cn(
                  "transition-opacity",
                  lit ? "stroke-foreground opacity-90" : "stroke-muted-foreground",
                  active !== null && !lit ? "opacity-15" : !lit && "opacity-40",
                )}
                strokeWidth={lit ? 2 : 1.25}
              />
            );
          })}

          {radius.nodes.map((node) => {
            const pos = layout.position.get(node.id)!;
            const changed = node.distance === 0;
            const dim = active !== null && active !== node.id && !connected(node.id);
            return (
              <g
                key={node.id}
                transform={`translate(${pos.x}, ${pos.y})`}
                tabIndex={0}
                role="button"
                aria-label={`${node.name}, ${where(node)}, ${changed ? "changed" : `${node.distance} calls from a change`}`}
                onMouseEnter={() => setActive(node.id)}
                onMouseLeave={() => setActive(null)}
                onFocus={() => setActive(node.id)}
                onBlur={() => setActive(null)}
                className={cn("cursor-default outline-none transition-opacity", dim && "opacity-30")}
              >
                <title>{`${node.name}\n${where(node)}`}</title>
                <rect
                  width={COLUMN_WIDTH}
                  height={NODE_HEIGHT}
                  rx={6}
                  className={cn(
                    changed ? "fill-primary" : "fill-card stroke-border",
                    active === node.id && "stroke-foreground",
                  )}
                  strokeWidth={active === node.id ? 2 : 1}
                />
                <text
                  x={10}
                  y={NODE_HEIGHT / 2 + 4}
                  className={cn("font-mono text-xs", changed ? "fill-primary-foreground" : "fill-foreground")}
                >
                  {shortName(node.name)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {byFile.size > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">List the affected code by file</summary>
          <ul className="mt-2 flex flex-col gap-2">
            {[...byFile.entries()].map(([file, nodes]) => (
              <li key={file}>
                <span className="font-mono text-xs">{file}</span>
                <ul className="ml-4 list-disc text-xs text-muted-foreground">
                  {nodes.map((n) => (
                    <li key={n.id}>
                      <span className="font-mono text-foreground">{n.name}</span> (line {n.line_start}) ·{" "}
                      {n.distance === 1 ? "calls a changed symbol" : `${n.distance} calls away`}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
