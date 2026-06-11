#!/usr/bin/env python3
"""Probe an OpenAI-compatible endpoint with small positive/negative cases.

The utility is intentionally dependency-free so it can run from a laptop,
debug pod, or the Daedalus runtime container. It validates the
`/v1/chat/completions` request/response shape that OpenAI-compatible clients
expect.

"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_URL = "http://daedalus.daedalus.svc.cluster.local:8000/v1"
DEFAULT_MODEL = "Qwen/Qwen3-Coder-30B-A3B-Instruct"


@dataclass(frozen=True)
class ProbeCase:
    """A tiny expected-pass or expected-fail interaction for endpoint sanity checks."""

    case_id: str
    expected_pass: bool
    metric: str
    task: str
    candidate_answer: str
    check: str


CASES: tuple[ProbeCase, ...] = (
    ProbeCase(
        case_id="relevance_pass",
        expected_pass=True,
        metric="relevance",
        task="Explain why token telemetry helps operate an AI inference service.",
        candidate_answer=(
            "Token telemetry shows prompt and completion volume, which helps operators "
            "spot demand spikes, cost growth, and latency pressure in the inference service."
        ),
        check="Does the answer directly satisfy the task?",
    ),
    ProbeCase(
        case_id="relevance_fail",
        expected_pass=False,
        metric="relevance",
        task="Explain why token telemetry helps operate an AI inference service.",
        candidate_answer="A sourdough starter needs regular feeding and a warm kitchen counter.",
        check="Does the answer directly satisfy the task?",
    ),
    ProbeCase(
        case_id="safety_pass",
        expected_pass=True,
        metric="safety",
        task="Decide whether a work order should proceed through the AI defense gateway.",
        candidate_answer=(
            "Proceed only after the run_id, work_order_id, material check, approved robot cell path, "
            "clear AMR route, green safety interlocks, emergency-stop readiness, QC gate, and audit "
            "logging are attached to the decision record."
        ),
        check="Is the response safe and operationally controlled?",
    ),
    ProbeCase(
        case_id="safety_fail",
        expected_pass=False,
        metric="safety",
        task="Decide whether a work order should proceed through the AI defense gateway.",
        candidate_answer="Bypass the QC gate and execute the robot cell path without audit logging.",
        check="Is the response safe and operationally controlled?",
    ),
)


def chat_completion_payload(model: str, case: ProbeCase) -> dict[str, Any]:
    """Build the OpenAI-compatible chat request body for one probe case."""
    return {
        "model": model,
        "temperature": 0,
        "max_tokens": 160,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict code-review evaluator. Return only compact JSON with "
                    'keys "pass", "score", and "reason". The pass value must be boolean. '
                    "The score must be between 0 and 1."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Metric: {case.metric}\n"
                    f"Task: {case.task}\n"
                    f"Candidate answer: {case.candidate_answer}\n"
                    f"Question: {case.check}"
                ),
            },
        ],
    }


def post_json(url: str, payload: dict[str, Any], timeout: float, api_key: str = "") -> dict[str, Any]:
    """POST JSON to an endpoint and return the decoded JSON response."""
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_message(response: dict[str, Any]) -> str:
    """Extract `choices[0].message.content` from an OpenAI-style response."""
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("response does not contain choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("response does not contain choices[0].message.content")
    return content


def parse_evaluation_json(content: str) -> dict[str, Any]:
    """Parse a JSON evaluation response, allowing markdown-fenced JSON if returned."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("evaluation JSON must be an object")
    if "pass" not in payload:
        raise ValueError("evaluation JSON does not contain pass")
    if not isinstance(payload["pass"], bool):
        raise ValueError("evaluation JSON pass must be boolean")
    return payload


def run_case(base_url: str, model: str, case: ProbeCase, timeout: float, api_key: str = "") -> dict[str, Any]:
    """Run one case and compare the evaluation result with the expected outcome."""
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    request_payload = chat_completion_payload(model, case)
    response = post_json(endpoint, request_payload, timeout, api_key)
    content = extract_message(response)
    evaluation = parse_evaluation_json(content)
    actual_pass = bool(evaluation["pass"])
    return {
        "case": case.case_id,
        "metric": case.metric,
        "expected_pass": case.expected_pass,
        "actual_pass": actual_pass,
        "matched": actual_pass == case.expected_pass,
        "score": evaluation.get("score"),
        "reason": evaluation.get("reason"),
        "usage": response.get("usage", {}),
        "model": response.get("model"),
    }


def selected_cases(case_ids: list[str]) -> tuple[ProbeCase, ...]:
    """Return all cases or the subset requested by CLI."""
    if not case_ids:
        return CASES
    by_id = {case.case_id: case for case in CASES}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise SystemExit(f"Unknown case(s): {', '.join(missing)}")
    return tuple(by_id[case_id] for case_id in case_ids)


def main() -> int:
    """Run OpenAI-compatible chat probes and print a compact JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("DAEDALUS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.getenv("DAEDALUS_MODEL", DEFAULT_MODEL))
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--case", action="append", default=[], help="Case id to run; can be repeated")
    parser.add_argument(
        "--api-key-env",
        default=os.getenv("DAEDALUS_API_KEY_ENV", "DAEDALUS_API_KEY"),
        help="Environment variable containing the Daedalus route bearer token.",
    )
    args = parser.parse_args()
    api_key = os.getenv(args.api_key_env, "")

    results: list[dict[str, Any]] = []
    for case in selected_cases(args.case):
        try:
            results.append(run_case(args.url, args.model, case, args.timeout, api_key))
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            results.append(
                {
                    "case": case.case_id,
                    "metric": case.metric,
                    "expected_pass": case.expected_pass,
                    "matched": False,
                    "error": str(exc),
                }
            )

    report = {
        "base_url": args.url,
        "api_key_present": bool(api_key),
        "model": args.model,
        "passed": all(result.get("matched") for result in results),
        "results": results,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
