#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
runtime_dir="${repo_dir}/runtime/daedalus-vllm"
report_dir="${REPORT_DIR:-${repo_dir}/reports/live-smoke}"

tmp_profile=""
tmp_kubeconfig=""

cleanup() {
  rm -f "${tmp_profile:-}" "${tmp_kubeconfig:-}"
}
trap cleanup EXIT

blocker() {
  printf 'BLOCKER: %s\n' "$1" >&2
  exit "${2:-78}"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || blocker "$1 is required for live GitLab smoke tests."
}

prepare_kubeconfig() {
  if [ -n "${KUBECONFIG_FILE:-}" ]; then
    [ -r "${KUBECONFIG_FILE}" ] || blocker "KUBECONFIG_FILE is set but is not readable."
    export KUBECONFIG="${KUBECONFIG_FILE}"
    return
  fi

  if [ -n "${KUBECONFIG_B64:-}" ]; then
    require_command base64
    tmp_kubeconfig="$(mktemp "${TMPDIR:-/tmp}/daedalus-kubeconfig.XXXXXX")"
    chmod 0600 "${tmp_kubeconfig}"
    printf '%s' "${KUBECONFIG_B64}" | base64 --decode > "${tmp_kubeconfig}" ||
      blocker "KUBECONFIG_B64 could not be decoded."
    export KUBECONFIG="${tmp_kubeconfig}"
  fi
}

prepare_profile() {
  if [ -n "${DAEDALUS_RUNTIME_PROFILE_FILE:-}" ]; then
    [ -r "${DAEDALUS_RUNTIME_PROFILE_FILE}" ] ||
      blocker "DAEDALUS_RUNTIME_PROFILE_FILE is set but is not readable."
    printf '%s\n' "${DAEDALUS_RUNTIME_PROFILE_FILE}"
    return
  fi

  if [ -n "${DAEDALUS_RUNTIME_PROFILE_B64:-}" ]; then
    require_command base64
    tmp_profile="$(mktemp "${TMPDIR:-/tmp}/daedalus-runtime-profile.XXXXXX")"
    chmod 0600 "${tmp_profile}"
    printf '%s' "${DAEDALUS_RUNTIME_PROFILE_B64}" | base64 --decode > "${tmp_profile}" ||
      blocker "DAEDALUS_RUNTIME_PROFILE_B64 could not be decoded."
    printf '%s\n' "${tmp_profile}"
    return
  fi

  if [ -n "${DAEDALUS_RUNTIME_PROFILE:-}" ]; then
    tmp_profile="$(mktemp "${TMPDIR:-/tmp}/daedalus-runtime-profile.XXXXXX")"
    chmod 0600 "${tmp_profile}"
    printf '%s\n' "${DAEDALUS_RUNTIME_PROFILE}" > "${tmp_profile}"
    printf '%s\n' "${tmp_profile}"
    return
  fi

  blocker "Set DAEDALUS_RUNTIME_PROFILE_FILE, DAEDALUS_RUNTIME_PROFILE_B64, or DAEDALUS_RUNTIME_PROFILE."
}

prepare_hf_token() {
  if [ -n "${HF_TOKEN_FILE:-}" ]; then
    [ -r "${HF_TOKEN_FILE}" ] || blocker "HF_TOKEN_FILE is set but is not readable."
    HF_TOKEN="$(tr -d '\r\n' < "${HF_TOKEN_FILE}")"
    export HF_TOKEN
  fi

  if [ -n "${HUGGING_FACE_HUB_TOKEN_FILE:-}" ] && [ -z "${HF_TOKEN:-}" ]; then
    [ -r "${HUGGING_FACE_HUB_TOKEN_FILE}" ] ||
      blocker "HUGGING_FACE_HUB_TOKEN_FILE is set but is not readable."
    HF_TOKEN="$(tr -d '\r\n' < "${HUGGING_FACE_HUB_TOKEN_FILE}")"
    export HF_TOKEN
  fi

  if [ -n "${HF_TOKEN:-}" ] && [ "${#HF_TOKEN}" -lt 8 ]; then
    blocker "HF_TOKEN is set but looks too short."
  fi
}

prepare_daedalus_api_key() {
  if [ -n "${DAEDALUS_API_KEY_FILE:-}" ]; then
    [ -r "${DAEDALUS_API_KEY_FILE}" ] || blocker "DAEDALUS_API_KEY_FILE is set but is not readable."
    DAEDALUS_API_KEY="$(tr -d '\r\n' < "${DAEDALUS_API_KEY_FILE}")"
    export DAEDALUS_API_KEY
  fi

  if [ -n "${DAEDALUS_API_KEY:-}" ] && [ "${#DAEDALUS_API_KEY}" -lt 24 ]; then
    blocker "DAEDALUS_API_KEY is set but looks too short."
  fi
}

probe_models() {
  local namespace="$1"
  local app="$2"
  local output_file="$3"
  local auth_enabled="${4:-false}"

  if [ "${auth_enabled}" = "true" ]; then
    oc -n "${namespace}" exec "deploy/${app}" -c daedalus-auth-proxy -- python3 -c \
      'import os, urllib.request; token=os.environ["DAEDALUS_API_KEY"]; req=urllib.request.Request("http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer " + token}); print(urllib.request.urlopen(req, timeout=30).read().decode())' \
      > "${output_file}"
  else
    oc -n "${namespace}" exec "deploy/${app}" -- python3 -c \
      'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=30).read().decode())' \
      > "${output_file}"
  fi

  grep -q '"data"' "${output_file}" || blocker "OpenAI-compatible /v1/models response did not include data."
}

