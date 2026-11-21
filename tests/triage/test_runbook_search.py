"""Runbook retrieval: chunking, ranking, and a labelled retrieval eval (recall@1, recall@3)."""

import json

import pytest

from triage.tools import ToolContext, ToolSession, build_registry
from triage.tools.runbooks import BM25Index, load_sections, tokenize

INDEX = BM25Index(load_sections())

# Queries phrased the way the agent would ask, with the runbook that should answer them.
# Includes the distractor topics, so the index must also find those when they are asked for.
LABELLED = [
    ("POST /orders returning 504 checkout errors", "checkout-failures.md"),
    ("payment provider timed out timeout_s", "checkout-failures.md"),
    ("payment provider returned an error 500 card processor unavailable", "payment-provider.md"),
    ("payment-provider /charge latency high", "payment-provider.md"),
    ("slow database query product_stats", "database-slow-queries.md"),
    ("missing index full table scan", "database-slow-queries.md"),
    ("database connection pool exhausted 503", "database-connection-pool.md"),
    ("db_pool_checked_out equals pool size", "database-connection-pool.md"),
    ("cache read failed falling back to database", "redis-cache-and-queue.md"),
    ("health check reports redis error", "redis-cache-and-queue.md"),
    ("worker_queue_depth growing orders stuck in paid", "fulfillment-backlog.md"),
    ("fulfillment job slow warnings", "fulfillment-backlog.md"),
    ("recent deploy rollback", "change-management.md"),
    ("customer complaint no alert fired", "false-alarms.md"),
    ("certificate expired", "tls-certificates.md"),
    ("no space left on device", "disk-space.md"),
    ("container OOM killed exit code 137", "memory-pressure.md"),
    ("latency high on one route", "latency-triage.md"),
]


def recall_at(k: int) -> float:
    hits = sum(
        expected in [s.runbook for _, s in INDEX.search(query, top_k=k)]
        for query, expected in LABELLED
    )
    return hits / len(LABELLED)


def test_retrieval_quality_on_the_labelled_queries():
    r1, r3 = recall_at(1), recall_at(3)
    print(f"\nrunbook retrieval: recall@1={r1:.2f} recall@3={r3:.2f} ({len(LABELLED)} queries)")
    assert r3 == 1.0, "every query must find its runbook in the top 3"
    assert r1 >= 0.85


@pytest.mark.parametrize("query,expected", LABELLED, ids=[q for q, _ in LABELLED])
def test_each_labelled_query_finds_its_runbook_in_the_top_three(query, expected):
    assert expected in [s.runbook for _, s in INDEX.search(query, top_k=3)]


def test_tokenizer_keeps_metric_names_and_their_parts():
    tokens = tokenize("db_pool_checked_out Timeouts are rising!")
    assert "db_pool_checked_out" in tokens and "pool" in tokens and "checked" in tokens
    assert "timeout" in tokens  # light plural strip
    assert "are" not in tokens  # stopword


def test_every_runbook_is_chunked_by_section_with_its_title():
    sections = load_sections()
    assert len({s.runbook for s in sections}) == 12
    pool = [s for s in sections if s.runbook == "database-connection-pool.md"]
    assert [s.heading for s in pool] == ["When to use", "How to confirm", "Common causes",
                                         "Mitigation"]  # fmt: skip
    assert all("exhaustion" in s.tokens for s in pool)  # the title travels with every chunk


def test_results_are_one_section_per_runbook():
    hits = INDEX.search("database", top_k=5)
    names = [s.runbook for _, s in hits]
    assert len(names) == len(set(names))


def test_runbooks_never_name_the_scenarios():
    """Runbooks may describe common causes ("a missing index"), as real runbooks do, but
    they must not be an index of the lab's scenarios."""
    from faults.models import load_scenarios

    text = " ".join(s.text + s.title for s in load_sections()).lower()
    for scenario_id in load_scenarios():
        assert scenario_id not in text, scenario_id


def test_the_tool_returns_bounded_results():
    session = ToolSession(build_registry(ToolContext()))
    # Orders stuck in "paid" can mean a slow worker or a redis outage: both runbooks apply.
    call = session.call("search_runbooks", {"query": "orders stuck in paid", "top_k": 2})
    out = json.loads(call.result)
    assert not call.is_error and len(out["results"]) == 2
    assert {r["runbook"] for r in out["results"]} == {
        "fulfillment-backlog.md",
        "redis-cache-and-queue.md",
    }
    assert {"runbook", "title", "section", "score", "text"} <= out["results"][0].keys()
    nothing = json.loads(session.call("search_runbooks", {"query": "zzzz qqqq"}).result)
    assert nothing["results"] == [] and "rephrase" in nothing["note"]
