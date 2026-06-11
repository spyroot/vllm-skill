"""Unit tests for the Daedalus latency experiment harness."""

from __future__ import annotations

import argparse
import errno
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


APP_PATH = Path(__file__).resolve().parents[1] / "scripts" / "daedalus-latency-probe.py"
APP_MODULE_NAME = "daedalus_latency_probe_under_test"


def load_module():
    """Load the latency probe as an isolated module for each test."""

    sys.modules.pop(APP_MODULE_NAME, None)
    spec = importlib.util.spec_from_file_location(APP_MODULE_NAME, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeClient:
    """Provide the small skill-client surface needed by the probe."""

    def chat_payload(
        self,
        model,
        mode,
        task,
        max_tokens,
        prompt_dir=None,
        context_files=None,
        diff_files=None,
        max_context_chars=20000,
    ):
        """Return a deterministic payload for serialization measurement."""

        content_parts = [task]
        for path in diff_files or []:
            content_parts.append(Path(path).read_text(encoding="utf-8"))
        for path in context_files or []:
            content_parts.append(Path(path).read_text(encoding="utf-8"))
        content = "\n".join(content_parts)[:max_context_chars]
        return {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": f"mode={mode}"},
                {"role": "user", "content": content},
            ],
        }


class FakeCompletedProcess:
    """Provide subprocess output for probe helpers that shell out."""

    def __init__(self, stdout: str = "{}\n"):
        self.stdout = stdout


class FakeUrlopenResponse:
    """Provide a context-managed fake HTTP JSON response."""

    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_load_env_file_preserves_exports_and_ignores_comments(tmp_path, monkeypatch):
    """Verify env loading supports local defaults without overriding secrets."""

    module = load_module()
    env_file = tmp_path / "daedalus.env"
    env_file.write_text(
        "# comment\n\nDAEDALUS_BASE_URL=https://unit.example/v1\nNO_EQUALS\nDAEDALUS_API_KEY=file-token\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DAEDALUS_BASE_URL", raising=False)
    monkeypatch.setenv("DAEDALUS_API_KEY", "exported-token")

    module.load_env_file(str(env_file))
    module.load_env_file(str(tmp_path / "missing.env"))

    assert module.os.environ["DAEDALUS_BASE_URL"] == "https://unit.example/v1"
    assert module.os.environ["DAEDALUS_API_KEY"] == "exported-token"


def test_summarize_reports_stable_latency_fields():
    """Verify latency summaries expose count, median, p95, and bounds."""

    module = load_module()

    assert module.percentile([], 95) == 0.0
    assert module.summarize([])["count"] == 0
    summary = module.summarize([3.0, 1.0, 2.0, 10.0])

    assert summary["count"] == 4
    assert summary["min_ms"] == 1.0
    assert summary["median_ms"] == 2.5
    assert summary["p95_ms"] == 10.0
    assert summary["max_ms"] == 10.0


def test_time_call_returns_elapsed_and_callable_result():
    """Verify timing wrapper preserves callable results for measurements."""

    module = load_module()

    elapsed_ms, result = module.time_call(lambda: "ok")

    assert elapsed_ms >= 0
    assert result == "ok"


def test_import_skill_client_loads_module_and_reports_loader_errors(tmp_path, monkeypatch):
    """Verify dynamic client loading succeeds and reports unusable specs."""

    module = load_module()
    client_file = tmp_path / "client.py"
    client_file.write_text("VALUE = 42\n", encoding="utf-8")

    loaded = module.import_skill_client(client_file)

    assert loaded.VALUE == 42

    monkeypatch.setattr(
        module.importlib.util,
        "spec_from_file_location",
        lambda *_args: importlib.machinery.ModuleSpec("broken", None),
    )
    try:
        module.import_skill_client(client_file)
    except RuntimeError as exc:
        assert "cannot load client module" in str(exc)
    else:
        raise AssertionError("expected a missing loader to fail")


