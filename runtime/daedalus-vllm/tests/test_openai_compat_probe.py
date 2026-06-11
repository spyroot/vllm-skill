"""Unit tests for the OpenAI-compatible endpoint probe.

The tests verify payload shape, mocked chat completions, and evaluation JSON
parsing without calling a live model endpoint.

"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


APP_PATH = Path(__file__).resolve().parents[1] / "openai_compat_probe.py"
APP_MODULE_NAME = "openai_compat_probe_under_test"


def load_module():
    """Load the probe helper as an isolated module for each test."""

    sys.modules.pop(APP_MODULE_NAME, None)
    spec = importlib.util.spec_from_file_location(APP_MODULE_NAME, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeUrlopenResponse:
    """Provide a context-managed fake HTTP response with JSON content."""

    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_chat_completion_payload_uses_openai_chat_shape():
    """Verify probe payloads use the OpenAI chat completions shape."""

    module = load_module()
    case = module.CASES[0]

    payload = module.chat_completion_payload("unit-model", case)

    assert payload["model"] == "unit-model"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 160
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert case.task in payload["messages"][1]["content"]


def test_default_model_matches_h100_coder_runtime():
    """Verify the probe default model matches the current H100 coder runtime."""

    module = load_module()

    assert module.DEFAULT_MODEL == "Qwen/Qwen3-Coder-30B-A3B-Instruct"


def test_run_case_posts_to_chat_completions_and_extracts_usage(monkeypatch):
    """Verify a probe case posts to chat completions and reports usage."""

    module = load_module()
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "body": json.loads(request.data.decode("utf-8")),
                "authorization": request.get_header("Authorization"),
            }
        )
        return _FakeUrlopenResponse(
            {
                "model": "unit-model",
                "choices": [{"message": {"content": '{"pass": true, "score": 0.93, "reason": "aligned"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.run_case("http://daedalus.example.test/v1/", "unit-model", module.CASES[0], 9, "unit-token")

    assert calls[0]["url"] == "http://daedalus.example.test/v1/chat/completions"
    assert calls[0]["timeout"] == 9
    assert calls[0]["body"]["model"] == "unit-model"
    assert calls[0]["authorization"] == "Bearer unit-token"
    assert result == {
        "case": "relevance_pass",
        "metric": "relevance",
        "expected_pass": True,
        "actual_pass": True,
        "matched": True,
        "score": 0.93,
        "reason": "aligned",
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        "model": "unit-model",
    }


def test_parse_evaluation_json_accepts_markdown_fenced_json():
    """Verify evaluation JSON can be parsed from markdown fences."""

    module = load_module()

    assert module.parse_evaluation_json('```json\n{"pass": false, "score": 0.2, "reason": "off topic"}\n```') == {
        "pass": False,
        "score": 0.2,
        "reason": "off topic",
    }


def test_parse_evaluation_json_rejects_non_object_payload():
    """Verify evaluation parsing rejects JSON arrays and other non-objects."""

    module = load_module()

    with pytest.raises(ValueError, match="must be an object"):
        module.parse_evaluation_json("[1, 2, 3]")


def test_parse_evaluation_json_rejects_non_boolean_pass():
    """Verify evaluation parsing requires a boolean pass field."""

    module = load_module()

    with pytest.raises(ValueError, match="pass must be boolean"):
        module.parse_evaluation_json('{"pass": "false", "score": 0.2, "reason": "off topic"}')
