"""Unit tests for the Daedalus bearer-token auth proxy.

The tests use local in-process HTTP servers so auth decisions are verified
without OpenShift, vLLM, network credentials, or a live model endpoint.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "daedalus_auth_proxy.py"
APP_MODULE_NAME = "daedalus_auth_proxy_under_test"


def load_module():
    """Load the proxy helper as an isolated module for each test."""

    sys.modules.pop(APP_MODULE_NAME, None)
    spec = importlib.util.spec_from_file_location(APP_MODULE_NAME, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _UpstreamHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, object]] = []

    def do_GET(self):
        """Return a tiny model list and record request headers."""

        self.__class__.calls.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
            }
        )
        body = json.dumps({"object": "list", "data": [{"id": "unit-model"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _fmt, *_args):
        """Keep unit test output quiet."""


def _start_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_is_authorized_requires_exact_bearer_token():
    """Verify token checks are exact and reject missing or malformed headers."""

    module = load_module()

    assert module.is_authorized("Bearer expected-token", "expected-token") is True
    assert module.is_authorized("Bearer wrong-token", "expected-token") is False
    assert module.is_authorized("Basic expected-token", "expected-token") is False
    assert module.is_authorized(None, "expected-token") is False


def test_proxy_rejects_missing_token_before_upstream():
    """Verify unauthenticated requests do not reach the upstream model server."""

    module = load_module()
    _UpstreamHandler.calls = []
    upstream = _start_server(_UpstreamHandler)
    proxy = _start_server(module.make_handler("expected-token", f"http://127.0.0.1:{upstream.server_port}", 1024))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{proxy.server_port}/v1/models", timeout=5):
            raise AssertionError("request should have failed")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        assert exc.code == 401
        assert "expected-token" not in body
    finally:
        proxy.shutdown()
        upstream.shutdown()

    assert _UpstreamHandler.calls == []


def test_proxy_forwards_valid_token_and_strips_authorization():
    """Verify authorized requests are proxied without leaking the bearer token upstream."""

    module = load_module()
    _UpstreamHandler.calls = []
    upstream = _start_server(_UpstreamHandler)
    proxy = _start_server(module.make_handler("expected-token", f"http://127.0.0.1:{upstream.server_port}", 1024))
    request = urllib.request.Request(
        f"http://127.0.0.1:{proxy.server_port}/v1/models",
        headers={"Authorization": "Bearer expected-token"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        proxy.shutdown()
        upstream.shutdown()

    assert payload["data"][0]["id"] == "unit-model"
    assert _UpstreamHandler.calls == [{"path": "/v1/models", "authorization": None}]


def test_proxy_rejects_non_v1_paths_even_with_valid_token():
    """Verify the public proxy exposes only the OpenAI-compatible /v1 surface."""

    module = load_module()
    _UpstreamHandler.calls = []
    upstream = _start_server(_UpstreamHandler)
    proxy = _start_server(module.make_handler("expected-token", f"http://127.0.0.1:{upstream.server_port}", 1024))
    request = urllib.request.Request(
        f"http://127.0.0.1:{proxy.server_port}/metrics",
        headers={"Authorization": "Bearer expected-token"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5):
            raise AssertionError("request should have failed")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    finally:
        proxy.shutdown()
        upstream.shutdown()

    assert _UpstreamHandler.calls == []
