import { Link } from "react-router-dom";
import { api } from "../api";
import { Card, ErrorBox, Loading, fmtNum, fmtTime } from "../components/ui";
import { useData } from "../useData";

export default function EvalsPage() {
  const { data, error, loading } = useData(api.evals, "evals");
  if (loading) return <Loading />;
  if (error) return <ErrorBox message={error} />;
  return (
    <div className="space-y-4">
      {(data ?? []).map((e) => (
        <Card key={e.id}>
          <Link to={`/evals/${e.id}`} className="font-mono font-semibold text-indigo-700 hover:underline">
            {e.id}
          </Link>
          <p className="text-sm text-slate-500">
            {fmtTime(e.meta.started_at)} · model {e.meta.primary_model} · {e.meta.scenarios.length} scenarios ·{" "}
            {e.meta.repeats} repeat(s) · commit {e.meta.commit}
          </p>
          <div className="mt-2 flex flex-wrap gap-4 text-sm">
            {Object.entries(e.headline).map(([variant, h]) => (
              <span key={variant}>
                <span className="font-mono">{variant}</span>: <b>{h.correct}/{h.scored}</b> correct,{" "}
                {fmtNum(h.avg_tokens)} tokens avg
              </span>
            ))}
          </div>
        </Card>
      ))}
      {data?.length === 0 && <p className="text-slate-500">No evaluations yet.</p>}
    </div>
  );
}
