#!/usr/bin/env bash
set -euo pipefail

# One-shot wrapper: build the reusable Daedalus runtime image,
# deploy it with a portable runtime profile, then optionally run
# lightweight CUDA and OpenAI-compatible endpoint smoke checks.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="${SCRIPT_DIR}/profiles"

CONFIG_FILE="${DAEDALUS_RUNTIME_CONFIG:-}"

PROJECT="daedalus"
IMAGE_STREAM="daedalus"
APP="daedalus"
MODEL="Qwen/Qwen3-Coder-30B-A3B-Instruct"
SERVED_MODEL_NAME=""
GPU_NODE=""
NODE_SELECTOR_KEY="kubernetes.io/hostname"
GPU_COUNT="1"
TENSOR_PARALLEL_SIZE=""
CACHE_MODE="pvc"
CACHE_PV=""
CACHE_PVC=""
CACHE_SIZE="160Gi"
CACHE_STORAGE_CLASS=""
CACHE_PATH=""
MAX_MODEL_LEN="32768"
GPU_MEMORY_UTILIZATION="0.80"
DTYPE="auto"
CPU_REQUEST="8"
CPU_LIMIT="24"
MEMORY_REQUEST="64Gi"
MEMORY_LIMIT="128Gi"
STARTUP_INITIAL_DELAY_SECONDS="30"
STARTUP_PERIOD_SECONDS="15"
STARTUP_FAILURE_THRESHOLD="160"
BASE_IMAGE="vllm/vllm-openai:v0.21.0"
MODE="dry-run"
YES="false"
YES_CLUSTER_RESOURCES="false"
SMOKE="true"
HF_SECRET_NAME=""
HF_CREATE_SECRET="false"
HF_TOKEN_ENV="HF_TOKEN"
API_KEY_SECRET_NAME=""
API_KEY_CREATE_SECRET="false"
API_KEY_ENV_NAME="DAEDALUS_API_KEY"
ROUTE_ENABLED="false"
ROUTE_NAME=""
ROUTE_HOST=""
ROUTE_TLS_TERMINATION="edge"

