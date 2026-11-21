"""The agent's report vocabulary must match the scenarios', without the agent importing them."""

import ast
from pathlib import Path

from faults import models as answer_key
from triage.prompts import system_prompt
from triage.report import Category, Component


def test_categories_match_the_scenarios_plus_inconclusive():
    assert {c.value for c in Category} == {c.value for c in answer_key.Category} | {"inconclusive"}


def test_components_match():
    assert {c.value for c in Component} == {c.value for c in answer_key.Component}


def test_the_agent_package_never_imports_the_answer_key():
    for path in Path("triage").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert not any(n == "faults" or n.startswith("faults.") for n in names), path


def test_prompt_lists_the_vocabulary_but_no_scenario_names():
    prompt = system_prompt(max_tool_calls=20)
    for c in Category:
        assert f"- {c.value}:" in prompt
    # Some scenario ids are also category names (e.g. bad_release); those must appear.
    categories = {c.value for c in Category}
    for scenario_id in set(answer_key.load_scenarios()) - categories:
        assert scenario_id not in prompt, scenario_id
    assert "at most 20 tool calls" in prompt
