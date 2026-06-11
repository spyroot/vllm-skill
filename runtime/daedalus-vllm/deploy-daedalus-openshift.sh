#!/usr/bin/env bash
set -euo pipefail

# Deploys the Daedalus OpenAI-compatible runtime for coding review,
# code-generation, and model-serving experiments.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_DIR="${SCRIPT_DIR}/templates"
PROFILE_DIR="${SCRIPT_DIR}/profiles"

CONFIG_FILE="${DAEDALUS_RUNTIME_CONFIG:-}"

PROJECT="daedalus"
APP="daedalus"
MODEL="Qwen/Qwen3-Coder-30B-A3B-Instruct"
SERVED_MODEL_NAME=""
IMAGE="vllm/vllm-openai:v0.21.0"
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
MODE="dry-run"
YES="false"
YES_CLUSTER_RESOURCES="false"
MANIFEST_ONLY="false"
HF_SECRET_NAME=""
HF_CREATE_SECRET="false"
HF_TOKEN_ENV="HF_TOKEN"
HF_SECRET_ENV_FILE=""
API_KEY_SECRET_NAME=""
API_KEY_CREATE_SECRET="false"
API_KEY_ENV_NAME="DAEDALUS_API_KEY"
API_KEY_SECRET_ENV_FILE=""
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

cleanup() {
  rm -f "${MANIFEST:-}"
  rm -f "${HF_SECRET_ENV_FILE:-}"
  rm -f "${API_KEY_SECRET_ENV_FILE:-}"
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

render_template() {
  local template="$1"
  (
    export PROJECT APP MODEL SERVED_MODEL_NAME IMAGE GPU_NODE NODE_SELECTOR_KEY
    export GPU_COUNT TENSOR_PARALLEL_SIZE CACHE_MODE CACHE_PV CACHE_PVC CACHE_SIZE
    export CACHE_STORAGE_CLASS CACHE_PATH CACHE_PV_MANIFEST CACHE_STORAGE_CLASS_LINE
    export CACHE_VOLUME_NAME_LINE NODE_SELECTOR_BLOCK
    export AUTH_PROXY_CONFIGMAP AUTH_PROXY_CONTAINER AUTH_PROXY_VOLUME SERVICE_TARGET_PORT VLLM_HOST
    export MAX_MODEL_LEN GPU_MEMORY_UTILIZATION DTYPE HF_ENV_FROM
    export CPU_REQUEST CPU_LIMIT MEMORY_REQUEST MEMORY_LIMIT
    export STARTUP_INITIAL_DELAY_SECONDS STARTUP_PERIOD_SECONDS STARTUP_FAILURE_THRESHOLD
    export SECRET_NAME
    export ROUTE_MANIFEST ROUTE_NAME ROUTE_HOST ROUTE_HOST_LINE ROUTE_TLS_BLOCK ROUTE_TLS_TERMINATION
    perl -0pe 's/__([A-Z0-9_]+)__/exists $ENV{$1} ? $ENV{$1} : ""/ge' "${template}"
  )
}

usage() {
  cat <<EOF
Usage: $0 [--dry-run|--apply] [--yes] [options]

Portable configuration:
  --config PATH                 Load runtime settings from a shell-style config file
  --profile NAME                Load profiles/NAME.env, or a path if NAME contains /

Core options:
  --namespace NAME              OpenShift project to use (default: ${PROJECT})
  --app NAME                    Deployment/service name (default: ${APP})
  --model NAME                  Hugging Face model id (default: ${MODEL})
  --served-model-name NAME      Model name exposed by /v1/models
  --image IMAGE                 vLLM OpenAI-compatible image (default: ${IMAGE})
  --gpu-count N                 Number of GPUs to request (default: ${GPU_COUNT})
  --tensor-parallel-size N      vLLM tensor parallel size (default: GPU count)
  --max-model-len N             vLLM max context length (default: ${MAX_MODEL_LEN})
  --gpu-memory-utilization N    vLLM GPU memory utilization (default: ${GPU_MEMORY_UTILIZATION})
  --dtype VALUE                 vLLM dtype value (default: ${DTYPE})

Scheduling and storage:
  --node NAME                   Optional GPU node hostname. Omit to let the scheduler choose.
  --node-selector-key KEY       Node selector key used with --node (default: ${NODE_SELECTOR_KEY})
  --cache-mode MODE             pvc or local (default: ${CACHE_MODE})
  --cache-size SIZE             PVC/PV size for model cache (default: ${CACHE_SIZE})
  --cache-path PATH             Host path for local cache mode
  --cache-pv NAME               PersistentVolume name for local cache mode
  --cache-pvc NAME              PersistentVolumeClaim name (default: <app>-model-cache)
  --storage-class NAME          StorageClass for the PVC/PV. Omit to use the cluster default in pvc mode.

Resources:
  --cpu-request VALUE           CPU request for the runtime pod (default: ${CPU_REQUEST})
  --cpu-limit VALUE             CPU limit for the runtime pod (default: ${CPU_LIMIT})
  --memory-request VALUE        Memory request for the runtime pod (default: ${MEMORY_REQUEST})
  --memory-limit VALUE          Memory limit for the runtime pod (default: ${MEMORY_LIMIT})

Startup probe:
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
  --yes-cluster-resources       Acknowledge cluster-scoped PV and node host-path use in local cache mode
  --dry-run                     Print the generated manifest
  --manifest-only               With --dry-run, print only YAML without the human-readable plan
  --apply                       Deploy the LLM runtime service
  --yes                         Required with --apply
  -h, --help                    Show this help

After this service is Ready, verify the OpenAI-compatible endpoint with:

  python3 ./openai_compat_probe.py \\
    --url http://${APP}.${PROJECT}.svc.cluster.local:8000/v1 \\
    --model "${SERVED_MODEL_NAME:-${MODEL}}"
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
    --image)
      IMAGE="$2"
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
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --manifest-only)
      MANIFEST_ONLY="true"
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

