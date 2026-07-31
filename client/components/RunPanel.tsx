"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import TaskGraph from "@/components/TaskGraph";
import ChatPanel, { type Message } from "@/components/ChatPanel";
import { Button } from "@/components/ui/button";
import {
  approveRun, createRun, getMessages, getRun, sendMessage,
  type Artifact, type RouterRunDetail,
} from "@/lib/api";

/**
 * Plan an analysis by talking about it.
 *
 * The run opens empty and the conversation is the plan: once the planner has
 * enough it writes the task graph, the run parks in `awaiting_approval`, and
 * the graph appears below the chat. Nothing executes until it is approved.
 *
 * Messages are not kept anywhere but here. The backend holds the conversation
 * keyed by run id, and this only draws what it has seen this session — a
 * reload starts the transcript over even though the planner still remembers.
 */
export default function RunPanel({
  input: chosen,
  sessionId,
  openRunId,
  onChanged,
}: {
  input: Artifact | null;
  sessionId?: string | null;
  openRunId?: string | null;
  onChanged?: () => void;
}) {
  const [runId, setRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RouterRunDetail | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [tool, setTool] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const planned = (detail?.tasks?.length ?? 0) > 0;

  // Everything on screen belongs to one run in one session. When either
  // changes, all of it goes — including the case that used to be skipped:
  // `openRunId` back to null means "start a new one", and returning early there
  // left the previous run's graph on screen and its id in `runId`, so the next
  // message was posted into a run belonging to the session just left.
  useEffect(() => {
    let live = true;
    setRunId(openRunId ?? null);
    setDetail(null);
    setMessages([]);
    setInput("");
    setError(null);

    // Reopening a past run shows its graph and what was said to produce it.
    // The planner never forgot — the transcript simply used to live only in
    // the browser tab that typed it.
    if (openRunId) {
      getRun(openRunId, sessionId)
        .then((d) => live && setDetail(d))
        .catch(() => {});
      getMessages(openRunId, sessionId)
        .then((m) => live && setMessages(m))
        .catch(() => {});
    }
    return () => {
      live = false;
    };
  }, [openRunId, sessionId]);

  // While work is in flight nothing pushes to the browser, so the graph would
  // sit on `running` until something else happened to refetch it.
  useEffect(() => {
    if (!runId || detail?.status !== "running") return;
    const timer = setInterval(async () => {
      try {
        const fresh = await getRun(runId, sessionId);
        setDetail(fresh);
        if (fresh.status !== "running") onChanged?.();
      } catch {
        /* a poll that fails is not worth surfacing; the next one may not */
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [runId, detail?.status, sessionId]);

  async function send() {
    const content = input.trim();
    if (!content || busy) return;

    setBusy(true);
    setError(null);
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content }]);

    try {
      // Open the run on the first message rather than up front, so browsing
      // the page does not litter the database with empty runs.
      let id = runId;
      if (!id) {
        id = (await createRun(sessionId)).run_id;
        setRunId(id);
        onChanged?.();
      }

      // The assistant bubble is appended empty and grown in place, so a
      // half-arrived reply renders exactly like a finished one.
      setMessages((prev) => [...prev, { role: "assistant", content: "" }]);
      await sendMessage(id, content, sessionId, (event) => {
        if ("delta" in event) {
          setMessages((prev) => {
            const next = [...prev];
            next[next.length - 1] = {
              role: "assistant",
              content: next[next.length - 1].content + event.delta,
            };
            return next;
          });
        } else if (event.state === "running") {
          // Tool calls are where the reply goes quiet — build_plan is several
          // model calls with no text — so name what is happening.
          setTool(event.tool);
        } else {
          setTool(null);
        }
      });

      // The planner may have written a plan during that turn. Ask once the
      // stream closes; there is no event for it yet.
      setDetail(await getRun(id, sessionId));
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "something went wrong");
    } finally {
      setTool(null);
      setBusy(false);
    }
  }

  /** Approving is what starts execution — everything before it is planning. */
  async function approve() {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      await approveRun(detail.run_id, sessionId);
      // The orchestrator picks it up in its own process, so the answer to
      // "what is it doing" comes from polling, not from this call.
      setDetail(await getRun(detail.run_id, sessionId));
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not start the run");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <ChatPanel
        messages={messages}
        input={input}
        onInputChange={setInput}
        onSend={send}
        busy={busy}
        tool={tool}
        placeholder={
          chosen ? "What do you want to find out?" : "Upload a CSV first, then ask"
        }
      />

      {error && <p className="text-xs text-rose-600">{error}</p>}

      {planned && detail && (
        <div className="rounded-xl border bg-slate-50/60 p-4">
          <div className="mb-3 flex items-center gap-2">
            <span className="text-sm font-medium text-slate-700">
              {detail.tasks.length} tasks
            </span>
            <span className="text-[10px] uppercase tracking-wide text-slate-400">
              {detail.status}
            </span>
          </div>

          <TaskGraph tasks={detail.tasks} />

          {detail.status === "awaiting_approval" && (
            <div className="mt-3 flex items-center justify-center gap-3">
              <Button size="sm" onClick={approve} disabled={busy}>
                {busy ? "Starting…" : "Approve and run"}
              </Button>
              <span className="text-[11px] text-slate-400">
                or say what you would change
              </span>
            </div>
          )}

          {detail.status === "planning" && (
            <p className="mt-3 text-center text-[11px] text-slate-400">
              Say what you would change and it will re-plan.
            </p>
          )}

          {detail.status === "running" && (
            <p className="mt-3 text-center text-[11px] text-slate-400">
              Running — this updates as tasks finish.
            </p>
          )}

          {/* A failed run said why, in the task that failed. Without this the
              graph goes red and the reason stays in the container logs. */}
          {detail.error && (
            <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-900">
              <p className="mb-1 font-medium">This run stopped</p>
              <p className="whitespace-pre-wrap">{detail.error}</p>
            </div>
          )}
        </div>
      )}

      {detail?.report && (
        <div className="space-y-2">
          {/* The report is markdown — headings, lists and tables — and was
              being printed as its own source until now. */}
          <div className="prose prose-sm max-w-none rounded-xl border bg-white p-4 text-slate-700 prose-headings:font-medium prose-headings:text-slate-800 prose-table:text-xs">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.report}</ReactMarkdown>
          </div>

          {/* What the faithfulness check found, beside the answer rather than
              buried in a log. An answer nobody can weigh is worth less. */}
          {detail.report_note && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
              <p className="mb-1 font-medium">Checked against the results</p>
              <p>{detail.report_note}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
