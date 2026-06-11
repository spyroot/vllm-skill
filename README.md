# Daedalus

Daedalus helps Codex review code, propose patches, verify changes, and prepare PR-ready output using a
vLLM-backed OpenAI-compatible endpoint.

## Who This Is For

Use this repo if you are a developer or platform team that wants a reusable Codex skill backed by your own vLLM
runtime. The installable skill stays small and client-focused; the OpenShift/vLLM deployment tools live separately
under `runtime/daedalus-vllm/`.

## What You Can Do In 5 Minutes

Install the skill:

```bash
./scripts/install.sh
```

Run the offline validation suite:

```bash
./scripts/test.sh
```

Expected result:

```text
All checks passed.
```

Preview the runtime manifests without touching a cluster:

```bash
cd runtime/daedalus-vllm
./deploy-daedalus-openshift.sh --dry-run
```

## Safety Promise

Daedalus does not touch OpenShift, GPU nodes, credentials, production, or live deployment unless the current task
explicitly approves that work. Local checks are intentionally offline: they do not require Hugging Face, kubeconfig,
GPU access, private GitLab variables, or a live model endpoint.

If a required tool or permission is missing, scripts report `BLOCKER:` with the attempted action and a safe next
step rather than guessing around the problem.

## Honest Validation

The tests are not wired to fake success. Unit tests use mocks only for external boundaries such as HTTP, subprocess,
CUDA, OpenShift, and generated model calls; their docstrings say what is mocked and why. Live endpoint and live
cluster checks are separate opt-in commands and must call the real route or cluster when you run them.

Use the checks this way:

| Check | Command | What it proves |
| --- | --- | --- |
| Local validation | `./scripts/test.sh` | Shell, Bats, pytest, and offline guard behavior pass locally. |
| Coverage | `./scripts/coverage.sh` | Python code paths are exercised; current gate is `80%`. |
| Runtime dry-run | `make dry-run` | OpenShift manifests render without applying anything. |
| Live endpoint sanity | `DAEDALUS_API_KEY=<token> ./run.me --base-url https://<route-host>/v1` | An authenticated OpenAI-compatible endpoint responds. |
| Dynamic quality | `DAEDALUS_API_KEY=<token> ./run.me --base-url https://<route-host>/v1 --mode quality-dynamic` | The model solves a seeded DynaCode-lite task and passes hidden local checks. |
| Client latency | `scripts/daedalus-latency-probe.py --samples 5 --size-sweep` | Python startup, prompt serialization, CLI preview overhead, and input-size effects are measured without model calls. |

`run.me` is the repo validator/evaluation harness. Use it when you want to check model behavior against a concrete
task, PR draft shape, review shape, blocker handling, or dynamic quality score. The installed skill client is separate
and is for day-to-day support-agent calls from any repo.

## Prompt Contract

The Daedalus skill client sends an OpenAI-compatible chat request: a mode template from `skills/daedalus/prompts/` as
the `system` message and a bounded request packet as the `user` message. It does not automatically send hidden
instructions, workspace memory, or the whole repository.

Use selected context instead of raw repo dumps:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/daedalus/scripts/daedalus-client.py" \
  --mode review \
  --task-file /tmp/daedalus-task.md \
  --diff-file /tmp/selected.diff \
  --context-file /tmp/relevant-file.py \
  --preview-payload
```

Expected behavior: `--preview-payload`, implemented by `skills/daedalus/scripts/daedalus-client.py`, prints the exact
JSON payload without inference. Secret-like paths such as `.env` files are rejected, and common token-looking lines are
redacted before context is sent.

To compare client overhead with the real endpoint, use the latency probe. The default mode is local-only; `--loopback`,
defined by `scripts/daedalus-latency-probe.py`, adds a local HTTP stub when sockets are allowed. `--size-sweep`,
defined by the same probe, generates deterministic task, diff, and context payloads from 100 bytes to 200 KiB so you can
see how prompt size affects client-side cost. `--warmup` records one cold sample, discards warmup samples, then reports
the requested measured samples. `--live` adds authenticated `/models` and chat-completion timing, and `--live-cli`
additionally spends calls on full CLI timing:

```bash
scripts/daedalus-latency-probe.py --samples 5
scripts/daedalus-latency-probe.py --samples 5 --warmup 2 --size-sweep
scripts/daedalus-latency-probe.py --samples 5 --diff-file /tmp/selected.diff --context-file /tmp/relevant-file.py
scripts/daedalus-latency-probe.py --loopback --samples 5
DAEDALUS_API_KEY=<token> scripts/daedalus-latency-probe.py --live --samples 3
```

## Prerequisites

- Bash
- Python with `pytest` and `coverage`
- `shellcheck`
- `bats-core`
- `ripgrep`
- Docker, only for Linux-container test runs
- `oc`, GPU node access, and model credentials only for explicitly approved live OpenShift/vLLM work

On macOS:

```bash
brew install shellcheck bats-core ripgrep
```

Use a project-scoped conda environment for Python checks:

```bash
conda env create -f environment.yml
conda activate vllm-skill
```

## Common Commands

| Task | Command |
| --- | --- |
| Install the skill | `./scripts/install.sh` |
| Run local validation | `./scripts/test.sh` |
| Run coverage | `./scripts/coverage.sh` |
| Measure client overhead | `scripts/daedalus-latency-probe.py --samples 5 --size-sweep` |
| Run all local CI checks | `make ci` |
| Render runtime manifests | `make dry-run` |
| Test inside a Debian container | `./scripts/docker-test.sh` |

## Documentation

| Need | Start here |
| --- | --- |
| First local run | [Quickstart](docs/quickstart.md) |
| Runtime profiles and deployment flags | [Runtime configuration](docs/runtime-configuration.md) |
| `/v1/models`, PR/review/blocker, and quality checks | [Endpoint checks](docs/endpoint-checks.md) |
| Tokens, kubeconfigs, GitLab-only files, and safe Secret handling | [Security and secrets](docs/security-and-secrets.md) |
| GitLab live smoke setup | [GitLab live smoke](docs/gitlab-live-smoke.md) |
| GitHub/GitLab sync model | [Repository sync](docs/repository-sync.md) |
| Common blockers and recovery steps | [Troubleshooting](docs/troubleshooting.md) |

## Repository Layout

```text
skills/daedalus/SKILL.md        installable Codex skill
runtime/daedalus-vllm/          OpenShift/vLLM runtime assets
scripts/                        install, test, coverage, and live-smoke helpers
tests/                          Bats and Python tests
docs/                           operator and contributor documentation
```

Keep this repo portable. Do not commit tokens, kubeconfigs, browser/session state, private `.env` files, model
credentials, generated reports, or machine-local logs.

## GitHub Repository Metadata

Suggested GitHub description:

```text
Reusable Codex skill for vLLM-backed code review, patch generation, verification, and PR preparation.
```

Suggested topics:

```text
codex vllm openshift llmops code-review ai-agents developer-tools ci-cd
```
