# Runtime Profiles

Profiles are shell-style config files loaded by `--profile` or `--config`.
Use them for cluster-specific settings instead of editing scripts or YAML templates.

Committed examples:

- `example-local-gpu.env`: minimal placeholder profile for one named GPU node.
- `h100-coder.example.env`: H100-class coding-model profile with a larger local model cache.

Change these values for your cluster:

- `GPU_NODE`: only set this when you want to pin the pod to a known GPU node.
- `NODE_SELECTOR_KEY`: use your cluster's node label key when it is not `kubernetes.io/hostname`.
- `CACHE_MODE`: use `pvc` for the portable default, or `local` for a node-local `PersistentVolume`.
- `CACHE_STORAGE_CLASS`: set this only when your cluster requires a specific storage class.
- `CACHE_PATH`: set this only with `CACHE_MODE=local`; it is the host path on the selected GPU node.
- `ROUTE_ENABLED`, `ROUTE_HOST`, `ROUTE_TLS_TERMINATION`: set these when the endpoint should be reachable from the skill or CI runner outside the cluster.
- `MODEL`, `GPU_COUNT`, `TENSOR_PARALLEL_SIZE`, `MEMORY_*`, and `CPU_*`: tune these for the model and hardware.

Do not commit real hostnames, kubeconfigs, tokens, provider credentials, or private registry credentials.
Keep local profiles outside the repo or name them so they remain ignored by your own workspace rules.

For private GitLab live smoke jobs, store the real profile as `DAEDALUS_RUNTIME_PROFILE_FILE` or
`DAEDALUS_RUNTIME_PROFILE_B64`; store `HF_TOKEN` separately as a masked/protected variable.

For H100/live profiles, prefer `build-deploy-daedalus-openshift.sh` or set `DAEDALUS_BUILD_INTERNAL_IMAGE=1` in
GitLab live smoke so the runtime uses a cluster-local Daedalus image from the OpenShift internal registry.
