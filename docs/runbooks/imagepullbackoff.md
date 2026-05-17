# ImagePullBackOff

## Symptoms

- `kubectl get pods` shows `ImagePullBackOff` or `ErrImagePull` in the STATUS column.
- `kubectl describe pod <name>` shows an event like:
  ```
  Failed to pull image "registry.example.com/app:v1.2.3": rpc error: code = Unknown
  desc = failed to pull and unpack image: ... unauthorized: authentication required
  ```
  or:
  ```
  ... not found
  ```
- The pod never reaches `Running` state; `RESTARTS` stays at 0.
- Alert `KubeContainerWaitingReasonImagePullBackOff` fires.

## Root Cause

The kubelet on the node cannot pull the container image from the specified registry. The three most common causes:

1. **Registry authentication failure**: the `imagePullSecret` is missing, wrong, or expired.
2. **Image or tag does not exist**: typo in the image name or tag, or the tag was deleted/overwritten.
3. **Registry is unreachable**: network policy blocking egress, DNS resolution failure, or the registry is down.
4. **Rate limiting**: Docker Hub enforces pull limits for unauthenticated requests (100 pulls/6h per IP on free tier).

## Investigation Steps

1. Read the full event message for the exact error:
   ```
   kubectl describe pod <pod-name> -n <namespace>
   ```
   Scroll to the `Events:` section. The `Failed` event will contain the registry's HTTP error response.

2. Verify the image reference is correct:
   ```
   kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[*].image}'
   ```
   Manually confirm the tag exists:
   ```
   docker pull <image>:<tag>
   ```
   Or for a private registry, use `crane ls <registry>/<repo>` to list available tags.

3. Check whether the imagePullSecret exists and is correctly formatted:
   ```
   kubectl get secret <pull-secret-name> -n <namespace> -o yaml
   kubectl get serviceaccount default -n <namespace> -o yaml
   ```
   The secret type must be `kubernetes.io/dockerconfigjson`.

4. Confirm the secret is attached to the pod's service account or spec:
   ```
   kubectl get pod <pod-name> -n <namespace> -o yaml | grep -A5 "imagePullSecrets:"
   ```

5. Test DNS resolution from inside the cluster:
   ```
   kubectl run dns-test --rm -it --image=busybox --restart=Never -- nslookup registry.example.com
   ```

6. Check for Docker Hub rate limit (HTTP 429):
   ```
   curl -I https://registry-1.docker.io/v2/
   ```
   Response headers include `X-RateLimit-Remaining`.

## Resolution

**Authentication failure — recreate the pull secret:**
```bash
kubectl create secret docker-registry regcred \
  --docker-server=<registry> \
  --docker-username=<user> \
  --docker-password=<token> \
  --docker-email=<email> \
  -n <namespace>
```
Then reference it in the Deployment:
```yaml
spec:
  imagePullSecrets:
    - name: regcred
```

**Wrong image tag — correct the Deployment:**
```bash
kubectl set image deployment/<name> <container>=<correct-image>:<correct-tag> -n <namespace>
```

**Docker Hub rate limit — authenticate or use a mirror:**
- Point to a pull-through cache or mirror in containerd's `config.toml`.
- Or switch the image to a public ECR mirror: `public.ecr.aws/docker/library/<image>:<tag>`.

**Registry unreachable — check NetworkPolicy:**
```bash
kubectl get networkpolicy -n <namespace>
```
Ensure egress to the registry's IP/port (443) is allowed.

## Prevention

- Store all `imagePullSecrets` as Kubernetes secrets managed by an operator (e.g., external-secrets-operator) so they rotate automatically before expiry.
- Pin image tags to digests (`image@sha256:...`) in production to prevent silent tag overwrites.
- Mirror all external images to an internal registry (Harbor, ECR, Artifact Registry) to eliminate Docker Hub rate limits and external registry availability as a dependency.
- Add `helm lint` and `kubectl apply --dry-run=server` to CI to catch image name typos before deployment.

## Related Alerts

- `KubeContainerWaitingReasonImagePullBackOff` — fires when container wait reason is `ImagePullBackOff`
- `KubePodNotReady` — fires alongside since the pod never becomes Ready
