#!/usr/bin/env python3
"""Call a Daedalus OpenAI-compatible endpoint as a support coding agent."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://agents.cnfdemo.io/v1"
DEFAULT_MODEL = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
VALID_MODES = ("code", "review", "pr", "plan", "blocker", "nonblocker", "quality-dynamic")
PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
DEFAULT_MAX_CONTEXT_CHARS = 20000
SECRET_SUFFIXES = (".key", ".pem", ".p12", ".pfx")
SECRET_FILENAMES = {
    ".env",
    ".env.local",
    ".env.daedalus.local",
    ".netrc",
    "credentials",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "kubeconfig",
}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)^(\s*[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE_KEY|KUBECONFIG|CREDENTIAL)"
    r"[A-Z0-9_]*\s*=\s*).*$"
)
BEARER_VALUE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}")


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


def load_system_prompt(mode: str, prompt_dir: str | Path | None = None) -> str:
    """Load the checked-in system prompt template for a Daedalus mode."""
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of: {', '.join(VALID_MODES)}")

    base_dir = Path(prompt_dir).expanduser() if prompt_dir else PROMPT_DIR
    prompt_path = base_dir / f"{mode}.md"
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"system prompt is empty: {prompt_path}")
    return prompt


def read_task(args: argparse.Namespace) -> str:
    """Read task text from a flag, file, stdin, or a mode-specific default."""
    if args.task_file:
        return Path(args.task_file).read_text(encoding="utf-8")
    if args.task:
        return args.task
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return "Review the current change and return concrete findings, validation, risk, and recommendation."


def build_user_message(
    mode: str,
    task: str,
    context_files: list[str] | None = None,
    diff_files: list[str] | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> str:
    """Build a bounded, labeled user prompt packet for Daedalus."""
    sections = [
        "# Daedalus Request",
        f"MODE: {mode}",
        "## Task",
        task.strip(),
        "## Context Policy",
        (
            "Use only the selected context below. If needed context is missing, say what is missing "
            "without inventing files, command output, or repository state."
        ),
    ]

    context_sections, remaining = build_context_sections(diff_files or [], "Diff", max_context_chars)
    file_sections, _remaining = build_context_sections(context_files or [], "File", remaining)
    context_sections.extend(file_sections)
    if context_sections:
        sections.append("## Selected Context")
        sections.extend(context_sections)

    return "\n\n".join(section for section in sections if section)


def build_context_sections(paths: list[str], label: str, max_chars: int) -> tuple[list[str], int]:
    """Read selected context files with redaction and return the remaining cap."""
    sections = []
    remaining = max(0, max_chars)
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        header = f"### {label}: {raw_path}\n"
        reject_reason = sensitive_path_reason(path)
        if reject_reason:
            raise ValueError(f"refusing to prompt with sensitive {label.lower()} path {raw_path}: {reject_reason}")
        if remaining <= len(header):
            section = f"{header}[OMITTED: context budget exhausted]"
            sections.append(section)
            remaining = 0
            continue

        text = read_prompt_context(path, remaining - len(header))
        section = f"{header}{text}"
        sections.append(section)
        remaining = max(0, remaining - len(section))
    return sections, remaining


def read_prompt_context(path: Path, max_chars: int) -> str:
    """Read bounded context and redact only text that can still be emitted."""
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    bounded_text = bound_for_redaction(raw_text, max_chars)
    redacted_text = redact_sensitive_text(bounded_text)
    trimmed, _consumed = trim_to_budget(redacted_text, max_chars, original_len=len(raw_text))
    return trimmed


def bound_for_redaction(text: str, max_chars: int, margin: int = 4096) -> str:
    """Keep enough text to redact a line that crosses the trim boundary."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    end = min(len(text), max_chars + margin)
    candidate = text[:end]
    newline = candidate.find("\n", max_chars)
    if newline != -1:
        return candidate[:newline]
    if end < len(text):
        last_newline = candidate.rfind("\n", 0, max_chars)
        if last_newline != -1:
            return candidate[: last_newline + 1]
        return ""
    return candidate


def sensitive_path_reason(path: Path) -> str:
    """Return a reason when a path is too likely to contain local secrets."""
    name = path.name.lower()
    parts = {part.lower() for part in path.parts}
    if name.startswith(".env") or name in SECRET_FILENAMES:
        return "secret-like filename"
    if name.endswith(SECRET_SUFFIXES):
        return "private key or certificate secret suffix"
    if ".kube" in parts:
        return "kubeconfig directory"
    return ""


