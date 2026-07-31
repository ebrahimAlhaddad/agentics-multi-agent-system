"use client";

import { useEffect, useState } from "react";
import {
  createSession, deleteInput, deleteSession, listInputs, listRuns, listSessions,
  type Artifact, type RouterRunSummary, type SessionSummary,
} from "@/lib/api";
import { Trash2, X } from "lucide-react";

/**
 * Everything already uploaded or run, so a session is not one-shot.
 *
 * Deliberately a flat list rather than anything cleverer — there is one session
 * per user, so this is the whole history.
 */
const STATUS_COLOUR: Record<string, string> = {
  done: "text-emerald-600",
  failed: "text-rose-600",
  awaiting_approval: "text-amber-600",
  blocked: "text-amber-600",
};

export default function HistoryPanel({
  inputId,
  sessionId,
  onPickSession,
  onPickInput,
  onPickRun,
  refreshKey,
}: {
  inputId?: string | null;
  sessionId?: string | null;
  onPickSession: (id: string) => void;
  onPickInput: (id: string) => void;
  onPickRun: (id: string) => void;
  refreshKey: number;
}) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [inputs, setInputs] = useState<Artifact[]>([]);
  const [runs, setRuns] = useState<RouterRunSummary[]>([]);
  const current = sessions.find((s) => s.session_id === sessionId);

  // The backend creates one on first call, so an empty list is never a state
  // the user has to resolve themselves.
  useEffect(() => {
    listSessions()
      .then((s) => {
        setSessions(s);
        if (!sessionId && s[0]) onPickSession(s[0].session_id);
      })
      .catch(() => {});
  }, [refreshKey]);

  useEffect(() => {
    listInputs(sessionId).then(setInputs).catch(() => {});
    listRuns(sessionId).then(setRuns).catch(() => {});
  }, [refreshKey, sessionId]);

  async function remove(input: Artifact) {
    // Confirmed because the bytes go with the row, and a run that already read
    // it keeps its results but the source is gone.
    if (!confirm(`Delete ${input.name}? This cannot be undone.`)) return;
    await deleteInput(input.artifact_id, sessionId);
    setInputs((prev) => prev.filter((i) => i.artifact_id !== input.artifact_id));
    if (input.artifact_id === inputId) onPickInput("");
  }

  async function removeSession(session: SessionSummary) {
    if (!confirm(`Delete "${session.title}" and everything in it? This cannot be undone.`))
      return;
    await deleteSession(session.session_id);
    const left = sessions.filter((s) => s.session_id !== session.session_id);
    setSessions(left);

    // Deleting the open one leaves nowhere to be, so land somewhere: the next
    // session if there is one, a fresh one if there is not.
    if (session.session_id === sessionId) {
      if (left[0]) {
        onPickSession(left[0].session_id);
      } else {
        const created = await createSession();
        setSessions([created]);
        onPickSession(created.session_id);
      }
    }
  }

  async function onNew() {
    const created = await createSession();
    setSessions((prev) => [created, ...prev]);
    onPickSession(created.session_id);
  }

  return (
    <div className="space-y-4 text-xs">
      <div>
        <h3 className="mb-1 font-medium text-slate-700">Session</h3>
        <div className="flex items-center gap-1">
          <select
            value={sessionId ?? ""}
            onChange={(e) => onPickSession(e.target.value)}
            className="min-w-0 flex-1 rounded border bg-white px-2 py-1 text-slate-700"
          >
            {sessions.map((s) => (
              <option key={s.session_id} value={s.session_id}>
                {s.title || s.session_id.slice(0, 8)}
              </option>
            ))}
          </select>
          {/* Beside the dropdown rather than on hover: a select has no row to
              point at, and this deletes the whole workspace. */}
          <button
            aria-label="Delete this session"
            title="Delete this session and everything in it"
            disabled={!current}
            onClick={() => current && removeSession(current)}
            className="rounded border p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-40"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
        <button
          onClick={onNew}
          className="mt-1 w-full rounded border border-dashed px-2 py-1 text-slate-500 hover:bg-slate-50"
        >
          + New session
        </button>
      </div>
      <div>
        <h3 className="mb-1 font-medium text-slate-700">Inputs</h3>
        {inputs.length === 0 && <p className="text-slate-400">None yet.</p>}
        <ul className="space-y-1">
          {inputs.map((d) => (
            // `group` so the delete only appears on the row being pointed at —
            // it is destructive and there is no undo.
            <li key={d.artifact_id} className="group relative">
              <button
                onClick={() => onPickInput(d.artifact_id)}
                className={`w-full truncate rounded px-2 py-1 pr-7 text-left hover:bg-slate-100 ${
                  d.artifact_id === inputId ? "bg-indigo-50 text-indigo-700" : "text-slate-600"
                }`}
              >
                {d.name}
                <span className="ml-1 text-slate-400">
                  {d.row_count != null && `· ${d.row_count} rows`}
                </span>
              </button>
              <button
                aria-label={`Delete ${d.name}`}
                title="Delete"
                onClick={() => remove(d)}
                className="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600 group-hover:block"
              >
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <h3 className="mb-1 font-medium text-slate-700">Runs</h3>
        {runs.length === 0 && <p className="text-slate-400">None yet.</p>}
        <ul className="space-y-1">
          {runs.map((r) => (
            <li key={r.run_id}>
              <button
                onClick={() => onPickRun(r.run_id)}
                className="w-full rounded px-2 py-1 text-left hover:bg-slate-100"
              >
                <span className="block truncate text-slate-600">
                  {r.question || "(no question)"}
                </span>
                <span className={`text-[10px] ${STATUS_COLOUR[r.status] ?? "text-slate-400"}`}>
                  {r.status}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
