"""Unit tests for the Daedalus endpoint sanity helper.

The tests verify payload construction, artifact validators, mocked HTTP modes,
and quality-dynamic CLI scoring without calling a live endpoint.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


APP_PATH = Path(__file__).resolve().parents[1] / "scripts" / "daedalus-code-sanity.py"
APP_MODULE_NAME = "daedalus_code_sanity_under_test"


class FakeUrlopenResponse:
    """Provide a context-managed fake HTTP response with JSON content."""

    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def load_module():
    """Load the sanity script as an isolated module for each test."""

    sys.modules.pop(APP_MODULE_NAME, None)
    spec = importlib.util.spec_from_file_location(APP_MODULE_NAME, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_chat_payload_uses_openai_chat_shape():
    """Verify code mode builds an OpenAI-compatible chat request."""

    module = load_module()

    payload = module.chat_payload("unit-model", "write code", 55)

    assert payload["model"] == "unit-model"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 55
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1] == {"role": "user", "content": "write code"}


def test_chat_payload_pr_mode_uses_pr_system_prompt():
    """Verify PR mode selects the pull-request drafting prompt."""

    module = load_module()

    payload = module.chat_payload("unit-model", module.DEFAULT_PR_TASK, 120, mode="pr")

    assert "pull request" in payload["messages"][0]["content"]
    assert "valid Python code" not in payload["messages"][0]["content"]


def test_chat_payload_review_mode_uses_review_system_prompt():
    """Verify review mode selects the code-review prompt and task."""

    module = load_module()

    payload = module.chat_payload("unit-model", module.DEFAULT_REVIEW_TASK, 180, mode="review")

    assert "code reviewer" in payload["messages"][0]["content"]
    assert "pull request" not in payload["messages"][0]["content"]
    assert payload["messages"][1]["content"] == module.DEFAULT_REVIEW_TASK


def test_chat_payload_blocker_mode_uses_blocker_system_prompt():
    """Verify blocker mode teaches the exact report shape for blocked work."""

    module = load_module()

    payload = module.chat_payload("unit-model", module.DEFAULT_BLOCKER_TASK, 180, mode="blocker")

    assert "BLOCKER:" in payload["messages"][0]["content"]
    assert "SAFE_NEXT_STEP:" in payload["messages"][0]["content"]
    assert "missing tools" in payload["messages"][0]["content"]
    assert "conda environment" in payload["messages"][1]["content"]
    assert payload["messages"][1]["content"] == module.DEFAULT_BLOCKER_TASK


def test_chat_payload_nonblocker_mode_rejects_false_blockers_in_prompt():
    """Verify nonblocker mode tells the model not to block ordinary findings."""

    module = load_module()

    payload = module.chat_payload("unit-model", module.DEFAULT_NONBLOCKER_TASK, 180, mode="nonblocker")

    assert "Do not use BLOCKER" in payload["messages"][0]["content"]
    assert "ordinary findings" in payload["messages"][0]["content"]
    assert payload["messages"][1]["content"] == module.DEFAULT_NONBLOCKER_TASK


def test_chat_payload_quality_dynamic_mode_uses_benchmark_prompt():
    """Verify quality mode asks for code only so the scorer can execute it."""

    module = load_module()

    payload = module.chat_payload("unit-model", "DynaCode-lite task", 1200, mode="quality-dynamic")

    assert "dynamic coding benchmark" in payload["messages"][0]["content"]
    assert "Return only Python code" in payload["messages"][0]["content"]
    assert payload["messages"][1]["content"] == "DynaCode-lite task"


def test_default_code_task_requires_docstring():
    """Verify the default code prompt asks for a documented add function."""

    module = load_module()

    assert "docstring" in module.DEFAULT_CODE_TASK
    assert "def add(a, b)" in module.DEFAULT_CODE_TASK


def test_extract_code_accepts_plain_and_fenced_code():
    """Verify code extraction handles plain and fenced Python snippets."""

    module = load_module()

    assert module.extract_code("def add(a, b):\n    return a + b") == "def add(a, b):\n    return a + b"
    assert module.extract_code("```python\ndef add(a, b):\n    return a + b\n```") == (
        "def add(a, b):\n    return a + b"
    )


def test_validate_add_function_accepts_expected_shape():
    """Verify a documented add function passes structural validation."""

    module = load_module()

    assert module.validate_add_function(
        'def add(a, b):\n    """Return the sum of a and b."""\n    return a + b\n'
    ) == {
        "function": "add",
        "args": ["a", "b"],
        "docstring": "Return the sum of a and b.",
        "return": "a + b",
    }


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("def subtract(a, b):\n    return a - b\n", "expected function named add"),
        ("def add(x, y):\n    return x + y\n", "expected add"),
        ("def add(a, b):\n    return a + b\n", "expected add docstring"),
        ('def add(a, b):\n    """Return the sum of a and b."""\n    return a - b\n', "expected return expression"),
        ('def add(a, b):\n    """Return the sum of a and b."""\n    return b + a\n', "expected left side"),
    ],
)
def test_validate_add_function_rejects_wrong_shapes(code, message):
    """Verify malformed add functions fail with targeted errors."""

    module = load_module()

    with pytest.raises(ValueError, match=message):
        module.validate_add_function(code)


def test_validate_pr_artifact_accepts_pr_ready_markdown():
    """Verify a complete PR draft artifact passes validation."""

    module = load_module()

    assert module.validate_pr_artifact(
        "\n".join(
            [
                "Title: Add Daedalus route sanity mode",
                "Summary:",
                "- Adds a route sanity mode for the Daedalus endpoint.",
                "Validation:",
                "- conda run -n vllm-skill pytest tests/test_daedalus_code_sanity.py",
                "Risk:",
                "- No live cluster mutation; HTTP is mocked in unit tests.",
            ]
        )
    ) == {
        "title": "Add Daedalus route sanity mode",
        "sections": ["summary", "validation", "risk"],
    }


def test_validate_pr_artifact_accepts_fenced_markdown_with_heading_title():
    """Verify fenced markdown with heading-style title validates as a PR draft."""

    module = load_module()

    assert module.validate_pr_artifact(
        "\n".join(
            [
                "```markdown",
                "# Add Daedalus route sanity mode",
                "## Summary",
                "- Adds a route sanity mode for the Daedalus endpoint.",
                "## Validation",
                "- pytest tests/test_daedalus_code_sanity.py",
                "## Risk",
                "- No live PR is created by this unit test.",
                "```",
            ]
        )
    ) == {
        "title": "Add Daedalus route sanity mode",
        "sections": ["summary", "validation", "risk"],
    }


def test_validate_pr_artifact_rejects_claimed_live_pr_creation():
    """Verify PR drafts cannot claim that a live pull request was created."""

    module = load_module()

    for claim in ("Created PR #42 for the route change.", "Opened pull request for the route change."):
        with pytest.raises(ValueError, match="must not claim"):
            module.validate_pr_artifact(
                "\n".join(
                    [
                        "Title: Add Daedalus route sanity mode",
                        "Summary:",
                        f"- {claim}",
                        "Validation:",
                        "- pytest tests/test_daedalus_code_sanity.py",
                        "Risk:",
                        "- Low.",
                    ]
                )
            )


def test_validate_pr_artifact_allows_negative_creation_statement():
    """Verify PR drafts may explicitly say no live pull request was created."""

    module = load_module()

    assert module.validate_pr_artifact(
        "\n".join(
            [
                "Title: Add Daedalus route sanity mode",
                "Summary:",
                "- No live PR is created by this check.",
                "Validation:",
                "- pytest tests/test_daedalus_code_sanity.py",
                "Risk:",
                "- Low.",
            ]
        )
    ) == {
        "title": "Add Daedalus route sanity mode",
        "sections": ["summary", "validation", "risk"],
    }


def test_validate_review_artifact_accepts_fenced_markdown():
    """Verify fenced markdown review output passes artifact validation."""

    module = load_module()

    assert module.validate_review_artifact(
        "\n".join(
            [
                "```markdown",
                "# Code Review",
                "## Findings",
                "- P2 scripts/daedalus-code-sanity.py: review mode needs docs.",
                "## Tests",
                "- pytest tests/test_daedalus_code_sanity.py",
                "## Risk",
                "- Low.",
                "## Recommendation",
                "- Keep review checks deterministic.",
                "```",
            ]
        )
    ) == {
        "sections": ["findings", "tests", "risk", "recommendation"],
        "finding_count": 1,
    }


def test_validate_review_artifact_accepts_bold_headings_and_numbered_findings():
    """Verify review validation accepts bold headings and numbered findings."""

    module = load_module()

    assert module.validate_review_artifact(
        "\n".join(
            [
                "**Findings:**",
                "1. **Missing Mode Validation**: review mode needs explicit validation.",
                "2. **Incomplete Test Coverage**: edge cases should be covered.",
                "**Tests:**",
                "- pytest tests/test_daedalus_code_sanity.py",
                "**Risk:**",
                "- Medium.",
                "**Recommendation:**",
                "1. Add deterministic review-mode tests.",
            ]
        )
    ) == {
        "sections": ["findings", "tests", "risk", "recommendation"],
        "finding_count": 2,
    }


def test_validate_review_artifact_rejects_empty_findings():
    """Verify review output must include at least one concrete finding."""

    module = load_module()

    with pytest.raises(ValueError, match="expected at least one finding"):
        module.validate_review_artifact(
            "\n".join(
                [
                    "Findings:",
                    "Tests:",
                    "- pytest tests/test_daedalus_code_sanity.py",
                    "Risk:",
                    "- Low.",
                    "Recommendation:",
                    "- Add a finding.",
                ]
            )
        )


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ("Summary:\n- Missing a title\nValidation:\n- pytest\nRisk:\n- low", "expected PR title"),
        ("Title: Add thing\nValidation:\n- pytest\nRisk:\n- low", "expected Summary section"),
        ("Title: Add thing\nSummary:\n- hi\nRisk:\n- low", "expected Validation section"),
        ("Title: Add thing\nSummary:\n- hi\nValidation:\n- pytest", "expected Risk section"),
    ],
)
def test_validate_pr_artifact_rejects_missing_pr_sections(artifact, message):
    """Verify PR artifacts reject each required section when it is absent."""

    module = load_module()

    with pytest.raises(ValueError, match=message):
        module.validate_pr_artifact(artifact)


def test_validate_review_artifact_accepts_review_markdown():
    """Verify standard review markdown passes section and finding validation."""

    module = load_module()

    assert module.validate_review_artifact(
        "\n".join(
            [
                "## Findings",
                "- P1 tests/test_daedalus_code_sanity.py: add coverage for review mode.",
                "## Tests",
                "- conda run -n vllm-skill pytest tests/test_daedalus_code_sanity.py",
                "## Risk",
                "- Low; HTTP calls are mocked.",
                "## Recommendation",
                "- Merge after full local validation passes.",
            ]
        )
    ) == {
        "sections": ["findings", "tests", "risk", "recommendation"],
        "finding_count": 1,
    }


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ("Tests:\n- pytest\nRisk:\n- low\nRecommendation:\n- merge", "expected Findings section"),
        ("Findings:\n- issue\nRisk:\n- low\nRecommendation:\n- merge", "expected Tests section"),
        ("Findings:\n- issue\nTests:\n- pytest\nRecommendation:\n- merge", "expected Risk section"),
        ("Findings:\n- issue\nTests:\n- pytest\nRisk:\n- low", "expected Recommendation section"),
    ],
)
def test_validate_review_artifact_rejects_missing_sections(artifact, message):
    """Verify review artifacts reject each required section when absent."""

    module = load_module()

    with pytest.raises(ValueError, match=message):
        module.validate_review_artifact(artifact)


def test_validate_blocker_artifact_accepts_daedalus_blocker_report():
    """Verify blocked tool access is reported with all Daedalus labels."""

    module = load_module()

    assert module.validate_blocker_artifact(
        "\n".join(
            [
                "BLOCKER: pytest is not installed in the project environment.",
                "ATTEMPTED: conda run -n vllm-skill python -m pytest tests/test_daedalus_code_sanity.py",
                "OBSERVED: ModuleNotFoundError: No module named pytest",
                "SAFE_NEXT_STEP: Install pytest in the vllm-skill conda environment, then rerun the test.",
            ]
        )
    ) == {
        "labels": ["blocker", "attempted", "observed", "safe_next_step"],
    }


def test_validate_blocker_artifact_accepts_fenced_markdown():
    """Verify fenced blocker reports validate because models may fence text."""

    module = load_module()

    assert module.validate_blocker_artifact(
        "\n".join(
            [
                "```markdown",
                "BLOCKER: OpenShift route lookup is blocked by missing cluster auth.",
                "ATTEMPTED: oc get route daedalus -n daedalus",
                "OBSERVED: You must be logged in to the server.",
                "SAFE_NEXT_STEP: Log in with oc, then repeat the read-only route check.",
                "```",
            ]
        )
    ) == {
        "labels": ["blocker", "attempted", "observed", "safe_next_step"],
    }


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        (
            "ATTEMPTED: pytest\nOBSERVED: missing pytest\nSAFE_NEXT_STEP: install pytest",
            "expected BLOCKER label",
        ),
        (
            "BLOCKER: pytest missing\nOBSERVED: missing pytest\nSAFE_NEXT_STEP: install pytest",
            "expected ATTEMPTED label",
        ),
        (
            "BLOCKER: pytest missing\nATTEMPTED: pytest\nSAFE_NEXT_STEP: install pytest",
            "expected OBSERVED label",
        ),
        (
            "BLOCKER: pytest missing\nATTEMPTED: pytest\nOBSERVED: missing pytest",
            "expected SAFE_NEXT_STEP label",
        ),
    ],
)
def test_validate_blocker_artifact_rejects_missing_labels(artifact, message):
    """Verify incomplete blocker reports fail because operators need steps."""

    module = load_module()

    with pytest.raises(ValueError, match=message):
        module.validate_blocker_artifact(artifact)


def test_validate_nonblocker_review_artifact_rejects_false_blocker():
    """Verify ordinary review findings cannot be mislabeled as blockers."""

    module = load_module()

    with pytest.raises(ValueError, match="must not use BLOCKER"):
        module.validate_nonblocker_review_artifact(
            "\n".join(
                [
                    "BLOCKER: Missing edge-case coverage.",
                    "Findings:",
                    "- P2 tests/test_daedalus_code_sanity.py: add coverage for blocker reports.",
                    "Tests:",
                    "- pytest tests/test_daedalus_code_sanity.py",
                    "Risk:",
                    "- Low.",
                    "Recommendation:",
                    "- Add deterministic unit tests.",
                ]
            )
        )


def test_validate_nonblocker_review_artifact_accepts_actionable_review():
    """Verify non-blocking review feedback stays in the review shape."""

    module = load_module()

    assert module.validate_nonblocker_review_artifact(
        "\n".join(
            [
                "Findings:",
                "- P2 tests/test_daedalus_code_sanity.py: blocker edge coverage is missing.",
                "Tests:",
                "- pytest tests/test_daedalus_code_sanity.py",
                "Risk:",
                "- Low; this is a local validation gap.",
                "Recommendation:",
                "- Add mocked tests for blocker and non-blocker behavior.",
            ]
        )
    ) == {
        "sections": ["findings", "tests", "risk", "recommendation"],
        "finding_count": 1,
        "blocker": False,
    }


def test_run_sanity_posts_to_chat_completion_and_validates(monkeypatch):
    """Verify code-mode sanity posts chat completions and validates returned code."""

    module = load_module()
    calls = []

    def fake_urlopen(request, timeout, context=None):
        calls.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "context": context,
                "body": json.loads(request.data.decode("utf-8")),
                "authorization": request.get_header("Authorization"),
            }
        )
        return FakeUrlopenResponse(
            {
                "model": "unit-model",
                "choices": [
                    {
                        "message": {
                            "content": 'def add(a, b):\n    """Return the sum of a and b."""\n    return a + b\n'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 9, "total_tokens": 19},
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    report = module.run_sanity("http://example.test/v1/", "unit-model", "task", 7, 33, api_key="unit-token")

    assert calls[0]["url"] == "http://example.test/v1/chat/completions"
    assert calls[0]["timeout"] == 7
    assert calls[0]["context"] is None
    assert calls[0]["body"]["model"] == "unit-model"
    assert calls[0]["body"]["max_tokens"] == 33
    assert calls[0]["authorization"] == "Bearer unit-token"
    assert report["passed"] is True
    assert report["api_key_present"] is True
    assert "unit-token" not in json.dumps(report)
    assert report["artifact_type"] == "python_function"
    assert report["insecure_skip_tls_verify"] is False
    assert report["code"] == 'def add(a, b):\n    """Return the sum of a and b."""\n    return a + b'
    assert report["usage"] == {"prompt_tokens": 10, "completion_tokens": 9, "total_tokens": 19}


def test_run_sanity_pr_mode_validates_pr_ready_artifact(monkeypatch):
    """Verify PR mode validates the returned pull-request draft artifact."""

    module = load_module()
    calls = []

    def fake_urlopen(request, timeout, context=None):
        calls.append(json.loads(request.data.decode("utf-8")))
        return FakeUrlopenResponse(
            {
                "model": "unit-model",
                "choices": [
                    {
                        "message": {
                            "content": "\n".join(
                                [
                                    "Title: Add Daedalus PR sanity check",
                                    "Summary:",
                                    "- Adds a mocked PR artifact validation path.",
                                    "Validation:",
                                    "- pytest tests/test_daedalus_code_sanity.py",
                                    "Risk:",
                                    "- No GitHub API call is made.",
                                ]
                            )
                        }
                    }
                ],
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    report = module.run_sanity("https://example.test/v1", "unit-model", module.DEFAULT_PR_TASK, 9, 120, mode="pr")

    assert "pull request" in calls[0]["messages"][0]["content"]
    assert "valid Python code" not in calls[0]["messages"][0]["content"]
    assert "pull request" in calls[0]["messages"][1]["content"]
    assert report["passed"] is True
    assert report["artifact_type"] == "pr_draft"
    assert report["validation"] == {
        "title": "Add Daedalus PR sanity check",
        "sections": ["summary", "validation", "risk"],
    }


def test_run_sanity_review_mode_validates_review_artifact(monkeypatch):
    """Verify review mode validates returned findings and review sections."""

    module = load_module()
    calls = []

    def fake_urlopen(request, timeout, context=None):
        calls.append(json.loads(request.data.decode("utf-8")))
        return FakeUrlopenResponse(
            {
                "model": "unit-model",
                "choices": [
                    {
                        "message": {
                            "content": "\n".join(
                                [
                                    "Findings:",
                                    "- P2 scripts/daedalus-code-sanity.py: review mode needs coverage.",
                                    "Tests:",
                                    "- pytest tests/test_daedalus_code_sanity.py",
                                    "Risk:",
                                    "- Low.",
                                    "Recommendation:",
                                    "- Add deterministic unit tests before live checks.",
                                ]
                            )
                        }
                    }
                ],
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    report = module.run_sanity(
        "https://example.test/v1", "unit-model", module.DEFAULT_REVIEW_TASK, 9, 220, mode="review"
    )

    assert "code reviewer" in calls[0]["messages"][0]["content"]
    assert report["passed"] is True
    assert report["artifact_type"] == "review"
    assert report["validation"] == {
        "sections": ["findings", "tests", "risk", "recommendation"],
        "finding_count": 1,
    }


def test_run_sanity_blocker_mode_validates_blocker_report(monkeypatch):
    """Verify blocker mode validates a mocked blocked-tool response."""

    module = load_module()

    def fake_urlopen(request, timeout, context=None):
        return FakeUrlopenResponse(
            {
                "model": "unit-model",
                "choices": [
                    {
                        "message": {
                            "content": "\n".join(
                                [
                                    "BLOCKER: pytest is unavailable.",
                                    "ATTEMPTED: conda run -n vllm-skill python -m pytest tests/test_daedalus_code_sanity.py",
                                    "OBSERVED: ModuleNotFoundError: No module named pytest",
                                    "SAFE_NEXT_STEP: Install pytest in the conda environment and rerun the command.",
                                ]
                            )
                        }
                    }
                ],
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    report = module.run_sanity(
        "https://example.test/v1", "unit-model", module.DEFAULT_BLOCKER_TASK, 9, 220, mode="blocker"
    )

    assert report["passed"] is True
    assert report["artifact_type"] == "blocker_report"
    assert report["validation"] == {
        "labels": ["blocker", "attempted", "observed", "safe_next_step"],
    }


def test_run_sanity_nonblocker_mode_validates_review_without_blocker(monkeypatch):
    """Verify nonblocker mode validates normal review feedback without BLOCKER."""

    module = load_module()

    def fake_urlopen(request, timeout, context=None):
        return FakeUrlopenResponse(
            {
                "model": "unit-model",
                "choices": [
                    {
                        "message": {
                            "content": "\n".join(
                                [
                                    "Findings:",
                                    "- P2 tests/test_daedalus_code_sanity.py: add blocker report coverage.",
                                    "Tests:",
                                    "- pytest tests/test_daedalus_code_sanity.py",
                                    "Risk:",
                                    "- Low.",
                                    "Recommendation:",
                                    "- Add mocked local tests.",
                                ]
                            )
                        }
                    }
                ],
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    report = module.run_sanity(
        "https://example.test/v1", "unit-model", module.DEFAULT_NONBLOCKER_TASK, 9, 220, mode="nonblocker"
    )

    assert report["passed"] is True
    assert report["artifact_type"] == "nonblocker_review"
    assert report["validation"] == {
        "sections": ["findings", "tests", "risk", "recommendation"],
        "finding_count": 1,
        "blocker": False,
    }


def test_run_sanity_quality_dynamic_mode_scores_generated_code(monkeypatch):
    """Verify quality mode scores generated code against dynamic hidden tests."""

    module = load_module()
    calls = []
    generated_code = '''
def evaluate_pipeline(records, rules):
    """Apply scoring rules to records and return deterministic results."""
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
    ordered = sorted(scores)
    return {
        "count": len(ordered),
        "accepted": [record_id for record_id in ordered if scores[record_id] >= accept_at],
        "rejected": [record_id for record_id in ordered if scores[record_id] < accept_at],
        "scores": scores,
        "checksum": sum((index + 1) * scores[record_id] for index, record_id in enumerate(ordered)),
    }
'''

    def fake_urlopen(request, timeout, context=None):
        calls.append(json.loads(request.data.decode("utf-8")))
        return FakeUrlopenResponse(
            {
                "model": "unit-model",
                "choices": [{"message": {"content": generated_code}}],
                "usage": {"prompt_tokens": 300, "completion_tokens": 500, "total_tokens": 800},
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    report = module.run_sanity(
        "https://example.test/v1",
        "unit-model",
        module.build_quality_task(20260611),
        9,
        1200,
        mode="quality-dynamic",
        quality_seed=20260611,
        quality_threshold=75,
        quality_timeout=3,
    )

    assert "dynamic coding benchmark" in calls[0]["messages"][0]["content"]
    assert "DynaCode-lite" in calls[0]["messages"][1]["content"]
    assert report["artifact_type"] == "quality_eval"
    assert report["benchmark"] == "dynacode_lite_pipeline_v1"
    assert report["score"] == 100
    assert report["max_score"] == 100
    assert report["passed"] is True
    assert report["validation"]["hidden_passed"] == report["validation"]["hidden_total"]


def test_run_sanity_rejects_unknown_mode_before_http(monkeypatch):
    """Verify unknown sanity modes fail before making HTTP requests."""

    module = load_module()

    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("HTTP should not run")),
    )

    with pytest.raises(ValueError, match="mode must be one of"):
        module.run_sanity("https://example.test/v1", "unit-model", "task", 9, 32, mode="unknown")


def test_read_task_file_loads_saved_evaluation_prompt(tmp_path):
    """Verify the validator can read reusable task prompts from files."""

    module = load_module()
    task_file = tmp_path / "review-task.md"
    task_file.write_text("Review this saved prompt.\n", encoding="utf-8")

    assert module.read_task_file(str(task_file)) == "Review this saved prompt.\n"


def test_run_sanity_can_skip_tls_verification(monkeypatch):
    """Verify sanity checks can opt into an insecure TLS context."""

    module = load_module()
    calls = []

    def fake_urlopen(request, timeout, context=None):
        calls.append({"context": context})
        return FakeUrlopenResponse(
            {
                "model": "unit-model",
                "choices": [
                    {
                        "message": {
                            "content": 'def add(a, b):\n    """Return the sum of a and b."""\n    return a + b\n'
                        }
                    }
                ],
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    report = module.run_sanity("https://example.test/v1", "unit-model", "task", 7, 33, True)

    assert calls[0]["context"] is not None
    assert report["passed"] is True
    assert report["insecure_skip_tls_verify"] is True
