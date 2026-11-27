import { Link } from "react-router-dom";
import { api } from "../api";
import { Card, ErrorBox, Loading, OutcomeBadge, Verdict, fmtNum, fmtTime } from "../components/ui";
import { useData } from "../useData";

export default function RunsPage() {
  const { data, error, loading } = useData(api.runs, "runs");
  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;
  const runs = data ?? [];
  return (
    <Card title={`Investigations (${runs.length})`}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-slate-500">
            <tr>
              <th className="py-2 pr-4">Started</th>
              <th className="pr-4">Source</th>
              <th className="pr-4">Alert</th>
              <th className="pr-4">Verdict</th>
              <th className="pr-4">Outcome</th>
              <th className="pr-4 text-right">Tools</th>
              <th className="pr-4 text-right">Tokens</th>
              <th className="text-right">Seconds</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="py-2 pr-4 whitespace-nowrap">
                  <Link className="text-indigo-700 hover:underline" to={`/runs/${r.id}`}>
                    {fmtTime(r.started_at)}
                  </Link>
                </td>
                <td className="pr-4 font-mono text-xs text-slate-500">{r.source}</td>
                <td className="max-w-md truncate pr-4" title={r.alert}>{r.alert}</td>
                <td className="pr-4 whitespace-nowrap"><Verdict category={r.category} component={r.component} /></td>
                <td className="pr-4"><OutcomeBadge outcome={r.outcome} /></td>
                <td className="pr-4 text-right">{r.tool_calls}</td>
                <td className="pr-4 text-right">{fmtNum(r.tokens)}</td>
                <td className="text-right">{r.seconds ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs.length === 0 && <p className="py-6 text-slate-500">No investigations saved yet.</p>}
      </div>
    </Card>
  );
}
