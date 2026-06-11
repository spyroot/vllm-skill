# Quickstart

This path gets a new checkout to a verified local state without using OpenShift, GPU nodes, model credentials, or a
live endpoint.

## 1. Install Local Tools

On macOS:

```bash
brew install shellcheck bats-core ripgrep
```

Create and activate the project Python environment:

```bash
conda env create -f environment.yml
conda activate vllm-skill
```

## 2. Install The Skill

From the repo root:

```bash
./scripts/install.sh
```

Expected output includes:

```text
Linked daedalus -> <codex-home>/skills/daedalus
Done. Installed skills into <codex-home>/skills
```

Use copy mode if the checkout path is not stable:

```bash
./scripts/install.sh --copy
```

## 3. Run Offline Validation

Run this before opening a PR or sharing a patch. It is intentionally offline.

```bash
./scripts/test.sh
```

Expected result:

```text
All checks passed.
```

If a required local tool is missing, the script reports `BLOCKER:` with the missing tool and safe next step.

## 4. Run Coverage

Coverage tells you whether tests exercise the Python validation helpers. It is not a model-quality score.

```bash
./scripts/coverage.sh
```

Expected result includes a coverage table and generated reports:

```text
TOTAL ... >= 80%
Wrote XML report to coverage.xml
Wrote HTML report to htmlcov/index.html
```

The default gate is `COVERAGE_FAIL_UNDER=80`.

## 5. Preview Runtime Manifests

This renders OpenShift YAML without applying it:

```bash
make dry-run
```

The rendered files appear under `reports/rendered-manifests/`, which is ignored by git.
