"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Upload } from "lucide-react";
import { uploadInput, type Artifact } from "@/lib/api";

/** CSV upload, and the profile that comes back — what the planner will reason over. */
export default function InputPanel({
  input: chosen,
  sessionId,
  onLoaded,
}: {
  input: Artifact | null;
  sessionId?: string | null;
  onLoaded: (a: Artifact) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function pick(file?: File) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      onLoaded(await uploadInput(file, sessionId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <input
          ref={input}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => pick(e.target.files?.[0])}
        />
        <Button size="sm" onClick={() => input.current?.click()} disabled={busy}>
          <Upload className="mr-2 h-3.5 w-3.5" />
          {busy ? "Profiling…" : chosen ? "Add another CSV" : "Upload CSV"}
        </Button>
        {chosen && (
          <span className="text-xs text-slate-500">
            {chosen.name} · {chosen.row_count?.toLocaleString()} rows ·{" "}
            {chosen.columns?.length} columns
          </span>
        )}
      </div>

      {error && <p className="text-xs text-rose-600">{error}</p>}

      {chosen?.columns && (
        <div className="max-h-64 overflow-auto rounded-lg border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-slate-50 text-slate-500">
              <tr>
                <th className="px-2 py-1 text-left font-medium">Column</th>
                <th className="px-2 py-1 text-left font-medium">Type</th>
                <th className="px-2 py-1 text-right font-medium">Null</th>
                <th className="px-2 py-1 text-right font-medium">Distinct</th>
                <th className="px-2 py-1 text-left font-medium">Notes</th>
              </tr>
            </thead>
            <tbody>
              {chosen.columns.map((c) => (
                <tr key={c.name} className="border-t">
                  <td className="px-2 py-1 font-mono">{c.name}</td>
                  <td className="px-2 py-1 text-slate-500">{c.dtype}</td>
                  <td
                    className={`px-2 py-1 text-right ${
                      c.nulls > 0.5 ? "text-rose-600 font-medium" : "text-slate-500"
                    }`}
                  >
                    {(c.nulls * 100).toFixed(0)}%
                  </td>
                  <td className="px-2 py-1 text-right text-slate-500">{c.distinct}</td>
                  <td className="px-2 py-1 text-slate-400">
                    {/* Flag exactly what a planner would want to notice. */}
                    {c.all_null && <span className="text-rose-600">all null</span>}
                    {c.candidate_key && <span className="text-emerald-600">candidate key</span>}
                    {c.top_values && (
                      <span>{c.top_values.slice(0, 3).map((v) => v.value).join(", ")}</span>
                    )}
                    {c.min !== undefined && !c.top_values && (
                      <span>
                        {String(c.min)} … {String(c.max)}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
