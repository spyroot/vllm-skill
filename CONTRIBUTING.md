# Contributing

Thanks for improving Daedalus. The best changes here are small, test-backed, and portable across machines and clusters.

## Start Here

Before opening a PR or sharing a patch, run the default offline checks:

```bash
./scripts/test.sh
```

Expected result:

```text
All checks passed.
```

If you are changing docs only, this still matters because the Bats tests check public docs for local lab leaks and broken layout assumptions.

## Shell Changes

For shell changes, run `shellcheck` and add Bats coverage for argument parsing, edge conditions,
blockers, and cleanup behavior where practical. On macOS, `/opt/homebrew/bin/bats` is the preferred Bats binary when present.

## Python Changes

For Python changes, use the project conda environment and focused pytest tests. Prefer `tmp_path`, `monkeypatch`, 
and mocks over live network, OpenShift, Docker, SSH, or GPU access.

## Live Systems

Do not run live OpenShift/GPU/deployment commands unless the current task explicitly approves it. Do not commit secrets, 
tokens, kubeconfigs, `.env` files, generated reports, or machine-local logs.

## CI Variables

Default GitLab CI is local/offline and needs no credentials.

Only optional manual live jobs should use CI variables such as:

- `HF_TOKEN` or `HF_TOKEN_FILE` for authenticated Hugging Face downloads. This may be the team-approved shared token.
- `DAEDALUS_RUNTIME_PROFILE_FILE`, `DAEDALUS_RUNTIME_PROFILE_B64`, or `DAEDALUS_RUNTIME_PROFILE` for GitLab-only runtime profiles.
- `KUBECONFIG_FILE` or `KUBECONFIG_B64` for approved live deployment/smoke tests.
- Registry credentials if a future pipeline publishes images outside the cluster.

Keep those variables masked/protected in GitLab. Do not echo them, write them to artifacts, or commit local env files.