resolve_profile() {
  local profile="$1"

  if [[ "${profile}" == */* ]]; then
    printf '%s\n' "${profile}"
    return
  fi

  if [[ -f "${PROFILE_DIR}/${profile}" ]]; then
    printf '%s\n' "${PROFILE_DIR}/${profile}"
    return
  fi

  printf '%s\n' "${PROFILE_DIR}/${profile}.env"
}

load_config() {
  local config_file="$1"

  if [[ ! -f "${config_file}" ]]; then
    echo "ERROR: config/profile file not found: ${config_file}" >&2
    exit 1
  fi

  # shellcheck source=/dev/null
  . "${config_file}"
}

usage() {
  cat <<EOF
Usage: $0 [--dry-run|--apply] [--yes] [options]

Portable configuration:
  --config PATH                 Load runtime settings from a shell-style config file
  --profile NAME                Load profiles/NAME.env, or a path if NAME contains /

Build options:
  --namespace NAME              OpenShift project to use (default: ${PROJECT})
  --image-stream NAME           ImageStream/BuildConfig name (default: ${IMAGE_STREAM})
  --base-image IMAGE            Base image for the custom vLLM image (default: ${BASE_IMAGE})

Runtime options:
  --app NAME                    Runtime deployment/service name (default: ${APP})
  --model NAME                  Hugging Face model id (default: ${MODEL})
  --served-model-name NAME      Model name exposed by /v1/models
  --node NAME                   Optional GPU node hostname. Omit to let the scheduler choose.
  --node-selector-key KEY       Node selector key used with --node (default: ${NODE_SELECTOR_KEY})
  --gpu-count N                 Number of GPUs to request (default: ${GPU_COUNT})
  --tensor-parallel-size N      vLLM tensor parallel size (default: GPU count)
  --cache-mode MODE             pvc or local (default: ${CACHE_MODE})
  --cache-size SIZE             PVC/PV size for model cache (default: ${CACHE_SIZE})
  --cache-path PATH             Host path for local cache mode
  --cache-pv NAME               PersistentVolume name for local cache mode
  --cache-pvc NAME              PersistentVolumeClaim name
  --storage-class NAME          StorageClass for PVC/PV
  --max-model-len N             vLLM max context length (default: ${MAX_MODEL_LEN})
  --gpu-memory-utilization N    vLLM GPU memory utilization (default: ${GPU_MEMORY_UTILIZATION})
  --dtype VALUE                 vLLM dtype value (default: ${DTYPE})
  --cpu-request VALUE           CPU request for the runtime pod (default: ${CPU_REQUEST})
  --cpu-limit VALUE             CPU limit for the runtime pod (default: ${CPU_LIMIT})
  --memory-request VALUE        Memory request for the runtime pod (default: ${MEMORY_REQUEST})
  --memory-limit VALUE          Memory limit for the runtime pod (default: ${MEMORY_LIMIT})
  --startup-delay-seconds N     Startup probe initial delay (default: ${STARTUP_INITIAL_DELAY_SECONDS})
  --startup-period-seconds N    Startup probe period (default: ${STARTUP_PERIOD_SECONDS})
  --startup-failure-threshold N Startup probe failure threshold (default: ${STARTUP_FAILURE_THRESHOLD})

Hugging Face authentication:
  --hf-secret NAME              Mount an existing Secret containing HF_TOKEN or HUGGING_FACE_HUB_TOKEN
  --create-hf-secret NAME       Create/update that Secret from an environment variable, then mount it
  --hf-token-env NAME           Environment variable used with --create-hf-secret (default: ${HF_TOKEN_ENV})

Daedalus endpoint authentication:
  --api-key-secret NAME         Mount an existing Secret into the auth proxy and require bearer auth
  --create-api-key-secret NAME  Create/update that Secret from an environment variable, then require bearer auth
  --api-key-env NAME            Environment variable and Secret key used for the API key (default: ${API_KEY_ENV_NAME})

Route exposure:
  --route                       Create/update an OpenShift Route for the service
  --route-name NAME             Route name (default: app name)
  --route-host HOST             Optional explicit Route host; omit to let OpenShift assign one
  --route-tls-termination MODE  TLS termination, edge or empty for plain HTTP (default: ${ROUTE_TLS_TERMINATION})

Execution:
  --yes-cluster-resources       Required when applying local cache mode
  --no-smoke                    Skip post-deploy smoke checks
  --dry-run                     Print the plan
  --apply                       Build, deploy, and optionally smoke test
  --yes                         Required with --apply
  -h, --help                    Show this help
EOF
}

original_args=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --profile)
      CONFIG_FILE="$(resolve_profile "$2")"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
set -- "${original_args[@]}"

if [[ -n "${CONFIG_FILE}" ]]; then
  load_config "${CONFIG_FILE}"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      shift 2
      ;;
    --profile)
      shift 2
      ;;
    --namespace)
      PROJECT="$2"
      shift 2
      ;;
    --image-stream)
      IMAGE_STREAM="$2"
      shift 2
      ;;
    --base-image)
      BASE_IMAGE="$2"
      shift 2
      ;;
    --app)
      APP="$2"
      shift 2
      ;;
    --model)
      MODEL="$2"
      if [[ -z "${SERVED_MODEL_NAME}" ]]; then
        SERVED_MODEL_NAME="$2"
      fi
      shift 2
      ;;
    --served-model-name)
      SERVED_MODEL_NAME="$2"
      shift 2
      ;;
    --node)
      GPU_NODE="$2"
      shift 2
      ;;
    --node-selector-key)
      NODE_SELECTOR_KEY="$2"
      shift 2
      ;;
    --gpu-count)
      GPU_COUNT="$2"
      shift 2
      ;;
    --tensor-parallel-size)
      TENSOR_PARALLEL_SIZE="$2"
      shift 2
      ;;
    --cache-mode)
      CACHE_MODE="$2"
      shift 2
      ;;
    --cache-size)
      CACHE_SIZE="$2"
      shift 2
      ;;
    --cache-path)
      CACHE_PATH="$2"
      shift 2
      ;;
    --cache-pv)
      CACHE_PV="$2"
      shift 2
      ;;
    --cache-pvc)
      CACHE_PVC="$2"
      shift 2
      ;;
    --storage-class)
      CACHE_STORAGE_CLASS="$2"
      shift 2
      ;;
    --max-model-len)
      MAX_MODEL_LEN="$2"
      shift 2
      ;;
    --gpu-memory-utilization)
      GPU_MEMORY_UTILIZATION="$2"
      shift 2
      ;;
    --dtype)
      DTYPE="$2"
      shift 2
      ;;
    --cpu-request)
      CPU_REQUEST="$2"
      shift 2
      ;;
    --cpu-limit)
      CPU_LIMIT="$2"
      shift 2
      ;;
    --memory-request)
      MEMORY_REQUEST="$2"
      shift 2
      ;;
    --memory-limit)
      MEMORY_LIMIT="$2"
      shift 2
      ;;
    --startup-delay-seconds)
      STARTUP_INITIAL_DELAY_SECONDS="$2"
      shift 2
      ;;
    --startup-period-seconds)
      STARTUP_PERIOD_SECONDS="$2"
      shift 2
      ;;
    --startup-failure-threshold)
      STARTUP_FAILURE_THRESHOLD="$2"
      shift 2
      ;;
    --hf-secret)
      HF_SECRET_NAME="$2"
      shift 2
      ;;
    --create-hf-secret)
      HF_SECRET_NAME="$2"
      HF_CREATE_SECRET="true"
      shift 2
      ;;
    --hf-token-env)
      HF_TOKEN_ENV="$2"
      shift 2
      ;;
    --api-key-secret)
      API_KEY_SECRET_NAME="$2"
      shift 2
      ;;
    --create-api-key-secret)
      API_KEY_SECRET_NAME="$2"
      API_KEY_CREATE_SECRET="true"
      shift 2
      ;;
    --api-key-env)
      API_KEY_ENV_NAME="$2"
      shift 2
      ;;
    --route)
      ROUTE_ENABLED="true"
      shift
      ;;
    --route-name)
      ROUTE_NAME="$2"
      ROUTE_ENABLED="true"
      shift 2
      ;;
    --route-host)
      ROUTE_HOST="$2"
      ROUTE_ENABLED="true"
      shift 2
      ;;
    --route-tls-termination)
      ROUTE_TLS_TERMINATION="$2"
      ROUTE_ENABLED="true"
      shift 2
      ;;
    --yes-cluster-resources)
      YES_CLUSTER_RESOURCES="true"
      shift
      ;;
    --no-smoke)
      SMOKE="false"
      shift
      ;;
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --apply)
      MODE="apply"
      shift
      ;;
    --yes)
      YES="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "${CACHE_MODE}" in
  pvc|local)
    ;;
  *)
    echo "ERROR: --cache-mode must be pvc or local" >&2
    exit 1
    ;;
esac

if [[ -z "${SERVED_MODEL_NAME}" ]]; then
  SERVED_MODEL_NAME="${MODEL}"
fi

if [[ -z "${TENSOR_PARALLEL_SIZE}" ]]; then
  TENSOR_PARALLEL_SIZE="${GPU_COUNT}"
fi

if [[ -z "${CACHE_PVC}" ]]; then
  CACHE_PVC="${APP}-model-cache"
fi

if [[ -z "${ROUTE_NAME}" ]]; then
  ROUTE_NAME="${APP}"
fi

if [[ "${CACHE_MODE}" == "local" && -z "${CACHE_PV}" ]]; then
  CACHE_PV="${APP}-model-cache-pv"
fi

if [[ "${MODE}" == "apply" && "${YES}" != "true" ]]; then
  echo "ERROR: --apply requires --yes" >&2
  exit 1
fi

if [[ "${MODE}" == "apply" && "${CACHE_MODE}" == "local" && "${YES_CLUSTER_RESOURCES}" != "true" ]]; then
  echo "ERROR: local cache mode requires --yes-cluster-resources." >&2
  exit 1
fi

INTERNAL_IMAGE="image-registry.openshift-image-registry.svc:5000/${PROJECT}/${IMAGE_STREAM}:latest"

echo "Daedalus runtime build/deploy"
echo "mode=${MODE}"
echo "config=${CONFIG_FILE:-}"
echo "namespace=${PROJECT}"
echo "app=${APP}"
echo "image=${INTERNAL_IMAGE}"
echo "model=${MODEL}"
echo "served_model_name=${SERVED_MODEL_NAME}"
echo "gpu_node=${GPU_NODE:-}"
echo "gpu_count=${GPU_COUNT}"
echo "tensor_parallel_size=${TENSOR_PARALLEL_SIZE}"
echo "cache_mode=${CACHE_MODE}"
echo "cache_pv=${CACHE_PV:-}"
echo "cache_pvc=${CACHE_PVC}"
echo "cache_size=${CACHE_SIZE}"
echo "cache_storage_class=${CACHE_STORAGE_CLASS:-}"
echo "cache_path=${CACHE_PATH:-}"
echo "max_model_len=${MAX_MODEL_LEN}"
echo "gpu_memory_utilization=${GPU_MEMORY_UTILIZATION}"
echo "smoke=${SMOKE}"
echo "hf_secret=${HF_SECRET_NAME:-}"
echo "hf_secret_create=${HF_CREATE_SECRET}"
echo "api_key_secret=${API_KEY_SECRET_NAME:-}"
echo "api_key_secret_create=${API_KEY_CREATE_SECRET}"
echo "api_key_env=${API_KEY_ENV_NAME}"
echo "route_enabled=${ROUTE_ENABLED}"
echo "route_name=${ROUTE_NAME}"
echo "route_host=${ROUTE_HOST:-}"
echo "route_tls_termination=${ROUTE_TLS_TERMINATION:-}"
echo

build_args=(
  --namespace "${PROJECT}"
  --image-stream "${IMAGE_STREAM}"
  --base-image "${BASE_IMAGE}"
)

deploy_args=(
  --namespace "${PROJECT}"
  --app "${APP}"
  --image "${INTERNAL_IMAGE}"
  --model "${MODEL}"
  --served-model-name "${SERVED_MODEL_NAME}"
  --gpu-count "${GPU_COUNT}"
  --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}"
  --cache-mode "${CACHE_MODE}"
  --cache-size "${CACHE_SIZE}"
  --cache-pvc "${CACHE_PVC}"
  --max-model-len "${MAX_MODEL_LEN}"
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}"
  --dtype "${DTYPE}"
  --cpu-request "${CPU_REQUEST}"
  --cpu-limit "${CPU_LIMIT}"
  --memory-request "${MEMORY_REQUEST}"
  --memory-limit "${MEMORY_LIMIT}"
  --startup-delay-seconds "${STARTUP_INITIAL_DELAY_SECONDS}"
  --startup-period-seconds "${STARTUP_PERIOD_SECONDS}"
  --startup-failure-threshold "${STARTUP_FAILURE_THRESHOLD}"
)

if [[ -n "${GPU_NODE}" ]]; then
  deploy_args+=(--node "${GPU_NODE}" --node-selector-key "${NODE_SELECTOR_KEY}")
fi

if [[ -n "${CACHE_PV}" ]]; then
  deploy_args+=(--cache-pv "${CACHE_PV}")
fi

if [[ -n "${CACHE_STORAGE_CLASS}" ]]; then
  deploy_args+=(--storage-class "${CACHE_STORAGE_CLASS}")
fi

if [[ -n "${CACHE_PATH}" ]]; then
  deploy_args+=(--cache-path "${CACHE_PATH}")
fi

if [[ -n "${HF_SECRET_NAME}" ]]; then
  if [[ "${HF_CREATE_SECRET}" == "true" ]]; then
    deploy_args+=(--create-hf-secret "${HF_SECRET_NAME}" --hf-token-env "${HF_TOKEN_ENV}")
  else
    deploy_args+=(--hf-secret "${HF_SECRET_NAME}")
  fi
fi

if [[ -n "${API_KEY_SECRET_NAME}" ]]; then
  if [[ "${API_KEY_CREATE_SECRET}" == "true" ]]; then
    deploy_args+=(--create-api-key-secret "${API_KEY_SECRET_NAME}" --api-key-env "${API_KEY_ENV_NAME}")
  else
    deploy_args+=(--api-key-secret "${API_KEY_SECRET_NAME}" --api-key-env "${API_KEY_ENV_NAME}")
  fi
fi

if [[ "${ROUTE_ENABLED}" == "true" ]]; then
  deploy_args+=(--route --route-name "${ROUTE_NAME}" --route-tls-termination "${ROUTE_TLS_TERMINATION}")
  if [[ -n "${ROUTE_HOST}" ]]; then
    deploy_args+=(--route-host "${ROUTE_HOST}")
  fi
fi

if [[ "${CACHE_MODE}" == "local" && "${YES_CLUSTER_RESOURCES}" == "true" ]]; then
  deploy_args+=(--yes-cluster-resources)
fi

if [[ "${MODE}" == "dry-run" ]]; then
  "${SCRIPT_DIR}/build-daedalus-image-openshift.sh" --dry-run "${build_args[@]}"
  "${SCRIPT_DIR}/deploy-daedalus-openshift.sh" --dry-run "${deploy_args[@]}"
  exit 0
fi

"${SCRIPT_DIR}/build-daedalus-image-openshift.sh" --apply --yes "${build_args[@]}"
"${SCRIPT_DIR}/deploy-daedalus-openshift.sh" --apply --yes "${deploy_args[@]}"

if [[ "${SMOKE}" == "true" ]]; then
  echo
  echo "CUDA smoke:"
  oc -n "${PROJECT}" exec "deploy/${APP}" -- python3 /opt/daedalus/daedalus_smoke.py cuda
  echo
  echo "OpenAI-compatible API smoke:"
  if [[ -n "${API_KEY_SECRET_NAME}" ]]; then
    oc -n "${PROJECT}" exec "deploy/${APP}" -c daedalus-auth-proxy -- python3 -c \
      'import os, urllib.request; token=os.environ["DAEDALUS_API_KEY"]; req=urllib.request.Request("http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer " + token}); print(urllib.request.urlopen(req, timeout=30).read().decode())'
  else
    oc -n "${PROJECT}" run "${APP}-curl" --rm -i --restart=Never --image=curlimages/curl:latest -- \
      curl -s "http://${APP}.${PROJECT}.svc.cluster.local:8000/v1/models"
  fi
fi

echo
echo "Debug shell:"
echo "oc -n ${PROJECT} exec -it deploy/${APP} -- bash"
