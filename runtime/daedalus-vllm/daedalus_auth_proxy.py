#!/usr/bin/env python3
"""Minimal bearer-token gate for the Daedalus OpenAI-compatible runtime."""

from __future__ import annotations

import argparse
import hmac
import http.client
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final
from urllib.parse import urlsplit


DEFAULT_LISTEN_HOST: Final = "0.0.0.0"
DEFAULT_PORT: Final = 8080
DEFAULT_UPSTREAM: Final = "http://127.0.0.1:8000"
DEFAULT_MAX_BODY_BYTES: Final = 32 * 1024 * 1024
HOP_BY_HOP_HEADERS: Final = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def is_authorized(header_value: str | None, token: str) -> bool:
    """Return true only when the header is exactly the configured bearer token."""
    if not header_value or not token:
        return False
    scheme, _, candidate = header_value.partition(" ")
    if scheme.lower() != "bearer" or not candidate:
        return False
    return hmac.compare_digest(candidate, token)


def json_error(status: int, message: str) -> bytes:
    """Build a compact JSON error body."""
    return json.dumps({"error": message, "status": status}, separators=(",", ":")).encode("utf-8")


def make_handler(token: str, upstream: str, max_body_bytes: int):
    """Create a request handler bound to one token and upstream URL."""
    parsed = urlsplit(upstream)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("upstream must be an http(s) URL with a hostname")

    class DaedalusAuthProxyHandler(BaseHTTPRequestHandler):
        server_version = "DaedalusAuthProxy/1.0"

        def _send_json_error(self, status: int, message: str) -> None:
            body = json_error(status, message)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes | None:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                self._send_json_error(400, "invalid content length")
                return None
            if length > max_body_bytes:
                self._send_json_error(413, "request body too large")
                return None
            return self.rfile.read(length) if length else b""

        def _proxy(self) -> None:
            if not is_authorized(self.headers.get("Authorization"), token):
                self._send_json_error(401, "missing or invalid bearer token")
                return
            if self.path != "/v1" and not self.path.startswith("/v1/"):
                self._send_json_error(404, "not found")
                return

            body = self._read_body()
            if body is None:
                return

            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "authorization"
            }
            headers["Host"] = parsed.netloc

            connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
            connection = connection_cls(parsed.hostname, parsed.port, timeout=300)
            upstream_path = f"{parsed.path.rstrip('/')}{self.path}"
            try:
                connection.request(self.command, upstream_path, body=body, headers=headers)
                response = connection.getresponse()
                self.send_response(response.status, response.reason)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_BY_HOP_HEADERS:
                        self.send_header(key, value)
                self.end_headers()
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            finally:
                connection.close()

        def do_GET(self) -> None:
            """Proxy an authorized GET request."""
            self._proxy()

        def do_POST(self) -> None:
            """Proxy an authorized POST request."""
            self._proxy()

        def do_OPTIONS(self) -> None:
            """Proxy an authorized OPTIONS request."""
            self._proxy()

        def log_message(self, fmt: str, *args: object) -> None:
            """Log request metadata without headers or token values."""
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    return DaedalusAuthProxyHandler


def main() -> int:
    """Run the bearer-token reverse proxy."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default=os.getenv("DAEDALUS_AUTH_PROXY_LISTEN_HOST", DEFAULT_LISTEN_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("DAEDALUS_AUTH_PROXY_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--upstream", default=os.getenv("DAEDALUS_AUTH_PROXY_UPSTREAM", DEFAULT_UPSTREAM))
    parser.add_argument(
        "--api-key-env",
        default=os.getenv("DAEDALUS_AUTH_PROXY_API_KEY_ENV", "DAEDALUS_API_KEY"),
        help="Environment variable containing the Daedalus route bearer token.",
    )
    parser.add_argument(
        "--max-body-bytes",
        type=int,
        default=int(os.getenv("DAEDALUS_AUTH_PROXY_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES))),
    )
    args = parser.parse_args()

    token = os.getenv(args.api_key_env, "")
    if not token:
        print(f"ERROR: {args.api_key_env} is empty or unset; refusing to start auth proxy.", file=sys.stderr)
        return 78
    if "\n" in token or "\r" in token:
        print(f"ERROR: {args.api_key_env} contains a newline; refusing to start auth proxy.", file=sys.stderr)
        return 78

    handler = make_handler(token, args.upstream, args.max_body_bytes)
    server = ThreadingHTTPServer((args.listen_host, args.port), handler)
    print(f"Daedalus auth proxy listening on {args.listen_host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
