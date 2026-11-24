"""Turn results.jsonl into a markdown summary: per-variant scores and a scenario matrix."""

import json
from collections import defaultdict
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def mean(values: list[float]) -> str:
    return f"{sum(values) / len(values):.2f}" if values else "-"


def avg(values: list[float], digits: int = 1) -> str:
    # One decimal, like the console UI (8.5 would round to 8 here and 9 in JavaScript).
    return f"{sum(values) / len(values):.{digits}f}" if values else "-"


def injection_cell(rs: list[dict]) -> str:
    """'saw/runs, followed/saw' for attacked scenarios: resisting only counts if seen."""
    attacked = [r for r in rs if r.get("attacked")]
    if not attacked:
        return "-"
    seen = [r for r in attacked if r.get("attack_seen")]
    followed = sum(r["followed_injection"] for r in seen)
    return f"saw {len(seen)}/{len(attacked)}, followed {followed}/{len(seen)}"


def summarize(rows: list[dict], meta: dict | None = None) -> str:
    by_variant: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_variant[r["variant"]].append(r)
    known = ("agent", "agent_no_runbooks", "agent_no_guard", "baseline", "baseline_no_guard")
    order = [v for v in known if v in by_variant]
    order += sorted(set(by_variant) - set(order))

    lines = []
    if meta:
        lines += [
            f"Evaluation `{meta['eval_id']}`: commit `{meta.get('commit')}`, model "
            f"`{meta.get('primary_model')}`, {meta.get('repeats')} repeat(s), "
            f"started {meta.get('started_at')}.",
            "",
        ]
    lines += [
        "| Variant | Runs | Correct (category + component) | Category only | False alarms "
        "| No report | Provider errors | Quoted numbers: grounded / mis-cited / unsupported "
        "| Avg tool calls | Avg tokens "
        "| Avg seconds | Confidence when right / wrong | Injection: saw / followed |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for v in order:
        # Runs that ended in a provider/network error say nothing about the diagnosis:
        # they are counted in their own column and left out of every other figure.
        errored = [r for r in by_variant[v] if r.get("error")]
        rs = [r for r in by_variant[v] if not r.get("error")]
        controls = [r for r in rs if r["expected_category"] == "no_fault"]
        quoted = sum(r["numbers_quoted"] for r in rs)
        grounded = sum(r["numbers_grounded"] for r in rs)
        miscited = sum(r.get("numbers_miscited", 0) for r in rs)
        unsupported = sum(r.get("numbers_unsupported", 0) for r in rs)
        right = [r["confidence"] for r in rs if r["correct"]]
        wrong = [r["confidence"] for r in rs if not r["correct"] and r["outcome"] != "failed"]
        lines.append(
            f"| `{v}` | {len(rs)} | {pct(sum(r['correct'] for r in rs), len(rs))} "
            f"| {pct(sum(r['category_correct'] for r in rs), len(rs))} "
            f"| {sum(r['false_alarm'] for r in controls)}/{len(controls)} "
            f"| {sum(r['outcome'] == 'failed' for r in rs)} | {len(errored)} "
            f"| {grounded} / {miscited} / {unsupported} of {quoted} ({pct(grounded, quoted)}) "
            f"| {avg([r['tool_calls'] for r in rs])} | {avg([r['tokens'] for r in rs], 0)} "
            f"| {avg([r['seconds'] for r in rs])} | {mean(right)} / {mean(wrong)} "
            f"| {injection_cell(rs)} |"
        )

    scenarios = sorted({r["scenario"] for r in rows})
    lines += ["", "| Scenario | " + " | ".join(f"`{v}`" for v in order) + " |",
              "|---|" + "---|" * len(order)]  # fmt: skip
    for s in scenarios:
        cells = []
        for v in order:
            rs = [r for r in by_variant[v] if r["scenario"] == s and not r.get("error")]
            errors = sum(1 for r in by_variant[v] if r["scenario"] == s and r.get("error"))
            if not rs:
                cells.append(f"error x{errors}" if errors else "-")
                continue
            ok = sum(r["correct"] for r in rs)
            wrong = sorted({f"{r['category']}@{r['component']}" for r in rs if not r["correct"]})
            cells.append(f"{ok}/{len(rs)}" + (f" ({', '.join(wrong)})" if wrong else ""))
        lines.append(f"| `{s}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_summary(results_path: Path) -> Path:
    meta_path = results_path.parent / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    out = results_path.parent / "summary.md"
    out.write_text(summarize(load_rows(results_path), meta))
    return out


def rescore(results_path: Path) -> int:
    """Recompute number grounding from the saved traces (no model calls). Returns rows."""
    from evals.scoring import ground_numbers

    rows = load_rows(results_path)
    root = results_path.parents[3]
    for row in rows:
        trace = json.loads((root / row["trace"]).read_text())
        calls = {c["id"]: (c["result"], json.dumps(c["arguments"])) for c in trace["tool_calls"]}
        evidence = [(e["tool_call_id"], e["finding"]) for e in trace["report"]["evidence"]]
        g = ground_numbers(evidence, calls)
        row.update({f"numbers_{k}": v for k, v in g.items()})
    results_path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return len(rows)
