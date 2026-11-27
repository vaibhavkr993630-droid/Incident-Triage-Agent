import { useParams } from "react-router-dom";
import { api } from "../api";
import { Card, ErrorBox, Loading, OutcomeBadge, Verdict, fmtNum, fmtTime } from "../components/ui";
import type { ToolCall } from "../types";
import { useData } from "../useData";

function pretty(result: string): string {
  try {
    return JSON.stringify(JSON.parse(result), null, 2);
  } catch {
    return result; // a capped (truncated) result is no longer valid JSON
  }
}

function ToolCallItem({ call, cited }: { call: ToolCall; cited: boolean }) {
  const flagged = call.result.includes('"suspected_prompt_injection":true');
  return (
    <details
      id={call.id}
      className={`rounded border p-2 ${cited ? "border-indigo-300 bg-indigo-50/40" : "border-slate-200"}`}
    >
      <summary className="cursor-pointer text-sm">
        <span className="font-mono font-semibold">[{call.id}]</span> {call.name}{" "}
        <span className="font-mono text-xs text-slate-500">{JSON.stringify(call.arguments)}</span>
        <span className="ml-2 text-xs text-slate-400">{call.duration_ms} ms</span>
        {call.is_error && <span className="ml-2 rounded bg-red-100 px-1 text-xs text-red-800">error</span>}
        {flagged && (
          <span className="ml-2 rounded bg-amber-100 px-1 text-xs text-amber-900">
            suspected prompt injection
          </span>
        )}
        {cited && <span className="ml-2 rounded bg-indigo-100 px-1 text-xs text-indigo-800">cited</span>}
      </summary>
      <pre className="mt-2 max-h-96 overflow-auto rounded bg-slate-900 p-3 text-xs text-slate-100">
        {pretty(call.result)}
      </pre>
    </details>
  );
}

export default function RunPage() {
  const id = useParams()["*"] ?? "";
  const { data: run, error, loading } = useData(() => api.run(id), id);
  if (loading) return <Loading />;
  if (error || !run) return <ErrorBox message={error ?? "not found"} />;
  const r = run.report;
  const cited = new Set(r.evidence.map((e) => e.tool_call_id));
  const callsByTurn: ToolCall[][] = [];
  let next = 0;
  for (const turn of run.turns) {
    const n = turn.requested.filter((name) => name !== "submit_report").length;
    callsByTurn.push(run.tool_calls.slice(next, next + n));
    next += n;
  }

  return (
    <div className="space-y-4">
      <Card>
        <p className="text-xs uppercase text-slate-500">Alert · {fmtTime(run.started_at)}</p>
        <p className="mt-1">{run.alert}</p>
      </Card>

      <Card title="Report">
        <div className="flex flex-wrap items-center gap-3">
          <Verdict category={r.category} component={r.component} />
          <OutcomeBadge outcome={run.outcome} />
          <span className="text-sm text-slate-500">confidence {r.confidence.toFixed(2)}</span>
        </div>
        <p className="mt-3">{r.summary}</p>
        <h3 className="mt-4 text-sm font-semibold">Evidence</h3>
        <ul className="mt-1 space-y-1 text-sm">
          {r.evidence.map((e, i) => (
            <li key={i}>
              <a className="font-mono text-indigo-700 hover:underline" href={`#${e.tool_call_id}`}
                 onClick={(ev) => { ev.preventDefault(); const el = document.getElementById(e.tool_call_id) as HTMLDetailsElement | null; if (el) { el.open = true; el.scrollIntoView({ behavior: "smooth" }); } }}>
                [{e.tool_call_id}]
              </a>{" "}
              {e.finding}
            </li>
          ))}
        </ul>
        {r.ruled_out.length > 0 && (
          <>
            <h3 className="mt-4 text-sm font-semibold">Ruled out</h3>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
              {r.ruled_out.map((x, i) => (
                <li key={i}><span className="font-medium">{x.hypothesis}:</span> {x.reason}</li>
              ))}
            </ul>
          </>
        )}
        <p className="mt-4 text-sm"><span className="font-semibold">Suggested next step:</span> {r.suggested_next_step}</p>
        {run.error && <div className="mt-3"><ErrorBox message={run.error} /></div>}
      </Card>

      <Card title={`Timeline: ${run.turns.length} model turns, ${run.tool_calls.length} tool calls, ${fmtNum(run.usage.total_tokens)} tokens, ${run.seconds}s`}>
        <ol className="space-y-3">
          {run.turns.map((t, i) => (
            <li key={t.number} className="border-l-2 border-slate-200 pl-3">
              <p className="text-xs text-slate-500">
                Turn {t.number} · {t.model} · {t.latency_ms} ms
                {t.attempts > 1 && ` · ${t.attempts} attempts`} · {t.usage.prompt}+{t.usage.completion} tokens
                {t.requested.includes("submit_report") && " · submitted report"}
              </p>
              {t.text && <p className="mt-1 text-sm italic text-slate-600">{t.text}</p>}
              <div className="mt-2 space-y-2">
                {callsByTurn[i].map((c) => <ToolCallItem key={c.id} call={c} cited={cited.has(c.id)} />)}
              </div>
            </li>
          ))}
        </ol>
        {next < run.tool_calls.length && (
          // Calls no model turn asked for: the one-shot baseline collects its snapshot first.
          <div className="mt-3 space-y-2">
            <p className="text-xs text-slate-500">Collected without a model request (fixed snapshot):</p>
            {run.tool_calls.slice(next).map((c) => <ToolCallItem key={c.id} call={c} cited={cited.has(c.id)} />)}
          </div>
        )}
        {run.invalid_reports.length > 0 && (
          <p className="mt-3 text-xs text-amber-800">Rejected reports: {run.invalid_reports.join(" | ")}</p>
        )}
      </Card>
    </div>
  );
}
