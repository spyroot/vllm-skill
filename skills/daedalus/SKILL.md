---
name: daedalus
description: Use when a vLLM-backed coding support agent should review code, plan fixes, generate code, draft PR text, check blocker handling, or compare model quality through an authenticated OpenAI-compatible endpoint.
---

# Daedalus Skill

Daedalus is a vLLM-backed coding support agent for review, code generation, plans, PR drafts, blocker checks, and
quality prompts. Codex stays the coordinator: Daedalus gives model output, while Codex decides what to edit, run, commit,
or push.

## Core Rules

- Do not run live OpenShift/Kubernetes, GPU-host, deployment, destructive, network, or credential-sensitive commands unless the current task explicitly approves them.
- Default to local validation: shell syntax, `shellcheck`, Bats, Python unit tests, and endpoint sanity checks.
- Treat the vLLM deployment assets as repo-level runtime operations under `runtime/daedalus-vllm/`, not as installable skill content.
- Do not print, commit, or prompt with Hugging Face tokens, provider keys, kubeconfigs, `.env` files, or other secrets.
- Use the authenticated route token from `DAEDALUS_API_KEY`; never paste or echo it.
- Do not tell the user Daedalus created a PR. PR mode drafts text only; Codex owns git, GitHub/GitLab, and PR creation.
- If required tools, sandbox access, cluster access, GPU access, or credentials are missing, stop and report:

```text
BLOCKER: <reason>
ATTEMPTED: <what was tried>
OBSERVED: <exact limitation or output>
SAFE_NEXT_STEP: <what should unblock the work>
```

## Runtime Boundary

The installable skill should assume a vLLM OpenAI-compatible endpoint already exists. Operators can deploy or
validate that endpoint from the repository root using:

```text
runtime/daedalus-vllm/
```

Those runtime scripts are one-time or operator-managed deployment tooling. Do not copy their contents into
`skills/daedalus/`; the skill install path should stay small and client-focused.

## Endpoint Setup

Prefer these environment variables, usually loaded from `~/.env.daedalus.local`:

```text
DAEDALUS_BASE_URL=https://agents.cnfdemo.io/v1
DAEDALUS_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
DAEDALUS_API_KEY=<stored outside git>
```

If `DAEDALUS_API_KEY` is missing, check whether `~/.env.daedalus.local` exists and source it without printing values:

```bash
set -a
. "$HOME/.env.daedalus.local"
set +a
```

## Support-Agent Client

Use the bundled client from any repository:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/daedalus/scripts/daedalus-client.py" \
  --mode review \
  --task-file /tmp/daedalus-task.md \
  --diff-file /tmp/selected.diff \
  --context-file /tmp/relevant-file.py
```

Modes:

- `review`: findings, tests, risk, recommendation.
- `code`: implementation guidance or code; Codex applies edits.
- `plan`: implementation plan and validation steps.
- `pr`: PR title/body draft only.
- `blocker`: validates true blocker wording.
- `nonblocker`: checks ordinary findings are not mislabeled as blockers.
- `quality-dynamic`: asks for benchmark-style code; use repo scoring when available.

Use a task file for long diffs or handoffs; task files are only for readable prompts and avoiding giant command
arguments.

Prompt templates live in `skills/daedalus/prompts/`, which the skill client loads as the OpenAI-compatible `system`
message for each mode. Daedalus does not inherit hidden instructions, workspace memory, or repo files.
Pass only selected diffs and files through `--diff-file` and `--context-file`; the client labels, caps, and redacts that
context before sending it as the `user` message. Use `--preview-payload` to inspect the exact request without calling the
endpoint.

## Repo Validation

From this skill repo checkout:

```bash
./scripts/test.sh
```

For full local/live model evaluation in this repo, use `run.me`. It is the validator harness for checking concrete
task behavior and output quality, separate from the installed support-agent client:

```bash
./run.me --mode code
./run.me --mode pr
./run.me --mode review
./run.me --mode blocker
./run.me --mode nonblocker
./run.me --mode quality-dynamic
```

## Delegation Pattern

When another agent is asked to use Daedalus, include:

- the goal and target model/runtime settings
- whether work is local-only, dry-run, endpoint-only, or live cluster approved
- exact files in scope
- exact verification commands
- any GPU host or OpenShift namespace constraints
- the rule that blockers must be reported as `BLOCKER:` rather than guessed around
- the reminder that Daedalus output is advisory until Codex verifies it locally