probe_chat() {
  local namespace="$1"
  local app="$2"
  local model="$3"
  local output_file="$4"
  local auth_enabled="${5:-false}"

  if [ "${auth_enabled}" = "true" ]; then
    oc -n "${namespace}" exec "deploy/${app}" -c daedalus-auth-proxy -- python3 -c \
      'import json, os, sys, urllib.request; token=os.environ["DAEDALUS_API_KEY"]; payload={"model":sys.argv[1],"messages":[{"role":"user","content":"Write a Python function add(a, b) returning their sum. Return only code."}],"max_tokens":96,"temperature":0}; data=json.dumps(payload).encode(); req=urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions", data=data, headers={"Content-Type":"application/json","Authorization":"Bearer "+token}); print(urllib.request.urlopen(req, timeout=120).read().decode())' \
      "${model}" > "${output_file}"
  else
    oc -n "${namespace}" exec "deploy/${app}" -- python3 -c \
      'import json, sys, urllib.request; payload={"model":sys.argv[1],"messages":[{"role":"user","content":"Write a Python function add(a, b) returning their sum. Return only code."}],"max_tokens":96,"temperature":0}; data=json.dumps(payload).encode(); req=urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions", data=data, headers={"Content-Type":"application/json"}); print(urllib.request.urlopen(req, timeout=120).read().decode())' \
      "${model}" > "${output_file}"
  fi

  grep -q '"choices"' "${output_file}" || blocker "OpenAI-compatible chat response did not include choices."
}

require_command oc
require_command grep
require_command perl
mkdir -p "${report_dir}"

prepare_kubeconfig
profile_config="$(prepare_profile)"
prepare_hf_token
prepare_daedalus_api_key

# shellcheck source=/dev/null
. "${profile_config}"

project="${PROJECT:-daedalus}"
app="${APP:-daedalus}"
served_model_name="${SERVED_MODEL_NAME:-${MODEL:-}}"
cache_mode="${CACHE_MODE:-pvc}"
route_enabled="${ROUTE_ENABLED:-false}"
api_key_secret_name="${API_KEY_SECRET_NAME:-${DAEDALUS_API_KEY_SECRET_NAME:-}}"
if [ "${route_enabled}" = "true" ] && [ -z "${api_key_secret_name}" ]; then
  api_key_secret_name="${DAEDALUS_API_KEY_SECRET_NAME:-daedalus-api-key}"
fi
[ -n "${served_model_name}" ] || blocker "Profile must set MODEL or SERVED_MODEL_NAME."

cluster_server="$(oc whoami --show-server)"
current_project="$(oc project)"

printf 'Cluster server: %s\n' "${cluster_server}"
printf 'Current project before deploy: %s\n' "${current_project}"
{
  if [ "${KEEP_CONTEXT:-0}" = "1" ]; then
    printf 'Cluster server: %s\n' "${cluster_server}"
  else
    printf 'Cluster server: <redacted; set KEEP_CONTEXT=1 to retain raw server in artifacts>\n'
  fi
  printf 'Current project before deploy: %s\n' "${current_project}"
  printf 'Target namespace: %s\nTarget app: %s\n' "${project}" "${app}"
} > "${report_dir}/context.txt"

deploy_args=(--config "${profile_config}" --apply --yes)
if [ "${cache_mode}" = "local" ]; then
  deploy_args+=(--yes-cluster-resources)
fi
if [ -n "${HF_TOKEN:-}" ]; then
  deploy_args+=(--create-hf-secret "${DAEDALUS_HF_SECRET_NAME:-daedalus-hf-token}" --hf-token-env HF_TOKEN)
fi
if [ -n "${api_key_secret_name}" ]; then
  if [ -n "${DAEDALUS_API_KEY:-}" ]; then
    deploy_args+=(--create-api-key-secret "${api_key_secret_name}" --api-key-env DAEDALUS_API_KEY)
  else
    deploy_args+=(--api-key-secret "${api_key_secret_name}" --api-key-env DAEDALUS_API_KEY)
  fi
fi

if [ "${DAEDALUS_BUILD_INTERNAL_IMAGE:-0}" = "1" ]; then
  deploy_cmd="${runtime_dir}/build-deploy-daedalus-openshift.sh"
  deploy_args+=(--no-smoke)
else
  deploy_cmd="${runtime_dir}/deploy-daedalus-openshift.sh"
fi

"${deploy_cmd}" "${deploy_args[@]}" 2>&1 |
  tee "${report_dir}/deploy.log"

auth_enabled="false"
[ -n "${api_key_secret_name}" ] && auth_enabled="true"
probe_models "${project}" "${app}" "${report_dir}/models.json" "${auth_enabled}"
probe_chat "${project}" "${app}" "${served_model_name}" "${report_dir}/chat-completion.json" "${auth_enabled}"

printf 'Live Daedalus smoke passed. Reports: %s\n' "${report_dir}"
