"""Tests for the POC deployment manifest.

Validates that the POC Deployment uses the correct OpenShift
image change trigger annotation (not the DeploymentConfig-only
triggers block) and sets imagePullPolicy: Always.
"""

import json

import yaml
import pytest


@pytest.fixture
def deployment():
    """Load the POC deployment manifest."""
    with open("openshift/poc/dashboard-deployment.yaml") as f:
        return yaml.safe_load(f)


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
