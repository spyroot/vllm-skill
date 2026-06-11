#!/usr/bin/env bats

setup() {
  export REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)"
  export RESOURCE_DIR="$REPO_ROOT/runtime/daedalus-vllm"
  export DEPLOY_SCRIPT="$RESOURCE_DIR/deploy-daedalus-openshift.sh"
  export BUILD_DEPLOY_SCRIPT="$RESOURCE_DIR/build-deploy-daedalus-openshift.sh"
}

@test "deploy rejects invalid cache mode before rendering" {
  run "$DEPLOY_SCRIPT" --cache-mode invalid --dry-run

  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: --cache-mode must be pvc or local"* ]]
}

@test "deploy rejects local cache without node path and storage settings" {
  run "$DEPLOY_SCRIPT" --cache-mode local --dry-run
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: --cache-mode local requires --node or GPU_NODE"* ]]

  run "$DEPLOY_SCRIPT" --cache-mode local --node gpu.example.invalid --dry-run
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: --cache-mode local requires --cache-path"* ]]

  run "$DEPLOY_SCRIPT" \
    --cache-mode local \
    --node gpu.example.invalid \
    --cache-path /var/lib/daedalus/model-cache \
    --dry-run
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: --cache-mode local requires --storage-class"* ]]
}

@test "deploy rejects local-only cache flags in pvc mode" {
  run "$DEPLOY_SCRIPT" --cache-path /var/lib/daedalus/model-cache --dry-run

  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: --cache-path is only valid with --cache-mode local."* ]]
}

@test "deploy apply requires explicit yes and local cluster-resource acknowledgement" {
  run "$DEPLOY_SCRIPT" --apply
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: --apply requires --yes"* ]]

  run "$DEPLOY_SCRIPT" \
    --cache-mode local \
    --node gpu.example.invalid \
    --cache-path /var/lib/daedalus/model-cache \
    --storage-class local-static-example \
    --apply \
    --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: local cache mode creates a cluster-scoped PersistentVolume"* ]]
}

@test "build deploy apply requires local cluster-resource acknowledgement" {
  run "$BUILD_DEPLOY_SCRIPT" \
    --cache-mode local \
    --node gpu.example.invalid \
    --cache-path /var/lib/daedalus/model-cache \
    --storage-class local-static-example \
    --apply \
    --yes

  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: local cache mode requires --yes-cluster-resources."* ]]
}

@test "deploy renders hf secret envFrom in dry-run" {
  run "$DEPLOY_SCRIPT" --hf-secret daedalus-hf-token --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"hf_secret=daedalus-hf-token"* ]]
  [[ "$output" == *"envFrom:"* ]]
  [[ "$output" == *"secretRef:"* ]]
  [[ "$output" == *"name: daedalus-hf-token"* ]]
}

@test "deploy rejects public route without api key secret" {
  run "$DEPLOY_SCRIPT" --route --dry-run

  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: --route requires --api-key-secret or --create-api-key-secret"* ]]
}

@test "deploy renders auth proxy when api key secret is configured" {
  run "$DEPLOY_SCRIPT" --route --api-key-secret daedalus-api-key --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"api_key_secret=daedalus-api-key"* ]]
  [[ "$output" == *"auth_proxy_enabled=true"* ]]
  [[ "$output" == *"kind: ConfigMap"* ]]
  [[ "$output" == *"name: daedalus-auth-proxy"* ]]
  [[ "$output" == *"value: \"http://127.0.0.1:8000\""* ]]
  [[ "$output" == *"targetPort: proxy-http"* ]]
  [[ "$output" == *"--host"* ]]
  [[ "$output" == *"127.0.0.1"* ]]
}

@test "deploy rejects create api key secret without a name" {
  run "$DEPLOY_SCRIPT" --create-api-key-secret "" --dry-run

  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: --create-api-key-secret requires a secret name."* ]]
}

@test "deploy rejects newline-bearing api key before creating secret" {
  test_bin="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$test_bin"
  cat > "$test_bin/oc" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "whoami" ]; then
  exit 0
fi
if [ "$1" = "get" ] && [ "$2" = "project" ]; then
  exit 0
fi
printf 'unexpected oc call: %s\n' "$*" >&2
exit 64
STUB
  chmod +x "$test_bin/oc"
  export PATH="$test_bin:$PATH"
  export DAEDALUS_API_KEY=$'line-one\nline-two'

  run "$DEPLOY_SCRIPT" --create-api-key-secret daedalus-api-key --apply --yes

  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: DAEDALUS_API_KEY contains a newline; refusing to create Secret."* ]]
}

@test "deploy rejects create hf secret without a name" {
  run "$DEPLOY_SCRIPT" --create-hf-secret "" --dry-run

  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: --create-hf-secret requires a secret name."* ]]
}

@test "deploy rejects newline-bearing hf token before creating secret" {
  test_bin="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$test_bin"
  cat > "$test_bin/oc" <<'STUB'
#!/usr/bin/env bash
if [ "$1" = "whoami" ]; then
  exit 0
fi
if [ "$1" = "get" ] && [ "$2" = "project" ]; then
  exit 0
fi
printf 'unexpected oc call: %s\n' "$*" >&2
exit 64
STUB
  chmod +x "$test_bin/oc"
  export PATH="$test_bin:$PATH"
  export HF_TOKEN=$'line-one\nline-two'

  run "$DEPLOY_SCRIPT" --create-hf-secret daedalus-hf-token --apply --yes

  [ "$status" -eq 1 ]
  [[ "$output" == *"ERROR: HF_TOKEN contains a newline; refusing to create Secret."* ]]
}

@test "manifest-only dry-run emits yaml without deployment plan header" {
  run "$DEPLOY_SCRIPT" --dry-run --manifest-only

  [ "$status" -eq 0 ]
  [[ "$output" == *"apiVersion: apps/v1"* ]]
  [[ "$output" == *"kind: Service"* ]]
  [[ "$output" != *"Daedalus runtime deploy"* ]]
  [[ "$output" != *"mode=dry-run"* ]]
}
