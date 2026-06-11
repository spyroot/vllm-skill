#!/usr/bin/env python3
"""Smoke checks for the Daedalus image.

The CUDA mode validates that the scheduled GPU can run a tiny PyTorch tensor.
The server mode validates that the OpenAI-compatible vLLM API is reachable.

"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request


def run_command(args: list[str]) -> str:
    """Run a local diagnostic command and return trimmed stdout."""
    completed = subprocess.run(args, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def check_cuda() -> dict[str, object]:
    """Validate CUDA visibility and a small tensor allocation through PyTorch."""
    import torch

    result: dict[str, object] = {
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "cuda_version": torch.version.cuda,
    }
    if not torch.cuda.is_available():
        return result

    result["device_name"] = torch.cuda.get_device_name(0)
    result["device_count"] = torch.cuda.device_count()
    tensor = torch.ones((1024, 1024), device="cuda")
    result["tensor_sum"] = float(tensor.sum().item())
    try:
        result["nvidia_smi"] = run_command(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.used",
                "--format=csv,noheader",
            ]
        )
    except Exception as exc:  # pragma: no cover - diagnostic fallback only
        result["nvidia_smi_error"] = str(exc)
    return result


def check_server(url: str) -> dict[str, object]:
    """Validate the vLLM OpenAI-compatible models endpoint."""
    with urllib.request.urlopen(f"{url.rstrip('/')}/models", timeout=10) as response:
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    return {"url": url, "model_count": len(payload.get("data", [])), "payload": payload}


def main() -> int:
    """Run the selected smoke check and print JSON for easy parsing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["cuda", "server"])
    parser.add_argument("--url", default="http://127.0.0.1:8000/v1")
    args = parser.parse_args()

    payload = check_cuda() if args.mode == "cuda" else check_server(args.url)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