def test_python_startup_measurement_uses_selected_interpreter(monkeypatch):
    """Verify startup bucket shells out to the requested Python binary."""

    module = load_module()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return FakeCompletedProcess()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.measure_python_startup("/unit/python", 2)

    assert result["name"] == "python_startup"
    assert result["summary"]["count"] == 2
    assert calls[0][0] == ["/unit/python", "-c", "pass"]
    assert calls[0][1]["stdout"] == module.subprocess.DEVNULL


def test_inprocess_payload_measurement_records_request_bytes(tmp_path):
    """Verify JSON serialization measurement captures request size."""

    module = load_module()
    context_file = tmp_path / "context.txt"
    context_file.write_text("selected context", encoding="utf-8")

    result = module.measure_inprocess_payload(
        FakeClient(),
        "unit-model",
        "review",
        "task text",
        16,
        2,
        context_files=[str(context_file)],
    )

    assert result["name"] == "inprocess_payload_json"
    assert result["summary"]["count"] == 2
    assert result["request_bytes"] > 0
    assert result["prompt_chars"] > len("task text")
    assert "cold_ms" in result


def test_json_roundtrip_reports_compact_and_pretty_sizes():
    """Verify compact and pretty JSON costs are separate measurements."""

    module = load_module()
    payload = FakeClient().chat_payload("unit-model", "review", "task", 16)

    compact = module.measure_json_roundtrip(payload, 1, 0, pretty=False)
    pretty = module.measure_json_roundtrip(payload, 1, 0, pretty=True)

    assert compact["name"] == "json_compact_roundtrip"
    assert pretty["name"] == "json_pretty_roundtrip"
    assert pretty["request_bytes"] > compact["request_bytes"]


def test_client_preview_subprocess_validates_json_preview(monkeypatch, tmp_path):
    """Verify preview bucket measures CLI subprocess output without endpoint calls."""

    module = load_module()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return FakeCompletedProcess(stdout='{"payload": {"ok": true}}\n')

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.measure_client_preview_subprocess(
        "/unit/python",
        tmp_path / "daedalus-client.py",
        "/tmp/no-env",
        "unit-model",
        "review",
        "task",
        16,
        1,
        0,
        context_files=[str(tmp_path / "context.txt")],
        diff_files=[str(tmp_path / "diff.patch")],
    )

    assert result["name"] == "client_preview_subprocess"
    assert result["response_bytes"] > 0
    assert "--preview-payload" in calls[0][0]
    assert "--context-file" in calls[0][0]
    assert "--diff-file" in calls[0][0]
    assert calls[0][1]["capture_output"] is True


def test_build_report_keeps_api_key_secret_out_of_output(monkeypatch):
    """Verify reports expose token presence only, never the token value."""

    module = load_module()
    args = argparse.Namespace(
        api_key_env="DAEDALUS_API_KEY",
        base_url="https://unit.example/v1",
        client=Path("/tmp/client.py"),
        env_file="/tmp/no-env",
        insecure_skip_tls_verify=False,
        live=False,
        live_cli=False,
        loopback=True,
        context_file=[],
        diff_file=[],
        max_tokens=16,
        max_context_chars=20000,
        mode="review",
        model="unit-model",
        python_bin=sys.executable,
        samples=1,
        size_sweep=False,
        task="measure",
        timeout=1.0,
        warmup=0,
    )
    monkeypatch.setattr(module, "measure_python_startup", lambda *_args: module.measurement("python_startup", [1.0]))
    monkeypatch.setattr(
        module,
        "measure_inprocess_payload",
        lambda *_args: module.measurement("inprocess_payload_json", [0.1]),
    )
    monkeypatch.setattr(
        module,
        "measure_client_preview_subprocess",
        lambda *_args: module.measurement("client_preview_subprocess", [2.0]),
    )
    monkeypatch.setattr(
        module,
        "measure_loopback_http_stub",
        lambda *_args: module.measurement("loopback_http_stub", [3.0]),
    )
    monkeypatch.setattr(module, "git_sha", lambda: "abc1234")

    report = module.build_report(args, "super-secret-token", FakeClient())
    encoded = json.dumps(report)

    assert report["api_key_present"] is True
    assert report["git_sha"] == "abc1234"
    assert report["metadata"]["request_bytes"] > 0
    assert "super-secret-token" not in encoded
    assert [item["name"] for item in report["measurements"]] == [
        "python_startup",
        "inprocess_payload_json",
        "json_compact_roundtrip",
        "json_pretty_roundtrip",
        "client_preview_subprocess",
        "loopback_http_stub",
    ]


