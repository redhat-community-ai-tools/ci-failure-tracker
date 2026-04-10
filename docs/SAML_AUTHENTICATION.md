# SAML/OAuth Authentication Setup

This guide explains how to protect your dashboard with Red Hat SSO (SAML) authentication using OpenShift OAuth Proxy.

## Overview

The OAuth proxy acts as a sidecar container that:
- Redirects unauthenticated users to Red Hat SSO login
- Validates authentication via OpenShift OAuth
- Maintains encrypted session cookies
- Proxies authenticated requests to the dashboard

## Architecture

```
User → Route (HTTPS) → OAuth Proxy (port 8443) → Dashboard (port 8080)
                ↓
         Red Hat SSO (SAML)
```

## Prerequisites

- OpenShift cluster with OAuth configured
- Red Hat SSO / Keycloak integration
- Namespace with sufficient RBAC permissions

## Deployment Steps

### 1. Create OAuth Cookie Secret

```bash
oc create secret generic oauth-proxy-secret \
  --from-literal=cookie-secret=$(python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())") \
  -n <namespace>
```

### 2. Create ServiceAccount with OAuth Annotation

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: <dashboard-name>
  namespace: <namespace>
  annotations:
    serviceaccounts.openshift.io/oauth-redirectreference.dashboard: '{"kind":"OAuthRedirectReference","apiVersion":"v1","reference":{"kind":"Route","name":"<route-name>"}}'
```

### 3. Update Service for HTTPS

```yaml
apiVersion: v1
kind: Service
metadata:
  name: <dashboard-name>
  namespace: <namespace>
  annotations:
    service.alpha.openshift.io/serving-cert-secret-name: <dashboard-name>-tls
spec:
  ports:
  - name: https
    port: 8443
    targetPort: 8443
  - name: http
    port: 8080
    targetPort: 8080
  selector:
    app: <dashboard-name>
```

### 4. Update Route for Re-encrypt Termination

```yaml
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: <dashboard-name>
  namespace: <namespace>
spec:
  port:
    targetPort: https
  tls:
    insecureEdgeTerminationPolicy: Redirect
    termination: reencrypt
  to:
    kind: Service
    name: <dashboard-name>
```

### 5. Add OAuth Proxy Sidecar to Deployment

```yaml
spec:
  template:
    spec:
      serviceAccountName: <dashboard-name>
      containers:
      # OAuth Proxy sidecar
      - name: oauth-proxy
        image: quay.io/openshift/origin-oauth-proxy:4.14
        ports:
        - containerPort: 8443
          name: https
        args:
        - --https-address=:8443
        - --provider=openshift
        - --openshift-service-account=<dashboard-name>
        - --upstream=http://localhost:8080
        - --tls-cert=/etc/tls/private/tls.crt
        - --tls-key=/etc/tls/private/tls.key
        - --cookie-secret=$(COOKIE_SECRET)
        - --openshift-sar={"namespace":"<namespace>","resource":"services","name":"<dashboard-name>","verb":"get"}
        env:
        - name: COOKIE_SECRET
          valueFrom:
            secretKeyRef:
              name: oauth-proxy-secret
              key: cookie-secret
        volumeMounts:
        - mountPath: /etc/tls/private
          name: oauth-tls
        resources:
          requests:
            cpu: 10m
            memory: 20Mi
          limits:
            cpu: 100m
            memory: 100Mi
      
      # Existing dashboard container (unchanged)
      - name: dashboard
        # ... existing config ...
      
      volumes:
      - name: oauth-tls
        secret:
          secretName: <dashboard-name>-tls
      # ... other volumes ...
```

## Authorization

The `--openshift-sar` flag controls who can access the dashboard:

**Current setting:**
```bash
--openshift-sar={"namespace":"<namespace>","resource":"services","name":"<dashboard-name>","verb":"get"}
```

This requires users to have permission to `get` the service in the namespace.

### Alternative Authorization Options

**Allow all authenticated users:**
```bash
--openshift-sar={"resource":"namespaces","resourceName":"<namespace>","verb":"get"}
```

**Require specific role:**
```bash
--openshift-sar={"namespace":"<namespace>","resource":"deployments","verb":"list"}
```

**Require group membership:**
Use `--openshift-delegate-urls` for more complex authorization.

## Testing

1. Access the dashboard URL
2. Should redirect to Red Hat SSO login
3. After authentication, dashboard loads
4. Session persists for 7 days (configurable via `--cookie-expire`)

## Troubleshooting

### 403 Forbidden after login

User doesn't have required permissions. Check:
```bash
oc auth can-i get services/<dashboard-name> -n <namespace> --as=<username>
```

### Pod not starting

Check OAuth proxy logs:
```bash
oc logs deployment/<dashboard-name> -c oauth-proxy -n <namespace>
```

### TLS certificate issues

Verify secret was auto-generated:
```bash
oc get secret <dashboard-name>-tls -n <namespace>
```

If missing, check the service annotation is correct.

### Session expires immediately

Check cookie secret is properly set:
```bash
oc get secret oauth-proxy-secret -n <namespace> -o jsonpath='{.data.cookie-secret}' | base64 -d | wc -c
```

Should be 32+ bytes.

## Security Considerations

1. **Cookie secret** - Stored in Kubernetes Secret, never hardcoded
2. **HTTPS end-to-end** - TLS from route through to pod (reencrypt)
3. **RBAC-controlled** - Authorization via OpenShift RBAC
4. **Session management** - Encrypted cookies, configurable expiry
5. **No code changes** - Dashboard remains stateless, auth at proxy layer

## Configuration Options

### Cookie expiry
```bash
--cookie-expire=168h  # 7 days (default)
```

### Skip authentication for specific paths
```bash
--skip-auth-regex=^/metrics$  # Allow unauthenticated metrics endpoint
```

### Custom redirect URL
```bash
--redirect-url=https://custom-domain.example.com/oauth/callback
```

## References

- [OpenShift OAuth Proxy](https://github.com/openshift/oauth-proxy)
- [Red Hat SSO Documentation](https://access.redhat.com/products/red-hat-single-sign-on)
- [OpenShift OAuth Configuration](https://docs.openshift.com/container-platform/4.14/authentication/configuring-oauth-clients.html)
