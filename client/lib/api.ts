// Typed client for the backend. One place that knows the endpoints exist.
import { fetchAuthSession } from "aws-amplify/auth";

const BACKEND = process.env.NEXT_PUBLIC_BACKEND_PORT
  ? `${process.env.NEXT_PUBLIC_BACKEND_URL}:${process.env.NEXT_PUBLIC_BACKEND_PORT}`
  : process.env.NEXT_PUBLIC_BACKEND_URL;

export type ColumnProfile = {
  name: string;
  dtype: string;
  nulls: number;
  distinct: number;
  candidate_key?: boolean;
  all_null?: boolean;
  min?: number | string;
  max?: number | string;
  mean?: number;
  top_values?: { value: string; count: number }[];
  examples?: string[];
};

/** An upload and a task's output are the same thing; `origin` is the difference.
 *  `name` is qualified by its producer — 'input/sales', 'n_cohorts/cohorts'. */
export type Artifact = {
  artifact_id: string;
  name: string;
  origin: "input" | "transient" | "terminal";
  kind: "frame" | "chart" | "report";
  row_count?: number | null;
  columns?: ColumnProfile[] | null;
};

/** A task row is the graph node: depends_on rides on it, so there is no
 *  separate plan shape to keep in step with the states. */
export type TaskState = {
  task_id: string;
  role: string;
  description: string;
  status: "pending" | "running" | "validating" | "rework" | "done" | "failed";
  attempts: number;
  depends_on: string[];
  produces: string[];
  consumes: string[];
  error?: string | null;
};

export type Run = {
  run_id: string;
  status: string;
  question: string;
  rounds?: number;
  completed?: string[];
  failed?: string[];
  report?: string | null;
  error?: string | null;
  warnings?: string[];
};

export type RouterRunSummary = {
  run_id: string;
  question: string;
  status: string;
  inputs: string[] | null;
  created_at: string | null;
};

export type RouterRunDetail = {
  report?: string | null;
  /** What the faithfulness check found — shown beside the report, not instead. */
  report_note?: string | null;
  run_id: string;
  status: string;
  question: string;
  error?: string | null;
  tasks: TaskState[];
};

async function headers(json = true): Promise<Record<string, string>> {
  const base: Record<string, string> = json ? { "Content-Type": "application/json" } : {};
  // Auth is opt-in; with it disabled the backend is reachable without a token.
  if (process.env.NEXT_PUBLIC_DISABLE_AUTH === "false") {
    const token = (await fetchAuthSession()).tokens?.accessToken?.toString();
    if (token) base["Authorization"] = `Bearer ${token}`;
  }
  return base;
}

async function unwrap<T>(response: Response): Promise<T> {
  if (response.ok) return response.json();
  // FastAPI puts the useful message in `detail`; surface it rather than a status code.
  let detail = `${response.status} ${response.statusText}`;
  try {
    const body = await response.json();
    if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    /* non-JSON error body */
  }
  throw new Error(detail);
}

export async function uploadInput(file: File, sessionId?: string | null): Promise<Artifact> {
  const form = new FormData();
  form.append("file", file);
  return unwrap(
    await fetch(scoped("/artifacts", sessionId), {
      method: "POST",
      headers: await headers(false), // let the browser set the multipart boundary
      body: form,
    })
  );
}

/** Open a run. It starts empty, in `planning` — the question and the inputs
 *  both come out of the conversation. */
export async function createRun(sessionId?: string | null): Promise<Run> {
  return unwrap(
    await fetch(`${BACKEND}/runs`, {
      method: "POST",
      headers: await headers(),
      body: JSON.stringify({ session_id: sessionId ?? null }),
    })
  );
}

/** One event from the planner: text arriving, or a tool starting or finishing. */
export type StreamEvent =
  | { delta: string }
  | { tool: string; call_id: string; state: "running" }
  | { call_id: string; state: "done" };

export type ChatMessage = { role: "user" | "assistant"; content: string };

/** What was already said on a run, so reopening one shows its conversation
 *  rather than an empty panel. */
export async function getMessages(
  runId: string, sessionId?: string | null
): Promise<ChatMessage[]> {
  return unwrap(
    await fetch(scoped(`/agents/${runId}/messages`, sessionId), {
      headers: await headers(),
    })
  );
}

/**
 * Say something to the planner. Calls `onEvent` as things happen.
 *
 * Server-sent events, so `fetch` rather than EventSource — EventSource cannot
 * POST and cannot send an auth header. Resolves when the stream closes.
 *
 * The backend holds the conversation, keyed by run id, so nothing about the
 * history is passed here or kept by the caller beyond what it chooses to draw.
 */
