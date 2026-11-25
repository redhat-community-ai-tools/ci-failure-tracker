#!/usr/bin/env bash
#
# Deploy the POC dashboard from the current checkout.
#
# Refreshes the ConfigMap, builds the image, tags it :latest, rolls the
# deployment and verifies that the running pod uses the new digest.
#
# The deployed configuration is the working-tree copy of
# dashboard/config.yaml, not master's copy. Commit or stash local config
# edits before deploying if that is not what you intend.
#
# Overridable environment variables:
#   NAMESPACE        target namespace            (default winc-dashboard-poc)
#   BUILD_CONFIG     BuildConfig name            (default winc-dashboard-poc)
#   IMAGE_STREAM     ImageStream name            (default winc-dashboard-poc)
#   CONFIGMAP        ConfigMap name              (default dashboard-config-poc)
#   DEPLOYMENT       Deployment name             (default winc-dashboard-poc)
#   CONFIG_FILE      config to publish           (default dashboard/config.yaml)
#   BUILD_TIMEOUT    seconds to wait for build   (default 600)
#   ROLLOUT_TIMEOUT  rollout status timeout      (default 180s)
#   EXPECTED_SERVER  substring of the expected API server, as a guard
#   ROUTE_NAME       Route to print on success   (default empty)
#   SKIP_CONFIG=1    deploy code only, leave the ConfigMap untouched
#
# Usage: scripts/deploy-poc.sh

set -euo pipefail

NAMESPACE="${NAMESPACE:-winc-dashboard-poc}"
BUILD_CONFIG="${BUILD_CONFIG:-winc-dashboard-poc}"
IMAGE_STREAM="${IMAGE_STREAM:-winc-dashboard-poc}"
CONFIGMAP="${CONFIGMAP:-dashboard-config-poc}"
DEPLOYMENT="${DEPLOYMENT:-winc-dashboard-poc}"
CONFIG_FILE="${CONFIG_FILE:-dashboard/config.yaml}"
BUILD_TIMEOUT="${BUILD_TIMEOUT:-600}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"
EXPECTED_SERVER="${EXPECTED_SERVER:-}"
ROUTE_NAME="${ROUTE_NAME:-}"
SKIP_CONFIG="${SKIP_CONFIG:-0}"

die() {
    echo "error: $*" >&2
    exit 1
}

command -v oc >/dev/null 2>&1 || die "oc is not installed or not on PATH"

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)" \
    || die "could not find the repository root from $(dirname "$0")"
cd "$REPO_ROOT"

[[ -f "$CONFIG_FILE" ]] || die "config file not found: $CONFIG_FILE"

SERVER="$(oc whoami --show-server 2>/dev/null)" \
    || die "not logged in to a cluster (run oc login first)"
USER_NAME="$(oc whoami 2>/dev/null || echo unknown)"

echo "=== POC dashboard deployment ==="
echo "  cluster:   $SERVER"
echo "  user:      $USER_NAME"
echo "  namespace: $NAMESPACE"
echo "  config:    $CONFIG_FILE (working tree)"
echo "  build:     $BUILD_CONFIG"
echo

if [[ -n "$EXPECTED_SERVER" && "$SERVER" != *"$EXPECTED_SERVER"* ]]; then
    die "connected to $SERVER but expected an API server containing '$EXPECTED_SERVER'"
fi

if [[ "$SKIP_CONFIG" == "1" ]]; then
    echo "Step 1/5: skip ConfigMap refresh (SKIP_CONFIG=1)"
else
    echo "Step 1/5: refresh ConfigMap $CONFIGMAP"
    oc -n "$NAMESPACE" create configmap "$CONFIGMAP" \
        --from-file="config.yaml=$CONFIG_FILE" \
        --dry-run=client -o yaml | oc -n "$NAMESPACE" apply -f -
fi

echo "Step 2/5: start build $BUILD_CONFIG"
BUILD="$(oc -n "$NAMESPACE" start-build "$BUILD_CONFIG" -o name)"
echo "  started $BUILD"

echo "Step 3/5: wait for build (timeout ${BUILD_TIMEOUT}s)"
deadline=$((SECONDS + BUILD_TIMEOUT))
while :; do
    phase="$(oc -n "$NAMESPACE" get "$BUILD" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    case "$phase" in
        Complete)
            break
            ;;
        Failed|Error|Cancelled)
            echo "--- last build log lines ---" >&2
            oc -n "$NAMESPACE" logs "$BUILD" --tail=40 >&2 || true
            die "build $BUILD ended in phase $phase"
            ;;
    esac
    if (( SECONDS >= deadline )); then
        echo "--- last build log lines ---" >&2
        oc -n "$NAMESPACE" logs "$BUILD" --tail=40 >&2 || true
        die "build $BUILD did not finish within ${BUILD_TIMEOUT}s"
    fi
    sleep 5
done
echo "  build complete"

DIGEST="$(oc -n "$NAMESPACE" get "$BUILD" -o jsonpath='{.status.output.to.imageDigest}')"
[[ -n "$DIGEST" ]] || die "build $BUILD produced no image digest"

echo "Step 4/5: tag ${DIGEST} as ${IMAGE_STREAM}:latest"
oc -n "$NAMESPACE" tag "${IMAGE_STREAM}@${DIGEST}" "${IMAGE_STREAM}:latest"

echo "Step 5/5: roll out deployment/$DEPLOYMENT"
oc -n "$NAMESPACE" rollout restart "deployment/$DEPLOYMENT"
oc -n "$NAMESPACE" rollout status "deployment/$DEPLOYMENT" --timeout="$ROLLOUT_TIMEOUT"

POD="$(oc -n "$NAMESPACE" get pods -l "app=$DEPLOYMENT" \
    -o jsonpath='{.items[0].metadata.name}')"
POD_IMAGE="$(oc -n "$NAMESPACE" get pod "$POD" \
    -o jsonpath='{.status.containerStatuses[?(@.name=="dashboard")].imageID}')"

echo
echo "=== Deployment complete ==="
echo "  pod:   $POD"
echo "  image: $POD_IMAGE"

if [[ "$POD_IMAGE" == *"${DIGEST#sha256:}"* ]]; then
    echo "  image matches build $BUILD"
else
    echo "  pod image does not match build digest $DIGEST" >&2
    exit 1
fi

if [[ -n "$ROUTE_NAME" ]]; then
    HOST="$(oc -n "$NAMESPACE" get route "$ROUTE_NAME" \
        -o jsonpath='{.spec.host}' 2>/dev/null || true)"
    [[ -n "$HOST" ]] && echo "  url:   https://$HOST"
fi

echo
echo "Hard-refresh the browser (Cmd+Shift+R, or Ctrl+Shift+R) to bypass"
echo "cached HTML before concluding that a change did not deploy."
