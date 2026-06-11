#!/usr/bin/env bats

setup() {
  export REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)"
  export SKILL_DIR="$REPO_ROOT/skills/daedalus"
  export RESOURCE_DIR="$REPO_ROOT/runtime/daedalus-vllm"
}

@test "skill metadata names daedalus" {
  run grep -E '^name: daedalus$' "$SKILL_DIR/SKILL.md"
  [ "$status" -eq 0 ]
  grep -q '^description: Use when' "$SKILL_DIR/SKILL.md"
}

@test "installable Daedalus skill stays lean and runtime has entry points" {
  [ -x "$REPO_ROOT/run.me" ]
  [ -x "$REPO_ROOT/scripts/daedalus-code-sanity.py" ]
  [ -x "$REPO_ROOT/scripts/daedalus-latency-probe.py" ]
  [ -x "$SKILL_DIR/scripts/daedalus-client.py" ]
  [ -f "$SKILL_DIR/prompts/review.md" ]
  [ -f "$SKILL_DIR/prompts/pr.md" ]
  [ -f "$SKILL_DIR/prompts/quality-dynamic.md" ]
  [ ! -e "$SKILL_DIR/resources/daedalus-runtime" ]
  [ -f "$RESOURCE_DIR/README.md" ]
  [ -x "$RESOURCE_DIR/run.sh" ]
  [ -x "$RESOURCE_DIR/openai_compat_probe.py" ]
  [ -x "$RESOURCE_DIR/daedalus_smoke.py" ]
  [ -x "$RESOURCE_DIR/deploy-daedalus-openshift.sh" ]
  [ -x "$RESOURCE_DIR/build-deploy-daedalus-openshift.sh" ]
  [ -x "$RESOURCE_DIR/bind-daedalus-public-host-openshift.sh" ]
  [ -f "$RESOURCE_DIR/templates/daedalus-runtime.yaml.tpl" ]
  [ -f "$RESOURCE_DIR/profiles/example-local-gpu.env" ]
  [ -f "$RESOURCE_DIR/profiles/h100-coder.example.env" ]
  [ -f "$RESOURCE_DIR/tests/test_openai_compat_probe.py" ]
}

@test "deploy dry-run uses Daedalus coding review defaults" {
  run "$RESOURCE_DIR/deploy-daedalus-openshift.sh" --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"namespace=daedalus"* ]]
  [[ "$output" == *"app=daedalus"* ]]
  [[ "$output" == *"model=Qwen/Qwen3-Coder-30B-A3B-Instruct"* ]]
  [[ "$output" == *"gpu_node="* ]]
  [[ "$output" == *"cache_mode=pvc"* ]]
  [[ "$output" == *"gpu_count=1"* ]]
  [[ "$output" == *"tensor_parallel_size=1"* ]]
  [[ "$output" == *"cache_size=160Gi"* ]]
  [[ "$output" == *"memory_request=64Gi"* ]]
  [[ "$output" == *"memory_limit=128Gi"* ]]
  [[ "$output" == *"route_enabled=false"* ]]
  [[ "$output" == *"--tensor-parallel-size"* ]]
  [[ "$output" == *"startupProbe:"* ]]
  [[ "$output" == *"failureThreshold: 160"* ]]
  [[ "$output" != *"kind: PersistentVolume"$'\n'"metadata:"* ]]
  [[ "$output" != *"nodeSelector:"* ]]
  [[ "$output" != *"volumeName:"* ]]
  [[ "$output" != *"cai-llm"* ]]
}

@test "build deploy dry-run uses portable coder defaults" {
  run "$RESOURCE_DIR/build-deploy-daedalus-openshift.sh" --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"model=Qwen/Qwen3-Coder-30B-A3B-Instruct"* ]]
  [[ "$output" == *"gpu_node="* ]]
  [[ "$output" == *"cache_mode=pvc"* ]]
  [[ "$output" == *"gpu_count=1"* ]]
  [[ "$output" == *"tensor_parallel_size=1"* ]]
  [[ "$output" == *"cache_size=160Gi"* ]]
  [[ "$output" != *"node-x210"* ]]
  [[ "$output" != *"ww-cai"* ]]
}

@test "example local gpu profile renders explicit node-local cache settings" {
  run "$RESOURCE_DIR/deploy-daedalus-openshift.sh" --profile example-local-gpu --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"gpu_node=gpu-worker-1.example.invalid"* ]]
  [[ "$output" == *"cache_mode=local"* ]]
  [[ "$output" == *"cache_storage_class=local-static-example"* ]]
  [[ "$output" == *"cache_path=/var/lib/daedalus/model-cache"* ]]
  [[ "$output" == *"kind: PersistentVolume"* ]]
  [[ "$output" == *"nodeSelector:"* ]]
  [[ "$output" == *"volumeName: daedalus-model-cache-pv"* ]]
}

