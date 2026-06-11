# Security And Secrets

This repo is designed to stay portable. Public GitHub source should contain examples and instructions, not real
tokens, kubeconfigs, private profiles, or lab-only hostnames.

## What Stays Out Of GitHub

Keep these out of GitHub:

| Secret or private value | Safe place |
| --- | --- |
| Hugging Face model-download token | Dedicated least-privilege GitLab masked/protected `HF_TOKEN` or `HF_TOKEN_FILE` |
| Daedalus route bearer token | OpenShift Secret `daedalus-api-key`, GitLab masked/protected `DAEDALUS_API_KEY` or `DAEDALUS_API_KEY_FILE` |
| Kubeconfig | GitLab File variable `KUBECONFIG_FILE` or protected `KUBECONFIG_B64` |
| Runtime profile with real hostnames or nodes | Approved private CI/runtime secret store |
| Private route hostnames | Task handoff or GitLab artifacts only when approved |

## Safer Local Token Flow

Avoid putting secrets directly in command history. Read a token silently into the current shell, run the command, then
unset it:

```bash
read -rsp "HF token: " HF_TOKEN
printf '\n'
export HF_TOKEN

cd runtime/daedalus-vllm
./deploy-daedalus-openshift.sh \
  --create-hf-secret daedalus-hf-token \
  --hf-token-env HF_TOKEN \
  --dry-run

unset HF_TOKEN
```

`daedalus-hf-token` is the Kubernetes Secret created or updated by that step when the command is later run with
approved `--apply --yes` flags.

## Daedalus Route Token

Public Routes must not point directly at an anonymous vLLM service. When route auth is enabled,
`deploy-daedalus-openshift.sh` creates a small `daedalus-auth-proxy` sidecar from `daedalus_auth_proxy.py`, a
ConfigMap-mounted Python proxy in `runtime/daedalus-vllm/`. The Service targets that proxy, the proxy checks
`Authorization: Bearer <token>` on every request with an in-memory constant-time comparison, and only accepted
requests are forwarded to vLLM on `127.0.0.1:8000`.

Generate the token in a trusted shell, then store it as the OpenShift Secret `daedalus-api-key`, created by
`deploy-daedalus-openshift.sh` when `--create-api-key-secret daedalus-api-key` is used:

```bash
read -rsp "Daedalus route token: " DAEDALUS_API_KEY
printf '\n'
export DAEDALUS_API_KEY

cd runtime/daedalus-vllm
./deploy-daedalus-openshift.sh \
  --route \
  --create-api-key-secret daedalus-api-key \
  --api-key-env DAEDALUS_API_KEY \
  --dry-run

unset DAEDALUS_API_KEY
```

Use a dedicated shared team token only when that is the approved policy. Prefer a generated, least-privilege token
for this endpoint over a personal account token.

## GitHub Boundary

GitHub Actions runs offline validation and coverage only. It must not require GitLab-only team files, sandbox
profiles, kubeconfigs, token-bearing `.env` files, private URLs, GPU hosts, OpenShift access, or live endpoints.

## GitLab Boundary

GitLab may hold private CI/CD variables and manual live-smoke controls. Keep those variables masked/protected, and do
not print, echo, or write them to artifacts.
