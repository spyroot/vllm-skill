# Troubleshooting

## `BLOCKER: shellcheck is required`

Install shellcheck and rerun local validation:

```bash
brew install shellcheck
./scripts/test.sh
```

## `BLOCKER: bats is required`

Install Bats:

```bash
brew install bats-core
./scripts/test.sh
```

On macOS, the test runner prefers `/opt/homebrew/bin/bats` when present.

## `BLOCKER: ripgrep is required`

Install ripgrep:

```bash
brew install ripgrep
./scripts/test.sh
```

## `BLOCKER: pytest is required`

Activate the project environment or install pytest into that environment:

```bash
conda activate vllm-skill
./scripts/test.sh
```

## Coverage Fails Under The Threshold

Run the coverage command locally and inspect the missing lines:

```bash
./scripts/coverage.sh
open htmlcov/index.html
```

Coverage is a test-health signal. It does not prove the deployed model is good; use `quality-dynamic` for model
quality checks.

## Local Cache PV Is Released

If `CACHE_MODE=local` uses a `Retain` PV and the PVC is deleted, the PV can remain in `Released` state. Reuse the
same PVC name or have an operator clear the PV `spec.claimRef` after confirming the retained cache data should be
reused.

## Endpoint Check Cannot Resolve The Route

Use an existing reachable route host:

```bash
export DAEDALUS_API_KEY=<token-from-approved-secret-source>
./run.me --base-url https://<route-host>/v1 --mode code
```

If the service is only reachable inside the cluster, use port-forwarding or run the probe from a pod that can reach
the service network.
