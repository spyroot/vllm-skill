#!/usr/bin/env python3
"""Daedalus OpenAI-compatible endpoint sanity checks.

The CLI probes code generation, PR/review artifacts, blocker handling, and
seeded dynamic quality scoring for reusable model comparisons.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from daedalus_quality import (  # noqa: E402
    DEFAULT_QUALITY_SEED,
    DEFAULT_QUALITY_THRESHOLD,
    DEFAULT_QUALITY_TIMEOUT,
    build_dynamic_quality_problem,
    score_quality_solution,
)


DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_MODEL = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
DEFAULT_CODE_TASK = (
    "Return only Python code defining: def add(a, b): with a concise docstring "
    "and return a + b"
)
DEFAULT_PR_TASK = (
    "Return a pull request-ready markdown draft for a code change. Include exactly these "
    "sections: Title, Summary, Validation, and Risk. Do not claim the PR was created."
)
DEFAULT_REVIEW_TASK = (
    "Return a concise code review artifact for a proposed change. Include exactly these "
    "sections: Findings, Tests, Risk, and Recommendation. Include at least one concrete finding."
)
DEFAULT_BLOCKER_TASK = (
    "A required local validation command cannot run because pytest is missing from the vllm-skill conda "
    "environment. Return only a blocker report with BLOCKER, ATTEMPTED, OBSERVED, and SAFE_NEXT_STEP. "
    "Make SAFE_NEXT_STEP concrete, non-destructive, and specific to the project conda environment."
)
DEFAULT_NONBLOCKER_TASK = (
    "A code review finds missing edge-case unit coverage, but local validation can still run. "
    "Return a review artifact with Findings, Tests, Risk, and Recommendation. Do not use BLOCKER."
)
DEFAULT_TASK = DEFAULT_CODE_TASK
VALID_MODES = ("code", "pr", "review", "blocker", "nonblocker", "quality-dynamic")


def chat_payload(model: str, task: str, max_tokens: int, mode: str = "code") -> dict[str, Any]:
    """Build a small OpenAI-compatible coding request."""
    if mode == "pr":
        system_content = (
            "You are a coding assistant preparing pull request artifacts. "
            "Return only the requested PR draft content. Do not claim that an external PR was created."
        )
    elif mode == "review":
        system_content = (
            "You are a strict code reviewer. Return only the requested review artifact. "
            "Ground findings in concrete files, tests, risks, and recommendations."
        )
    elif mode == "blocker":
        system_content = (
            "You follow Daedalus blocker rules. Use BLOCKER only when required work cannot continue "
            "because of missing tools, sandbox access, cluster access, GPU access, auth, network, or "
            "credentials. Return exactly these labels: BLOCKER:, ATTEMPTED:, OBSERVED:, SAFE_NEXT_STEP:. "
            "For missing Python tools, prefer the project conda environment in SAFE_NEXT_STEP."
        )
    elif mode == "nonblocker":
        system_content = (
            "You follow Daedalus blocker rules. Do not use BLOCKER for ordinary findings, code risks, "
            "missing coverage, or recommendations when work can continue. Return only the requested "
            "review artifact."
        )
    elif mode == "quality-dynamic":
        system_content = (
            "You are solving a dynamic coding benchmark. Return only Python code that implements the "
            "requested function. Do not include markdown fences, prose, imports, filesystem access, "
            "network access, subprocess calls, eval, exec, or open."
        )
    else:
        system_content = (
            "You are a coding assistant. Return only valid Python code. "
            "Do not include markdown fences or explanation."
        )

    return {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "system",
                "content": system_content,
            },
            {"role": "user", "content": task},
        ],
    }


def post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    insecure_skip_tls_verify: bool = False,
    api_key: str = "",
) -> dict[str, Any]:
    """POST JSON and return decoded JSON."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    context = ssl._create_unverified_context() if insecure_skip_tls_verify else None
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_message(response: dict[str, Any]) -> str:
    """Extract the assistant message text from a chat completion response."""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("response does not contain choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("response does not contain choices[0].message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("response does not contain non-empty message content")
    return content.strip()


def extract_code(content: str) -> str:
    """Extract code from plain text or a markdown fenced block."""
    text = content.strip()
    fence = re.search(r"```(?:python)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def validate_add_function(code: str) -> dict[str, Any]:
    """Validate that the response contains a documented add(a, b) implementation."""
    module = ast.parse(code)
    functions = [node for node in module.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1:
        raise ValueError("expected exactly one top-level function")

    function = functions[0]
    if function.name != "add":
        raise ValueError("expected function named add")
    args = [arg.arg for arg in function.args.args]
    if args != ["a", "b"]:
        raise ValueError("expected add(a, b)")
    docstring = ast.get_docstring(function, clean=True)
    if not docstring:
        raise ValueError("expected add docstring")

    executable_body = function.body[1:] if _is_docstring_node(function.body[0]) else function.body
    if len(executable_body) != 1 or not isinstance(executable_body[0], ast.Return):
        raise ValueError("expected one return statement")

    value = executable_body[0].value
    if not isinstance(value, ast.BinOp) or not isinstance(value.op, ast.Add):
        raise ValueError("expected return expression to add two values")
    if not isinstance(value.left, ast.Name) or value.left.id != "a":
        raise ValueError("expected left side to be a")
    if not isinstance(value.right, ast.Name) or value.right.id != "b":
        raise ValueError("expected right side to be b")

    return {"function": function.name, "args": args, "docstring": docstring, "return": "a + b"}


def _is_docstring_node(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def validate_pr_artifact(content: str) -> dict[str, Any]:
    """Validate that model output is a PR-ready draft, not a live PR claim."""
    if _claims_live_pr_creation(content):
        raise ValueError("PR draft must not claim a live PR was created")

    lines = _artifact_lines(content)
    title_line = next((line for line in lines if line.lower().startswith("title:")), "")
    if title_line:
        title = title_line.split(":", 1)[1].strip()
    else:
        heading_line = next((line for line in lines if line.startswith("# ") and not line.startswith("## ")), "")
        title = heading_line.lstrip("#").strip() if heading_line else ""
    if not title:
        raise ValueError("expected PR title")

    sections = []
    for section in ("summary", "validation", "risk"):
        if not _has_section(lines, section):
            raise ValueError(f"expected {section.title()} section")
        sections.append(section)

    return {"title": title, "sections": sections}


def validate_review_artifact(content: str) -> dict[str, Any]:
    """Validate that model output is a review artifact with actionable sections."""
    lines = _artifact_lines(content)
    sections = []
    for section in ("findings", "tests", "risk", "recommendation"):
        if not _has_section(lines, section):
            raise ValueError(f"expected {section.title()} section")
        sections.append(section)

    finding_count = _section_item_count(lines, "findings")
    if finding_count < 1:
        raise ValueError("expected at least one finding")
    return {"sections": sections, "finding_count": finding_count}


def validate_blocker_artifact(content: str) -> dict[str, Any]:
    """Validate that model output reports a true blocker with operator steps."""
    lines = _artifact_lines(content)
    expected = (
        ("blocker", "BLOCKER"),
        ("attempted", "ATTEMPTED"),
        ("observed", "OBSERVED"),
        ("safe_next_step", "SAFE_NEXT_STEP"),
    )
    labels = []
    for normalized, label in expected:
        if not any(_line_label(line) == normalized for line in lines):
            raise ValueError(f"expected {label} label")
        labels.append(normalized)
    return {"labels": labels}


def validate_nonblocker_review_artifact(content: str) -> dict[str, Any]:
    """Validate ordinary review output and reject false blocker reports."""
    if re.search(r"^\s*BLOCKER\s*:", content, flags=re.IGNORECASE | re.MULTILINE):
        raise ValueError("non-blocker review must not use BLOCKER")
    validation = validate_review_artifact(content)
    validation["blocker"] = False
    return validation


def _artifact_lines(content: str) -> list[str]:
    text = content.strip()
    fence = re.fullmatch(r"```(?:markdown)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    return [line.strip() for line in text.splitlines() if line.strip()]


def _has_section(lines: list[str], section: str) -> bool:
    expected = section.lower()
    return any(_section_name(line) == expected for line in lines)


def _section_item_count(lines: list[str], section: str) -> int:
    start = next((index for index, line in enumerate(lines) if _section_name(line) == section), None)
    if start is None:
        return 0
    count = 0
    for line in lines[start + 1 :]:
        if _section_name(line) in {"findings", "tests", "risk", "recommendation", "summary", "validation"}:
            break
        if line.startswith(("-", "*")) or re.match(r"^\d+\.\s+", line):
            count += 1
    return count


def _section_name(line: str) -> str:
    name = line.strip().lstrip("#").strip().strip("*").strip()
    return name.lower().rstrip(":").strip()


def _line_label(line: str) -> str:
    label = line.split(":", 1)[0].strip().lstrip("#").strip().strip("*").strip()
    return label.lower().replace("-", "_").replace(" ", "_")


def _claims_live_pr_creation(content: str) -> bool:
    return bool(
        re.search(
            r"\b(?:created|opened|issued)\s+(?:a\s+)?(?:pull request|pr)\b",
            content,
            flags=re.IGNORECASE,
        )
    )


def build_quality_task(seed: int = DEFAULT_QUALITY_SEED) -> str:
    """Build the default seeded DynaCode-lite quality task prompt."""
    return build_dynamic_quality_problem(seed)["task"]


def read_task_file(path: str) -> str:
    """Read a model-evaluation task from a UTF-8 text file."""
    with open(path, "r", encoding="utf-8") as task_file:
        return task_file.read()


def run_sanity(
    base_url: str,
    model: str,
    task: str,
    timeout: float,
    max_tokens: int,
    insecure_skip_tls_verify: bool = False,
    mode: str = "code",
    quality_seed: int = DEFAULT_QUALITY_SEED,
    quality_threshold: int = DEFAULT_QUALITY_THRESHOLD,
    quality_timeout: float = DEFAULT_QUALITY_TIMEOUT,
    api_key: str = "",
) -> dict[str, Any]:
    """Run the coding sanity check and return an evidence report."""
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of: {', '.join(VALID_MODES)}")

    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    request_payload = chat_payload(model, task, max_tokens, mode)
    response = post_json(endpoint, request_payload, timeout, insecure_skip_tls_verify, api_key)
    content = extract_message(response)

    report = {
        "base_url": base_url,
        "artifact_type": {
            "code": "python_function",
            "pr": "pr_draft",
            "review": "review",
            "blocker": "blocker_report",
            "nonblocker": "nonblocker_review",
            "quality-dynamic": "quality_eval",
        }[mode],
        "api_key_present": bool(api_key),
        "insecure_skip_tls_verify": insecure_skip_tls_verify,
        "model": response.get("model", model),
        "passed": True,
        "usage": response.get("usage", {}),
    }
    if mode == "code":
        code = extract_code(content)
        report["code"] = code
        report["validation"] = validate_add_function(code)
    elif mode == "pr":
        report["artifact"] = content
        report["validation"] = validate_pr_artifact(content)
    elif mode == "review":
        report["artifact"] = content
        report["validation"] = validate_review_artifact(content)
    elif mode == "blocker":
        report["artifact"] = content
        report["validation"] = validate_blocker_artifact(content)
    else:
        report["artifact"] = content
        if mode == "nonblocker":
            report["validation"] = validate_nonblocker_review_artifact(content)
        else:
            quality = score_quality_solution(
                content,
                seed=quality_seed,
                threshold=quality_threshold,
                timeout=quality_timeout,
            )
            report.update(
                {
                    "benchmark": quality["benchmark"],
                    "code": quality["code"],
                    "errors": quality["errors"],
                    "max_score": quality["max_score"],
                    "passed": quality["passed"],
                    "score": quality["score"],
                    "seed": quality["seed"],
                    "test_results": quality["test_results"],
                    "threshold": quality["threshold"],
                    "validation": quality["validation"],
                }
            )
    return report


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("DAEDALUS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.getenv("DAEDALUS_MODEL", DEFAULT_MODEL))
    parser.add_argument("--mode", choices=VALID_MODES, default=os.getenv("DAEDALUS_SANITY_MODE", "code"))
    parser.add_argument("--task", default=None)
    parser.add_argument("--task-file", default=os.getenv("DAEDALUS_TASK_FILE", ""))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("DAEDALUS_TIMEOUT", "180")))
    parser.add_argument("--max-tokens", type=int, default=int(os.getenv("DAEDALUS_MAX_TOKENS", "96")))
    parser.add_argument("--quality-seed", type=int, default=int(os.getenv("DAEDALUS_QUALITY_SEED", DEFAULT_QUALITY_SEED)))
    parser.add_argument(
        "--quality-threshold",
        type=int,
        default=int(os.getenv("DAEDALUS_QUALITY_THRESHOLD", DEFAULT_QUALITY_THRESHOLD)),
    )
    parser.add_argument(
        "--quality-timeout",
        type=float,
        default=float(os.getenv("DAEDALUS_QUALITY_TIMEOUT", DEFAULT_QUALITY_TIMEOUT)),
    )
    parser.add_argument(
        "--insecure-skip-tls-verify",
        action="store_true",
        default=os.getenv("DAEDALUS_INSECURE_SKIP_TLS_VERIFY", "") == "1",
        help="Skip TLS certificate verification for lab routes with self-signed CAs.",
    )
    parser.add_argument(
        "--api-key-env",
        default=os.getenv("DAEDALUS_API_KEY_ENV", "DAEDALUS_API_KEY"),
        help="Environment variable containing the Daedalus route bearer token.",
    )
    args = parser.parse_args()
    task = read_task_file(args.task_file).strip() if args.task_file else args.task
    if task is None:
        if args.mode == "pr":
            task = os.getenv("DAEDALUS_PR_TASK", DEFAULT_PR_TASK)
        elif args.mode == "review":
            task = os.getenv("DAEDALUS_REVIEW_TASK", DEFAULT_REVIEW_TASK)
        elif args.mode == "blocker":
            task = os.getenv("DAEDALUS_BLOCKER_TASK", DEFAULT_BLOCKER_TASK)
        elif args.mode == "nonblocker":
            task = os.getenv("DAEDALUS_NONBLOCKER_TASK", DEFAULT_NONBLOCKER_TASK)
        elif args.mode == "quality-dynamic":
            task = os.getenv("DAEDALUS_QUALITY_TASK", build_quality_task(args.quality_seed))
        else:
            task = os.getenv("DAEDALUS_CODE_TASK", DEFAULT_CODE_TASK)

    try:
        report = run_sanity(
            args.base_url,
            args.model,
            task,
            args.timeout,
            args.max_tokens,
            args.insecure_skip_tls_verify,
            args.mode,
            args.quality_seed,
            args.quality_threshold,
            args.quality_timeout,
            os.getenv(args.api_key_env, ""),
        )
    except (urllib.error.URLError, TimeoutError, ValueError, SyntaxError, json.JSONDecodeError) as exc:
        report = {
            "base_url": args.base_url,
            "api_key_present": bool(os.getenv(args.api_key_env, "")),
            "insecure_skip_tls_verify": args.insecure_skip_tls_verify,
            "model": args.model,
            "passed": False,
            "error": str(exc),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("passed", False) else 1


if __name__ == "__main__":
    sys.exit(main())