if [[ "${CACHE_MODE}" == "local" ]]; then
  if [[ -z "${CACHE_PV}" ]]; then
    CACHE_PV="${APP}-model-cache-pv"
  fi
  if [[ -z "${GPU_NODE}" ]]; then
    echo "ERROR: --cache-mode local requires --node or GPU_NODE in a profile." >&2
    exit 1
  fi
  if [[ -z "${CACHE_PATH}" ]]; then
    echo "ERROR: --cache-mode local requires --cache-path or CACHE_PATH in a profile." >&2
    exit 1
  fi
  if [[ -z "${CACHE_STORAGE_CLASS}" ]]; then
    echo "ERROR: --cache-mode local requires --storage-class or CACHE_STORAGE_CLASS in a profile." >&2
    exit 1
  fi
else
  if [[ -n "${CACHE_PV}" ]]; then
    echo "ERROR: --cache-pv is only valid with --cache-mode local." >&2
    exit 1
  fi
  if [[ -n "${CACHE_PATH}" ]]; then
    echo "ERROR: --cache-path is only valid with --cache-mode local." >&2
    exit 1
  fi
fi

if [[ "${MODE}" == "apply" && "${YES}" != "true" ]]; then
  echo "ERROR: --apply requires --yes" >&2
  exit 1
fi

if [[ "${MODE}" == "apply" && "${CACHE_MODE}" == "local" && "${YES_CLUSTER_RESOURCES}" != "true" ]]; then
  echo "ERROR: local cache mode creates a cluster-scoped PersistentVolume and a node-local cache path." >&2
  echo "Re-run with --yes-cluster-resources after confirming this is intended for the target GPU node." >&2
  exit 1
fi

if [[ "${HF_CREATE_SECRET}" == "true" && -z "${HF_SECRET_NAME}" ]]; then
  echo "ERROR: --create-hf-secret requires a secret name." >&2
  exit 1
fi

if [[ "${API_KEY_CREATE_SECRET}" == "true" && -z "${API_KEY_SECRET_NAME}" ]]; then
  echo "ERROR: --create-api-key-secret requires a secret name." >&2
  exit 1
fi

if [[ "${ROUTE_ENABLED}" == "true" && -z "${API_KEY_SECRET_NAME}" ]]; then
  echo "ERROR: --route requires --api-key-secret or --create-api-key-secret so the public endpoint is not anonymous." >&2
  exit 1
fi

for f in daedalus-runtime.yaml.tpl envfrom-secret.yaml.tpl; do
  if [[ ! -f "${TEMPLATE_DIR}/${f}" ]]; then
    echo "ERROR: Missing required template: ${TEMPLATE_DIR}/${f}" >&2
    exit 1
  fi
