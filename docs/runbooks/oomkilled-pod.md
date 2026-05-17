# OOMKilled Pod

## Symptoms

- Pod status shows `OOMKilled` or `Error` with exit code 137.
- `kubectl describe pod <name>` shows `Last State: Terminated Reason: OOMKilled`.
- Alert `KubePodOOMKilled` fires from the Prometheus rule set.
- Container restarts incrementing rapidly; `kubectl get pods` shows high `RESTARTS` count.
- Application logs may be absent or cut off mid-line at the moment of kill.

## Root Cause

The Linux kernel's Out-Of-Memory (OOM) killer terminated the container process because it exceeded its configured memory limit (`resources.limits.memory`). Kubernetes enforces cgroup memory limits strictly; once the container's RSS plus page cache crosses the limit, the kernel sends SIGKILL (signal 9) to the process. Exit code 137 = 128 + 9. Common causes:

- Memory limit set too low relative to actual working-set size.
- Memory leak in application code causing unbounded growth.
- Sudden traffic spike causing legitimate spike in working-set (e.g., large in-memory caches, goroutine pools).
- JVM heap configured larger than container limit (JVM does not respect cgroup limits by default before Java 11).

## Investigation Steps

1. Confirm the OOMKill and read the limit:
   ```
   kubectl describe pod <pod-name> -n <namespace>
   ```
   Look for `Last State: Terminated Reason: OOMKilled` and `Limits: memory: <value>`.

2. Check restart count and timing:
   ```
   kubectl get pod <pod-name> -n <namespace> -o wide
   ```

3. Examine memory usage trend before the kill using Prometheus:
   ```
   container_memory_working_set_bytes{pod="<pod-name>", container!=""}
   ```
   Query in Grafana or `kubectl exec` into Prometheus and run the PromQL query.

4. Inspect application logs from the previous container instance:
   ```
   kubectl logs <pod-name> -n <namespace> --previous
   ```

5. Check if the node itself was under memory pressure:
   ```
   kubectl describe node <node-name> | grep -A5 "Conditions:"
   kubectl top node <node-name>
   ```

6. Profile heap usage if the application exposes a `/debug/pprof` endpoint (Go) or a `/actuator/metrics` endpoint (Spring Boot).

## Resolution

**Immediate — raise the memory limit:**
```yaml
# In the Deployment spec, under containers[].resources:
resources:
  requests:
    memory: "256Mi"
  limits:
    memory: "512Mi"
```
Apply:
```
kubectl apply -f infra/k8s/deployment.yaml
```

**If a memory leak is suspected:**
- Roll back the most recent deployment to restore stability:
  ```
  kubectl rollout undo deployment/<name> -n <namespace>
  ```
- Capture a heap dump or profile before rollback if the application supports it.

**JVM-specific:**
- Add `-XX:+UseContainerSupport` (Java 11+) or `-XX:MaxRAMPercentage=75.0` to JVM flags so the heap is bounded to 75% of the cgroup limit.

## Prevention

- Set both `requests` and `limits` for every container. Never leave `limits.memory` unset.
- Configure a Prometheus alert on `container_memory_working_set_bytes / container_spec_memory_limit_bytes > 0.85` to get early warning before the OOM kill occurs.
- Add memory profiling to the application's CI pipeline (e.g., pytest-memray for Python, go test -memprofile for Go).
- Use a VPA (Vertical Pod Autoscaler) in recommendation mode to observe actual usage before setting limits.
- For JVM workloads, always set `-XX:+UseContainerSupport`.

## Related Alerts

- `KubePodOOMKilled` — fires when `kube_pod_container_status_last_terminated_reason == "OOMKilled"`
- `KubeContainerWaitingReasonCrashLoopBackOff` — may co-fire if the pod enters CrashLoopBackOff after repeated OOM kills
- `KubeNodeMemoryPressure` — fires if the node-level OOM condition is broader
