# GitLab Live Smoke Setup

This repo keeps the default GitLab pipeline offline. The manual live smoke job is for a trusted private GitLab
project with approved OpenShift access.

Do not commit real tokens, kubeconfigs, or lab-only runtime profiles. The committed files are examples only; real
values belong in the approved private CI/runtime secret store.

## Runner And Access Prerequisites

The `live-daedalus-smoke` job, defined in `.gitlab-ci.yml`, needs a protected GitLab runner that can reach the
approved OpenShift API and route host. The runner image must have `bash`, `oc`, `grep`, and `perl`; it needs `base64`
only when using `KUBECONFIG_B64` or `DAEDALUS_RUNTIME_PROFILE_B64`. Python for endpoint probes runs inside the
Daedalus pod or auth-proxy sidecar.

Before enabling the manual job, confirm:

- The GitLab project has protected branches or tags for trusted live runs.
- The runner can reach the OpenShift API from its network segment.
- The kubeconfig in `KUBECONFIG_FILE`, created as a GitLab File variable, has permission to manage the Daedalus namespace resources required by `scripts/gitlab-live-smoke.sh`.
- The runtime profile in `DAEDALUS_RUNTIME_PROFILE_FILE`, created as a GitLab File variable, points at approved cluster resources.
- The Hugging Face token in `HF_TOKEN`, created as a masked/protected GitLab variable, has read access to the configured model when the model is gated.
- The Daedalus route token in `DAEDALUS_API_KEY`, created as a masked/protected GitLab variable or File variable, is the bearer token accepted by the auth proxy.

## Pipeline Controls

Set these variables in GitLab under **Settings > CI/CD > Variables**.

| Variable | Type | Required | Recommended flags | Purpose |
| --- | --- | --- | --- | --- |
| `LIVE_OPENSHIFT_SMOKE` | Variable | Yes | Protected | Set to `1` to expose the manual `live-daedalus-smoke` job. |
| `DAEDALUS_RUNTIME_PROFILE_FILE` | File | Yes, unless using another profile variable | Protected | Shell-style runtime profile used by the deploy script. |
| `DAEDALUS_RUNTIME_PROFILE_B64` | Variable | Alternative | Masked, protected | Base64-encoded runtime profile when File variables are not available. |
| `DAEDALUS_RUNTIME_PROFILE` | Variable | Alternative | Protected | Plain multiline runtime profile when your GitLab handles multiline variables safely. |
| `KUBECONFIG_FILE` | File | Yes, unless runner already has cluster auth | Protected | Kubeconfig for approved live deployment and smoke validation. |
| `KUBECONFIG_B64` | Variable | Alternative | Masked, protected | Base64-encoded kubeconfig when File variables are not available. |
| `HF_TOKEN` | Variable | Recommended | Masked, protected | Team-approved Hugging Face read token for authenticated model downloads. |
| `HF_TOKEN_FILE` | File | Alternative | Protected | File variable containing the team-approved Hugging Face read token. |
| `DAEDALUS_API_KEY` | Variable | Required for public Route auth | Masked, protected | Dedicated bearer token used by the Daedalus auth proxy. |
| `DAEDALUS_API_KEY_FILE` | File | Alternative | Protected | File variable containing the Daedalus route bearer token. |
| `DAEDALUS_API_KEY_SECRET_NAME` | Variable | Optional | Protected | Kubernetes Secret name for the auth proxy token. Defaults to `daedalus-api-key`. |
| `DAEDALUS_HF_SECRET_NAME` | Variable | Optional | Protected | Kubernetes Secret name. Defaults to `daedalus-hf-token`. |
| `DAEDALUS_BUILD_INTERNAL_IMAGE` | Variable | Optional | Protected | Set to `1` to build the Daedalus image into the OpenShift internal registry before deploying. |

Prefer a dedicated least-privilege Hugging Face read token owned by the team or org. Use a personal token only when
policy explicitly approves it. Team members do not need to see raw token values; they only need permission to run the
protected manual job. The job creates or updates Kubernetes Secrets only when the matching variable is present. Tokens
are not printed or written to artifacts.

If using a GitLab File variable instead, name it `HF_TOKEN_FILE`; the script reads the file content into `HF_TOKEN`
in memory before creating the Kubernetes Secret.

## Runtime Profile

Use this committed example as the schema reference:

```text
runtime/daedalus-vllm/profiles/h100-coder.example.env
```

The real `DAEDALUS_RUNTIME_PROFILE_FILE` value should define:

- `GPU_NODE`
- `CACHE_STORAGE_CLASS`
- `CACHE_PATH`
- `CACHE_SIZE`
- `CPU_*`
- `MEMORY_*`
- model settings, if choosing a different model

Keep `HF_TOKEN` out of the profile. Store the team token as its own masked/protected variable.

For H100/live runs, set `DAEDALUS_BUILD_INTERNAL_IMAGE=1` when you want the pipeline to use the OpenShift
internal registry as the image cache. The first run builds from the pinned vLLM base image; later runtime restarts
pull the cluster-local Daedalus image and the pod template uses `imagePullPolicy: IfNotPresent`.

## Manual Invocation

After the variables are present:

1. Push the branch to the private GitLab project.
2. Open the pipeline.
3. Start the manual `live-daedalus-smoke` job.
4. Inspect `reports/live-smoke/` artifacts:
   - `context.txt`
   - `deploy.log`
   - `models.json`
   - `chat-completion.json`

Expected success indicators:

- The `live-daedalus-smoke` job finishes with status `passed`.
- `deploy.log`, written by `scripts/gitlab-live-smoke.sh`, includes a successful deployment or rollout wait.
- `models.json`, captured from the Daedalus `/v1/models` endpoint, contains `object: "list"` and at least one model id.
- `chat-completion.json`, captured from the Daedalus `/v1/chat/completions` endpoint, contains a non-empty assistant message and usage fields.

By default `context.txt` redacts the raw cluster API hostname. Set `KEEP_CONTEXT=1` only when storing that
private URL in GitLab artifacts is explicitly acceptable.

## Local Equivalent

For an approved local live smoke, export the same variables in a trusted shell and run
`scripts/gitlab-live-smoke.sh`. Do not document local secret file paths, and do not paste token values into logs,
docs, commits, issue comments, or pull requests.