done

if ! command -v perl >/dev/null 2>&1; then
  echo "BLOCKER: perl is required to render Daedalus YAML templates." >&2
  echo "Safe next step: install perl in the local or CI runtime, or use an image that includes perl." >&2
  exit 78
fi

MANIFEST="$(mktemp)"
trap cleanup EXIT

CACHE_PV_MANIFEST=""
CACHE_STORAGE_CLASS_LINE=""
CACHE_VOLUME_NAME_LINE=""
NODE_SELECTOR_BLOCK=""
HF_ENV_FROM=""
AUTH_PROXY_CONFIGMAP=""
AUTH_PROXY_CONTAINER=""
AUTH_PROXY_VOLUME=""
SERVICE_TARGET_PORT="http"
VLLM_HOST="0.0.0.0"
SECRET_NAME=""
ROUTE_MANIFEST=""
ROUTE_HOST_LINE=""
ROUTE_TLS_BLOCK=""

if [[ -n "${CACHE_STORAGE_CLASS}" ]]; then
  CACHE_STORAGE_CLASS_LINE="  storageClassName: ${CACHE_STORAGE_CLASS}"
fi

if [[ "${CACHE_MODE}" == "local" ]]; then
  CACHE_VOLUME_NAME_LINE="  volumeName: ${CACHE_PV}"
  CACHE_PV_MANIFEST="$(cat <<EOF
apiVersion: v1
kind: PersistentVolume
metadata:
  name: ${CACHE_PV}
  labels:
    app: ${APP}
    app.kubernetes.io/name: ${APP}
    app.kubernetes.io/part-of: daedalus
    app.kubernetes.io/managed-by: daedalus-skill
    app.kubernetes.io/component: runtime
spec:
  capacity:
    storage: ${CACHE_SIZE}
  accessModes:
    - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ${CACHE_STORAGE_CLASS}
  volumeMode: Filesystem
  local:
    path: ${CACHE_PATH}
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: ${NODE_SELECTOR_KEY}
              operator: In
              values:
                - ${GPU_NODE}
---
EOF
)"
fi

if [[ -n "${GPU_NODE}" ]]; then
  NODE_SELECTOR_BLOCK="$(cat <<EOF
      nodeSelector:
        ${NODE_SELECTOR_KEY}: ${GPU_NODE}
EOF
)"
fi

if [[ -n "${HF_SECRET_NAME}" ]]; then
  SECRET_NAME="${HF_SECRET_NAME}"
  HF_ENV_FROM="$(render_template "${TEMPLATE_DIR}/envfrom-secret.yaml.tpl")"
fi

if [[ -n "${API_KEY_SECRET_NAME}" ]]; then
  AUTH_PROXY_SCRIPT="$(
    while IFS= read -r line; do
      printf '    %s\n' "${line}"
    done < "${SCRIPT_DIR}/daedalus_auth_proxy.py"
  )"
  AUTH_PROXY_CONFIGMAP="$(cat <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${APP}-auth-proxy
  namespace: ${PROJECT}
  labels:
    app: ${APP}
    app.kubernetes.io/name: ${APP}
    app.kubernetes.io/part-of: daedalus
    app.kubernetes.io/managed-by: daedalus-skill
    app.kubernetes.io/component: auth-proxy
data:
  daedalus_auth_proxy.py: |
