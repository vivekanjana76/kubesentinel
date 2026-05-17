# Node NotReady

## Symptoms

- `kubectl get nodes` shows a node with STATUS `NotReady`.
- Alert `KubeNodeNotReady` fires.
- Pods that were running on the affected node may transition to `Unknown` status after the `node-monitor-grace-period` (default 40s).
- After the `pod-eviction-timeout` (default 5 minutes), the controller manager begins evicting pods from the NotReady node and rescheduling them on healthy nodes.
- New pods may not be schedulable if there are insufficient resources on remaining nodes.
- `kubectl describe node <name>` shows a stale `Ready` condition timestamp and events like:
  ```
  Kubelet stopped posting node status.
  ```

## Root Cause

A node enters the `NotReady` state when the control plane loses contact with its kubelet. This happens for several reasons:

1. **Kubelet crash or hang**: the kubelet process on the node has exited or become unresponsive.
2. **Node OS crash or kernel panic**: the underlying VM or bare-metal host has crashed.
3. **Network partition**: the node cannot reach the API server (firewall rule change, VPC routing issue, NIC failure).
4. **Resource exhaustion**: extreme memory pressure causing OOM of the kubelet itself, or disk pressure causing kubelet failure.
5. **Certificate expiry**: the kubelet's TLS client certificate has expired and the API server rejects its connections.
6. **Cloud provider failure**: the underlying cloud instance (EC2, GCE, etc.) has been terminated or stopped.

## Investigation Steps

1. Identify all NotReady nodes and check the conditions:
   ```
   kubectl get nodes
   kubectl describe node <node-name> | grep -A10 "Conditions:"
   ```
   Check `Ready`, `MemoryPressure`, `DiskPressure`, `PIDPressure`, and `NetworkUnavailable` conditions.

2. Check whether the node is still reachable via SSH (if using cloud VMs):
   ```
   ssh <user>@<node-ip> "systemctl status kubelet"
   ```
   If SSH is unreachable, the issue is likely a cloud instance failure or network partition.

3. Read kubelet logs on the node (if accessible):
   ```
   journalctl -u kubelet -n 100 --no-pager
   ```
   Look for TLS errors, OOM messages, or panics.

4. Check cluster-level events:
   ```
   kubectl get events -A --sort-by='.lastTimestamp' | tail -30
   ```

5. Identify which pods are on the affected node and their current status:
   ```
   kubectl get pods -A -o wide | grep <node-name>
   ```

6. Check cloud provider console for the instance status (AWS EC2 status checks, GCP instance health, etc.).

7. Verify kubelet certificate expiry:
   ```
   ssh <node> "openssl x509 -in /var/lib/kubelet/pki/kubelet-client-current.pem -noout -dates"
   ```

## Resolution

**Kubelet is crashed — restart it:**
```bash
ssh <node>
systemctl restart kubelet
systemctl status kubelet
```
Monitor: `kubectl get node <node-name> -w`

**Cloud instance has failed — replace the node:**
1. Terminate the failed instance via the cloud console or CLI.
2. If using a node group / autoscaling group, the new instance will be provisioned automatically.
3. Ensure pods rescheduled successfully:
   ```
   kubectl get pods -A -o wide | grep -v Running
   ```

**Network partition:**
- Check VPC routing tables, security groups, and firewall rules for recent changes.
- Verify the node can reach the API server endpoint:
  ```
  ssh <node> "curl -k https://<api-server>:6443/healthz"
  ```

**Certificate expired:**
- Rotate kubelet certificates. On kubeadm clusters:
  ```
  kubeadm certs renew all
  systemctl restart kubelet
  ```

**Immediate mitigation — cordon the node to stop new scheduling:**
```bash
kubectl cordon <node-name>
```
Pods on the node will be rescheduled once eviction timeout is reached, or drain manually:
```bash
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --force
```

## Prevention

- Enable automatic kubelet certificate rotation: set `rotateCertificates: true` in the kubelet configuration.
- Use a managed node group (EKS Managed Node Groups, GKE node auto-provisioning) so failed instances are replaced automatically.
- Configure pod disruption budgets (PDB) on critical workloads to ensure availability during node failures.
- Monitor `kube_node_status_condition{condition="Ready",status="true"}` and alert if it drops to 0 for any node.
- Run at least 3 worker nodes in a production cluster to tolerate a single node failure without capacity impact.

## Related Alerts

- `KubeNodeNotReady` — fires when node Ready condition is not True for > 15 minutes
- `KubeNodeUnreachable` — fires faster (< 1 minute) when the node is completely unreachable
- `KubePodNotReady` — fires for pods on the affected node that become Unknown/Evicted
- `KubeNodeDiskPressure` — may precede NotReady if disk pressure kills the kubelet