export async function sendMessage(
  runId: string,
  content: string,
  sessionId: string | null | undefined,
  onEvent: (event: StreamEvent) => void
): Promise<void> {
  const response = await fetch(`${BACKEND}/agents/${runId}/messages`, {
    method: "POST",
    headers: await headers(),
    body: JSON.stringify({ content, session_id: sessionId ?? null }),
  });
  if (!response.ok || !response.body) {
    await unwrap(response); // throws with FastAPI's `detail`
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // A chunk can split an event, so keep the tail until its blank line lands.
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const event of events) {
      const line = event.trim();
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice(6);
      if (payload === "[DONE]") return;

      let parsed: StreamEvent & { error?: string };
      try {
        parsed = JSON.parse(payload);
      } catch {
        continue; // not something we know how to read; the next event will be
      }

      // The response has already started, so a failure arrives in the stream
      // rather than as a status code. Raise it like any other error.
      if (parsed.error) throw new Error(parsed.error);
      onEvent(parsed);
    }
  }
}

/** Hand the approved plan to the orchestrator. Lives under /agents, not /runs —
 *  approving starts work, and the run routes only read. */
export async function approveRun(
  runId: string, sessionId?: string | null
): Promise<Run> {
  return unwrap(
    await fetch(`${BACKEND}/agents/${runId}/approve`, {
      method: "POST",
      headers: await headers(),
      body: JSON.stringify({ session_id: sessionId ?? null }),
    })
  );
}

/** Delete a session and everything under it — runs, artifacts, objects. */
export async function deleteSession(id: string): Promise<void> {
  const response = await fetch(`${BACKEND}/sessions/${id}`, {
    method: "DELETE",
    headers: await headers(),
  });
  if (!response.ok) await unwrap(response);
}

export async function deleteInput(id: string, sessionId?: string | null): Promise<void> {
  const response = await fetch(scoped(`/artifacts/${id}`, sessionId), {
    method: "DELETE",
    headers: await headers(),
  });
  if (!response.ok) await unwrap(response);
}

export async function rejectRun(runId: string, sessionId?: string | null): Promise<Run> {
  return unwrap(
    await fetch(scoped(`/runs/${runId}/reject`, sessionId), {
      method: "POST",
      headers: await headers(),
    })
  );
}

export async function getRun(runId: string, sessionId?: string | null): Promise<RouterRunDetail> {
  return unwrap(
    await fetch(scoped(`/runs/${runId}`, sessionId), { headers: await headers() })
  );
}

export type ArtifactSummary = {
  name: string;
  kind: "frame" | "chart" | "report";
  task_id: string;
  row_count?: number | null;
};

export type ArtifactContent =
  | { name: string; kind: "frame"; columns: string[]; rows: Record<string, unknown>[]; row_count: number; truncated: boolean }
  | { name: string; kind: "chart" | "report"; value: any };

export async function listArtifacts(
  runId: string, sessionId?: string | null
): Promise<ArtifactSummary[]> {
  return unwrap(
    await fetch(scoped(`/runs/${runId}/artifacts`, sessionId), {
      headers: await headers(),
    })
  );
}

export async function getArtifact(
  runId: string, name: string, sessionId?: string | null
): Promise<ArtifactContent> {
  return unwrap(
    await fetch(
      scoped(`/runs/${runId}/artifacts/${encodeURIComponent(name)}`, sessionId),
      { headers: await headers() }
    )
  );
}

export type SessionSummary = { session_id: string; title: string | null; created: string | null };

export async function listSessions(): Promise<SessionSummary[]> {
  return unwrap(await fetch(`${BACKEND}/sessions`, { headers: await headers() }));
}

export async function createSession(title?: string): Promise<SessionSummary> {
  return unwrap(
    await fetch(`${BACKEND}/sessions`, {
      method: "POST",
      headers: await headers(),
      body: JSON.stringify({ title: title ?? null }),
    })
  );
}

// Every list and every run lookup is scoped to a session; omitting it falls
// back to the most recent, which would 404 a run opened from an older one.
function scoped(path: string, sessionId?: string | null): string {
  return sessionId ? `${BACKEND}${path}?session_id=${sessionId}` : `${BACKEND}${path}`;
}

export async function listInputs(sessionId?: string | null): Promise<Artifact[]> {
  return unwrap(await fetch(scoped("/artifacts", sessionId), { headers: await headers() }));
}

export async function getInput(id: string, sessionId?: string | null): Promise<Artifact> {
  return unwrap(await fetch(scoped(`/artifacts/${id}`, sessionId), { headers: await headers() }));
}

export async function listRuns(sessionId?: string | null): Promise<RouterRunSummary[]> {
  return unwrap(await fetch(scoped("/runs", sessionId), { headers: await headers() }));
}