${AUTH_PROXY_SCRIPT}
---
EOF
)"
  AUTH_PROXY_CONTAINER="$(cat <<EOF
        - name: daedalus-auth-proxy
          image: ${IMAGE}
          imagePullPolicy: IfNotPresent
          env:
            - name: ${API_KEY_ENV_NAME}
              valueFrom:
                secretKeyRef:
                  name: ${API_KEY_SECRET_NAME}
                  key: ${API_KEY_ENV_NAME}
            - name: DAEDALUS_AUTH_PROXY_API_KEY_ENV
              value: ${API_KEY_ENV_NAME}
            - name: DAEDALUS_AUTH_PROXY_LISTEN_HOST
              value: "0.0.0.0"
            - name: DAEDALUS_AUTH_PROXY_PORT
              value: "8080"
            - name: DAEDALUS_AUTH_PROXY_UPSTREAM
              value: "http://127.0.0.1:8000"
          command:
            - python3
            - /opt/daedalus-auth-proxy/daedalus_auth_proxy.py
          ports:
            - containerPort: 8080
              name: proxy-http
          readinessProbe:
            exec:
              command:
                - python3
                - -c
                - import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read()
            initialDelaySeconds: 15
            periodSeconds: 10
            failureThreshold: 12
          livenessProbe:
            tcpSocket:
              port: proxy-http
            initialDelaySeconds: 30
            periodSeconds: 30
            failureThreshold: 4
          volumeMounts:
            - name: auth-proxy
              mountPath: /opt/daedalus-auth-proxy
              readOnly: true
          resources:
            requests:
              cpu: "100m"
              memory: 128Mi
            limits:
              cpu: "1"
              memory: 512Mi
EOF
)"
  AUTH_PROXY_VOLUME="$(cat <<EOF
        - name: auth-proxy
          configMap:
            name: ${APP}-auth-proxy
            defaultMode: 0555
EOF
)"
  SERVICE_TARGET_PORT="proxy-http"
  VLLM_HOST="127.0.0.1"
fi

if [[ "${ROUTE_ENABLED}" == "true" ]]; then
  if [[ -n "${ROUTE_HOST}" ]]; then
    ROUTE_HOST_LINE="  host: ${ROUTE_HOST}"
  fi
  if [[ -n "${ROUTE_TLS_TERMINATION}" ]]; then
    ROUTE_TLS_BLOCK="$(cat <<EOF
  tls:
    termination: ${ROUTE_TLS_TERMINATION}
    insecureEdgeTerminationPolicy: Redirect
EOF
)"
  fi
  ROUTE_MANIFEST="$(cat <<EOF
---
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: ${ROUTE_NAME}
  namespace: ${PROJECT}
  labels:
    app: ${APP}
    app.kubernetes.io/name: ${APP}
    app.kubernetes.io/part-of: daedalus
    app.kubernetes.io/managed-by: daedalus-skill
    app.kubernetes.io/component: runtime
spec:
${ROUTE_HOST_LINE}
  to:
    kind: Service
    name: ${APP}
    weight: 100
  port:
    targetPort: http
${ROUTE_TLS_BLOCK}
EOF
)"
fi

render_template "${TEMPLATE_DIR}/daedalus-runtime.yaml.tpl" > "${MANIFEST}"

if [[ "${MODE}" == "dry-run" && "${MANIFEST_ONLY}" == "true" ]]; then
  cat "${MANIFEST}"
  exit 0
fi

echo "Daedalus runtime deploy"
echo "mode=${MODE}"
echo "config=${CONFIG_FILE:-}"
echo "namespace=${PROJECT}"
echo "app=${APP}"
echo "model=${MODEL}"
echo "served_model_name=${SERVED_MODEL_NAME}"
echo "image=${IMAGE}"
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
echo "cpu_request=${CPU_REQUEST}"
echo "cpu_limit=${CPU_LIMIT}"
echo "memory_request=${MEMORY_REQUEST}"
echo "memory_limit=${MEMORY_LIMIT}"
echo "startup_probe=${STARTUP_INITIAL_DELAY_SECONDS}+${STARTUP_PERIOD_SECONDS}x${STARTUP_FAILURE_THRESHOLD}"
echo "hf_secret=${HF_SECRET_NAME:-}"
echo "hf_secret_create=${HF_CREATE_SECRET}"
echo "api_key_secret=${API_KEY_SECRET_NAME:-}"
echo "api_key_secret_create=${API_KEY_CREATE_SECRET}"
echo "api_key_env=${API_KEY_ENV_NAME}"
echo "auth_proxy_enabled=$([[ -n "${API_KEY_SECRET_NAME}" ]] && echo true || echo false)"
echo "route_enabled=${ROUTE_ENABLED}"
echo "route_name=${ROUTE_NAME}"
echo "route_host=${ROUTE_HOST:-}"
echo "route_tls_termination=${ROUTE_TLS_TERMINATION:-}"
echo

if [[ "${MODE}" == "dry-run" ]]; then
  cat "${MANIFEST}"
  exit 0
fi

oc whoami >/dev/null

