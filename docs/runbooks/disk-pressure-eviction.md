# Disk Pressure Eviction

## Symptoms

- `kubectl get nodes` shows a node with `DiskPressure` condition set to `True`.
- `kubectl get pods` shows pods with status `Evicted` on the affected node.
- Alert `KubeNodeDiskPressure` fires.
- `kubectl describe node <name>` shows events like:
  ```
  Evicting pod default/<pod-name> to reclaim disk space
  ```
- New pods scheduled to the node may be stuck in `Pending` if the node is tainted with `node.kubernetes.io/disk-pressure:NoSchedule`.

## Root Cause

The kubelet monitors two disk usage thresholds on each node:

- **`nodefs`**: the root filesystem where kubelet data (pod logs, emptyDir volumes) is stored. Default eviction threshold: 85% full (`imagefs.available < 15%`).
- **`imagefs`**: the container runtime's image storage. Default threshold: same.

Common causes of disk pressure:

1. **Log accumulation**: a verbose application writing to stdout/stderr fills the node's log storage. Kubernetes stores container logs under `/var/log/pods/` on the node.
2. **Container image bloat**: too many large images cached on the node consume imagefs.
3. **emptyDir volumes**: a pod using an `emptyDir` volume and writing large amounts of data.
4. **PersistentVolume full**: if the PV is on the node's local filesystem.
5. **Kubelet or system component writing large files**: core dumps, audit logs, etc.

## Investigation Steps

1. Identify which node is under pressure:
   ```
   kubectl get nodes -o custom-columns=NAME:.metadata.name,DISK-PRESSURE:.status.conditions[?(@.type=="DiskPressure")].status
   ```

2. Read disk usage on the node (requires node shell access):
   ```
   kubectl debug node/<node-name> -it --image=busybox -- df -h
   ```
   Or SSH to the node and run:
   ```
   df -h /
   du -sh /var/log/pods/* | sort -rh | head -20
   du -sh /var/lib/containerd/* | sort -rh | head -10
   ```

3. Identify which pods were evicted and from which namespace:
   ```
   kubectl get pods -A --field-selector=status.phase=Failed | grep Evicted
   ```

4. Check kubelet logs for eviction events:
   ```
   kubectl describe node <node-name> | grep -A20 "Events:"
   ```

5. Identify the pod generating the most log volume:
   ```
   du -sh /var/log/pods/*/ | sort -rh | head -10
   ```
   The directory name encodes `<namespace>_<pod-name>_<uid>`.

## Resolution

**Immediate — free disk space:**

1. Remove evicted pods (they consume no disk but add noise):
   ```
   kubectl delete pods -A --field-selector=status.phase=Failed
   ```

2. Prune unused container images on the node:
   ```
   crictl rmi --prune
   ```
   Or via containerd directly:
   ```
   ctr -n k8s.io images prune --all
   ```

3. If a specific pod is generating excessive logs, reduce its log level via a ConfigMap update and restart the deployment:
   ```
   kubectl set env deployment/<name> LOG_LEVEL=WARNING -n <namespace>
   ```

4. If an emptyDir volume is the culprit, add a `sizeLimit` to it:
   ```yaml
   volumes:
     - name: tmp
       emptyDir:
         sizeLimit: "1Gi"
   ```

**Drain the node if it cannot recover:**
```
kubectl cordon <node-name>
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --force
```

## Prevention

- Configure log rotation on the kubelet: set `--container-log-max-size=50Mi` and `--container-log-max-files=3` in the kubelet configuration.
- Set structured log levels via environment variables and enforce them in all services.
- Monitor disk usage proactively with `node_filesystem_avail_bytes` alert at 80% and 90% thresholds.
- Use `imagefs` on a separate volume or disk from `nodefs` on production nodes to prevent image cache from evicting pod workloads.
- Set `resources.ephemeral-storage` limits on pods that write to emptyDir volumes.

## Related Alerts

- `KubeNodeDiskPressure` — fires when the node's DiskPressure condition is True
- `NodeFilesystemAlmostOutOfSpace` — early-warning alert at 80% disk usage
- `NodeFilesystemSpaceFillingUp` — predictive alert based on fill rate
