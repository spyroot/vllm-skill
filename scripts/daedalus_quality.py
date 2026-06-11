"""Reusable dynamic coding-quality checks.

Builds seeded DynaCode-lite prompts and scores generated Python with AST
safety checks, visible cases, hidden cases, and subprocess timeouts.

"""

from __future__ import annotations

import ast
import json
import random
import re
import subprocess
import sys
from typing import Any


BENCHMARK_NAME = "dynacode_lite_pipeline_v1"
MAX_SCORE = 100
DEFAULT_QUALITY_SEED = 20260611
DEFAULT_QUALITY_THRESHOLD = 85
DEFAULT_QUALITY_TIMEOUT = 3.0
UNSAFE_CALLS = {
    "__import__",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}


def build_dynamic_quality_problem(seed: int) -> dict[str, Any]:
    """Build a reproducible DynaCode-lite prompt and local oracle cases."""

    rng = random.Random(seed)
    tags = ("gpu", "api", "cache", "route", "model", "ci")
    bonus_tag = rng.choice(tags)
    rules = [
        {"op": "scale", "factor": rng.randint(2, 4)},
        {"op": "offset", "amount": rng.randint(-3, 8)},
        {"op": "tag_bonus", "tag": bonus_tag, "amount": rng.randint(5, 13)},
        {"op": "missing_dep_penalty", "amount": rng.randint(3, 9)},
        {"op": "floor", "limit": rng.randint(0, 8)},
        {"op": "cap", "limit": rng.randint(38, 70)},
        {"op": "accept_at", "score": rng.randint(24, 48)},
    ]
    visible_cases = _build_cases(rng, rules, "visible", 2)
    hidden_cases = _build_cases(rng, rules, "hidden", 4)
    visible_examples = [
        {
            "records": case["records"],
            "rules": case["rules"],
            "expected": case["expected"],
        }
        for case in visible_cases
    ]
    task = _quality_task(seed, visible_examples)
    return {
        "benchmark": BENCHMARK_NAME,
        "seed": seed,
        "task": task,
        "visible_cases": visible_cases,
        "hidden_cases": hidden_cases,
    }


def score_quality_solution(
    code: str,
    seed: int = DEFAULT_QUALITY_SEED,
    threshold: int = DEFAULT_QUALITY_THRESHOLD,
    timeout: float = DEFAULT_QUALITY_TIMEOUT,
) -> dict[str, Any]:
    """Score generated code against a seeded DynaCode-lite benchmark."""

    problem = build_dynamic_quality_problem(seed)
    candidate = extract_candidate_code(code)
    errors: list[str] = []
    validation = {
        "syntax": False,
        "function_signature": False,
        "docstring": False,
        "safe_ast": False,
        "execution_timed_out": False,
        "visible_passed": 0,
        "visible_total": len(problem["visible_cases"]),
        "hidden_passed": 0,
        "hidden_total": len(problem["hidden_cases"]),
    }
    score = 0

    try:
        module = ast.parse(candidate)
    except SyntaxError as exc:
        errors.append(f"syntax error: {exc.msg}")
        return _quality_report(problem, score, threshold, validation, errors, [], candidate)
    validation["syntax"] = True
    score += 10

    function = _find_quality_function(module)
    if function is not None:
        args = [arg.arg for arg in function.args.args]
        validation["function_signature"] = args == ["records", "rules"]
        if validation["function_signature"]:
            score += 10
        if ast.get_docstring(function, clean=True):
            validation["docstring"] = True
            score += 5
    else:
        errors.append("expected function named evaluate_pipeline")

    safety_errors = _candidate_safety_errors(module)
    if safety_errors:
        errors.extend(safety_errors)
        return _quality_report(problem, score, threshold, validation, errors, [], candidate)
    validation["safe_ast"] = True
    score += 20

    cases = problem["visible_cases"] + problem["hidden_cases"]
    execution = _run_candidate_cases(candidate, cases, timeout)
    if execution.get("timed_out"):
        validation["execution_timed_out"] = True
        errors.append(f"candidate execution timed out after {timeout} seconds")
        return _quality_report(problem, score, threshold, validation, errors, [], candidate)
    if not execution.get("ok"):
        errors.append(execution.get("error", "candidate execution failed"))
        return _quality_report(problem, score, threshold, validation, errors, execution.get("results", []), candidate)

    results = execution["results"]
    visible_results = [result for result in results if not result["hidden"]]
    hidden_results = [result for result in results if result["hidden"]]
    validation["visible_passed"] = sum(1 for result in visible_results if result["passed"])
    validation["hidden_passed"] = sum(1 for result in hidden_results if result["passed"])
    score += _proportional_points(validation["visible_passed"], validation["visible_total"], 25)
    score += _proportional_points(validation["hidden_passed"], validation["hidden_total"], 30)
    return _quality_report(problem, score, threshold, validation, errors, results, candidate)


