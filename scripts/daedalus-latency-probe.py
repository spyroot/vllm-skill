#!/usr/bin/env python3
"""Measure Daedalus client overhead separately from optional live vLLM latency."""

from __future__ import annotations

import argparse
import http.server
import importlib.util
import json
import math
import os
import platform
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLIENT_PATH = REPO_ROOT / "skills" / "daedalus" / "scripts" / "daedalus-client.py"
DEFAULT_BASE_URL = "https://agents.cnfdemo.io/v1"
DEFAULT_MODEL = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
DEFAULT_TASK = "Return one short sentence confirming the prompt contract."
VALID_MODES = ("code", "review", "pr", "plan", "blocker", "nonblocker", "quality-dynamic")
DEFAULT_MAX_CONTEXT_CHARS = 20000
SIZE_SWEEP_SIZES = (100, 1024, 10240, 20000, 50000, 200000)
STUB_RESPONSE = {
    "id": "chatcmpl-daedalus-latency-stub",
    "object": "chat.completion",
    "model": "stub-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def load_env_file(path: str) -> None:
    """Load KEY=VALUE lines without overriding already-exported values."""
    env_path = Path(path).expanduser()
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def percentile(values: list[float], pct: float) -> float:
    """Return a nearest-rank percentile for a small latency sample."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil((pct / 100) * len(ordered)) - 1))
    return ordered[index]


def summarize(samples_ms: list[float]) -> dict[str, float]:
    """Summarize latency samples with stable fields for JSON comparisons."""
    if not samples_ms:
        return {"count": 0, "min_ms": 0.0, "median_ms": 0.0, "mean_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    return {
        "count": len(samples_ms),
        "min_ms": min(samples_ms),
        "median_ms": statistics.median(samples_ms),
        "mean_ms": statistics.mean(samples_ms),
        "p95_ms": percentile(samples_ms, 95),
        "max_ms": max(samples_ms),
    }


def time_call(func: Callable[[], Any]) -> tuple[float, Any]:
    """Run a callable once and return elapsed milliseconds plus the result."""
    start_ns = time.perf_counter_ns()
    result = func()
    elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000
    return elapsed_ms, result


def collect_timing_samples(samples: int, warmup: int, func: Callable[[], Any]) -> tuple[list[float], Any, float]:
    """Collect timed samples after recording a cold sample and optional warmups."""
    cold_ms, result = time_call(func)
    for _ in range(max(0, warmup)):
        _elapsed_ms, result = time_call(func)

    timings = []
    for _ in range(samples):
        elapsed_ms, result = time_call(func)
        timings.append(elapsed_ms)
    return timings, result, cold_ms


def import_skill_client(client_path: Path):
    """Import the Daedalus skill client from its script path."""
    spec = importlib.util.spec_from_file_location("daedalus_client_latency_probe", client_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    if spec.loader is None:
        raise RuntimeError(f"cannot load client module: {client_path}")
    spec.loader.exec_module(module)
    return module


def measure_python_startup(python_bin: str, samples: int, warmup: int = 0) -> dict[str, Any]:
    """Measure bare Python interpreter process startup overhead."""
    timings, _result, cold_ms = collect_timing_samples(
        samples,
        warmup,
        lambda: subprocess.run(
            [python_bin, "-c", "pass"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    )
    return measurement("python_startup", timings, cold_ms)


def build_client_payload(
    client_module: Any,
    model: str,
    mode: str,
    task: str,
    max_tokens: int,
    context_files: list[str] | None = None,
    diff_files: list[str] | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> dict[str, Any]:
    """Build a Daedalus payload while forwarding selected context inputs."""
    return client_module.chat_payload(
        model,
        mode,
        task,
        max_tokens,
        None,
        context_files or [],
        diff_files or [],
        max_context_chars,
    )


def payload_prompt_chars(payload: dict[str, Any]) -> int:
    """Count prompt characters across message contents."""
    messages = payload.get("messages", [])
    return sum(len(message.get("content", "")) for message in messages if isinstance(message, dict))


def payload_user_content(payload: dict[str, Any]) -> str:
    """Return user message content for marker checks."""
    for message in payload.get("messages", []):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content", "")
            return content if isinstance(content, str) else ""
    return ""


def annotate_payload_measurement(result: dict[str, Any], payload: dict[str, Any], request_bytes: int) -> dict[str, Any]:
    """Attach size and truncation metadata to a measurement."""
    user_content = payload_user_content(payload)
    result["prompt_chars"] = payload_prompt_chars(payload)
    result["request_bytes"] = request_bytes
    result["truncated"] = "[TRUNCATED:" in user_content
    result["omitted"] = "[OMITTED:" in user_content
    return result


def measure_inprocess_payload(
    client_module: Any,
    model: str,
    mode: str,
    task: str,
    max_tokens: int,
    samples: int,
    warmup: int = 0,
    context_files: list[str] | None = None,
    diff_files: list[str] | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> dict[str, Any]:
    """Measure payload build plus compact JSON encode/decode without a subprocess."""

    def build_roundtrip() -> tuple[dict[str, Any], int]:
        payload = build_client_payload(
            client_module,
            model,
            mode,
            task,
            max_tokens,
            context_files,
            diff_files,
            max_context_chars,
        )
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        json.loads(encoded.decode("utf-8"))
        return payload, len(encoded)

    timings, payload_result, cold_ms = collect_timing_samples(samples, warmup, build_roundtrip)
    payload, request_bytes = payload_result
    return annotate_payload_measurement(measurement("inprocess_payload_json", timings, cold_ms), payload, request_bytes)


def measure_json_roundtrip(
    payload: dict[str, Any],
    samples: int,
    warmup: int,
    pretty: bool,
) -> dict[str, Any]:
    """Measure JSON encode/decode on a prebuilt payload."""
    name = "json_pretty_roundtrip" if pretty else "json_compact_roundtrip"

    def roundtrip() -> int:
        if pretty:
            encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        else:
            encoded = json.dumps(payload).encode("utf-8")
        json.loads(encoded.decode("utf-8"))
        return len(encoded)

    timings, request_bytes, cold_ms = collect_timing_samples(samples, warmup, roundtrip)
    return annotate_payload_measurement(measurement(name, timings, cold_ms), payload, request_bytes)


def measure_client_preview_subprocess(
    python_bin: str,
    client_path: Path,
    env_file: str,
    model: str,
    mode: str,
    task: str,
    max_tokens: int,
    samples: int,
    warmup: int = 0,
    context_files: list[str] | None = None,
    diff_files: list[str] | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> dict[str, Any]:
    """Measure the full Python CLI preview path without calling the endpoint."""
    command = [
        python_bin,
        str(client_path),
        "--env-file",
        env_file,
        "--model",
        model,
        "--mode",
        mode,
        "--task",
        task,
        "--max-tokens",
        str(max_tokens),
        "--max-context-chars",
        str(max_context_chars),
        "--preview-payload",
    ]
    for diff_file in diff_files or []:
        command.extend(["--diff-file", str(diff_file)])
    for context_file in context_files or []:
        command.extend(["--context-file", str(context_file)])

    def run_preview() -> int:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        json.loads(completed.stdout)
        return len(completed.stdout.encode("utf-8"))

    timings, response_bytes, cold_ms = collect_timing_samples(samples, warmup, run_preview)
    result = measurement("client_preview_subprocess", timings, cold_ms)
    result["response_bytes"] = response_bytes
    return result


class StubJsonHandler(http.server.BaseHTTPRequestHandler):
    """Serve fixed JSON responses for loopback transport measurements."""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        """Read and discard request JSON, then return a fixed chat response."""
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)
        body = json.dumps(STUB_RESPONSE).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        """Suppress per-request server logs so latency output stays clean."""


def measure_loopback_http_stub(
    client_module: Any,
    model: str,
    mode: str,
    task: str,
    max_tokens: int,
    timeout: float,
    samples: int,
    warmup: int = 0,
    context_files: list[str] | None = None,
    diff_files: list[str] | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> dict[str, Any]:
    """Measure urllib JSON POST cost against a local fixed-response server."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StubJsonHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/chat/completions"
        payload = build_client_payload(
            client_module,
            model,
            mode,
            task,
            max_tokens,
            context_files,
            diff_files,
            max_context_chars,
        )
        encoded = json.dumps(payload).encode("utf-8")

        def post_stub() -> int:
            body = http_json("POST", endpoint, encoded, "", timeout, False)
            return body["_response_bytes"]

        timings, response_bytes, cold_ms = collect_timing_samples(samples, warmup, post_stub)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    result = measurement("loopback_http_stub", timings, cold_ms)
    result["request_bytes"] = len(encoded)
    result["response_bytes"] = response_bytes
    return result


def measure_http_models(
    base_url: str,
    api_key: str,
    timeout: float,
    insecure_skip_tls_verify: bool,
    samples: int,
    warmup: int = 0,
) -> dict[str, Any]:
    """Measure authenticated /models transport and JSON decode latency."""

    def get_models() -> dict[str, Any]:
        return http_json("GET", f"{base_url.rstrip('/')}/models", None, api_key, timeout, insecure_skip_tls_verify)

    timings, body, cold_ms = collect_timing_samples(samples, warmup, get_models)
    result = measurement("http_models_get", timings, cold_ms)
    result["response_bytes"] = body["_response_bytes"]
    model_count = len(body.get("data", [])) if isinstance(body.get("data"), list) else 0
    result["model_count"] = model_count
    return result


def measure_client_live_subprocess(
    python_bin: str,
    client_path: Path,
    env_file: str,
    base_url: str,
    model: str,
    mode: str,
    task: str,
    max_tokens: int,
    api_key_env: str,
    insecure_skip_tls_verify: bool,
    samples: int,
    warmup: int = 0,
    context_files: list[str] | None = None,
    diff_files: list[str] | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> dict[str, Any]:
    """Measure full CLI-to-live-endpoint latency when explicitly requested."""
    command = [
        python_bin,
        str(client_path),
        "--env-file",
        env_file,
        "--base-url",
        base_url,
        "--model",
        model,
        "--mode",
        mode,
        "--task",
        task,
        "--max-tokens",
        str(max_tokens),
        "--max-context-chars",
        str(max_context_chars),
        "--api-key-env",
        api_key_env,
        "--json",
    ]
    for diff_file in diff_files or []:
        command.extend(["--diff-file", str(diff_file)])
    for context_file in context_files or []:
        command.extend(["--context-file", str(context_file)])
    if insecure_skip_tls_verify:
        command.append("--insecure-skip-tls-verify")

    def run_client() -> tuple[int, dict[str, Any]]:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        body = json.loads(completed.stdout)
        usage = body.get("usage", {}) if isinstance(body.get("usage"), dict) else {}
        return len(completed.stdout.encode("utf-8")), usage

    timings, measured, cold_ms = collect_timing_samples(samples, warmup, run_client)
    response_bytes, usage = measured
    result = measurement("client_live_subprocess", timings, cold_ms)
    result["response_bytes"] = response_bytes
    result["usage"] = usage
    return result


def measure_http_chat(
    client_module: Any,
    base_url: str,
    model: str,
    mode: str,
    task: str,
    max_tokens: int,
    api_key: str,
    timeout: float,
    insecure_skip_tls_verify: bool,
    samples: int,
    warmup: int = 0,
    context_files: list[str] | None = None,
    diff_files: list[str] | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> dict[str, Any]:
    """Measure live chat-completion latency including vLLM queue and generation."""
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    payload = build_client_payload(
        client_module,
        model,
        mode,
        task,
        max_tokens,
        context_files,
        diff_files,
        max_context_chars,
    )
    encoded = json.dumps(payload).encode("utf-8")

    def post_chat() -> tuple[int, dict[str, Any]]:
        body = http_json("POST", endpoint, encoded, api_key, timeout, insecure_skip_tls_verify)
        usage = body.get("usage", {}) if isinstance(body.get("usage"), dict) else {}
        return body["_response_bytes"], usage

    timings, measured, cold_ms = collect_timing_samples(samples, warmup, post_chat)
    response_bytes, usage = measured
    result = measurement("http_chat_completion", timings, cold_ms)
    result["request_bytes"] = len(encoded)
    result["response_bytes"] = response_bytes
    result["usage"] = usage
    return result


def http_json(
    method: str,
    url: str,
    data: bytes | None,
    api_key: str,
    timeout: float,
    insecure_skip_tls_verify: bool,
) -> dict[str, Any]:
    """Call an HTTP JSON endpoint without exposing bearer-token values."""
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    context = ssl._create_unverified_context() if insecure_skip_tls_verify else None
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        raw = response.read()
    decoded = json.loads(raw.decode("utf-8"))
    if isinstance(decoded, dict):
        decoded["_response_bytes"] = len(raw)
    return decoded


def measurement(name: str, samples_ms: list[float], cold_ms: float | None = None) -> dict[str, Any]:
    """Return a named measurement with raw samples and summary statistics."""
    result = {"name": name, "unit": "ms", "samples_ms": samples_ms, "summary": summarize(samples_ms)}
    if cold_ms is not None:
        result["cold_ms"] = cold_ms
    if len(samples_ms) < 20:
        result["p95_note"] = "nearest-rank p95 is effectively max for fewer than 20 samples"
    return result


def sized_text(size: int, label: str) -> str:
    """Build deterministic ASCII text of approximately size bytes."""
    line = f"{label} latency probe line with stable content and no real secrets.\n"
    repeat = (size // len(line)) + 1
    return (line * repeat)[:size]


def write_sized_file(directory: Path, name: str, size: int, label: str) -> str:
    """Write deterministic generated context and return its path."""
    path = directory / name
    path.write_text(sized_text(size, label), encoding="utf-8")
    return str(path)


def build_size_sweep_cases(directory: Path) -> list[dict[str, Any]]:
    """Create task, diff, and context cases for local input-size measurements."""
    cases: list[dict[str, Any]] = []
    for size in SIZE_SWEEP_SIZES:
        cases.append(
            {
                "case": f"task_{size}",
                "input_kind": "task",
                "target_bytes": size,
                "task": sized_text(size, "task"),
                "diff_files": [],
                "context_files": [],
            }
        )

    for size in SIZE_SWEEP_SIZES[1:]:
        cases.append(
            {
                "case": f"diff_{size}",
                "input_kind": "diff",
                "target_bytes": size,
                "task": DEFAULT_TASK,
                "diff_files": [write_sized_file(directory, f"diff-{size}.patch", size, "diff")],
                "context_files": [],
            }
        )
        cases.append(
            {
                "case": f"context_{size}",
                "input_kind": "context",
                "target_bytes": size,
                "task": DEFAULT_TASK,
                "diff_files": [],
                "context_files": [write_sized_file(directory, f"context-{size}.txt", size, "context")],
            }
        )

    cases.append(
        {
            "case": "split_diff_context_15000",
            "input_kind": "split",
            "target_bytes": 30000,
            "task": DEFAULT_TASK,
            "diff_files": [write_sized_file(directory, "split-diff-15000.patch", 15000, "split diff")],
            "context_files": [write_sized_file(directory, "split-context-15000.txt", 15000, "split context")],
        }
    )
    cases.append(
        {
            "case": "many_small_diff_context",
            "input_kind": "many-small",
            "target_bytes": 40000,
            "task": DEFAULT_TASK,
            "diff_files": [
                write_sized_file(directory, f"many-diff-{index}.patch", 1000, f"many diff {index}")
                for index in range(20)
            ],
            "context_files": [
                write_sized_file(directory, f"many-context-{index}.txt", 1000, f"many context {index}")
                for index in range(20)
            ],
        }
    )
    return cases


def measure_size_sweep(client_module: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    """Run local-only generated input-size measurements."""
    sweep_results = []
    with tempfile.TemporaryDirectory(prefix="daedalus-size-sweep-") as temp_dir:
        for case in build_size_sweep_cases(Path(temp_dir)):
            payload = build_client_payload(
                client_module,
                args.model,
                args.mode,
                case["task"],
                args.max_tokens,
                case["context_files"],
                case["diff_files"],
                args.max_context_chars,
            )
            compact = measure_json_roundtrip(payload, args.samples, args.warmup, pretty=False)
            pretty = measure_json_roundtrip(payload, args.samples, args.warmup, pretty=True)
            build = measure_inprocess_payload(
                client_module,
                args.model,
                args.mode,
                case["task"],
                args.max_tokens,
                args.samples,
                args.warmup,
                case["context_files"],
                case["diff_files"],
                args.max_context_chars,
            )
            sweep_results.append(
                {
                    "case": case["case"],
                    "input_kind": case["input_kind"],
                    "target_bytes": case["target_bytes"],
                    "context_file_count": len(case["context_files"]),
                    "diff_file_count": len(case["diff_files"]),
                    "prompt_chars": payload_prompt_chars(payload),
                    "request_bytes": compact["request_bytes"],
                    "truncated": compact["truncated"],
                    "omitted": compact["omitted"],
                    "measurements": [build, compact, pretty],
                }
            )
    return sweep_results


def build_report(args: argparse.Namespace, api_key: str, client_module: Any) -> dict[str, Any]:
    """Run selected measurements and return a secret-safe JSON report."""
    payload = build_client_payload(
        client_module,
        args.model,
        args.mode,
        args.task,
        args.max_tokens,
        args.context_file,
        args.diff_file,
        args.max_context_chars,
    )
    request_bytes = len(json.dumps(payload).encode("utf-8"))
    prompt_chars = payload_prompt_chars(payload)
    measurements = [
        measure_python_startup(args.python_bin, args.samples, args.warmup),
        measure_inprocess_payload(
            client_module,
            args.model,
            args.mode,
            args.task,
            args.max_tokens,
            args.samples,
            args.warmup,
            args.context_file,
            args.diff_file,
            args.max_context_chars,
        ),
        measure_json_roundtrip(payload, args.samples, args.warmup, pretty=False),
        measure_json_roundtrip(payload, args.samples, args.warmup, pretty=True),
        measure_client_preview_subprocess(
            args.python_bin,
            args.client,
            args.env_file,
            args.model,
            args.mode,
            args.task,
            args.max_tokens,
            args.samples,
            args.warmup,
            args.context_file,
            args.diff_file,
            args.max_context_chars,
        ),
    ]
    if args.loopback:
        measurements.append(
            measure_loopback_http_stub(
                client_module,
                args.model,
                args.mode,
                args.task,
                args.max_tokens,
                args.timeout,
                args.samples,
                args.warmup,
                args.context_file,
                args.diff_file,
                args.max_context_chars,
            )
        )
    if args.live:
        measurements.append(
            measure_http_models(
                args.base_url,
                api_key,
                args.timeout,
                args.insecure_skip_tls_verify,
                args.samples,
                args.warmup,
            )
        )
        measurements.append(
            measure_http_chat(
                client_module,
                args.base_url,
                args.model,
                args.mode,
                args.task,
                args.max_tokens,
                api_key,
                args.timeout,
                args.insecure_skip_tls_verify,
                args.samples,
                args.warmup,
                args.context_file,
                args.diff_file,
                args.max_context_chars,
            )
        )
        if args.live_cli:
            measurements.append(
                measure_client_live_subprocess(
                    args.python_bin,
                    args.client,
                    args.env_file,
                    args.base_url,
                    args.model,
                    args.mode,
                    args.task,
                    args.max_tokens,
                    args.api_key_env,
                    args.insecure_skip_tls_verify,
                    args.samples,
                    args.warmup,
                    args.context_file,
                    args.diff_file,
                    args.max_context_chars,
                )
            )

    return {
        "api_key_present": bool(api_key),
        "base_url": args.base_url,
        "client": str(args.client),
        "git_sha": git_sha(),
        "live": args.live,
        "live_cli": args.live_cli,
        "loopback": args.loopback,
        "max_context_chars": args.max_context_chars,
        "metadata": {
            "arch": platform.machine(),
            "os": platform.platform(),
            "prompt_chars": prompt_chars,
            "python_version": platform.python_version(),
            "request_bytes": request_bytes,
            "run_id": str(uuid.uuid4()),
            "timestamp_unix": time.time(),
        },
        "mode": args.mode,
        "model": args.model,
        "passed": True,
        "python": args.python_bin,
        "samples": args.samples,
        "size_sweep": measure_size_sweep(client_module, args) if args.size_sweep else [],
        "measurements": measurements,
        "warmup": args.warmup,
    }


def git_sha() -> str:
    """Return the current git SHA when available."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def main() -> int:
    """CLI entry point for local and optional live latency measurements."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=5, help="Samples per bucket.")
    parser.add_argument("--warmup", type=int, default=0, help="Discarded warmup samples after the cold sample.")
    parser.add_argument("--client", type=Path, default=DEFAULT_CLIENT_PATH)
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--env-file", default=os.getenv("DAEDALUS_ENV_FILE", "~/.env.daedalus.local"))
    parser.add_argument("--base-url", default=os.getenv("DAEDALUS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.getenv("DAEDALUS_MODEL", DEFAULT_MODEL))
    parser.add_argument("--mode", choices=VALID_MODES, default="review")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--context-file", action="append", default=[], help="Selected source/doc file to include.")
    parser.add_argument("--diff-file", action="append", default=[], help="Selected diff/patch file to include first.")
    parser.add_argument("--max-context-chars", type=int, default=DEFAULT_MAX_CONTEXT_CHARS)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=float(os.getenv("DAEDALUS_TIMEOUT", "180")))
    parser.add_argument("--api-key-env", default=os.getenv("DAEDALUS_API_KEY_ENV", "DAEDALUS_API_KEY"))
    parser.add_argument("--loopback", action="store_true", help="Also measure a local HTTP stub when sockets are allowed.")
    parser.add_argument("--size-sweep", action="store_true", help="Run generated local input-size sweep cases.")
    parser.add_argument("--live", action="store_true", help="Also measure authenticated /models and chat latency.")
    parser.add_argument("--live-cli", action="store_true", help="With --live, also spend calls on full CLI latency.")
    parser.add_argument("--insecure-skip-tls-verify", action="store_true")
    args = parser.parse_args()

    if args.samples < 1:
        print("ERROR: --samples must be >= 1", file=sys.stderr)
        return 2
    if args.warmup < 0:
        print("ERROR: --warmup must be >= 0", file=sys.stderr)
        return 2
    if args.max_context_chars < 1:
        print("ERROR: --max-context-chars must be >= 1", file=sys.stderr)
        return 2

    load_env_file(args.env_file)
    api_key = os.getenv(args.api_key_env, "")
    if args.live and not api_key:
        print(f"ERROR: --live requires {args.api_key_env} to be set.", file=sys.stderr)
        return 2
    if args.live_cli and not args.live:
        print("ERROR: --live-cli requires --live.", file=sys.stderr)
        return 2

    try:
        client_module = import_skill_client(args.client)
        report = build_report(args, api_key, client_module)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "api_key_present": bool(api_key),
                    "base_url": args.base_url,
                    "error": str(exc),
                    "live": args.live,
                    "passed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