if ! oc get project "${PROJECT}" >/dev/null 2>&1; then
  oc new-project "${PROJECT}"
fi

if [[ "${HF_CREATE_SECRET}" == "true" ]]; then
  hf_token="${!HF_TOKEN_ENV-}"
  if [[ -z "${hf_token}" ]]; then
    echo "ERROR: ${HF_TOKEN_ENV} is empty or unset; cannot create Hugging Face Secret." >&2
    exit 1
  fi
  case "${hf_token}" in
    *$'\n'*)
      echo "ERROR: ${HF_TOKEN_ENV} contains a newline; refusing to create Secret." >&2
      exit 1
      ;;
  esac

  HF_SECRET_ENV_FILE="$(mktemp)"
  chmod 0600 "${HF_SECRET_ENV_FILE}"
  {
    printf 'HF_TOKEN=%s\n' "${hf_token}"
    printf 'HUGGING_FACE_HUB_TOKEN=%s\n' "${hf_token}"
  } > "${HF_SECRET_ENV_FILE}"
  oc -n "${PROJECT}" create secret generic "${HF_SECRET_NAME}" \
    --from-env-file="${HF_SECRET_ENV_FILE}" \
    --dry-run=client \
    -o yaml | oc apply -f -
fi

if [[ "${API_KEY_CREATE_SECRET}" == "true" ]]; then
  api_key_token="${!API_KEY_ENV_NAME-}"
  if [[ -z "${api_key_token}" ]]; then
    echo "ERROR: ${API_KEY_ENV_NAME} is empty or unset; cannot create Daedalus API key Secret." >&2
    exit 1
  fi
  case "${api_key_token}" in
    *$'\n'*)
      echo "ERROR: ${API_KEY_ENV_NAME} contains a newline; refusing to create Secret." >&2
      exit 1
      ;;
  esac

  API_KEY_SECRET_ENV_FILE="$(mktemp)"
  chmod 0600 "${API_KEY_SECRET_ENV_FILE}"
  printf '%s=%s\n' "${API_KEY_ENV_NAME}" "${api_key_token}" > "${API_KEY_SECRET_ENV_FILE}"
  oc -n "${PROJECT}" create secret generic "${API_KEY_SECRET_NAME}" \
    --from-env-file="${API_KEY_SECRET_ENV_FILE}" \
    --dry-run=client \
    -o yaml | oc apply -f -
fi

if [[ "${CACHE_MODE}" == "local" ]]; then
  oc debug "node/${GPU_NODE}" --quiet -- chroot /host sh -c "mkdir -p '${CACHE_PATH}' && chmod 0777 '${CACHE_PATH}'"
fi

oc apply -f "${MANIFEST}"
oc rollout status "deploy/${APP}" -n "${PROJECT}" --timeout=1200s

echo
echo "Daedalus runtime service:"
echo "http://${APP}.${PROJECT}.svc.cluster.local:8000/v1"
if [[ "${ROUTE_ENABLED}" == "true" ]]; then
  route_host="$(oc -n "${PROJECT}" get route "${ROUTE_NAME}" -o jsonpath='{.spec.host}')"
  route_scheme="http"
  if [[ -n "${ROUTE_TLS_TERMINATION}" ]]; then
    route_scheme="https"
  fi
  echo "${route_scheme}://${route_host}/v1"
fi
echo
echo "Verify from inside the cluster:"
echo "oc -n ${PROJECT} run ${APP}-curl --rm -i --restart=Never --image=curlimages/curl:latest -- \\"
if [[ -n "${API_KEY_SECRET_NAME}" ]]; then
  echo "  # export ${API_KEY_ENV_NAME}=<token from the approved Secret source first>"
  echo "  curl -s -H \"Authorization: Bearer \${${API_KEY_ENV_NAME}}\" http://${APP}.${PROJECT}.svc.cluster.local:8000/v1/models"
else
  echo "  curl -s http://${APP}.${PROJECT}.svc.cluster.local:8000/v1/models"
fi
echo
echo "Run the OpenAI-compatible probe from a trusted environment:"
echo "python3 ./openai_compat_probe.py \\"
echo "  --url http://${APP}.${PROJECT}.svc.cluster.local:8000/v1 \\"
echo "  --model \"${SERVED_MODEL_NAME}\""
