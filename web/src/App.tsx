import { HashRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { STATIC } from "./api";
import EvalPage from "./pages/EvalPage";
import EvalsPage from "./pages/EvalsPage";
import NewInvestigationPage from "./pages/NewInvestigationPage";
import RunPage from "./pages/RunPage";
import RunsPage from "./pages/RunsPage";

const link = ({ isActive }: { isActive: boolean }) =>
  `px-3 py-1 rounded ${isActive ? "bg-white/15 text-white" : "text-indigo-100 hover:text-white"}`;

export default function App() {
  return (
    // Hash routing works on any static host with no server rewrites (the demo build).
    <HashRouter>
      <header className="bg-indigo-900 text-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-4 px-4 py-3">
          <span className="font-semibold">Incident Triage Agent</span>
          <nav className="flex gap-1 text-sm">
            <NavLink to="/runs" className={link}>Investigations</NavLink>
            <NavLink to="/evals" className={link}>Evaluations</NavLink>
            {!STATIC && <NavLink to="/new" className={link}>New investigation</NavLink>}
          </nav>
          {STATIC && (
            <span className="ml-auto text-xs text-indigo-200">
              Demo: real recorded investigations, read-only
            </span>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/runs" replace />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/*" element={<RunPage />} />
          <Route path="/evals" element={<EvalsPage />} />
          <Route path="/evals/:id" element={<EvalPage />} />
          {!STATIC && <Route path="/new" element={<NewInvestigationPage />} />}
        </Routes>
      </main>
    </HashRouter>
  );
}
