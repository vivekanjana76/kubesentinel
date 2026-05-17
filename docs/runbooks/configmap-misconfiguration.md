# ConfigMap Misconfiguration

## Symptoms

- Application behaves incorrectly after a ConfigMap change: wrong feature flags, wrong database host, wrong service URLs.
- Pods may enter `CrashLoopBackOff` if the application validates configuration at startup and exits on invalid values.
- Alert `HighErrorRate` or `KubeContainerWaitingReasonCrashLoopBackOff` may fire.
- `kubectl logs` shows application startup errors like:
  ```
  ValueError: CACHE_TTL must be a positive integer, got 'abc'
  RuntimeError: DATABASE_HOST is not set
  ```
- The application appears healthy in Kubernetes (pod is Running, probes pass) but returns wrong results — the most dangerous variant.

## Root Cause

A ConfigMap was modified (manually via `kubectl edit configmap`, via a Helm upgrade, or via a GitOps apply) and one or more of the following occurred:

1. **Wrong value type**: a numeric field was set to a non-numeric string.
2. **Missing key**: a required key was removed or renamed.
3. **Environment not updated**: the ConfigMap was updated but the pods were not restarted, so they are still running with the old values. (ConfigMap values projected as env vars do NOT hot-reload; only mounted volumes hot-reload within ~60s.)
4. **Wrong ConfigMap mounted**: the Deployment references a ConfigMap name that belongs to a different environment (e.g., staging ConfigMap in production).
5. **Encoding issue**: a multi-line value (e.g., a certificate PEM block) was broken by YAML indentation in the ConfigMap.

## Investigation Steps

1. Identify the ConfigMap(s) used by the Deployment:
   ```
   kubectl get deployment <name> -n <namespace> -o yaml | grep -A30 "envFrom:\|configMapRef:\|configMap:"
   ```

2. Inspect the current ConfigMap contents:
   ```
   kubectl get configmap <name> -n <namespace> -o yaml
   ```
   Compare against the expected values in the Git repository.

3. Check when the ConfigMap was last modified:
   ```
   kubectl describe configmap <name> -n <namespace> | grep "Annotations:\|Creation Timestamp:"
   ```
   Then cross-reference with `kubectl rollout history deployment/<name>` timestamps.

4. Check whether the pods have picked up the new values. For env vars, pods must be restarted:
   ```
   kubectl exec -it <pod-name> -n <namespace> -- env | grep <KEY_NAME>
   ```

5. Check recent changes to the ConfigMap via Git or audit log:
   ```
   git log --oneline -- infra/k8s/configmap.yaml
   ```

6. If the application is running but behaving incorrectly, check what value it actually resolved:
   ```
   kubectl exec -it <pod-name> -n <namespace> -- \
     python -c "import os; print(os.environ.get('DATABASE_HOST', 'NOT SET'))"
   ```

## Resolution

**Correct the ConfigMap:**
```bash
kubectl edit configmap <name> -n <namespace>
# or apply from the corrected file:
kubectl apply -f infra/k8s/configmap.yaml
```

**Restart pods to pick up new values** (required for env var projection):
```bash
kubectl rollout restart deployment/<name> -n <namespace>
kubectl rollout status deployment/<name> -n <namespace>
```

**If the ConfigMap change caused a crash — roll back to the last known-good ConfigMap:**
```bash
# Retrieve the previous version from Git
git show HEAD~1:infra/k8s/configmap.yaml | kubectl apply -f -
kubectl rollout restart deployment/<name> -n <namespace>
```

## Prevention

- Store all ConfigMaps in Git under `infra/k8s/` and apply them via GitOps (Argo CD, Flux). Never edit ConfigMaps directly in production via `kubectl edit`.
- Add schema validation for ConfigMap values in the application startup code. Fail fast with a descriptive error rather than silently using a bad value.
- Use Kubernetes admission webhooks or OPA/Gatekeeper policies to validate ConfigMap contents against a schema before they are written to the cluster.
- Annotate the Deployment to restart automatically when the ConfigMap changes:
  ```yaml
  # In the Deployment pod template annotations:
  annotations:
    checksum/config: "{{ include (print $.Template.BasePath \"/configmap.yaml\") . | sha256sum }}"
  ```
  Helm renders a new checksum on each config change, triggering a rollout.
- Maintain separate ConfigMaps per environment and use namespace-based RBAC to prevent cross-environment access.

## Related Alerts

- `KubeContainerWaitingReasonCrashLoopBackOff` — if the bad config causes a startup crash
- `HighErrorRate` — if the bad config causes runtime errors
- `HighLatency` — if the bad config points to a slower/wrong backend
