"""Unit tests for the installed Daedalus skill client.

The tests verify task-file prompts, bearer-token headers, and JSON reporting
without calling the hosted OpenShift/vLLM endpoint.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "skills" / "daedalus" / "scripts" / "daedalus-client.py"
APP_MODULE_NAME = "daedalus_skill_client_under_test"


def load_module():
    """Load the skill client as an isolated module for each test."""

    sys.modules.pop(APP_MODULE_NAME, None)
    spec = importlib.util.spec_from_file_location(APP_MODULE_NAME, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def test_chat_payload_uses_mode_specific_system_prompt():
    """Verify mode templates become system prompts and tasks stay user content."""

    module = load_module()

    payload = module.chat_payload("unit-model", "pr", "draft this", 123)

    assert payload["model"] == "unit-model"
    assert payload["max_tokens"] == 123
    assert "pull request" in payload["messages"][0]["content"]
    assert payload["messages"][1]["content"].startswith("# Daedalus Request")
    assert "MODE: pr" in payload["messages"][1]["content"]
    assert "draft this" in payload["messages"][1]["content"]


def test_all_modes_have_nonempty_prompt_templates():
    """Verify every support-agent mode has a checked-in system prompt."""

    module = load_module()

    for mode in module.VALID_MODES:
        prompt = module.load_system_prompt(mode)
        assert prompt.startswith("You are") or prompt.startswith("Solve")
        assert len(prompt) > 80


def test_build_user_message_packs_selected_diff_before_file(tmp_path):
    """Verify selected diffs/files are labeled and ordered for efficient review."""

    module = load_module()
    diff_file = tmp_path / "change.diff"
    source_file = tmp_path / "module.py"
    diff_file.write_text("--- a/module.py\n+++ b/module.py\n+return value\n", encoding="utf-8")
    source_file.write_text("def example():\n    return 'ok'\n", encoding="utf-8")

    message = module.build_user_message(
        "review",
        "review selected context",
        context_files=[str(source_file)],
        diff_files=[str(diff_file)],
    )

    assert "## Task" in message
    assert "review selected context" in message
    assert message.index(f"### Diff: {diff_file}") < message.index(f"### File: {source_file}")
    assert "def example" in message


def test_build_user_message_redacts_secret_like_content(tmp_path):
    """Verify accidental token-looking context is redacted before prompting."""

    module = load_module()
    notes_file = tmp_path / "notes.txt"
    notes_file.write_text(
        "DAEDALUS_API_KEY=live-secret-token\nAuthorization: Bearer abcdefghijklmnop123456\n",
        encoding="utf-8",
    )

    message = module.build_user_message("review", "check notes", context_files=[str(notes_file)])

    assert "DAEDALUS_API_KEY=<redacted>" in message
    assert "Bearer <redacted>" in message
    assert "live-secret-token" not in message
    assert "abcdefghijklmnop123456" not in message


def test_build_user_message_refuses_secret_like_context_paths(tmp_path):
    """Verify .env-style files are blocked because they often hold real tokens."""

    module = load_module()
    env_file = tmp_path / ".env.daedalus.local"
    env_file.write_text("DAEDALUS_API_KEY=secret\n", encoding="utf-8")

    try:
        module.build_user_message("review", "check env", context_files=[str(env_file)])
    except ValueError as exc:
        assert "secret-like filename" in str(exc)
    else:
        raise AssertionError("expected secret-like context path to be rejected")


def test_build_user_message_truncates_large_context(tmp_path):
    """Verify large selected files are capped so prompts stay bounded."""

    module = load_module()
    large_file = tmp_path / "large.py"
    large_file.write_text("x" * 500, encoding="utf-8")

    message = module.build_user_message("review", "check cap", context_files=[str(large_file)], max_context_chars=400)

    assert "[TRUNCATED:" in message
    assert "of 500 characters" in message


def test_build_user_message_keeps_shared_context_budget(tmp_path):
    """Verify diff sections consume budget before later file sections."""

    module = load_module()
    diff_file = tmp_path / "large.diff"
    source_file = tmp_path / "later.py"
    diff_file.write_text("d" * 500, encoding="utf-8")
    source_file.write_text("def later():\n    return True\n", encoding="utf-8")

    message = module.build_user_message(
        "review",
        "check budget",
        context_files=[str(source_file)],
        diff_files=[str(diff_file)],
        max_context_chars=140,
    )

    assert f"### Diff: {diff_file}" in message
    assert f"### File: {source_file}" in message
    assert "[OMITTED: context budget exhausted]" in message


def test_build_user_message_redacts_secret_crossing_trim_boundary(tmp_path):
    """Verify oversized context redacts secret-looking lines before trimming."""

    module = load_module()
    notes_file = tmp_path / "notes.txt"
    token = "abcdefghijklmnop1234567890"
    notes_file.write_text(
        "Authorization: Bearer " + token + "\n" + ("safe context\n" * 100),
        encoding="utf-8",
    )

    message = module.build_user_message("review", "check split secret", context_files=[str(notes_file)], max_context_chars=400)

    assert "Bearer <redacted>" in message
    assert token not in message


def test_build_user_message_omits_unfinished_oversized_line(tmp_path):
    """Verify long one-line context cannot leak partial bearer fragments."""

    module = load_module()
    notes_file = tmp_path / "one_line.txt"
    token = "abcdefghijklmnop1234567890"
    notes_file.write_text("safe " * 200 + "Authorization: Bearer " + token + ("z" * 5000), encoding="utf-8")

    message = module.build_user_message("review", "check long line", context_files=[str(notes_file)], max_context_chars=800)

    assert "[TRUNCATED:" in message
    assert "Bearer" not in message
    assert token not in message


def test_post_json_adds_bearer_token_without_leaking_it(monkeypatch):
    """Verify the client sends a bearer token and reports only token presence."""

    module = load_module()
    calls = []

    def fake_urlopen(request, timeout, context=None):
        calls.append(
            {
                "authorization": request.get_header("Authorization"),
                "body": json.loads(request.data.decode("utf-8")),
                "context": context,
                "timeout": timeout,
                "url": request.full_url,
            }
        )
        return FakeUrlopenResponse(
            {
                "model": "unit-model",
                "choices": [{"message": {"content": "Findings:\n- None."}}],
                "usage": {"total_tokens": 7},
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    response = module.post_json(
        "https://daedalus.example.test/v1",
        module.chat_payload("unit-model", "review", "review task", 80),
        "secret-token",
        9,
        False,
    )

    assert calls[0]["url"] == "https://daedalus.example.test/v1/chat/completions"
    assert calls[0]["authorization"] == "Bearer secret-token"
    assert "MODE: review" in calls[0]["body"]["messages"][1]["content"]
    assert "review task" in calls[0]["body"]["messages"][1]["content"]
    assert module.extract_content(response) == "Findings:\n- None."


def test_load_env_file_preserves_existing_environment(tmp_path, monkeypatch):
    """Verify local env loading fills missing values without overriding exports."""

    module = load_module()
    env_file = tmp_path / "daedalus.env"
    env_file.write_text("DAEDALUS_BASE_URL=https://unit.example/v1\nDAEDALUS_API_KEY=file-token\n", encoding="utf-8")
    monkeypatch.setenv("DAEDALUS_API_KEY", "exported-token")
    monkeypatch.delenv("DAEDALUS_BASE_URL", raising=False)

    module.load_env_file(str(env_file))

    assert module.os.environ["DAEDALUS_BASE_URL"] == "https://unit.example/v1"
    assert module.os.environ["DAEDALUS_API_KEY"] == "exported-token"


def test_preview_payload_prints_request_without_calling_endpoint(monkeypatch, capsys, tmp_path):
    """Verify preview mode exposes prompt shape without spending inference."""

    module = load_module()
    env_file = tmp_path / "missing.env"

    def fail_post_json(*_args, **_kwargs):
        raise AssertionError("preview mode must not call the endpoint")

    monkeypatch.setattr(module, "post_json", fail_post_json)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "daedalus-client.py",
            "--env-file",
            str(env_file),
            "--mode",
            "review",
            "--task",
            "preview this",
            "--preview-payload",
        ],
    )

    status = module.main()
    output = json.loads(capsys.readouterr().out)

    assert status == 0
    assert output["payload"]["messages"][0]["role"] == "system"
    assert "preview this" in output["payload"]["messages"][1]["content"]
    assert output["api_key_present"] is False