def test_build_report_adds_live_and_live_cli_measurements(monkeypatch):
    """Verify live buckets are included only after explicit opt-in."""

    module = load_module()
    args = argparse.Namespace(
        api_key_env="DAEDALUS_API_KEY",
        base_url="https://unit.example/v1",
        client=Path("/tmp/client.py"),
        env_file="/tmp/no-env",
        insecure_skip_tls_verify=True,
        live=True,
        live_cli=True,
        loopback=False,
        context_file=[],
        diff_file=[],
        max_tokens=16,
        max_context_chars=20000,
        mode="review",
        model="unit-model",
        python_bin=sys.executable,
        samples=1,
        size_sweep=False,
        task="measure",
        timeout=1.0,
        warmup=0,
    )
    monkeypatch.setattr(module, "measure_python_startup", lambda *_args: module.measurement("python_startup", [1.0]))
    monkeypatch.setattr(module, "measure_inprocess_payload", lambda *_args: module.measurement("inprocess_payload_json", [2.0]))
    monkeypatch.setattr(
        module,
        "measure_client_preview_subprocess",
        lambda *_args: module.measurement("client_preview_subprocess", [3.0]),
    )
    monkeypatch.setattr(module, "measure_http_models", lambda *_args: module.measurement("http_models_get", [4.0]))
    monkeypatch.setattr(module, "measure_http_chat", lambda *_args: module.measurement("http_chat_completion", [5.0]))
    monkeypatch.setattr(
        module,
        "measure_client_live_subprocess",
        lambda *_args: module.measurement("client_live_subprocess", [6.0]),
    )

    report = module.build_report(args, "token", FakeClient())

    assert [item["name"] for item in report["measurements"]] == [
        "python_startup",
        "inprocess_payload_json",
        "json_compact_roundtrip",
        "json_pretty_roundtrip",
        "client_preview_subprocess",
        "http_models_get",
        "http_chat_completion",
        "client_live_subprocess",
    ]
    assert report["live"] is True
    assert report["live_cli"] is True


def test_size_sweep_generates_task_diff_and_context_cases(monkeypatch):
    """Verify generated size sweeps exercise task, diff, and context inputs."""

    module = load_module()
    monkeypatch.setattr(module, "SIZE_SWEEP_SIZES", (100, 1024))
    args = argparse.Namespace(
        max_context_chars=20000,
        max_tokens=16,
        mode="review",
        model="unit-model",
        samples=1,
        warmup=0,
    )

    results = module.measure_size_sweep(FakeClient(), args)
    kinds = {case["input_kind"] for case in results}

    assert {"task", "diff", "context", "split", "many-small"}.issubset(kinds)
    assert all(case["request_bytes"] > 0 for case in results)
    assert all(case["measurements"][0]["name"] == "inprocess_payload_json" for case in results)


