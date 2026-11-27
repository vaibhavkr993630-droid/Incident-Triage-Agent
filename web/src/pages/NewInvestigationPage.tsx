import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Card, ErrorBox } from "../components/ui";

const EXAMPLE =
  "Checkout errors: more than 5% of POST /orders requests have failed with 5xx over the last 2 minutes.";

export default function NewInvestigationPage() {
  const [alert, setAlert] = useState(EXAMPLE);
  const [notBefore, setNotBefore] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      let job = await api.startInvestigation(alert, notBefore || undefined);
      setStatus("Investigating... (this calls the model; usually 10-60 s)");
      while (job.status === "running") {
        await new Promise((r) => setTimeout(r, 2000));
        job = await api.job(job.id);
      }
      if (job.status === "failed") throw new Error(job.error ?? "investigation failed");
      navigate(`/runs/${job.run_id}`);
    } catch (err) {
      setStatus(null);
      setError((err as Error).message);
    }
  }

  return (
    <Card title="New investigation">
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm font-medium">
          Alert text
          <textarea
            className="mt-1 block w-full rounded border border-slate-300 p-2 font-normal"
            rows={3}
            value={alert}
            onChange={(e) => setAlert(e.target.value)}
          />
        </label>
        <label className="block text-sm font-medium">
          Ignore telemetry before (optional, ISO time)
          <input
            className="mt-1 block w-full rounded border border-slate-300 p-2 font-mono font-normal"
            placeholder="2026-11-22T10:00:00+00:00"
            value={notBefore}
            onChange={(e) => setNotBefore(e.target.value)}
          />
        </label>
        <button
          type="submit"
          disabled={status !== null || alert.trim().length < 10}
          className="rounded bg-indigo-600 px-4 py-2 text-white disabled:opacity-50"
        >
          Investigate
        </button>
        {status && <p className="text-sm text-slate-600">{status}</p>}
        {error && <ErrorBox message={error} />}
      </form>
    </Card>
  );
}
