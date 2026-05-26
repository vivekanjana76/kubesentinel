# Safety Boundaries

KubeSentinel's `RealToolkit` wraps every write operation in a set of hard
safety guards (`agent/tools/safety.py`). These guards are non-negotiable: they
cannot be disabled via settings or flags. The *inputs* (allowed namespaces,
PR target branch) come from `.env`, but the guard logic itself is fixed.

The threat model is: **the LLM may hallucinate, be confused, or be the target
of a prompt-injection attack via malicious log content**. These guards are the
last line of defence before a destructive action reaches the cluster, GitHub,
or Slack.

---

## Guards

### 1. Namespace allowlist (`validate_namespace`)

**What it blocks:** Any Kubernetes operation targeting a namespace not in
`ALLOWED_NAMESPACES` (default: `kubesentinel`, `kubesentinel-demo`).

**Threat it addresses:** A confused or compromised LLM produces a fix
targeting `kube-system`, `monitoring`, or `default`. A patch to `kube-system`
could disable the cluster control plane. A patch to `default` could affect
workloads the agent was never meant to touch.

**How it works:**

```python
if namespace not in allowed:
    raise SafetyViolationError(...)
```

**Production equivalent:** A Kubernetes RBAC `ServiceAccount` scoped to only
the demo namespace(s). The agent's service account has no API Server access
outside those namespaces, so even if the guard were removed the API call would
fail with `403 Forbidden`. This guard catches the mistake before the round-trip.

---

### 2. Protected resource-kind block (`validate_resource_action`)

**What it blocks:** `delete` operations on `Namespace`, `PersistentVolume`,
`PersistentVolumeClaim`, `pv`, and `pvc` resource kinds.

**Threat it addresses:**
- Namespace deletion cascades to every resource inside it — all pods, services,
  configmaps, and secrets are gone immediately and unrecoverably.
- PV/PVC deletion can permanently destroy stateful application data. Many
  storage back-ends (EBS, Azure Disk) delete the underlying volume when the
  PV is deleted with `persistentVolumeReclaimPolicy: Delete`.

The agent may still *patch* these resources (e.g., annotate a PV). Only the
`delete` verb is blocked.

**Production equivalent:** Kubernetes admission webhooks (OPA Gatekeeper /
Kyverno) with a `deny` policy on `delete` operations for these kinds. Finalizer
policies on PVs/PVCs also provide a safety net at the storage layer.

---

### 3. PR target branch guard (`validate_pr_target`)

**What it blocks:** PRs that target `main` or any branch other than the value
in `PR_TARGET_BRANCH` (default: `develop`).

**Threat it addresses:** An LLM-generated PR that auto-merges or targets `main`
directly. Even with branch protection, a mistaken PR to `main` creates noise
and requires manual cleanup. This guard catches the mistake before the GitHub
API call.

**How it works:**

```python
if target_branch == "main":
    raise SafetyViolationError(...)
if target_branch != configured:
    raise SafetyViolationError(...)
```

**Production equivalent:** GitHub branch protection rules on `main` require
pull request review + status checks before merge. The guard is defence-in-depth
for cases where branch protection is misconfigured or missing.

---

### 4. Slack channel lock (`validate_slack_channel`)

**What it blocks:** Messages to any Slack channel other than the configured
`SLACK_INCIDENTS_CHANNEL`. Both `incidents` and `#incidents` forms are
normalised before comparison.

**Threat it addresses:** A hallucinated or injection-influenced channel name
that causes the agent to spam or leak incident context to unintended audiences.
If an attacker injects `channel: #general` via a pod log line, this guard
stops the message.

**Production equivalent:** Slack app configuration at the API level: restrict
the bot's channel access to a named set of channels. This can be done via Slack
Enterprise Grid channel restrictions or by the Slack app admin limiting the
app's channel membership.

---

### 5. Shell injection filter (`validate_command_safe`)

**What it blocks:** `kubectl` commands or patch bodies containing shell
metacharacters: semicolons (`;`), pipe characters (`|`), double ampersands
(`&&`), and backticks (`` ` ``).

**Threat it addresses:** Prompt-injection via log content. If a malicious
container writes `OOM; rm -rf /` to its logs, and the LLM includes that string
in a `command_or_diff` field, this guard stops the command before it reaches
the cluster. Single `&` is permitted (valid in some `kubectl` flag values);
the pattern catches `&&` as the chaining operator.

**How it works:**

```python
_SHELL_INJECTION_RE = re.compile(r"[;`]|\|+|&&")
```

**Production equivalent:** Submit all cluster changes directly via the
Kubernetes API (`kubernetes` Python client) rather than shelling out to
`kubectl`. API-first eliminates the shell injection surface entirely. This
guard is defence-in-depth for the cases where a command string is constructed
for logging, display, or templating purposes.

---

### 6. DRY_RUN mode (operational gate, not a `SafetyViolationError`)

`DRY_RUN=true` (the default) short-circuits all three write operations —
`apply_remediation`, `open_pr`, `post_slack` — before any of the above guards
are checked for the external call. Each method logs the action that *would*
have been taken and returns an `ActionLog` with `metadata.dry_run=True`.

This is not technically a `SafetyViolationError` guard, but it is the most
important operational safety control: **the agent is safe to run anywhere**
without credentials or cluster access as long as `DRY_RUN=true`.

Note: the namespace, resource-kind, PR-target, channel, and injection guards
still run in DRY_RUN mode. They validate the LLM's intent even when no external
call is made, so you can see violations in the logs during dry runs.

---

## Summary Table

| Guard | Module function | Raises on |
|---|---|---|
| Namespace allowlist | `validate_namespace` | `fix.namespace not in ALLOWED_NAMESPACES` |
| Protected resource | `validate_resource_action` | `verb=delete` on Namespace / PV / PVC |
| PR target branch | `validate_pr_target` | `target == 'main'` or not matching configured |
| Slack channel lock | `validate_slack_channel` | channel != `SLACK_INCIDENTS_CHANNEL` (normalised) |
| Shell injection | `validate_command_safe` | `;` `\|` `&&` `` ` `` found in command |
| DRY_RUN mode | (in each write method) | not raised — silently no-ops the write |

---

## Extending the guards

To add a new guard:

1. Add a function to `agent/tools/safety.py` that raises `SafetyViolationError`
   on violation.
2. Call it from the relevant `RealToolkit` write method *before* any external
   calls and *before* the `dry_run` check if you want it to fire during dry runs.
3. Add tests to `tests/tools/test_safety.py` for positive, negative, and
   edge-case inputs.

Guards should be stateless pure functions. They take only the data they need
to evaluate, not the entire `settings` or `fix` object — this makes them
trivially testable and composition-friendly.
