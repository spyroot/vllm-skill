"""Unit tests for the DynaCode-lite quality scorer.

The tests verify seeded problem generation, hidden-case scoring, safety
rejection, and timeout handling without calling a live model.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "scripts" / "daedalus_quality.py"
APP_MODULE_NAME = "daedalus_quality_under_test"


GOOD_SOLUTION = '''
def evaluate_pipeline(records, rules):
    """Apply scoring rules to records and return deterministic pipeline results."""
    indexed = {}
    for record in records:
        if record.get("id") is not None:
            indexed[str(record.get("id"))] = record

    accept_at = 0
    for rule in rules:
        if rule.get("op") == "accept_at":
            accept_at = int(rule.get("score", 0))

    scores = {}
    for record_id in sorted(indexed):
        record = indexed[record_id]
        value = int(record.get("value", 0) or 0)
        tags = set(str(tag) for tag in (record.get("tags") or []))
        deps = [str(dep) for dep in (record.get("deps") or [])]
        for rule in rules:
            op = rule.get("op")
            if op == "scale":
                value *= int(rule.get("factor", 1))
            elif op == "offset":
                value += int(rule.get("amount", 0))
            elif op == "tag_bonus":
                if str(rule.get("tag")) in tags:
                    value += int(rule.get("amount", 0))
            elif op == "missing_dep_penalty":
                missing = sum(1 for dep in deps if dep not in indexed)
                value -= int(rule.get("amount", 0)) * missing
            elif op == "cap":
                value = min(value, int(rule.get("limit", value)))
            elif op == "floor":
                value = max(value, int(rule.get("limit", value)))
        scores[record_id] = int(value)

    ordered_ids = sorted(scores)
    accepted = [record_id for record_id in ordered_ids if scores[record_id] >= accept_at]
    rejected = [record_id for record_id in ordered_ids if scores[record_id] < accept_at]
    checksum = sum((index + 1) * scores[record_id] for index, record_id in enumerate(ordered_ids))
    return {
        "count": len(ordered_ids),
        "accepted": accepted,
        "rejected": rejected,
        "scores": scores,
        "checksum": checksum,
    }
'''


def load_module():
    """Load the quality module in isolation so tests see fresh constants."""

    sys.modules.pop(APP_MODULE_NAME, None)
    spec = importlib.util.spec_from_file_location(APP_MODULE_NAME, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_dynamic_quality_problem_is_seeded_and_reproducible():
    """Verify the generated benchmark is stable for a seed and varies by seed."""

    module = load_module()

    first = module.build_dynamic_quality_problem(20260611)
    second = module.build_dynamic_quality_problem(20260611)
    different = module.build_dynamic_quality_problem(20260612)

    assert first == second
    assert first["seed"] == 20260611
    assert first["benchmark"] == "dynacode_lite_pipeline_v1"
    assert first["visible_cases"]
    assert first["hidden_cases"]
    assert first["visible_cases"] != different["visible_cases"]


def test_dynamic_quality_task_explains_signature_and_rules():
    """Verify the prompt is reusable and names the required function contract."""

    module = load_module()

    problem = module.build_dynamic_quality_problem(7)

    assert "DynaCode-lite" in problem["task"]
    assert "def evaluate_pipeline(records, rules)" in problem["task"]
    assert "actual function docstring" in problem["task"]
    assert "Ignore records whose id is None" in problem["task"]
    assert "missing_dep_penalty" in problem["task"]
    assert "Return only Python code" in problem["task"]


def test_score_quality_solution_awards_full_score_to_correct_solution():
    """Verify a correct implementation passes all visible and hidden cases."""

    module = load_module()

    report = module.score_quality_solution(GOOD_SOLUTION, seed=20260611, threshold=75, timeout=3)

    assert report["benchmark"] == "dynacode_lite_pipeline_v1"
    assert report["seed"] == 20260611
    assert report["score"] == 100
    assert report["max_score"] == 100
    assert report["passed"] is True
    assert report["validation"]["visible_passed"] == report["validation"]["visible_total"]
    assert report["validation"]["hidden_passed"] == report["validation"]["hidden_total"]


def test_score_quality_solution_penalizes_shallow_constant_output():
    """Verify shallow hard-coded code fails hidden cases from generated variants."""

    module = load_module()
    code = '''
def evaluate_pipeline(records, rules):
    """Return a fixed result that should fail dynamic quality checks."""
    return {"count": 0, "accepted": [], "rejected": [], "scores": {}, "checksum": 0}
'''

    report = module.score_quality_solution(code, seed=20260611, threshold=75, timeout=3)

    assert report["score"] < 75
    assert report["passed"] is False
    assert report["validation"]["hidden_passed"] < report["validation"]["hidden_total"]


def test_score_quality_solution_requires_docstring_for_pass():
    """Verify structurally good code still fails pass when the docstring is absent."""

    module = load_module()
    code = GOOD_SOLUTION.replace(
        '    """Apply scoring rules to records and return deterministic pipeline results."""\n',
        "",
    )

    report = module.score_quality_solution(code, seed=20260611, threshold=75, timeout=3)

    assert report["score"] == 95
    assert report["passed"] is False
    assert report["validation"]["docstring"] is False


def test_score_quality_solution_rejects_unsafe_imports():
    """Verify generated code cannot use imports because live eval runs locally."""

    module = load_module()
    code = '''
import os

def evaluate_pipeline(records, rules):
    """Attempt an unsafe import."""
    return {"count": 0, "accepted": [], "rejected": [], "scores": {}, "checksum": 0}
'''

    report = module.score_quality_solution(code, seed=20260611, threshold=75, timeout=3)

    assert report["passed"] is False
    assert report["validation"]["safe_ast"] is False
    assert "import statements are not allowed" in report["errors"]


def test_score_quality_solution_times_out_nonterminating_code():
    """Verify nonterminating generated code is stopped by the subprocess timeout."""

    module = load_module()
    code = '''
def evaluate_pipeline(records, rules):
    """Loop forever so the quality runner proves timeout handling."""
    while True:
        pass
'''

    report = module.score_quality_solution(code, seed=20260611, threshold=75, timeout=0.2)

    assert report["passed"] is False
    assert report["validation"]["execution_timed_out"] is True
