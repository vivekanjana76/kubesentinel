# High HTTP Error Rate

## Symptoms

- Alert `HighErrorRate` fires: more than 5% of HTTP responses in the last 5 minutes have status codes 5xx.
- Grafana shows a sharp spike in `http_requests_total{status=~"5.."}` coinciding with a deployment or config change.
- Users report errors or timeouts; SLO error budget is burning.
- `kubectl get pods` may show pods in `Terminating` state if a rolling update is in progress.

## Root Cause

A 5xx error rate spike following a deployment is almost always caused by:

1. **Application bug introduced in the new version**: an unhandled exception path, a nil pointer, a missing dependency.
2. **Failed rolling update**: new pods crash on startup and traffic is routed to them before health checks catch up.
3. **Database schema mismatch**: the new application version expects a schema that hasn't been migrated yet (or vice versa during rollback).
4. **Downstream dependency failure**: a service the application calls (another microservice, external API) became unavailable.
5. **Resource exhaustion**: CPU throttling or memory pressure causing request timeouts, which the application surfaces as 500s.

## Investigation Steps

1. Check the error rate in Prometheus:
   ```promql
   sum(rate(http_requests_total{status=~"5.."}[5m])) by (path, status)
   /
   sum(rate(http_requests_total[5m])) by (path, status)
   ```
   Identify which endpoints are affected.

2. Check recent deployment activity:
   ```
   kubectl rollout history deployment/<name> -n <namespace>
   kubectl describe deployment <name> -n <namespace> | grep -A5 "Conditions:"
   ```

3. Read application logs from the erroring pods:
   ```
   kubectl logs -l app=<label> -n <namespace> --since=10m | grep -i "error\|panic\|exception\|traceback"
   ```

4. Check pod health and readiness:
   ```
   kubectl get pods -n <namespace> -o wide
   kubectl describe pods -n <namespace> | grep -A10 "Conditions:"
   ```

5. Check downstream service health:
   ```
   kubectl exec -it <pod-name> -n <namespace> -- curl -s http://<downstream-svc>:<port>/healthz
   ```

6. Check CPU throttling (a leading indicator of timeout-induced 500s):
   ```promql
   rate(container_cpu_cfs_throttled_seconds_total{pod=~"<name>.*"}[5m])
   ```

## Resolution

**If caused by a bad deployment — roll back immediately:**
```bash
kubectl rollout undo deployment/<name> -n <namespace>
kubectl rollout status deployment/<name> -n <namespace>
```

**If caused by a downstream service failure:**
- Check the downstream service's own alerts and runbooks.
- If the downstream is recoverable, the application's error rate should recover automatically once it is healthy.
- If not, consider activating a circuit breaker or returning a degraded response instead of a 500.

**If caused by database schema mismatch:**
- Do not roll back the schema. Roll back the application to the previous version that matches the current schema.
- Run the schema migration and then redeploy the new application version.

**Reduce blast radius during investigation:**
- Scale the Deployment to fewer replicas to limit the rate of errors while you diagnose:
  ```bash
  kubectl scale deployment/<name> --replicas=1 -n <namespace>
  ```

## Prevention

- Implement progressive delivery (canary releases) using Argo Rollouts or Flagger so errors are detected at low traffic percentages before full rollout.
- Configure readiness probes correctly so pods do not receive traffic until the application is fully initialized.
- Add an SLO-based alert that fires before the error budget is exhausted, not just after the threshold is crossed.
- Run schema migrations as a Kubernetes Job in an init container before the new Deployment rolls out.
- Add error-rate assertions to your smoke test suite and gate deployments on passing smoke tests.

## Related Alerts

- `HighErrorRate` — primary alert; fires when 5xx rate > 5% for 5 minutes
- `KubePodNotReady` — may co-fire during a failed rolling update
- `KubeDeploymentReplicasMismatch` — fires if the desired replica count is not met
