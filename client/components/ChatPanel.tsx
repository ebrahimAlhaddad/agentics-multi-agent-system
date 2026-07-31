"use client";

import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

/**
 * The planning conversation.
 *
 * Presentational only — it holds no messages and fetches nothing. Streaming
 * belongs to the parent, which appends deltas to the last assistant message;
 * this just draws whatever it is given, so a half-arrived reply renders the
 * same way a finished one does.
 *
 * Restored from the panel that predated the run-based rewrite, minus the
 * syntax highlighter: the planner talks in prose, and a code block in a
 * planning chat would mean something has gone wrong.
 */

export type Message = { role: "user" | "assistant"; content: string };

/** What each tool is doing, in the user's terms rather than the function's. */
const TOOL_LABELS: Record<string, string> = {
  list_inputs: "Looking at what data you have",
  describe_input: "Reading the columns",
  sample_rows: "Looking at some rows",
  column_values: "Checking the values in a column",
  use_inputs: "Choosing the data to use",
  build_plan: "Working out the plan",
};

export default function ChatPanel({
  messages,
  input,
  onInputChange,
  onSend,
  busy = false,
  tool = null,
  placeholder = "What do you want to find out?",
}: {
  messages: Message[];
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  busy?: boolean;
  tool?: string | null;
  placeholder?: string;
}) {
  const scroller = useRef<HTMLDivElement>(null);

  // Follow the stream. Depends on the message contents, not just the count,
  // because a reply grows in place while it arrives.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy, tool]);

  const canSend = !busy && input.trim().length > 0;

  return (
    <div className="flex h-full flex-col">
      <div ref={scroller} className="max-h-96 flex-1 space-y-2 overflow-y-auto pr-1">
        {messages.length === 0 && (
          <p className="py-6 text-center text-xs text-slate-400">
            Describe what you want to learn from your data.
          </p>
        )}

        {/* An assistant message is appended empty and grown as deltas arrive, so
            it has no content at all while a tool runs. Rendering it would put an
            empty grey box above the indicator. */}
        {messages.filter((m) => m.content).map((message, i) => (
          <div
            key={i}
            className={`rounded-lg px-3 py-2 text-xs shadow-sm ${
              message.role === "user"
                ? "ml-8 bg-indigo-50 text-indigo-900"
                : "mr-8 bg-slate-100 text-slate-800"
            }`}
          >
            {/* prose-sm, not prose-xs: that size does not exist, so this fell
                back to base prose — 1rem text and paragraph margins that
                overrode the bubble's own sizing. The margin resets keep a
                two-line answer from looking like a page. */}
            <div className="prose prose-sm max-w-none text-xs leading-snug prose-p:my-1 prose-headings:my-1 prose-headings:text-sm prose-ul:my-1 prose-ol:my-1 prose-li:my-0 prose-pre:my-1 prose-pre:text-[11px]">
              <ReactMarkdown>{message.content}</ReactMarkdown>
            </div>
          </div>
        ))}

        {/* A tool call is the one time the reply goes quiet for several
            seconds, so say what it is doing. Otherwise this only shows before
            the first delta — after that the growing bubble is its own
            indicator. */}
        {busy && (tool || !messages[messages.length - 1]?.content) && (
          <div className="mr-8 flex items-center gap-2 rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-500">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-slate-400" />
            {tool ? TOOL_LABELS[tool] ?? tool.replace(/_/g, " ") : "Thinking…"}
          </div>
        )}
      </div>

      <div className="mt-3 flex gap-2">
        <Input
          value={input}
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && canSend && onSend()}
          placeholder={placeholder}
          disabled={busy}
          className="flex-1 text-xs"
        />
        <Button size="sm" onClick={onSend} disabled={!canSend}>
          Send
        </Button>
      </div>
    </div>
  );
}
