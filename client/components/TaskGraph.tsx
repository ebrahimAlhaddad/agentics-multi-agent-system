"use client";

import { useMemo } from "react";
import type { TaskState } from "@/lib/api";

/**
 * The task graph, laid out by dependency depth.
 *
 * Nodes in the same row have no dependency between them, which means they run at
 * the same time — that is the whole point of the plan being a graph, so the
 * layout is the explanation.
 *
 * Deliberately hand-rolled rather than pulling in a graph library: depth-ranking
 * a few dozen nodes is a dozen lines, and the dependency structure is the thing
 * worth showing, not curved edges.
 */

/** A task row is the node. It carries its own depends_on, so the shape of the
 *  graph and the state of each node arrive together — there is no second list
 *  to hold in step. */
type Node = TaskState;

const STATUS_STYLES: Record<string, { dot: string; ring: string; label: string }> = {
  pending: { dot: "bg-slate-300", ring: "border-slate-200", label: "pending" },
  running: { dot: "bg-amber-400 animate-pulse", ring: "border-amber-300", label: "running" },
  validating: { dot: "bg-sky-400 animate-pulse", ring: "border-sky-300", label: "checking" },
  rework: { dot: "bg-orange-500", ring: "border-orange-300", label: "rework" },
  done: { dot: "bg-emerald-500", ring: "border-emerald-300", label: "done" },
  failed: { dot: "bg-rose-500", ring: "border-rose-300", label: "failed" },
};

/** Longest path from a root — nodes at the same depth are independent. */
function rank(nodes: Node[]): Node[][] {
  const byId = new Map(nodes.map((n) => [n.task_id, n]));
  const depth = new Map<string, number>();

  const compute = (id: string, seen: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!;
    // Guard against a cycle rather than recursing forever. Validation should
    // make this unreachable, but a view must not hang on bad data.
    if (seen.has(id)) return 0;
    seen.add(id);
    const node = byId.get(id);
    const deps = (node?.depends_on ?? []).filter((d) => byId.has(d));
    const d = deps.length === 0 ? 0 : 1 + Math.max(...deps.map((x) => compute(x, seen)));
    depth.set(id, d);
    return d;
  };

  nodes.forEach((n) => compute(n.task_id, new Set()));
  const rows: Node[][] = [];
  nodes.forEach((n) => {
    const d = depth.get(n.task_id) ?? 0;
    (rows[d] ||= []).push(n);
  });
  return rows.filter(Boolean);
}

export default function TaskGraph({
  tasks,
  onSelect,
  selected,
  dropped,
  onToggleDrop,
}: {
  tasks?: TaskState[];
  onSelect?: (id: string) => void;
  selected?: string | null;
  dropped?: Set<string>;
  onToggleDrop?: (id: string) => void;
}) {
  // Defensive: task ids are unique per run, but a view must not break if one
  // is not — React silently drops or duplicates same-key children.
  const unique = useMemo(() => {
    const seen = new Set<string>();
    return (tasks ?? []).filter((t) => !seen.has(t.task_id) && seen.add(t.task_id));
  }, [tasks]);
  const rows = useMemo(() => rank(unique), [unique]);

  if (!unique.length) return null;

  return (
    <div className="space-y-3">
      {rows.map((row, i) => (
        <div key={i}>
          {i > 0 && (
            <div className="flex justify-center text-slate-300 text-xs leading-none mb-3">
              ↓
            </div>
          )}
          <div className="flex flex-wrap gap-3 justify-center">
            {row.map((node) => {
              const style = STATUS_STYLES[node.status ?? "pending"] ?? STATUS_STYLES.pending;
              const isDropped = dropped?.has(node.task_id);
              return (
                <button
                  key={node.task_id}
                  onClick={() =>
                    onToggleDrop ? onToggleDrop(node.task_id) : onSelect?.(node.task_id)
                  }
                  className={`text-left w-64 rounded-xl border bg-white px-3 py-2 shadow-sm transition
                    hover:shadow-md ${style.ring}
                    ${selected === node.task_id ? "ring-2 ring-indigo-400" : ""}
                    ${isDropped ? "opacity-40 line-through" : ""}`}
                >
                  <div className="flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${style.dot}`} />
                    <span className="font-mono text-[11px] text-slate-500">{node.task_id}</span>
                    <span className="ml-auto text-[10px] uppercase tracking-wide text-slate-400">
                      {node.role}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-700 line-clamp-2">{node.description}</p>
                  <div className="mt-1 flex items-center gap-2 text-[10px] text-slate-400">
                    <span>{style.label}</span>
                    {(node.attempts ?? 0) > 0 && <span>· {node.attempts} attempts</span>}
                    {!!node.depends_on?.length && <span>· needs {node.depends_on.length}</span>}
                  </div>
                </button>
              );
            })}
          </div>
          {row.length > 1 && (
            <p className="mt-2 text-center text-[10px] text-slate-400">
              {row.length} tasks · no dependency between them, so they run together
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
