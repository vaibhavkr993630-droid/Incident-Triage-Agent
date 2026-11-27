// Shapes of the JSON written by triage/trace.py and evals/runner.py. Compile-time only.

export interface RunSummary {
  id: string;
  source: string;
  started_at: string;
  alert: string;
  category: string | null;
  component: string | null;
  confidence: number | null;
  outcome: string;
  turns: number;
  tool_calls: number;
  tokens: number | null;
  seconds: number | null;
}

export interface Evidence {
  tool_call_id: string;
  finding: string;
}

export interface Report {
  category: string;
  component: string;
  summary: string;
  evidence: Evidence[];
  ruled_out: { hypothesis: string; reason: string }[];
  confidence: number;
  suggested_next_step: string;
}

export interface Turn {
  number: number;
  provider: string;
  model: string;
  latency_ms: number;
  attempts: number;
  finish_reason: string;
  text: string;
  requested: string[];
  usage: { prompt: number; completion: number };
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result: string;
  is_error: boolean;
  duration_ms: number;
  truncated: boolean;
}

export interface Run {
  id: string;
  alert: string;
  started_at: string;
  seconds: number;
  outcome: string;
  stop_reason: string;
  error: string | null;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  report: Report;
  invalid_reports: string[];
  turns: Turn[];
  tool_calls: ToolCall[];
}

export interface EvalRow {
  variant: string;
  scenario: string;
  repeat: number;
  expected_category: string;
  expected_component: string;
  category: string;
  component: string;
  correct: boolean;
  category_correct: boolean;
  false_alarm: boolean;
  attacked?: boolean;
  attack_seen?: boolean;
  followed_injection?: boolean;
  outcome: string;
  confidence: number;
  tool_calls: number;
  tokens: number;
  seconds: number;
  numbers_quoted: number;
  numbers_grounded: number;
  numbers_miscited?: number;
  numbers_unsupported?: number;
  error: string | null;
  run_id: string | null;
}

export interface EvalMeta {
  eval_id: string;
  started_at: string;
  commit: string;
  primary_model: string;
  repeats: number;
  scenarios: string[];
  variants: string[];
}

export interface EvalListItem {
  id: string;
  meta: EvalMeta;
  headline: Record<string, { runs: number; correct: number; scored: number; avg_tokens: number | null }>;
}

export interface EvalDetail {
  id: string;
  meta: EvalMeta;
  rows: EvalRow[];
}

export interface Job {
  id: string;
  status: "running" | "done" | "failed";
  alert: string;
  run_id?: string;
  error?: string;
}