def test_size_sweep_reports_real_client_truncation_markers(monkeypatch):
    """Verify real client sweep flags expose over-budget context behavior."""

    module = load_module()
    client = module.import_skill_client(module.DEFAULT_CLIENT_PATH)
    monkeypatch.setattr(module, "SIZE_SWEEP_SIZES", (100, 1024))
    args = argparse.Namespace(
        max_context_chars=600,
        max_tokens=16,
        mode="review",
        model="unit-model",
        samples=1,
        warmup=0,
    )

    results = module.measure_size_sweep(client, args)
    selected_context_cases = [case for case in results if case["input_kind"] in {"diff", "context"}]
    many_small = next(case for case in results if case["input_kind"] == "many-small")

    assert any(case["truncated"] is True for case in selected_context_cases)
    assert many_small["omitted"] is True


def test_http_measurements_use_redacted_request_helpers(monkeypatch):
    """Verify live measurement helpers record bytes and usage without secrets."""

    module = load_module()
    calls = []

    def fake_http_json(method, url, data, api_key, timeout, insecure):
        calls.append((method, url, data, api_key, timeout, insecure))
        if method == "GET":
            return {"data": [{"id": "model-a"}], "_response_bytes": 31}
        return {"usage": {"total_tokens": 2}, "_response_bytes": 41}

    monkeypatch.setattr(module, "http_json", fake_http_json)

    models = module.measure_http_models("https://unit.example/v1", "secret-token", 2.0, True, 1)
    chat = module.measure_http_chat(FakeClient(), "https://unit.example/v1", "unit-model", "review", "task", 16, "secret-token", 2.0, True, 1)

    assert models["name"] == "http_models_get"
    assert models["model_count"] == 1
    assert chat["name"] == "http_chat_completion"
    assert chat["usage"]["total_tokens"] == 2
    post_calls = [call for call in calls if call[0] == "POST"]
    assert calls[0][0] == "GET"
    assert post_calls
    assert post_calls[0][2]


def test_client_live_subprocess_measures_json_result(monkeypatch, tmp_path):
    """Verify full CLI live bucket records response size and usage."""

    module = load_module()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return FakeCompletedProcess(stdout='{"usage": {"total_tokens": 3}}\n')

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.measure_client_live_subprocess(
        "/unit/python",
        tmp_path / "daedalus-client.py",
        "/tmp/no-env",
        "https://unit.example/v1",
        "unit-model",
        "review",
        "task",
        16,
        "DAEDALUS_API_KEY",
        True,
        1,
    )

    assert result["name"] == "client_live_subprocess"
    assert result["usage"]["total_tokens"] == 3
    assert "--insecure-skip-tls-verify" in calls[0][0]
    assert calls[0][1]["capture_output"] is True


def test_http_json_builds_headers_and_decodes_response(monkeypatch):
    """Verify HTTP helper sends JSON headers without exposing token in reports."""

    module = load_module()
    calls = []

    def fake_urlopen(request, timeout, context=None):
        calls.append(
            {
                "authorization": request.get_header("Authorization"),
                "content_type": request.get_header("Content-type"),
                "data": request.data,
                "timeout": timeout,
                "context": context,
            }
        )
        return FakeUrlopenResponse({"ok": True})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.http_json("POST", "https://unit.example/v1/chat/completions", b"{}", "secret-token", 3.0, True)

    assert result["ok"] is True
    assert result["_response_bytes"] > 0
    assert calls[0]["authorization"] == "Bearer secret-token"
    assert calls[0]["content_type"] == "application/json"
    assert calls[0]["data"] == b"{}"
    assert calls[0]["context"] is not None


def test_main_rejects_live_mode_without_api_key(monkeypatch, capsys, tmp_path):
    """Verify live measurements require an auth token before endpoint calls."""

    module = load_module()
    monkeypatch.delenv("DAEDALUS_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "daedalus-latency-probe.py",
            "--live",
            "--env-file",
            str(tmp_path / "missing.env"),
        ],
    )

    status = module.main()
    captured = capsys.readouterr()

    assert status == 2
    assert "requires DAEDALUS_API_KEY" in captured.err


