# POC Deployment Guide

How to deploy merged changes to the POC dashboard, and how to rescue data
collection when a source is unavailable.

Reference environment: namespace `winc-dashboard-poc` on
`https://api.build10.ci.devcluster.openshift.com:6443`.

## Quick reference

```bash
# Deploy code and the current working-tree config
scripts/deploy-poc.sh

# Deploy code only, leave the ConfigMap untouched
SKIP_CONFIG=1 scripts/deploy-poc.sh

# Refuse to run against the wrong cluster
EXPECTED_SERVER=api.build10.ci.devcluster.openshift.com scripts/deploy-poc.sh
```

## Two resources, not one

A merge to `master` updates the image, but not the runtime configuration:

```yaml
deployment:
  containers:
    - name: dashboard
      image: winc-dashboard-poc:latest   # built from master by the BuildConfig
      volumeMounts:
        - name: config
          mountPath: /app/config.yaml    # ConfigMap, updated only by hand
  volumes:
    - name: config
      configMap:
        name: dashboard-config-poc
```

Consequences:

- A config change in `dashboard/config.yaml` does nothing until the
  ConfigMap is updated and the pod restarts.
- The image tag `:latest` does not move by itself when a build completes.
  The build digest has to be tagged explicitly.
- `scripts/deploy-poc.sh` builds the **local HEAD commit** and publishes
  the **working-tree** `dashboard/config.yaml`. To keep the two from
  silently diverging the script:
  - refuses to run when `HEAD` is not on the source branch
    (`SOURCE_REMOTE`, default `upstream`; `SOURCE_BRANCH`, default
    `master`), so the image always comes from a pushed commit;
  - refuses to publish a config file that differs from `HEAD`, unless
    `ALLOW_DIRTY_CONFIG=1` is set, and prints a warning when it does;
  - use `SKIP_CONFIG=1` to deploy code only.

## Before you deploy

1. Authenticate to the cluster that hosts the deployment:

   ```bash
   oc login https://api.build10.ci.devcluster.openshift.com:6443
   oc whoami --show-server
   ```

2. Confirm you are on the commit you intend to deploy:

   ```bash
   git log --oneline -3
   ```

3. Confirm the commit is pushed to the source branch; the script builds
   `HEAD` explicitly and refuses if that commit is not on
   `upstream/master`.

4. Confirm the Prow token in the `prow-api-token` secret has not expired.
   User tokens last 24 hours; see
   [`dashboard/docs/token-renewal.md`](dashboard/docs/token-renewal.md).
   An expired token does not fail the deployment, it silently degrades
   collection, so check the pod logs for HTTP 403 after the rollout.

## Deploying with the script

`scripts/deploy-poc.sh` performs all five steps and verifies the result:

1. Applies the ConfigMap from `dashboard/config.yaml`.
2. Starts a build and waits for it, failing fast and printing the last log
   lines if the build ends in `Failed`, `Error` or `Cancelled`.
3. Reads the build's image digest and tags it as `<stream>:latest`.
4. Restarts the deployment and waits for the rollout to finish.
5. Confirms the running pod's image matches the build digest, and prints
   the route when `ROUTE_NAME` is set. The pod is taken from the current
   ReplicaSet (no `deletionTimestamp`, `Ready=True`), so a terminating pod
   from the previous ReplicaSet cannot cause a false failure.

Override targets through environment variables rather than editing the
script: `NAMESPACE`, `BUILD_CONFIG`, `IMAGE_STREAM`, `CONFIGMAP`,
`DEPLOYMENT`, `CONFIG_FILE`, `BUILD_TIMEOUT`, `ROLLOUT_TIMEOUT`,
`SOURCE_REMOTE`, `SOURCE_BRANCH`, `EXPECTED_SERVER`, `ROUTE_NAME`,
`SKIP_CONFIG`, `ALLOW_DIRTY_CONFIG`.

## Deploying by hand

