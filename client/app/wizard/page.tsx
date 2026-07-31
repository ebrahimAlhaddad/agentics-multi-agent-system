"use client"

import { useState } from "react"
import { Toaster } from "@/components/ui/toaster"
import SlideDownNav from "@/components/SlideDownNav"
import InputPanel from "@/components/InputPanel"
import RunPanel from "@/components/RunPanel"
import HistoryPanel from "@/components/HistoryPanel"
import { getInput, type Artifact } from "@/lib/api"

export default function WorkspacePage() {
  const [input, setInput] = useState<Artifact | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [openRunId, setOpenRunId] = useState<string | null>(null)
  // Bumped whenever a run is created or finishes, so the sidebar refreshes.
  const [refreshKey, setRefreshKey] = useState(0)

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-100 via-blue-50 to-indigo-100">
      <SlideDownNav>
        <div className="mx-auto flex max-w-6xl gap-4 px-4 pt-4 pb-16">
          <aside className="w-56 shrink-0 rounded-2xl bg-white/80 p-3 shadow-sm">
            <HistoryPanel
              inputId={input?.artifact_id}
              sessionId={sessionId}
              refreshKey={refreshKey}
              onPickSession={(id) => {
                // Switching workspace clears what belonged to the old one.
                setSessionId(id)
                setInput(null)
                setOpenRunId(null)
              }}
              onPickInput={(id) => {
                getInput(id).then(setInput).catch(() => {})
                setOpenRunId(null)
              }}
              onPickRun={setOpenRunId}
            />
          </aside>

          <div className="min-w-0 flex-1 space-y-4">
            <section className="rounded-2xl bg-white/80 p-4 shadow-sm">
              <h2 className="mb-3 text-sm font-medium text-slate-800">Data</h2>
              <InputPanel
                sessionId={sessionId}
                input={input}
                onLoaded={(d) => {
                  setInput(d)
                  setRefreshKey((k) => k + 1)
                }}
              />
            </section>

            <section className="rounded-2xl bg-white/80 p-4 shadow-sm">
              <h2 className="mb-3 text-sm font-medium text-slate-800">Analysis</h2>
              <RunPanel
                sessionId={sessionId}
                input={input}
                openRunId={openRunId}
                onChanged={() => setRefreshKey((k) => k + 1)}
              />
            </section>
          </div>
        </div>
      </SlideDownNav>
      <Toaster />
    </div>
  )
}