@test "h100 coder profile renders route and larger local cache" {
  run "$RESOURCE_DIR/deploy-daedalus-openshift.sh" --profile h100-coder.example --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"gpu_node=replace-with-h100-node-hostname.example.invalid"* ]]
  [[ "$output" == *"cache_pvc=daedalus-model-cache-h100"* ]]
  [[ "$output" == *"cache_size=512Gi"* ]]
  [[ "$output" == *"route_enabled=true"* ]]
  [[ "$output" == *"api_key_secret=daedalus-api-key"* ]]
  [[ "$output" == *"auth_proxy_enabled=true"* ]]
  [[ "$output" == *"kind: Route"* ]]
  [[ "$output" == *"targetPort: proxy-http"* ]]
  [[ "$output" == *"termination: edge"* ]]
}

@test "build deploy passes example profile settings through to deploy dry-run" {
  run "$RESOURCE_DIR/build-deploy-daedalus-openshift.sh" --profile example-local-gpu --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"gpu_node=gpu-worker-1.example.invalid"* ]]
  [[ "$output" == *"cache_mode=local"* ]]
  [[ "$output" == *"cache_storage_class=local-static-example"* ]]
  [[ "$output" == *"cache_path=/var/lib/daedalus/model-cache"* ]]
  [[ "$output" == *"kind: PersistentVolume"* ]]
  [[ "$output" == *"nodeSelector:"* ]]
}

@test "runtime scripts and public docs do not hardcode local lab infrastructure" {
  run rg -n \
    'node-x210|ww-cai|dcloud|198\.18|/var/mnt/daedalus' \
    "$REPO_ROOT/README.md" \
    "$RESOURCE_DIR" \
    "$REPO_ROOT/scripts" \
    "$REPO_ROOT/.gitlab-ci.yml"

  [ "$status" -eq 1 ]
  [ "$output" = "" ]
}

@test "runtime files do not retain old judge or demo references" {
  run rg -n \
    'vLLM Judge Runtime|custom judge image|Judge deployment|judge\.example\.test|deploy-apm-ai-agents|deepeval|Qwen2\.5|ue-3\.ww-cai-cisco-live|cai-llm' \
    "$RESOURCE_DIR/README.md" \
    "$RESOURCE_DIR/deploy-daedalus-openshift.sh" \
    "$RESOURCE_DIR/build-deploy-daedalus-openshift.sh" \
    "$RESOURCE_DIR/bind-daedalus-public-host-openshift.sh" \
    "$RESOURCE_DIR/openai_compat_probe.py" \
    "$RESOURCE_DIR/tests"

  [ "$status" -eq 1 ]
  [ "$output" = "" ]
}

@test "gitlab ci disables auto devops and runs only local validation by default" {
  ci_file="$REPO_ROOT/.gitlab-ci.yml"

  [ -f "$ci_file" ]
  grep -q 'AUTO_DEVOPS_DISABLED: "true"' "$ci_file"
  grep -q 'make test' "$ci_file"
  grep -q 'live-daedalus-smoke' "$ci_file"
  grep -q 'LIVE_OPENSHIFT_SMOKE == "1"' "$ci_file"
  ! grep -q 'docker build' "$ci_file"
  ! grep -q 'oc apply' "$ci_file"
  ! grep -q 'kubectl apply' "$ci_file"
  ! grep -q 'image-registry.openshift-image-registry' "$ci_file"
}

@test "makefile and gitlab live smoke script are present" {
  [ -f "$REPO_ROOT/Makefile" ]
  [ -f "$REPO_ROOT/.env.gitlab.example" ]
  [ -f "$REPO_ROOT/docs/gitlab-live-smoke.md" ]
  [ -f "$REPO_ROOT/docs/repository-sync.md" ]
  [ -x "$REPO_ROOT/scripts/gitlab-live-smoke.sh" ]
  grep -q 'DAEDALUS_RUNTIME_PROFILE_FILE' "$REPO_ROOT/scripts/gitlab-live-smoke.sh"
  grep -q 'KUBECONFIG_FILE' "$REPO_ROOT/scripts/gitlab-live-smoke.sh"
  grep -q 'HF_TOKEN' "$REPO_ROOT/scripts/gitlab-live-smoke.sh"
  grep -q 'HF_TOKEN_FILE' "$REPO_ROOT/scripts/gitlab-live-smoke.sh"
  grep -q 'DAEDALUS_BUILD_INTERNAL_IMAGE' "$REPO_ROOT/scripts/gitlab-live-smoke.sh"
  grep -q 'h100-coder.example.env' "$REPO_ROOT/docs/gitlab-live-smoke.md"
  grep -q 'HF_TOKEN' "$RESOURCE_DIR/profiles/h100-coder.example.env"
}

@test "project docs do not retain unrelated demo stack wording" {
  scan_paths=(
    "$REPO_ROOT/README.md"
    "$REPO_ROOT/docs"
    "$RESOURCE_DIR/README.md"
    "$RESOURCE_DIR/profiles"
  )
  existing_scan_paths=()
  for scan_path in "${scan_paths[@]}"; do
    if [ -e "$scan_path" ]; then
      existing_scan_paths+=("$scan_path")
    fi
  done

  run rg -n \
    'browser capture|video capture|UI demo|Splunk|Cilium|Tetragon|demo-readiness|demo workflow|security simulation|Product-demo|live demo|demo-ready' \
    "${existing_scan_paths[@]}"

  [ "$status" -eq 1 ]
  [ "$output" = "" ]
}
