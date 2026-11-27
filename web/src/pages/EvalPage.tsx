import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Card, ErrorBox, Loading, fmtTime } from "../components/ui";
import type { EvalRow } from "../types";
import { useData } from "../useData";

const ORDER = ["agent", "agent_no_runbooks", "agent_no_guard", "baseline"];
// One decimal: avoids half-way rounding differences with the Python report (8.5 -> 8 vs 9).
const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
const one = (x: number) => x.toFixed(1);

function VariantRow({ variant, rows }: { variant: string; rows: EvalRow[] }) {
  // Provider/network errors are counted apart and left out of every score, as in the CLI report.
  const scored = rows.filter((r) => !r.error);
  const controls = scored.filter((r) => r.expected_category === "no_fault");
  const attacked = scored.filter((r) => r.attacked);
  const seen = attacked.filter((r) => r.attack_seen);
  const quoted = scored.reduce((a, r) => a + r.numbers_quoted, 0);
  const grounded = scored.reduce((a, r) => a + r.numbers_grounded, 0);
  const unsupported = scored.reduce((a, r) => a + (r.numbers_unsupported ?? 0), 0);
  return (
    <tr className="border-t border-slate-100">
      <td className="py-2 pr-4 font-mono">{variant}</td>
      <td className="pr-4"><b>{scored.filter((r) => r.correct).length}/{scored.length}</b></td>
      <td className="pr-4">{controls.filter((r) => r.false_alarm).length}/{controls.length}</td>
      <td className="pr-4">
        {attacked.length
          ? `saw ${seen.length}/${attacked.length}, followed ${seen.filter((r) => r.followed_injection).length}/${seen.length}`
          : "-"}
      </td>
      <td className="pr-4">{grounded}/{quoted} grounded, {unsupported} invented</td>
      <td className="pr-4 text-right">{one(avg(scored.map((r) => r.tool_calls)))}</td>
      <td className="pr-4 text-right">{Math.round(avg(scored.map((r) => r.tokens))).toLocaleString()}</td>
      <td className="pr-4 text-right">{one(avg(scored.map((r) => r.seconds)))}</td>
      <td className="text-right">{rows.length - scored.length}</td>
    </tr>
  );
}

export default function EvalPage() {
  const id = useParams().id ?? "";
  const { data, error, loading } = useData(() => api.evaluation(id), id);
  if (loading) return <Loading />;
  if (error || !data) return <ErrorBox message={error ?? "not found"} />;
  const variants = [...new Set(data.rows.map((r) => r.variant))].sort(
    (a, b) => (ORDER.indexOf(a) + 99) % 99 - (ORDER.indexOf(b) + 99) % 99,
  );
  const scenarios = [...new Set(data.rows.map((r) => r.scenario))].sort();
  const m = data.meta;

  return (
    <div className="space-y-4">
      <Card>
        <h1 className="font-mono text-lg font-semibold">{data.id}</h1>
        <p className="text-sm text-slate-500">
          {fmtTime(m.started_at)} · model {m.primary_model} · {m.repeats} repeat(s) · commit {m.commit}
        </p>
      </Card>
      <Card title="Scores by variant">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr>
                <th className="py-2 pr-4">Variant</th><th className="pr-4">Correct</th>
                <th className="pr-4">False alarms</th><th className="pr-4">Injection: saw / followed</th>
                <th className="pr-4">Quoted numbers</th><th className="pr-4 text-right">Tools</th>
                <th className="pr-4 text-right">Tokens</th><th className="pr-4 text-right">Seconds</th>
                <th className="text-right">Provider errors</th>
              </tr>
            </thead>
            <tbody>
              {variants.map((v) => <VariantRow key={v} variant={v} rows={data.rows.filter((r) => r.variant === v)} />)}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Every run (click a verdict to open its investigation)">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr><th className="py-2 pr-4">Scenario (true cause)</th>{variants.map((v) => <th key={v} className="pr-4 font-mono">{v}</th>)}</tr>
            </thead>
            <tbody>
              {scenarios.map((s) => {
                const any = data.rows.find((r) => r.scenario === s)!;
                return (
                  <tr key={s} className="border-t border-slate-100 align-top">
                    <td className="py-2 pr-4">
                      <span className="font-mono">{s}</span>
                      <br /><span className="text-xs text-slate-500">{any.expected_category} @ {any.expected_component}</span>
                    </td>
                    {variants.map((v) => (
                      <td key={v} className="pr-4">
                        {data.rows.filter((r) => r.scenario === s && r.variant === v).map((r, i) => (
                          <div key={i}>
                            <Link to={r.run_id ? `/runs/${r.run_id}` : "#"} className={r.error ? "text-slate-400" : r.correct ? "text-emerald-700 hover:underline" : "text-red-700 hover:underline"}>
                              {r.error ? "provider error" : r.correct ? "correct" : `${r.category}@${r.component}`}
                            </Link>
                          </div>
                        ))}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
