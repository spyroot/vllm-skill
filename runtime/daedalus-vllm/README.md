# Daedalus vLLM Runtime

This directory contains support resources for deploying and validating the `daedalus` OpenAI-compatible runtime in an OpenShift/Kubernetes cluster.

The default consumer is a coding-review and code-generation workflow: agents can send diffs, findings, proposed patches, or implementation prompts to the cluster endpoint and get structured model feedback. Other OpenAI-compatible workflows can reuse the same endpoint by overriding the prompt and model settings.

## What It Deploys

| Component          | Default                             | Purpose                                                                                 |
|--------------------|-------------------------------------|-----------------------------------------------------------------------------------------|
| Namespace          | `daedalus`                          | Keeps the runtime/model-serving infrastructure isolated.                                |
| Deployment/Service | `daedalus`                          | OpenAI-compatible `/v1` API powered by vLLM.                                            |
| Route              | disabled by default                 | Optional external OpenShift Route for skill/CI access outside the cluster.              |
| Default model      | `Qwen/Qwen3-Coder-30B-A3B-Instruct` | Coding-review and agentic coding model sized for a single-GPU runtime shape.            |
| GPU node           | scheduler-selected                  | Set `--node` or a profile only when you intentionally pin to a GPU host.                |
| Cache              | namespaced PVC                      | Portable model cache. Node-local PV is optional with `CACHE_MODE=local`.                |

The default image is pinned to `vllm/vllm-openai:v0.21.0` instead of `latest` so CUDA/vLLM behavior is repeatable.
The larger `Qwen/Qwen3-Coder-480B-A35B-Instruct` target needs a larger multi-GPU serving profile than this
single-pod default.

First startup can spend several minutes downloading model shards, loading weights, and compiling CUDA graphs.
The manifest includes a generous `startupProbe` so Kubernetes does not restart the vLLM process before `/health`
is available. Readiness still gates traffic on the same `/health` endpoint.

## Configuration Rules

Cluster-specific settings belong in profiles or flags, not in scripts or YAML templates. This keeps the skill
portable for another OpenShift/Kubernetes cluster.

Change these values for your environment:

- `--model` / `MODEL`: model id to serve.
- `--gpu-count` / `GPU_COUNT`: number of GPUs to request.
- `--tensor-parallel-size` / `TENSOR_PARALLEL_SIZE`: vLLM tensor parallel size; normally equals GPU count.
- `--node` / `GPU_NODE`: optional node pinning; leave empty for scheduler placement.
- `--cache-mode` / `CACHE_MODE`: `pvc` for portable default, `local` for node-local PV.
- `--storage-class` / `CACHE_STORAGE_CLASS`: storage class only when your cluster requires one.
- `--cache-path` / `CACHE_PATH`: host path only with `CACHE_MODE=local`.
- `--route` / `ROUTE_ENABLED`: create an OpenShift Route when the skill or CI runner should call the endpoint directly.
- `--cpu-*`, `--memory-*`, and startup probe settings: tune for model size and first-load time.

Profiles live under `profiles/`. The shipped `example-local-gpu.env` uses placeholder values and documents the profile
shape for a cluster-specific runtime configuration.

## Safety Notes

Default `CACHE_MODE=pvc` renders only namespaced resources. `CACHE_MODE=local` creates a cluster-scoped
`PersistentVolume` and prepares a node host path, so `--apply` requires both `--yes` and
`--yes-cluster-resources`.

Do not store model tokens or provider credentials in this directory. If a model requires authentication,
create an OpenShift Secret and pass its name with `--hf-secret`, or create/update the Secret from a local
environment variable with `--create-hf-secret`.

## Build The Custom Dev Image

The custom image extends the vLLM OpenAI-compatible image with small operator tools and smoke-test helpers, 
so the running pod can also be used as a CUDA/vLLM debug box. Building this image into the OpenShift internal
registry also gives the cluster a local image source. Combined with `imagePullPolicy: IfNotPresent`, runtime
restarts should reuse the node image cache instead of repeatedly pulling the large upstream vLLM image.

```bash
./build-daedalus-image-openshift.sh --dry-run
```

```bash
./build-daedalus-image-openshift.sh --apply --yes
```

## Deploy The Runtime

Dry run:

```bash
./deploy-daedalus-openshift.sh --dry-run
```

Apply with the upstream vLLM image:

```bash
./deploy-daedalus-openshift.sh \
  --apply \
  --yes
```

Apply with an approved runtime config:

```bash
./deploy-daedalus-openshift.sh \
  --config /path/to/your-daedalus-runtime.env \
  --apply \
  --yes
```

If that profile uses `CACHE_MODE=local`, add the explicit cluster-resource acknowledgement:

```bash
./deploy-daedalus-openshift.sh \
  --config /path/to/your-daedalus-runtime.env \
  --apply \
  --yes \
  --yes-cluster-resources
```

For authenticated Hugging Face downloads:

```bash
read -rsp "HF token: " HF_TOKEN
printf '\n'
export HF_TOKEN
./deploy-daedalus-openshift.sh \
  --create-hf-secret daedalus-hf-token \
  --hf-token-env HF_TOKEN \
  --apply \
  --yes
unset HF_TOKEN
```

Apply with the custom image built in the OpenShift internal registry:

```bash
./build-deploy-daedalus-openshift.sh \
  --config /path/to/your-daedalus-runtime.env \
  --apply \
  --yes
```

Expose the service with an OpenShift Route:

```bash
./deploy-daedalus-openshift.sh \
  --config /path/to/your-daedalus-runtime.env \
  --route \
  --api-key-secret daedalus-api-key \
  --apply \
  --yes
```

`daedalus-api-key` is the OpenShift Secret consumed by the auth proxy created by this step. Use
`--create-api-key-secret daedalus-api-key --api-key-env DAEDALUS_API_KEY` when creating or rotating that Secret from
an approved local or CI environment.

To keep the generated OpenShift apps Route and add a second public host:

```bash
./bind-daedalus-public-host-openshift.sh \
  --host daedalus.example.com \
  --dry-run
```

After DNS points that host at the ingress IP, apply it:

```bash
./bind-daedalus-public-host-openshift.sh \
  --host daedalus.example.com \
  --apply
```

## Validation Gates

Start with a dry run so you can review rendered manifests before changing the cluster:

```bash
./deploy-daedalus-openshift.sh --dry-run
```

After an approved apply, wait for the Deployment named `daedalus`, created by `deploy-daedalus-openshift.sh`, to roll out:

```bash
oc -n daedalus rollout status deploy/daedalus
```

Then confirm the OpenAI-compatible model list:

```bash
oc -n daedalus run daedalus-curl \
  --rm -i --restart=Never --image=curlimages/curl:latest -- \
  curl -s http://daedalus.daedalus.svc.cluster.local:8000/v1/models
```

Expected result: the response contains `object: "list"` and at least one model in `data`.

Finally, run a chat probe:

```bash
python3 ./openai_compat_probe.py \
  --url http://daedalus.daedalus.svc.cluster.local:8000/v1
```

Expected result: the probe prints JSON with a successful chat completion, parsed content, and token usage.

## Smoke Checks

After the pod is ready:

```bash
oc -n daedalus exec deploy/daedalus -- \
  python3 /opt/daedalus/daedalus_smoke.py cuda
```

```bash
oc -n daedalus run daedalus-curl \
  --rm -i --restart=Never --image=curlimages/curl:latest -- \
  curl -s http://daedalus.daedalus.svc.cluster.local:8000/v1/models
```

Run a few positive/negative endpoint interactions against the OpenAI-compatible chat endpoint:

```bash
python3 ./openai_compat_probe.py \
  --url http://daedalus.daedalus.svc.cluster.local:8000/v1
```

Or run it from the custom Daedalus runtime image:

```bash
oc -n daedalus exec deploy/daedalus -c daedalus-auth-proxy -- \
  python3 /opt/daedalus/openai_compat_probe.py \
  --url http://127.0.0.1:8080/v1
```

For the direct external path used by the skill, run the repo-level sanity check against the Route:

```bash
export DAEDALUS_API_KEY=<token from approved Secret source>
../../run.me --base-url https://<route-host>/v1
```

For lab routes with a self-signed cluster CA, add `--insecure-skip-tls-verify`.

## Troubleshooting

If `CACHE_MODE=local` uses a `Retain` PV and the PVC is deleted, the PV can remain in `Released` state and will
not automatically bind to a new PVC. Reuse the same PVC name or have an operator clear the PV `spec.claimRef`
after confirming the retained cache data should be reused.
