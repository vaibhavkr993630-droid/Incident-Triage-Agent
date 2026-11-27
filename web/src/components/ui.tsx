import type { ReactNode } from "react";

export function Loading() {
  return <p className="py-8 text-slate-500">Loading...</p>;
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <p className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">{message}</p>
  );
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      {title && <h2 className="mb-3 text-sm font-semibold uppercase text-slate-500">{title}</h2>}
      {children}
    </section>
  );
}

const OUTCOME_STYLE: Record<string, string> = {
  submitted: "bg-emerald-100 text-emerald-800",
  forced: "bg-amber-100 text-amber-800",
  failed: "bg-red-100 text-red-800",
};

export function OutcomeBadge({ outcome }: { outcome: string }) {
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${OUTCOME_STYLE[outcome] ?? "bg-slate-100"}`}>
      {outcome}
    </span>
  );
}

export function Verdict({ category, component }: { category: string | null; component: string | null }) {
  if (!category) return <span className="text-slate-400">-</span>;
  return (
    <span className="font-mono text-sm">
      <span className="font-semibold">{category}</span>
      <span className="text-slate-400"> @ </span>
      {component}
    </span>
  );
}

export const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
export const fmtNum = (n: number | null | undefined) => (n == null ? "-" : n.toLocaleString());