def test_main_rejects_live_cli_without_live(monkeypatch, capsys, tmp_path):
    """Verify full CLI live timing cannot run without live opt-in."""

    module = load_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "daedalus-latency-probe.py",
            "--live-cli",
            "--env-file",
            str(tmp_path / "missing.env"),
        ],
    )

    status = module.main()
    captured = capsys.readouterr()

    assert status == 2
    assert "--live-cli requires --live" in captured.err


def test_main_rejects_invalid_sample_count(monkeypatch, capsys):
    """Verify sample count guard fails before running measurements."""

    module = load_module()
    monkeypatch.setattr(sys, "argv", ["daedalus-latency-probe.py", "--samples", "0"])

    status = module.main()
    captured = capsys.readouterr()

    assert status == 2
    assert "--samples must be >= 1" in captured.err


def test_main_rejects_invalid_warmup_count(monkeypatch, capsys):
    """Verify warmup count guard prevents ambiguous latency sampling."""

    module = load_module()
    monkeypatch.setattr(sys, "argv", ["daedalus-latency-probe.py", "--warmup", "-1"])

    status = module.main()
    captured = capsys.readouterr()

    assert status == 2
    assert "--warmup must be >= 0" in captured.err


def test_main_rejects_invalid_context_budget(monkeypatch, capsys):
    """Verify context budget guard rejects impossible prompt caps."""

    module = load_module()
    monkeypatch.setattr(sys, "argv", ["daedalus-latency-probe.py", "--max-context-chars", "0"])

    status = module.main()
    captured = capsys.readouterr()

    assert status == 2
    assert "--max-context-chars must be >= 1" in captured.err


def test_main_success_prints_secret_safe_report(monkeypatch, capsys, tmp_path):
    """Verify CLI success prints report JSON without endpoint access."""

    module = load_module()
    monkeypatch.setenv("DAEDALUS_API_KEY", "secret-token")
    monkeypatch.setattr(module, "import_skill_client", lambda _path: FakeClient())
    monkeypatch.setattr(
        module,
        "build_report",
        lambda _args, api_key, _client: {"api_key_present": bool(api_key), "passed": True},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "daedalus-latency-probe.py",
            "--env-file",
            str(tmp_path / "missing.env"),
            "--samples",
            "1",
        ],
    )

    status = module.main()
    output = json.loads(capsys.readouterr().out)

    assert status == 0
    assert output == {"api_key_present": True, "passed": True}


def test_main_reports_measurement_errors_without_secret(monkeypatch, capsys, tmp_path):
    """Verify CLI error path reports failure without printing API key values."""

    module = load_module()
    monkeypatch.setenv("DAEDALUS_API_KEY", "secret-token")
    monkeypatch.setattr(module, "import_skill_client", lambda _path: (_ for _ in ()).throw(RuntimeError("bad client")))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "daedalus-latency-probe.py",
            "--env-file",
            str(tmp_path / "missing.env"),
        ],
    )

    status = module.main()
    output = json.loads(capsys.readouterr().out)

    assert status == 1
    assert output["api_key_present"] is True
    assert output["passed"] is False
    assert "secret-token" not in json.dumps(output)


def test_git_sha_returns_unknown_when_git_fails(monkeypatch):
    """Verify git metadata fallback keeps the probe portable outside git."""

    module = load_module()

    def fake_run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.git_sha() == "unknown"


def test_loopback_http_stub_measures_local_transport_only():
    """Verify the loopback bucket records local HTTP request/response bytes."""

    module = load_module()

    try:
        result = module.measure_loopback_http_stub(FakeClient(), "unit-model", "review", "task", 16, 1.0, 1)
    except OSError as exc:
        if exc.errno == errno.EPERM:
            pytest.skip("sandbox does not allow local loopback sockets")
        raise

    assert result["name"] == "loopback_http_stub"
    assert result["summary"]["count"] == 1
    assert result["request_bytes"] > 0
    assert result["response_bytes"] > 0
