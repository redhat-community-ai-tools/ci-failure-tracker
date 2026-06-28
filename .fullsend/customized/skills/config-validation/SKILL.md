---
name: config-validation
description: >-
  Validates config.yaml changes for the CI Dashboard Tracker. Checks
  collector type, version format, platform names, job patterns, and
  backwards compatibility with existing team deployments.
---

# Config Validation Guide

## config.yaml Schema

The dashboard configuration file controls all behavior. When reviewing or
modifying config.yaml, validate these rules:

### Collector Section

```yaml
collector:
  type: "gcsweb"  # Must be one of: reportportal, gcsweb, prow_gcs, prow_mcp
```

Each collector type has a required subsection with type-specific fields.
The active `type` must have its corresponding subsection present.

### Tracking Section

```yaml
tracking:
  versions: ["4.19", "4.21"]     # Must be valid OpenShift version strings
  platforms: ["aws", "azure"]     # Must be recognized platform identifiers
  test_suite_filter: "name"       # Optional, filters by test description
  lookback_days: 30               # Positive integer
  blocklist: ["OCP-12345"]        # Test IDs to exclude (OCP-NNNNN format)
```

**Valid platforms:** aws, azure, gcp, vsphere, nutanix, metal, alibabacloud,
ibmcloud, openstack, ovirt, none

**Version format:** Major.Minor (e.g., "4.19", "5.0")

### Database Section

```yaml
database:
  path: "./data/dashboard.db"     # Must be writable path
```

For OpenShift: use `/data/dashboard.db` (persistent volume mount point).

### Web Section

```yaml
web:
  host: "0.0.0.0"
  port: 8080
  debug: false                    # Must be false in production
```

## Backwards Compatibility Rules

1. Never remove existing config keys -- add new ones with defaults
2. Never rename config keys -- add aliases if needed
3. New collector types must not break existing collector configs
4. Default values must produce working behavior (no required new keys)
5. Changes to `tracking.versions` or `tracking.platforms` must preserve
   existing values (add, don't replace)

## Common Validation Errors

- Job pattern with `{version}` placeholder but no versions listed
- Platform name not in the recognized set
- Collector type doesn't match any subsection
- Database path pointing to non-persistent storage in OpenShift
- Debug mode left enabled in production config
