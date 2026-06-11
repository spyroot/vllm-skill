#!/usr/bin/env bash
set -euo pipefail

# Adds a custom OpenShift Route host for Daedalus. DNS should point this host
# at the same external ingress IP used by existing OpenShift app routes.

NAMESPACE="${NAMESPACE:-daedalus}"
APP="${APP:-daedalus}"
ROUTE_NAME="${ROUTE_NAME:-daedalus-public}"
HOST="${HOST:-}"
PORT="${PORT:-http}"
MODE="${MODE:-dry-run}"

usage() {
  cat <<EOF
Usage:
  ./bind-daedalus-public-host-openshift.sh [--dry-run|--apply|--delete] [options]

Options:
  --dry-run              Render/check the custom Route without changing the cluster. Default.
  --apply                Create/update the custom Route.
  --delete               Delete only the custom Route.
  --namespace NAME       Namespace containing Daedalus. Default: ${NAMESPACE}
  --app NAME             Service name to route to. Default: ${APP}
  --route-name NAME      Route object name. Default: ${ROUTE_NAME}
  --host HOSTNAME        Public hostname. Required unless HOST is exported.
  --port PORT            Service port name. Default: ${PORT}
  -h, --help             Show this help.

DNS:
  Create an A record for the chosen host pointing at the lab ingress IP.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --apply)
      MODE="apply"
      shift
      ;;
    --delete)
      MODE="delete"
      shift
      ;;
    --namespace)
      NAMESPACE="$2"
      shift 2
      ;;
    --app)
      APP="$2"
      shift 2
      ;;
    --route-name)
      ROUTE_NAME="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "${MODE}" in
  dry-run|apply|delete)
    ;;
  *)
    echo "ERROR: MODE must be dry-run, apply, or delete. Got: ${MODE}" >&2
    exit 1
    ;;
esac

if [[ -z "${HOST}" ]]; then
  echo "ERROR: --host is required, for example --host daedalus.<demo-domain>." >&2
  usage >&2
  exit 1
fi

echo "Daedalus public host"
echo "namespace=${NAMESPACE}"
echo "service=${APP}"
echo "route=${ROUTE_NAME}"
echo "host=${HOST}"
echo "port=${PORT}"
echo "mode=${MODE}"
echo

if [[ "${MODE}" == "delete" ]]; then
  oc -n "${NAMESPACE}" delete route "${ROUTE_NAME}" --ignore-not-found=true
  exit 0
fi

oc -n "${NAMESPACE}" get svc "${APP}" >/dev/null

if [[ "${MODE}" == "dry-run" ]]; then
  oc -n "${NAMESPACE}" create route edge "${ROUTE_NAME}" \
    --service="${APP}" \
    --port="${PORT}" \
    --hostname="${HOST}" \
    --insecure-policy=Redirect \
    --dry-run=client \
    -o yaml >/dev/null
  echo "Dry-run OK"
  echo
  echo "After DNS is created:"
  echo "  curl -kI https://${HOST}/v1/models"
  echo "  ./run.me --base-url https://${HOST}/v1 --insecure-skip-tls-verify"
  exit 0
fi

oc -n "${NAMESPACE}" create route edge "${ROUTE_NAME}" \
  --service="${APP}" \
  --port="${PORT}" \
  --hostname="${HOST}" \
  --insecure-policy=Redirect \
  --dry-run=client \
  -o yaml | oc apply -f -

echo
echo "Public Route bound:"
echo "  https://${HOST}/v1"
echo
echo "DNS reminder:"
echo "  ${HOST} A <LAB_INGRESS_IP>"
echo
echo "Check:"
echo "  oc -n ${NAMESPACE} get route ${ROUTE_NAME}"
echo "  curl -kI https://${HOST}/v1/models"
echo "  ./run.me --base-url https://${HOST}/v1 --insecure-skip-tls-verify"