```bash
NS=winc-dashboard-poc

# 1. ConfigMap (only if dashboard/config.yaml changed)
oc -n "$NS" create configmap dashboard-config-poc \
  --from-file=config.yaml=dashboard/config.yaml \
  --dry-run=client -o yaml | oc -n "$NS" apply -f -

# 2. Build
BUILD=$(oc -n "$NS" start-build winc-dashboard-poc -o name)
oc -n "$NS" logs -f "$BUILD"
oc -n "$NS" get "$BUILD" -o jsonpath='{.status.phase}{"\n"}'   # expect Complete

# 3. Tag the build output
DIGEST=$(oc -n "$NS" get "$BUILD" -o jsonpath='{.status.output.to.imageDigest}')
oc -n "$NS" tag "winc-dashboard-poc@$DIGEST" winc-dashboard-poc:latest

# 4. Roll out
oc -n "$NS" rollout restart deployment/winc-dashboard-poc
oc -n "$NS" rollout status deployment/winc-dashboard-poc --timeout=180s

# 5. Verify
POD=$(oc -n "$NS" get pods -l app=winc-dashboard-poc -o jsonpath='{.items[0].metadata.name}')
oc -n "$NS" get pod "$POD" \
  -o jsonpath='{.status.containerStatuses[?(@.name=="dashboard")].imageID}{"\n"}'
oc -n "$NS" exec "$POD" -c dashboard -- grep -A 3 "pr_log_sources:" /app/config.yaml
```

## Verifying a deployment

The deployment is only correct when all three are true:

- The pod image contains the build digest.
- `/app/config.yaml` inside the pod shows the intended values.
- The pod logs show a recent collection without `HTTP 403` or
  `Private API token rejected`.

```bash
oc -n winc-dashboard-poc logs deploy/winc-dashboard-poc -c dashboard \
  | grep -E "Using API token|health check|HTTP 403|PR log scan|Collection complete"
```

Then hard-refresh the browser. The HTML is served server-side and cached
aggressively; `Cmd+Shift+R` on macOS or `Ctrl+Shift+R` elsewhere.

## Rescuing collection when gcsweb is unavailable

The dashboard scrapes `gcsweb-qe-private-deck-ci` for job and artifact
listings. When that instance fails (for example an invalid GCS credential
behind it), collection stores nothing even though the Prow deck still
works.

The image ships `scripts/backfill_from_deck.py`, which reads job runs from
the deck instead:

```bash
POD=$(oc -n winc-dashboard-poc get pods -l app=winc-dashboard-poc \
  -o jsonpath='{.items[0].metadata.name}')

# Dry run first
oc -n winc-dashboard-poc exec "$POD" -c dashboard -- \
  python3 /app/scripts/backfill_from_deck.py --dry-run --days 3 --pattern '*winc*'

# Then write the rows
oc -n winc-dashboard-poc exec "$POD" -c dashboard -- \
  python3 /app/scripts/backfill_from_deck.py --days 3 --pattern '*winc*'
```

Limits worth knowing:

- It can only see ProwJobs still present in `prowjobs.js`. Older builds
  are not enumerable without directory listings, so they stay missing
  until gcsweb recovers.
- It keeps tests matching `--suite-filter` (`Windows_Containers` by
  default) and collapses duplicate OCP IDs with failure precedence.
- It uses the pod's `API_KEY`, so the token still has to be valid.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Merge is in `master` but the dashboard is unchanged | `:latest` was not re-tagged, or the pod was not restarted | Run `scripts/deploy-poc.sh`; never assume the webhook did it |
| Config change has no effect | ConfigMap not updated, or pod not restarted | Re-run with `CONFIG_FILE=dashboard/config.yaml` and restart |
| Logs show `HTTP 403` / `Private API token rejected` | Prow user token expired (24h) | Renew per `dashboard/docs/token-renewal.md`, update the secret, restart |
| Logs say `Continuing with public source` and collection saves 0 rows | Private gcsweb failed and collection degraded | Fix gcsweb with the deck operators, or backfill with the deck script |
| Build failed | Compile error or failed test in the image build | Read the step 3 log tail printed by the script |
| Deployment ran against the wrong cluster | Wrong `oc` context | Always check the `cluster:` line, or set `EXPECTED_SERVER` |