def extract_candidate_code(content: str) -> str:
    """Extract generated Python from plain text or a markdown fence."""

    text = content.strip()
    fence = re.search(r"```(?:python)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _quality_task(seed: int, visible_examples: list[dict[str, Any]]) -> str:
    examples_json = json.dumps(visible_examples, indent=2, sort_keys=True)
    return f"""DynaCode-lite seeded quality check, seed {seed}.

Return only Python code. Define exactly this function:

def evaluate_pipeline(records, rules)

The function must include an actual function docstring as the first statement in the function body; comments do not
count as docstrings. It must return a deterministic dict with:
- count: number of records with a non-null id after converting ids to strings
- accepted: sorted string ids whose final score is greater than or equal to the accept_at score
- rejected: sorted string ids whose final score is below the accept_at score
- scores: mapping of sorted string ids to final integer scores
- checksum: sum((1-based sorted index) * score) across sorted ids

Records are dictionaries with id, value, tags, and deps fields. Ignore records whose id is None before building the
id index; never create an id string named "None". Missing value means 0, missing tags/deps means an empty list, and
the last record wins when duplicate ids appear.

Apply rules in order to each record score:
- scale: multiply by integer factor
- offset: add integer amount
- tag_bonus: add amount when tag is present in the record tags
- missing_dep_penalty: subtract amount for each dep id that is absent from the indexed records
- floor: clamp score up to limit
- cap: clamp score down to limit
- accept_at: set the acceptance threshold; it does not change score

Do not import modules, read files, write files, open sockets, spawn subprocesses, call eval/exec/open, or use
external packages.

Visible examples:
{examples_json}
"""


def _build_cases(rng: random.Random, rules: list[dict[str, Any]], prefix: str, count: int) -> list[dict[str, Any]]:
    cases = []
    for index in range(count):
        records = _build_records(rng, 3 + index)
        expected = _reference_evaluate_pipeline(records, rules)
        cases.append(
            {
                "name": f"{prefix}_{index + 1}",
                "hidden": prefix == "hidden",
                "records": records,
                "rules": rules,
                "expected": expected,
            }
        )
    return cases


def _build_records(rng: random.Random, count: int) -> list[dict[str, Any]]:
    tags = ("gpu", "api", "cache", "route", "model", "ci")
    records = []
    for index in range(count):
        record_id = f"r{index + 1}"
        possible_deps = [f"r{dep}" for dep in range(1, index + 1)] + [f"missing-{index}", "ghost"]
        dep_count = rng.randint(0, min(2, len(possible_deps)))
        tag_count = rng.randint(0, 3)
        records.append(
            {
                "id": record_id,
                "value": rng.randint(-4, 18),
                "tags": sorted(rng.sample(tags, tag_count)),
                "deps": sorted(rng.sample(possible_deps, dep_count)),
            }
        )
    if count >= 4:
        records.append(
            {
                "id": "r2",
                "value": rng.randint(6, 16),
                "tags": sorted(rng.sample(tags, rng.randint(1, 3))),
                "deps": ["r1", "ghost"],
            }
        )
    if count >= 5:
        records.append({"id": None, "value": rng.randint(1, 9), "tags": ["ignored"], "deps": []})
    return records


def _reference_evaluate_pipeline(records: list[dict[str, Any]], rules: list[dict[str, Any]]) -> dict[str, Any]:
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
            elif op == "tag_bonus" and str(rule.get("tag")) in tags:
                value += int(rule.get("amount", 0))
            elif op == "missing_dep_penalty":
                value -= int(rule.get("amount", 0)) * sum(1 for dep in deps if dep not in indexed)
            elif op == "floor":
                value = max(value, int(rule.get("limit", value)))
            elif op == "cap":
                value = min(value, int(rule.get("limit", value)))
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


def _find_quality_function(module: ast.Module) -> ast.FunctionDef | None:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate_pipeline":
            return node
    return None


def _candidate_safety_errors(module: ast.Module) -> list[str]:
    errors = []
    for node in module.body:
        if _is_module_docstring(node) or isinstance(node, (ast.FunctionDef, ast.Assign, ast.AnnAssign)):
            continue
        errors.append("top-level executable statements are not allowed")
        break
    for node in ast.walk(module):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            errors.append("import statements are not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in UNSAFE_CALLS:
            errors.append(f"unsafe call is not allowed: {node.func.id}")
        elif isinstance(node, ast.Name) and node.id.startswith("__"):
            errors.append(f"dunder name is not allowed: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append(f"dunder attribute is not allowed: {node.attr}")
    return sorted(set(errors))


def _is_module_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _run_candidate_cases(code: str, cases: list[dict[str, Any]], timeout: float) -> dict[str, Any]:
    payload = json.dumps({"code": code, "cases": cases})
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", _SUBPROCESS_RUNNER],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "timed_out": True, "results": []}
    stdout = completed.stdout.strip()
    if not stdout:
        return {"ok": False, "error": completed.stderr.strip() or "candidate produced no runner output", "results": []}
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": stdout, "results": []}
    return result


def _proportional_points(passed: int, total: int, points: int) -> int:
    if total == 0:
        return 0
    return round((passed / total) * points)


def _quality_report(
    problem: dict[str, Any],
    score: int,
    threshold: int,
    validation: dict[str, Any],
    errors: list[str],
    results: list[dict[str, Any]],
    code: str,
) -> dict[str, Any]:
    critical_passed = (
        validation.get("syntax") is True
        and validation.get("function_signature") is True
        and validation.get("docstring") is True
        and validation.get("safe_ast") is True
        and validation.get("execution_timed_out") is False
    )
    validation["critical_passed"] = critical_passed
    return {
        "benchmark": problem["benchmark"],
        "seed": problem["seed"],
        "score": score,
        "max_score": MAX_SCORE,
        "threshold": threshold,
        "passed": score >= threshold and not errors and critical_passed,
        "validation": validation,
        "test_results": results,
        "errors": errors,
        "code": code,
    }


_SUBPROCESS_RUNNER = r'''
import json
import sys
import traceback

payload = json.loads(sys.stdin.read())
allowed_builtins = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
}
namespace = {"__builtins__": allowed_builtins}
try:
    exec(compile(payload["code"], "<candidate>", "exec"), namespace)
    fn = namespace.get("evaluate_pipeline")
    if not callable(fn):
        raise AssertionError("evaluate_pipeline is not callable")
    results = []
    for case in payload["cases"]:
        actual = fn(case["records"], case["rules"])
        passed = actual == case["expected"]
        results.append(
            {
                "name": case["name"],
                "hidden": case["hidden"],
                "passed": passed,
                "actual": None if passed else actual,
                "expected": None if passed else case["expected"],
            }
        )
    print(json.dumps({"ok": True, "results": results}, sort_keys=True))
except BaseException as exc:
    print(
        json.dumps(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=3),
                "results": [],
            },
            sort_keys=True,
        )
    )
    raise SystemExit(1)
'''
