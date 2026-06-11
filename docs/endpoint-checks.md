# Endpoint Checks

Use these checks only after an approved live deployment or when someone gives you an existing OpenAI-compatible
endpoint. Local unit tests do not call a live route.

`run.me` is the model validator/evaluation harness for this repo. It checks specific task modes and output quality;
it is separate from the installed Daedalus skill client used for general support-agent calls.

The installed skill client, defined in `skills/daedalus/scripts/daedalus-client.py`, uses prompt templates from
`skills/daedalus/prompts/` and sends only the task plus selected `--diff-file` and `--context-file` content. Preview the
exact payload without inference before a large review:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/daedalus/scripts/daedalus-client.py" \
  --mode review \
  --task-file /path/to/review-task.md \
  --diff-file /path/to/selected.diff \
  --preview-payload
```

## Basic Code Generation

Check the exposed route shape first:

```bash
export DAEDALUS_API_KEY=<token-from-approved-secret-source>
./run.me --base-url https://<route-host>/v1 --mode code
```

For reusable prompts, save the task and pass it as a file:

```bash
./run.me \
  --base-url https://<route-host>/v1 \
  --mode review \
  --task-file /path/to/review-task.md
```

Expected JSON fields:

```json
{
  "artifact_type": "python_function",
  "passed": true,
  "validation": {
    "function": "add",
    "args": ["a", "b"],
    "return": "a + b"
  }
}
```

For lab routes with a self-signed cluster CA, add `--insecure-skip-tls-verify`.

## PR Draft Check

This checks whether the model can draft PR-ready text. It does not call GitHub or GitLab and does not create a PR.

```bash
./run.me \
  --base-url https://<route-host>/v1 \
  --mode pr \
  --max-tokens 320
```

Expected validation includes `title`, `summary`, `validation`, and `risk`.

## Review Check

```bash
./run.me \
  --base-url https://<route-host>/v1 \
  --mode review \
  --max-tokens 500 \
  --task "Review a change that adds tests for a Daedalus validation helper. Return Findings, Tests, Risk, and Recommendation."
```

`finding_count` must be a positive integer.

## Blocker And Non-Blocker Checks

The blocker check verifies that the model reports a true blocked action with `BLOCKER`, `ATTEMPTED`, `OBSERVED`,
and `SAFE_NEXT_STEP` sections:

```bash
./run.me \
  --base-url https://<route-host>/v1 \
  --mode blocker \
  --max-tokens 320
```

The non-blocker check verifies that ordinary findings are not mislabeled as blockers:

```bash
./run.me \
  --base-url https://<route-host>/v1 \
  --mode nonblocker \
  --max-tokens 500
```

## Dynamic Quality Check

`quality-dynamic` runs a seeded DynaCode-lite coding benchmark. It calls the live model, extracts generated Python,
blocks unsafe imports and side effects with AST checks, then runs visible and hidden cases in a timed subprocess.

```bash
./run.me \
  --base-url https://<route-host>/v1 \
  --mode quality-dynamic \
  --quality-seed 20260611 \
  --quality-threshold 85 \
  --quality-timeout 3 \
  --max-tokens 1400
```

Expected JSON fields:

```json
{
  "artifact_type": "quality_eval",
  "benchmark": "dynacode_lite_pipeline_v1",
  "passed": true,
  "score": 85,
  "max_score": 100,
  "threshold": 85
}
```

The command passes only when `score >= threshold` and `critical_passed` is true.

## Latency Probe

`scripts/daedalus-latency-probe.py`, defined in this repo under `scripts/`, measures client overhead separately from
model latency. The default command runs local-only buckets: Python startup, prompt build plus compact JSON
serialization, compact JSON roundtrip, pretty JSON roundtrip, and preview CLI subprocess cost. Each bucket records a
cold sample separately from the measured samples; `--warmup`, implemented by the same probe, discards extra warmup
samples after that cold sample.

```bash
scripts/daedalus-latency-probe.py --samples 5
```

Use `--diff-file` and `--context-file`, both forwarded to `skills/daedalus/scripts/daedalus-client.py`, when you want
the probe to measure the selected prompt packet you would send during review. Use `--size-sweep` when you want a
generated local experiment across task-only, diff-only, context-only, split diff/context, and many-small-file cases.

Add `--loopback` to measure a local HTTP stub when the sandbox allows local sockets. With an approved route token in
`DAEDALUS_API_KEY`, add `--live` to measure `/v1/models` and chat-completion latency. Add `--live-cli` only when you
also want to spend extra inference calls measuring full skill-client invocation.

```bash
scripts/daedalus-latency-probe.py --samples 5 --warmup 2 --size-sweep
scripts/daedalus-latency-probe.py --samples 5 --diff-file /tmp/selected.diff --context-file /tmp/relevant-file.py
scripts/daedalus-latency-probe.py --loopback --samples 5

DAEDALUS_API_KEY=<token-from-approved-secret-source> \
  scripts/daedalus-latency-probe.py --live --samples 3
```

## Calibration Pattern

Keep the seed fixed when tuning prompts. Inspect failed cases, clarify only the task contract, and rerun without
changing the scorer or lowering the threshold.
