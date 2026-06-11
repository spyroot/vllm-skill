# Runtime Configuration

Use this when you are preparing the vLLM runtime for a real OpenShift/Kubernetes cluster. Start with dry-runs and
review the rendered manifest before any apply step.

## What The Runtime Deploys

| Component | Default | Purpose |
| --- | --- | --- |
| Namespace | `daedalus` | Isolates model-serving resources. |
| Deployment/Service | `daedalus` | Serves an OpenAI-compatible `/v1` API through vLLM. |
| Route | disabled by default | Optional external endpoint for Codex or CI access. |
| Default model | `Qwen/Qwen3-Coder-30B-A3B-Instruct` | Coding-review model sized for the provided single-GPU profile. |
| Cache | namespaced PVC | Portable model cache; node-local PV is opt-in. |

## Configuration Rules

Do not edit YAML templates for cluster-specific values. Use profiles or flags so another cluster can reuse the repo.

Change these when moving clusters:

| Setting | Why it matters |
| --- | --- |
| `MODEL`, `SERVED_MODEL_NAME` | Selects the model and served model name. |
| `GPU_COUNT`, `TENSOR_PARALLEL_SIZE` | Matches the model to the available GPU shape. |
| `GPU_NODE` | Pins to a node only when you intentionally need that. |
| `CACHE_MODE` | Use `pvc` for portability or `local` for an approved node-local PV. |
| `CACHE_STORAGE_CLASS`, `CACHE_PATH` | Required only when your cluster or local-cache mode needs them. |
| `ROUTE_ENABLED`, `ROUTE_HOST`, `ROUTE_TLS_TERMINATION` | Exposes the endpoint outside the cluster. |
| `API_KEY_SECRET_NAME`, `API_KEY_ENV_NAME` | Enables the auth proxy for public Route access. |
| `CPU_*`, `MEMORY_*`, startup probe values | Tunes startup and scheduling behavior for model size. |

## Dry-Run First

```bash
cd runtime/daedalus-vllm
./deploy-daedalus-openshift.sh --profile example-local-gpu --dry-run
```

For an H100-class single-GPU profile, start from:

```text
runtime/daedalus-vllm/profiles/h100-coder.example.env
```

Use committed profiles as schema references only; keep real cluster values outside git.

## Approved Apply Flow

After reviewing the dry-run and getting explicit approval:

```bash
cd runtime/daedalus-vllm
./deploy-daedalus-openshift.sh \
  --config /path/to/your-daedalus-runtime.env \
  --apply \
  --yes
```

When enabling a public Route, configure the auth proxy Secret in the profile or command:

```bash
./deploy-daedalus-openshift.sh \
  --config /path/to/your-daedalus-runtime.env \
  --route \
  --api-key-secret daedalus-api-key \
  --apply \
  --yes
```

If `CACHE_MODE=local`, add the explicit cluster-resource acknowledgement because the script creates a
cluster-scoped PV and touches a node host path:

```bash
./deploy-daedalus-openshift.sh \
  --config /path/to/your-daedalus-runtime.env \
  --apply \
  --yes \
  --yes-cluster-resources
```

## Immediate Validation Gates

After an approved apply, verify the rollout and API shape:

```bash
oc -n daedalus rollout status deploy/daedalus
```

```bash
export DAEDALUS_API_KEY=<token-from-approved-secret-source>
curl -s \
  -H "Authorization: Bearer ${DAEDALUS_API_KEY}" \
  https://<route-host>/v1/models
```

Expected `/v1/models` output contains:

```json
{
  "object": "list",
  "data": []
}
```

The `data` list should contain the served model once the runtime is ready.

## Image And Model Caching

The runtime has two caches:

- Model cache: `CACHE_MODE=local` with a large node-local PV keeps Hugging Face/vLLM model files on the selected GPU node.
- Image cache: `build-deploy-daedalus-openshift.sh` builds the Daedalus image into the OpenShift internal registry.

Use the combined build/deploy flow when you want to avoid repeatedly pulling the upstream vLLM image:

```bash
cd runtime/daedalus-vllm
./build-deploy-daedalus-openshift.sh \
  --config /path/to/your-daedalus-runtime.env \
  --apply \
  --yes
```
