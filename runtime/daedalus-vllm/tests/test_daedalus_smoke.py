"""Unit tests for the Daedalus runtime smoke helper.

The tests mock HTTP, subprocess, and CUDA surfaces so runtime smoke behavior
stays local and deterministic.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "daedalus_smoke.py"
APP_MODULE_NAME = "daedalus_smoke_under_test"


def load_module():
    """Load the smoke helper as an isolated module for each test."""

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


def test_check_server_requests_models_endpoint_and_counts_models(monkeypatch):
    """Verify server checks call /models and count returned model entries."""

    module = load_module()
    calls = []

    def fake_urlopen(url, timeout):
        calls.append({"url": url, "timeout": timeout})
        return _FakeUrlopenResponse({"data": [{"id": "daedalus-a"}, {"id": "daedalus-b"}]})

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.check_server("http://daedalus.example.test/v1/")

    assert calls == [{"url": "http://daedalus.example.test/v1/models", "timeout": 10}]
    assert result == {
        "url": "http://daedalus.example.test/v1/",
        "model_count": 2,
        "payload": {"data": [{"id": "daedalus-a"}, {"id": "daedalus-b"}]},
    }


def test_check_server_treats_missing_data_as_zero_models(monkeypatch):
    """Verify server checks report zero models when the data list is absent."""

    module = load_module()

    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _FakeUrlopenResponse({"object": "list"}),
    )

    result = module.check_server("http://127.0.0.1:8000/v1")

    assert result["model_count"] == 0
    assert result["payload"] == {"object": "list"}


def test_run_command_uses_checked_captured_text_subprocess(monkeypatch):
    """Verify command execution uses checked subprocess output capture."""

    module = load_module()
    calls = []

    def fake_run(args, check, capture_output, text):
        calls.append(
            {
                "args": args,
                "check": check,
                "capture_output": capture_output,
                "text": text,
            }
        )
        return types.SimpleNamespace(stdout="  diagnostic output\n")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.run_command(["nvidia-smi", "--query-gpu=name"]) == "diagnostic output"
    assert calls == [
        {
            "args": ["nvidia-smi", "--query-gpu=name"],
            "check": True,
            "capture_output": True,
            "text": True,
        }
    ]


class _FakeCuda:
    """Model the small CUDA API surface used by the smoke helper."""

    def __init__(self, available: bool):
        self.available = available

    def is_available(self):
        return self.available

    def get_device_name(self, index):
        assert index == 0
        return "Unit Test GPU"

    def device_count(self):
        return 1


class _FakeTensorSum:
    """Return a deterministic tensor sum value for CUDA tests."""

    def item(self):
        return 1_048_576


class _FakeTensor:
    """Provide the sum method used by the CUDA allocation check."""

    def sum(self):
        return _FakeTensorSum()


def test_check_cuda_reports_unavailable_cuda_without_running_nvidia_smi(monkeypatch):
    """Verify unavailable CUDA skips nvidia-smi and reports minimal details."""

    module = load_module()
    fake_torch = types.SimpleNamespace(
        __version__="2.4.0",
        cuda=_FakeCuda(available=False),
        version=types.SimpleNamespace(cuda=None),
        ones=lambda *_args, **_kwargs: _FakeTensor(),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(
        module,
        "run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("nvidia-smi should not run")),
    )

    assert module.check_cuda() == {
        "torch_version": "2.4.0",
        "cuda_available": False,
        "cuda_version": None,
    }


def test_check_cuda_allocates_tensor_and_captures_nvidia_smi_when_cuda_is_available(monkeypatch):
    """Verify available CUDA allocates a tensor and captures nvidia-smi output."""

    module = load_module()
    ones_calls = []
    command_calls = []
    fake_torch = types.SimpleNamespace(
        __version__="2.4.0",
        cuda=_FakeCuda(available=True),
        version=types.SimpleNamespace(cuda="12.4"),
        ones=lambda *args, **kwargs: ones_calls.append({"args": args, "kwargs": kwargs}) or _FakeTensor(),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    def fake_run_command(args):
        command_calls.append(args)
        return "Unit Test GPU, 550.54, 81920 MiB, 1024 MiB"

    monkeypatch.setattr(module, "run_command", fake_run_command)

    result = module.check_cuda()

    assert ones_calls == [{"args": ((1024, 1024),), "kwargs": {"device": "cuda"}}]
    assert command_calls == [
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total,memory.used",
            "--format=csv,noheader",
        ]
    ]
    assert result == {
        "torch_version": "2.4.0",
        "cuda_available": True,
        "cuda_version": "12.4",
        "device_name": "Unit Test GPU",
        "device_count": 1,
        "tensor_sum": 1_048_576.0,
        "nvidia_smi": "Unit Test GPU, 550.54, 81920 MiB, 1024 MiB",
    }
