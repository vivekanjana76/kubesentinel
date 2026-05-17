# High Latency

## Symptoms

- Alert `HighLatency` fires: P95 request latency has been above the SLO threshold (e.g., 500ms) for more than 5 minutes.
- Grafana shows the `http_request_duration_seconds` histogram shifting right.
- Users report slow responses; the application appears to be working but is sluggish.
- Error rate may remain normal (requests are succeeding, just slowly).

## Root Cause

Latency degradation without a corresponding error rate spike typically indicates a resource or saturation problem rather than a code bug:

1. **CPU throttling**: the container is hitting its `resources.limits.cpu` ceiling. The kernel's CFS scheduler throttles the container's CPU time, adding delay to every operation.
2. **Memory pressure causing GC pressure**: high memory usage triggers frequent garbage collection pauses (JVM, Go, Python).
3. **Database query slowdown**: a slow query, missing index, table lock, or connection pool exhaustion upstream.
4. **Network latency to a dependency**: increased round-trip time to an external API, cache, or database.
5. **Thread/goroutine pool saturation**: the application's internal concurrency limit is reached and requests are queuing.
6. **Node resource contention**: another noisy-neighbor workload on the same node is competing for CPU or disk I/O.

## Investigation Steps

1. Confirm the latency percentile and affected endpoints:
   ```promql
   histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, path))
   ```

2. Check CPU throttling:
   ```promql
   rate(container_cpu_cfs_throttled_seconds_total{pod=~"<app>.*"}[5m])
   / rate(container_cpu_cfs_periods_total{pod=~"<app>.*"}[5m])
   ```
   Values above 0.25 indicate significant throttling.

3. Check CPU and memory usage vs limits:
   ```
   kubectl top pods -n <namespace> -l app=<label>
   ```

4. Check connection pool metrics if the application exposes them (e.g., `db_pool_waiting_connections`).

5. Check for node-level issues:
   ```
   kubectl top nodes
   kubectl describe node <node-name> | grep -A5 "Conditions:"
   ```

6. Look for slow database queries in application logs:
   ```
   kubectl logs -l app=<label> -n <namespace> --since=10m | grep -i "slow\|query\|timeout"
   ```

7. Check network latency to dependencies from inside the cluster:
   ```
   kubectl exec -it <pod-name> -n <namespace> -- \
     curl -w "@-" -o /dev/null -s http://<dep-host>:<port>/healthz <<'CURL'
   time_connect: %{time_connect}\ntime_total: %{time_total}\n
   CURL
   ```

## Resolution

**CPU throttling — raise the CPU limit or request:**
```yaml
resources:
  requests:
    cpu: "250m"
  limits:
    cpu: "1000m"
```
Apply and monitor throttling metric to confirm it drops.

**If a single slow query is causing latency:**
- Add the missing index directly in Postgres:
  ```sql
  CREATE INDEX CONCURRENTLY idx_table_column ON table(column);
  ```
  (`CONCURRENTLY` avoids a table lock.)
- Or patch the query in the application and deploy.

**If thread/goroutine pool is saturated:**
- Increase the pool size via environment variable if the application supports it.
- Add a horizontal pod autoscaler (HPA) to scale out:
  ```bash
  kubectl autoscale deployment/<name> --min=2 --max=10 --cpu-percent=70 -n <namespace>
  ```

**Noisy neighbor — cordon and drain the node:**
```bash
kubectl cordon <node-name>
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data
```

## Prevention

- Set CPU `requests` equal to the application's steady-state usage measured over one week; set `limits` to 2–3× requests to allow bursting.
- Configure HPA on CPU utilization so the application scales out before latency degrades.
- Add a latency SLO alert that fires when error budget burn rate is > 5× to catch degradation early.
- Instrument database queries with slow-query logging (threshold: 100ms) and review weekly.
- Use PodAntiAffinity rules to spread replicas across nodes and reduce noisy-neighbor risk.

## Related Alerts

- `HighLatency` — fires when P95 latency > SLO threshold for 5 minutes
- `KubeCPUThrottlingHigh` — fires when CPU throttling ratio > 25%
- `KubePodNotReady` — may co-fire if latency is high enough to fail readiness probes