def redact_sensitive_text(text: str) -> str:
    """Redact common secret assignment and bearer-token patterns."""
    redacted_lines = []
    for line in text.splitlines():
        redacted = SECRET_ASSIGNMENT.sub(r"\1<redacted>", line)
        redacted = BEARER_VALUE.sub(r"\1<redacted>", redacted)
        redacted_lines.append(redacted)
    return "\n".join(redacted_lines)


def trim_to_budget(text: str, max_chars: int, original_len: int | None = None) -> tuple[str, int]:
    """Trim text to a character budget and report consumed context characters."""
    full_len = len(text) if original_len is None else original_len
    if max_chars <= 0:
        return "[OMITTED: context budget exhausted]", 0
    if len(text) <= max_chars and full_len <= max_chars:
        return text, len(text)

    marker = f"\n[TRUNCATED: kept first {max_chars} of {full_len} characters]"
    keep = max(0, max_chars - len(marker))
    return f"{text[:keep].rstrip()}{marker}", max_chars


def chat_payload(
    model: str,
    mode: str,
    task: str,
    max_tokens: int,
    prompt_dir: str | Path | None = None,
    context_files: list[str] | None = None,
    diff_files: list[str] | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> dict[str, Any]:
    """Build the OpenAI-compatible chat payload."""
    return {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": load_system_prompt(mode, prompt_dir)},
            {
                "role": "user",
                "content": build_user_message(mode, task, context_files, diff_files, max_context_chars),
            },
        ],
    }


def post_json(
    base_url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: float,
    insecure_skip_tls_verify: bool,
) -> dict[str, Any]:
    """POST a chat completion request and decode the response."""
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    context = ssl._create_unverified_context() if insecure_skip_tls_verify else None
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_content(response: dict[str, Any]) -> str:
    """Extract assistant content from an OpenAI-compatible chat completion."""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("response does not contain choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("response does not contain choices[0].message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("response does not contain non-empty message content")
    return content.strip()


def main() -> int:
    """Run the Daedalus support-agent client."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=os.getenv("DAEDALUS_ENV_FILE", "~/.env.daedalus.local"))
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--mode", choices=VALID_MODES, default="review")
    parser.add_argument("--task", default="")
    parser.add_argument("--task-file", default="")
    parser.add_argument("--context-file", action="append", default=[], help="Selected source or doc file to include.")
    parser.add_argument("--diff-file", action="append", default=[], help="Selected diff or patch file to include first.")
    parser.add_argument("--prompt-dir", default="", help="Directory containing mode prompt templates.")
    parser.add_argument(
        "--max-context-chars",
        type=int,
        default=int(os.getenv("DAEDALUS_MAX_CONTEXT_CHARS", str(DEFAULT_MAX_CONTEXT_CHARS))),
    )
    parser.add_argument("--timeout", type=float, default=float(os.getenv("DAEDALUS_TIMEOUT", "180")))
    parser.add_argument("--max-tokens", type=int, default=int(os.getenv("DAEDALUS_MAX_TOKENS", "900")))
    parser.add_argument("--api-key-env", default=os.getenv("DAEDALUS_API_KEY_ENV", "DAEDALUS_API_KEY"))
    parser.add_argument("--json", action="store_true", help="Print the full evidence JSON instead of only content.")
    parser.add_argument("--preview-payload", action="store_true", help="Print request payload without calling Daedalus.")
    parser.add_argument("--insecure-skip-tls-verify", action="store_true")
    args = parser.parse_args()

    load_env_file(args.env_file)
    base_url = args.base_url or os.getenv("DAEDALUS_BASE_URL", DEFAULT_BASE_URL)
    model = args.model or os.getenv("DAEDALUS_MODEL", DEFAULT_MODEL)
    api_key = os.getenv(args.api_key_env, "")
    try:
        task = read_task(args).strip()
        if not task:
            print("ERROR: task text is empty.", file=sys.stderr)
            return 2
        payload = chat_payload(
            model,
            args.mode,
            task,
            args.max_tokens,
            args.prompt_dir or None,
            args.context_file,
            args.diff_file,
            args.max_context_chars,
        )
        if args.preview_payload:
            print(
                json.dumps(
                    {
                        "api_key_present": bool(api_key),
                        "base_url": base_url,
                        "mode": args.mode,
                        "model": model,
                        "payload": payload,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        response = post_json(base_url, payload, api_key, args.timeout, args.insecure_skip_tls_verify)
        content = extract_content(response)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "api_key_present": bool(api_key),
                    "base_url": base_url,
                    "error": str(exc),
                    "mode": args.mode,
                    "model": model,
                    "passed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "api_key_present": bool(api_key),
                    "base_url": base_url,
                    "content": content,
                    "mode": args.mode,
                    "model": response.get("model", model),
                    "passed": True,
                    "usage": response.get("usage", {}),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
