// One data layer, two sources. Live: the console API (/api/...). Static: JSON files exported
// by `python -m triage.export` into public/data, so a demo build needs no backend.
import type { EvalDetail, EvalListItem, Job, Run, RunSummary } from "./types";

export const STATIC = import.meta.env.VITE_DATA_MODE === "static";
const DATA = `${import.meta.env.BASE_URL}data`;

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText} for ${url}`);
  return resp.json() as Promise<T>;
}

export const api = {
  runs: () => getJson<RunSummary[]>(STATIC ? `${DATA}/runs.json` : "/api/runs"),
  run: (id: string) => getJson<Run>(STATIC ? `${DATA}/runs/${id}.json` : `/api/runs/${id}`),
  evals: () => getJson<EvalListItem[]>(STATIC ? `${DATA}/evals.json` : "/api/evals"),
  evaluation: (id: string) =>
    getJson<EvalDetail>(STATIC ? `${DATA}/evals/${id}.json` : `/api/evals/${id}`),
  async startInvestigation(alert: string, notBefore?: string): Promise<Job> {
    const resp = await fetch("/api/investigations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alert, not_before: notBefore || null }),
    });
    const body = await resp.json();
    if (!resp.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
    return body as Job;
  },
  job: (id: string) => getJson<Job>(`/api/investigations/${id}`),
};
