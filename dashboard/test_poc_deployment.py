"""Tests for the POC deployment manifests.

Validates that the POC Deployment uses the correct OpenShift
image change trigger annotation (not the DeploymentConfig-only
triggers block) and sets imagePullPolicy: Always.

Also validates the oauth-proxy sidecar, Service ports/annotations,
and Route TLS configuration for the re-encrypted route.
"""

import json

import yaml
import pytest


@pytest.fixture
def deployment():
    """Load the POC deployment manifest."""
    with open("openshift/poc/dashboard-deployment.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def service():
    """Load the POC service manifest."""
    with open("openshift/poc/dashboard-service.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def route():
    """Load the POC route manifest."""
    with open("openshift/poc/dashboard-route.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def serviceaccount():
    """Load the POC ServiceAccount manifest."""
    with open("openshift/poc/dashboard-serviceaccount.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def oauth_proxy(deployment):
    """Return the oauth-proxy sidecar container from the Deployment."""
    containers = deployment["spec"]["template"]["spec"]["containers"]
    proxy = [c for c in containers if c["name"] == "oauth-proxy"]
    assert len(proxy) == 1
    return proxy[0]


def test_has_image_change_trigger_annotation(deployment):
    """The Deployment must have the image.openshift.io/triggers annotation."""
    annotations = deployment["metadata"].get("annotations", {})
    assert "image.openshift.io/triggers" in annotations

    triggers = json.loads(annotations["image.openshift.io/triggers"])
    assert isinstance(triggers, list)
    assert len(triggers) > 0

    trigger = triggers[0]
    assert trigger["from"]["kind"] == "ImageStreamTag"
    assert trigger["from"]["name"] == "winc-dashboard-poc:latest"
    assert "fieldPath" in trigger


def test_dashboard_container_pull_policy(deployment):
    """The dashboard container must use imagePullPolicy: Always."""
    containers = deployment["spec"]["template"]["spec"]["containers"]
    dashboard = [c for c in containers if c["name"] == "dashboard"]
    assert len(dashboard) == 1
    assert dashboard[0].get("imagePullPolicy") == "Always"


def test_no_top_level_triggers(deployment):
    """The Deployment spec must not have a top-level triggers block.

    The triggers field is only valid on DeploymentConfig resources,
    not on apps/v1 Deployments.
    """
    assert "triggers" not in deployment.get("spec", {})


def test_deployment_sets_service_account_name(deployment):
    """The Deployment must set serviceAccountName for oauth-proxy."""
    sa_name = deployment["spec"]["template"]["spec"].get("serviceAccountName")
    assert sa_name == "winc-dashboard-poc"


def test_oauth_proxy_service_account_arg(oauth_proxy):
    """The oauth-proxy must pass --openshift-service-account."""
    args = oauth_proxy.get("args", [])
    assert "--openshift-service-account=winc-dashboard-poc" in args


def test_oauth_proxy_provider_openshift(oauth_proxy):
    """The oauth-proxy must use --provider=openshift."""
    args = oauth_proxy.get("args", [])
    assert "--provider=openshift" in args


def test_oauth_proxy_sidecar_present(deployment):
    """The Deployment must include an oauth-proxy sidecar container."""
    containers = deployment["spec"]["template"]["spec"]["containers"]
    proxy = [c for c in containers if c["name"] == "oauth-proxy"]
    assert len(proxy) == 1


def test_oauth_proxy_listens_on_8443(oauth_proxy):
    """The oauth-proxy must listen on port 8443."""
    ports = [p["containerPort"] for p in oauth_proxy.get("ports", [])]
    assert 8443 in ports


def test_oauth_proxy_upstream_targets_dashboard(oauth_proxy):
    """The oauth-proxy must forward to the dashboard on localhost:8080."""
    args = oauth_proxy.get("args", [])
    assert "--upstream=http://localhost:8080" in args


def test_oauth_proxy_tls_volume_mount(oauth_proxy):
    """The oauth-proxy must mount the TLS secret volume."""
    volume_mounts = oauth_proxy.get("volumeMounts", [])
    mount_names = [vm["name"] for vm in volume_mounts]
    assert "tls" in mount_names
    tls_mounts = [vm for vm in volume_mounts if vm["name"] == "tls"]
    assert len(tls_mounts) == 1
    tls_mount = tls_mounts[0]
    assert tls_mount["mountPath"] == "/etc/tls/private"
    assert tls_mount.get("readOnly") is True


def test_tls_volume_defined(deployment):
    """The Deployment must define a volume for the TLS secret."""
    volumes = deployment["spec"]["template"]["spec"].get("volumes", [])
    vol_names = [v["name"] for v in volumes]
    assert "tls" in vol_names
    tls_vols = [v for v in volumes if v["name"] == "tls"]
    assert len(tls_vols) == 1
    tls_vol = tls_vols[0]
    assert tls_vol["secret"]["secretName"] == "winc-dashboard-poc-tls"


def test_oauth_proxy_cookie_secret_volume_mount(oauth_proxy):
    """The oauth-proxy must mount the cookie-secret volume."""
    volume_mounts = oauth_proxy.get("volumeMounts", [])
    mount_names = [vm["name"] for vm in volume_mounts]
    assert "cookie-secret" in mount_names
    cookie_mounts = [vm for vm in volume_mounts if vm["name"] == "cookie-secret"]
    assert len(cookie_mounts) == 1
    cookie_mount = cookie_mounts[0]
    assert cookie_mount["mountPath"] == "/var/run/secrets/oauth-proxy"
    assert cookie_mount.get("readOnly") is True


def test_cookie_secret_volume_defined(deployment):
    """The Deployment must define a volume for the cookie-secret Secret."""
    volumes = deployment["spec"]["template"]["spec"].get("volumes", [])
    vol_names = [v["name"] for v in volumes]
    assert "cookie-secret" in vol_names
    cookie_vols = [v for v in volumes if v["name"] == "cookie-secret"]
    assert len(cookie_vols) == 1
    cookie_vol = cookie_vols[0]
    assert cookie_vol["secret"]["secretName"] == "oauth-proxy-secret"


def test_oauth_proxy_readiness_probe(oauth_proxy):
    """The oauth-proxy must have a readinessProbe on the healthz endpoint."""
    probe = oauth_proxy.get("readinessProbe")
    assert probe is not None
    assert probe["httpGet"]["path"] == "/oauth/healthz"
    assert probe["httpGet"]["port"] == 8443
    assert probe["httpGet"]["scheme"] == "HTTPS"


def test_service_has_serving_cert_annotation(service):
    """The Service must have the serving-cert-secret-name annotation."""
    annotations = service["metadata"].get("annotations", {})
    key = "service.alpha.openshift.io/serving-cert-secret-name"
    assert key in annotations
    assert annotations[key] == "winc-dashboard-poc-tls"


def test_service_exposes_https_port(service):
    """The Service must expose port 8443 named https."""
    ports = service["spec"]["ports"]
    https_ports = [p for p in ports if p.get("name") == "https"]
    assert len(https_ports) == 1
    assert https_ports[0]["port"] == 8443
    assert https_ports[0]["targetPort"] == 8443


def test_service_retains_http_port(service):
    """The Service must still expose port 8080 for internal access."""
    ports = service["spec"]["ports"]
    http_ports = [p for p in ports if p.get("name") == "http"]
    assert len(http_ports) == 1
    assert http_ports[0]["port"] == 8080


def test_route_uses_reencrypt_tls(route):
    """The Route must use reencrypt TLS termination."""
    tls = route["spec"]["tls"]
    assert tls["termination"] == "reencrypt"


def test_route_targets_https_port(route):
    """The Route must target the https port, not http."""
    assert route["spec"]["port"]["targetPort"] == "https"


def test_serviceaccount_has_oauth_redirect_annotation(serviceaccount):
    """The ServiceAccount must have the oauth-redirectreference annotation."""
    annotations = serviceaccount["metadata"].get("annotations", {})
    key = "serviceaccounts.openshift.io/oauth-redirectreference.dashboard"
    assert key in annotations
    redirect = json.loads(annotations[key])
    assert redirect["kind"] == "OAuthRedirectReference"
    assert redirect["reference"]["kind"] == "Route"
    assert redirect["reference"]["name"] == "winc-dashboard-poc"


def test_serviceaccount_name_matches_deployment(serviceaccount, deployment):
    """The ServiceAccount name must match the Deployment serviceAccountName."""
    sa_name = serviceaccount["metadata"]["name"]
    deploy_sa = deployment["spec"]["template"]["spec"].get("serviceAccountName")
    assert sa_name == deploy_sa
