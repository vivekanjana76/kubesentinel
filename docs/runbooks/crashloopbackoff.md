# CrashLoopBackOff

## Symptoms

- `kubectl get pods` shows `CrashLoopBackOff` in the STATUS column.
- The `RESTARTS` counter is non-zero and climbing.
- `kubectl describe pod <name>` shows the container state as `Waiting Reason: CrashLoopBackOff` and lists a terminated exit code.
- Alert `KubeContainerWaitingReasonCrashLoopBackOff` fires.
- Back-off delay between restarts increases exponentially: 10s, 20s, 40s, 80s, up to 5 minutes.

## Root Cause

Kubernetes restarts a container whenever it exits with a non-zero exit code. After several rapid restarts it applies an exponential back-off to prevent thrashing. CrashLoopBackOff is not a root cause — it is a symptom. The actual causes are varied:

- **Application startup failure**: misconfigured environment variables, missing secrets, failed database connection, port already in use.
- **Missing entrypoint**: the container image has a wrong or missing `CMD`/`ENTRYPOINT`.
- **OOMKilled on startup**: memory limit too low to start the process.
- **Liveness probe failing immediately**: probe is misconfigured and kills the container before the application is ready.
- **Dependency not available**: the container depends on a sidecar or init container that is not yet healthy.
- **Corrupted image layer**: rare, but an image with a bad layer can fail immediately on exec.

## Investigation Steps

1. Read the exit code and termination reason:
   ```
   kubectl describe pod <pod-name> -n <namespace>
   ```
   Check `Last State: Terminated` for `Reason`, `Exit Code`, and `Message`.

2. Read the logs from the previous (crashed) container instance:
   ```
   kubectl logs <pod-name> -n <namespace> --previous
   ```
   This is the most informative step — startup stack traces appear here.

3. Check for missing environment variables or secrets:
   ```
   kubectl get pod <pod-name> -n <namespace> -o yaml | grep -A20 "env:"
   kubectl get secret <secret-name> -n <namespace>
   ```

4. Verify the liveness probe configuration:
   ```
   kubectl get deployment <name> -n <namespace> -o yaml | grep -A10 "livenessProbe:"
   ```
   A probe with `initialDelaySeconds: 0` and a fast `failureThreshold` will kill a slow-starting app.

5. Check init container status if present:
   ```
   kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Init Containers:"
   ```

6. Try running the container image locally to reproduce the crash:
   ```
   docker run --rm -e ENV_VAR=value <image>:<tag>
   ```

## Resolution

**Startup failure due to missing config:**
- Patch the Deployment with the correct environment variable or secret reference.
- If a secret is missing, create it:
  ```
  kubectl create secret generic <name> --from-literal=KEY=value -n <namespace>
  ```

**Liveness probe misconfigured:**
```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: 8080
  initialDelaySeconds: 30   # Give the app time to start
  periodSeconds: 10
  failureThreshold: 3
```

**OOMKilled on startup — raise the memory limit** (see oomkilled-pod runbook).

**Wrong image entrypoint:**
```
kubectl set image deployment/<name> <container>=<correct-image>:<tag> -n <namespace>
```

## Prevention

- Always set `initialDelaySeconds` on liveness probes to at least twice your application's P95 cold-start time.
- Use `startupProbe` (Kubernetes 1.20+) instead of a long `initialDelaySeconds` for applications with variable startup times.
- Validate all required environment variables at application startup and fail fast with a clear error message.
- Add integration tests that boot the container image in CI and assert a healthy response within a timeout.

## Related Alerts

- `KubeContainerWaitingReasonCrashLoopBackOff` — primary alert for this condition
- `KubePodOOMKilled` — if the crash is memory-related
- `KubePodNotReady` — fires alongside when the pod never reaches Ready state
