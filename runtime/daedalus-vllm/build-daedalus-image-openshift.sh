#!/usr/bin/env bash
set -euo pipefail

# Builds the reusable Daedalus runtime image in the OpenShift internal registry.
# The image extends the official vLLM OpenAI-compatible server image and adds
# operator tooling so the same pod can be used as a CUDA/vLLM debug box.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROJECT="daedalus"
IMAGE_STREAM="daedalus"
BASE_IMAGE="vllm/vllm-openai:v0.21.0"
MODE="dry-run"
YES="false"

usage() {
  cat <<EOF
Usage: $0 [--dry-run|--apply] [--yes] [options]

Options:
  --namespace NAME       OpenShift project to use (default: ${PROJECT})
  --image-stream NAME    ImageStream/BuildConfig name (default: ${IMAGE_STREAM})
  --base-image IMAGE     Base image for Dockerfile.daedalus (default: ${BASE_IMAGE})
  --dry-run              Print the build plan
  --apply                Build the image in OpenShift
  --yes                  Required with --apply
  -h, --help             Show this help

After apply, deploy the LLM runtime with:

  ./deploy-daedalus-openshift.sh --apply --yes \\
    --yes-cluster-resources \\
    --image image-registry.openshift-image-registry.svc:5000/${PROJECT}/${IMAGE_STREAM}:latest
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
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

if [[ "${MODE}" == "apply" && "${YES}" != "true" ]]; then
  echo "ERROR: --apply requires --yes" >&2
  exit 1
fi

for f in Dockerfile.daedalus daedalus_smoke.py openai_compat_probe.py; do
  if [[ ! -f "${SCRIPT_DIR}/${f}" ]]; then
    echo "ERROR: Missing required file: ${SCRIPT_DIR}/${f}" >&2
    exit 1
  fi
done

echo "Daedalus runtime image build"
echo "mode=${MODE}"
echo "namespace=${PROJECT}"
echo "image_stream=${IMAGE_STREAM}"
echo "base_image=${BASE_IMAGE}"
echo "output=image-registry.openshift-image-registry.svc:5000/${PROJECT}/${IMAGE_STREAM}:latest"
echo

if [[ "${MODE}" == "dry-run" ]]; then
  echo "Would create/update ImageStream and binary Docker BuildConfig, then start a build."
  exit 0
fi

oc whoami >/dev/null

if ! oc get project "${PROJECT}" >/dev/null 2>&1; then
  oc new-project "${PROJECT}"
fi

oc create imagestream "${IMAGE_STREAM}" -n "${PROJECT}" --dry-run=client -o yaml | oc apply -f -

BUILD_CONTEXT="$(mktemp -d)"
cleanup() {
  rm -rf "${BUILD_CONTEXT}"
}
trap cleanup EXIT

cp "${SCRIPT_DIR}/Dockerfile.daedalus" "${BUILD_CONTEXT}/Dockerfile"
cp "${SCRIPT_DIR}/daedalus_smoke.py" "${BUILD_CONTEXT}/daedalus_smoke.py"
cp "${SCRIPT_DIR}/openai_compat_probe.py" "${BUILD_CONTEXT}/openai_compat_probe.py"

if ! oc get buildconfig "${IMAGE_STREAM}" -n "${PROJECT}" >/dev/null 2>&1; then
  oc new-build --name "${IMAGE_STREAM}" --binary --strategy=docker --to "${IMAGE_STREAM}:latest" -n "${PROJECT}"
fi

oc patch buildconfig "${IMAGE_STREAM}" -n "${PROJECT}" --type=merge -p \
  "{\"spec\":{\"strategy\":{\"dockerStrategy\":{\"buildArgs\":[{\"name\":\"BASE_IMAGE\",\"value\":\"${BASE_IMAGE}\"}]}}}}"

oc start-build "${IMAGE_STREAM}" --from-dir="${BUILD_CONTEXT}" --follow -n "${PROJECT}"

echo
echo "Built image:"
echo "image-registry.openshift-image-registry.svc:5000/${PROJECT}/${IMAGE_STREAM}:latest"
